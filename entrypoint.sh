#!/usr/bin/env bash
# Entrypoint Railway UMAR ERP.
# 1. Install Litestream kalau belum ada (idempotent).
# 2. Restore umar_crm.db dari R2 kalau file belum ada (first boot / disaster recovery).
# 3. Start uvicorn.
#
# Env vars diperlukan:
#   UMAR_DB_FILE          - path SQLite di persistent volume (mis. /data/umar_crm.db)
#   R2_BUCKET             - nama bucket Cloudflare R2
#   R2_ENDPOINT           - S3 API endpoint (https://xxx.r2.cloudflarestorage.com)
#   R2_ACCESS_KEY         - Access Key ID
#   R2_SIGNING            - Secret Access Key (renamed dari R2_SECRET_KEY;
#                           Railway Railpack treat *_SECRET_* sbg BuildKit secret
#                           yang butuh mount khusus, cause build fail).
#   PORT                  - port yang di-inject Railway
set -euo pipefail

DB_PATH="${UMAR_DB_FILE:-/data/umar_crm.db}"
DB_DIR="$(dirname "$DB_PATH")"
mkdir -p "$DB_DIR"

# ---- Install Litestream jika belum ada ----
# Railway container tidak include curl/wget, pakai Python urllib (built-in).
# Extract ke /tmp/bin (writable) karena /usr/local/bin sering read-only.
LITESTREAM_BIN=/tmp/bin/litestream
if [ ! -x "$LITESTREAM_BIN" ]; then
  echo "[entrypoint] Litestream tidak ada, install v0.5.17 via Python..."
  mkdir -p /tmp/bin
  python3 <<'PYEOF'
import urllib.request, tarfile, os, stat
url = "https://github.com/benbjohnson/litestream/releases/download/v0.5.17/litestream-0.5.17-linux-x86_64.tar.gz"
tgz = "/tmp/litestream.tgz"
print(f"[entrypoint] Downloading {url}")
urllib.request.urlretrieve(url, tgz)
with tarfile.open(tgz) as t:
    t.extractall("/tmp/bin/")
os.remove(tgz)
os.chmod("/tmp/bin/litestream", 0o755)
print("[entrypoint] Extract OK")
PYEOF
  export PATH="/tmp/bin:$PATH"
  echo "[entrypoint] Litestream installed: $($LITESTREAM_BIN version)"
else
  export PATH="/tmp/bin:$PATH"
fi

# ---- Generate litestream config sementara dari env vars ----
LITESTREAM_YML=/tmp/litestream.yml
cat > "$LITESTREAM_YML" <<EOF
dbs:
  - path: $DB_PATH
    replicas:
      - type: s3
        bucket: ${R2_BUCKET:?R2_BUCKET tidak set}
        path: umar_crm
        endpoint: ${R2_ENDPOINT:?R2_ENDPOINT tidak set}
        region: auto
        access-key-id: ${R2_ACCESS_KEY:?R2_ACCESS_KEY tidak set}
        secret-access-key: ${R2_SIGNING:?R2_SIGNING tidak set}
        sync-interval: 1s
        snapshot-interval: 1h
        retention: 24h
        force-path-style: true
EOF

# ---- Restore DB dari R2 kalau belum ada ----
if [ ! -f "$DB_PATH" ]; then
  echo "[entrypoint] $DB_PATH tidak ada, restore dari R2..."
  "$LITESTREAM_BIN" restore -config "$LITESTREAM_YML" "$DB_PATH" || {
    echo "[entrypoint] Restore gagal. Kemungkinan snapshot R2 tidak ada."
    echo "[entrypoint] App akan boot dengan DB kosong (schema baru via migrations)."
  }
  if [ -f "$DB_PATH" ]; then
    echo "[entrypoint] Restore sukses, size: $(stat -c%s "$DB_PATH") bytes"
  fi
else
  echo "[entrypoint] $DB_PATH sudah ada, skip restore. Litestream continues sync."
fi

# ---- Emergency admin password reset ----
# Kalau env RESET_ADMIN_PASSWORD di-set, reset password admin ke nilai tsb.
# Hapus env var setelah login sukses supaya tidak re-reset di next redeploy.
if [ -n "${RESET_ADMIN_PASSWORD:-}" ]; then
  echo "[entrypoint] RESET_ADMIN_PASSWORD detected, resetting admin password..."
  python3 <<PYEOF
import bcrypt, sqlite3, os
new_pass = os.environ.get("RESET_ADMIN_PASSWORD")
db_path = os.environ.get("UMAR_DB_FILE", "/data/umar_crm.db")
h = bcrypt.hashpw(new_pass.encode(), bcrypt.gensalt()).decode()
c = sqlite3.connect(db_path)
cur = c.execute(
    "UPDATE users SET password_hash=?, must_change_password=0 WHERE username=?",
    (h, "admin"),
)
c.commit()
c.close()
print(f"[entrypoint] Admin password reset: {cur.rowcount} row updated")
print("[entrypoint] REMINDER: hapus env RESET_ADMIN_PASSWORD setelah login sukses.")
PYEOF
fi

# ---- Start uvicorn ----
echo "[entrypoint] Start uvicorn app:app --port ${PORT:-8000}"
exec uvicorn app:app --host 0.0.0.0 --port "${PORT:-8000}"
