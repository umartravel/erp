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
#   R2_SECRET_KEY         - Secret Access Key
#   PORT                  - port yang di-inject Railway
set -euo pipefail

DB_PATH="${UMAR_DB_FILE:-/data/umar_crm.db}"
DB_DIR="$(dirname "$DB_PATH")"
mkdir -p "$DB_DIR"

# ---- Install Litestream jika belum ada ----
if ! command -v litestream >/dev/null 2>&1; then
  echo "[entrypoint] Litestream tidak ada, install v0.5.17..."
  curl -sSfL \
    "https://github.com/benbjohnson/litestream/releases/download/v0.5.17/litestream-0.5.17-linux-x86_64.tar.gz" \
    -o /tmp/litestream.tgz
  tar -xzf /tmp/litestream.tgz -C /usr/local/bin/
  rm /tmp/litestream.tgz
  echo "[entrypoint] Litestream installed: $(litestream version)"
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
        secret-access-key: ${R2_SECRET_KEY:?R2_SECRET_KEY tidak set}
        sync-interval: 1s
        snapshot-interval: 1h
        retention: 24h
        force-path-style: true
EOF

# ---- Restore DB dari R2 kalau belum ada ----
if [ ! -f "$DB_PATH" ]; then
  echo "[entrypoint] $DB_PATH tidak ada, restore dari R2..."
  litestream restore -config "$LITESTREAM_YML" "$DB_PATH" || {
    echo "[entrypoint] Restore gagal. Kemungkinan snapshot R2 tidak ada."
    echo "[entrypoint] App akan boot dengan DB kosong (schema baru via migrations)."
  }
  if [ -f "$DB_PATH" ]; then
    echo "[entrypoint] Restore sukses, size: $(stat -c%s "$DB_PATH") bytes"
  fi
else
  echo "[entrypoint] $DB_PATH sudah ada, skip restore. Litestream continues sync."
fi

# ---- Start uvicorn ----
echo "[entrypoint] Start uvicorn app:app --port ${PORT:-8000}"
exec uvicorn app:app --host 0.0.0.0 --port "${PORT:-8000}"
