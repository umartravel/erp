"""
CLI backup + restore utk Umar CRM.

Yang di-backup:
- umar_crm.db (sqlite; via `VACUUM INTO` supaya konsisten walau ada writer aktif).
- uploads_private/ (KTP/KK/paspor/vaksin jamaah -- PII, wajib backup).

Yang TIDAK di-backup:
- public/ (static assets, ada di git).
- .venv/, __pycache__/ (deps + cache).
- .backup/ sendiri (biar tidak infinite recursion).

Struktur output:
  .backup/umar_backup_YYYYMMDD_HHMMSS/
    - umar_crm.db
    - uploads_private.zip
    - manifest.json   # {created_at, git_commit, sizes, sha256, migrations}

Sub-command:
  python backup.py create           # bikin backup baru
  python backup.py list             # list backup yg ada
  python backup.py verify <NAME>    # verify checksum manifest
  python backup.py restore <NAME>   # replace DB+uploads dari backup (bikin
                                    #   pre-restore-* dulu utk rollback)
"""
import argparse
import datetime as _dt
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = Path(os.environ.get("UMAR_DB_FILE", BASE_DIR / "umar_crm.db"))
UPLOADS_DIR = BASE_DIR / "uploads_private"
BACKUP_ROOT = BASE_DIR / ".backup"
MANIFEST_NAME = "manifest.json"
DB_BACKUP_NAME = "umar_crm.db"
UPLOADS_BACKUP_NAME = "uploads_private.zip"


# ============================================================================
# helpers
# ============================================================================
def _sha256_file(path: Path, chunk: int = 65536) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _git_commit_short() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=BASE_DIR, capture_output=True, text=True, check=True, timeout=5,
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _sqlite_backup(src: Path, dst: Path) -> None:
    """VACUUM INTO -- konsisten snapshot walau server tetap nulis. Fallback:
    sqlite3 backup API kalau VACUUM INTO gagal (misal DB dienkripsi extension)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    con = sqlite3.connect(str(src))
    try:
        # VACUUM INTO -- tersedia sejak SQLite 3.27 (Python 3.10+ pasti punya).
        con.execute(f"VACUUM INTO '{dst.as_posix()}'")
    except sqlite3.OperationalError as e:  # noqa: BLE001
        # Fallback backup API (menghasilkan copy WAL-safe juga).
        print(f"[backup] VACUUM INTO gagal ({e}); pakai sqlite3 backup API")
        with sqlite3.connect(str(dst)) as dst_con:
            con.backup(dst_con)
    finally:
        con.close()


def _zip_uploads(src_dir: Path, dst_zip: Path) -> int:
    """Zip seluruh isi uploads_private recursively. Return jumlah file."""
    dst_zip.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(dst_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        if src_dir.exists():
            for p in src_dir.rglob("*"):
                if p.is_file():
                    zf.write(p, arcname=p.relative_to(src_dir))
                    count += 1
    return count


def _read_schema_migrations() -> list[str]:
    """Baca daftar migration yg sudah applied ke DB (audit trail)."""
    if not DB_FILE.exists():
        return []
    try:
        con = sqlite3.connect(str(DB_FILE))
        try:
            rows = con.execute(
                "SELECT name FROM schema_migrations ORDER BY applied_at"
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            con.close()
    except sqlite3.Error:
        return []  # table belum ada (fresh DB)


# ============================================================================
# create
# ============================================================================
def cmd_create() -> Path:
    if not DB_FILE.exists():
        raise SystemExit(f"[backup] DB tidak ditemukan: {DB_FILE}")

    ts = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = BACKUP_ROOT / f"umar_backup_{ts}"
    out_dir.mkdir(parents=True, exist_ok=False)

    db_dst = out_dir / DB_BACKUP_NAME
    _sqlite_backup(DB_FILE, db_dst)

    uploads_dst = out_dir / UPLOADS_BACKUP_NAME
    uploads_count = _zip_uploads(UPLOADS_DIR, uploads_dst)

    manifest = {
        "created_at": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        "git_commit": _git_commit_short(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "db_bytes": db_dst.stat().st_size,
        "db_sha256": _sha256_file(db_dst),
        "uploads_bytes": uploads_dst.stat().st_size,
        "uploads_sha256": _sha256_file(uploads_dst),
        "uploads_file_count": uploads_count,
        "schema_migrations": _read_schema_migrations(),
    }
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"[backup] OK -> {out_dir}")
    print(f"  DB       {manifest['db_bytes']:>10} bytes  sha256={manifest['db_sha256'][:12]}...")
    print(f"  Uploads  {manifest['uploads_bytes']:>10} bytes  ({uploads_count} files)")
    print(f"  Commit   {manifest['git_commit']}")
    return out_dir


# ============================================================================
# list
# ============================================================================
def cmd_list() -> list[Path]:
    if not BACKUP_ROOT.exists():
        print("[backup] belum ada backup di", BACKUP_ROOT)
        return []
    entries = sorted([p for p in BACKUP_ROOT.iterdir() if p.is_dir() and p.name.startswith("umar_backup_")])
    if not entries:
        print("[backup] belum ada backup di", BACKUP_ROOT)
        return []
    print(f"[backup] {len(entries)} backup di {BACKUP_ROOT}:")
    for p in entries:
        mf = p / MANIFEST_NAME
        if mf.exists():
            m = json.loads(mf.read_text(encoding="utf-8"))
            size_mb = (m["db_bytes"] + m["uploads_bytes"]) / (1024 * 1024)
            print(f"  {p.name}  {size_mb:6.2f} MB  commit={m['git_commit']}  "
                  f"migrations={len(m['schema_migrations'])}")
        else:
            print(f"  {p.name}  <manifest hilang>")
    return entries


# ============================================================================
# verify
# ============================================================================
def cmd_verify(name: str) -> bool:
    target = BACKUP_ROOT / name
    if not target.is_dir():
        raise SystemExit(f"[backup] backup tidak ada: {target}")
    manifest = json.loads((target / MANIFEST_NAME).read_text(encoding="utf-8"))
    db_ok = _sha256_file(target / DB_BACKUP_NAME) == manifest["db_sha256"]
    uploads_ok = _sha256_file(target / UPLOADS_BACKUP_NAME) == manifest["uploads_sha256"]
    print(f"[verify] {target.name}")
    print(f"  DB       sha256 {'OK' if db_ok else 'MISMATCH'}")
    print(f"  Uploads  sha256 {'OK' if uploads_ok else 'MISMATCH'}")
    ok = db_ok and uploads_ok
    if not ok:
        raise SystemExit(1)
    return ok


# ============================================================================
# restore
# ============================================================================
def cmd_restore(name: str) -> Path:
    target = BACKUP_ROOT / name
    if not target.is_dir():
        raise SystemExit(f"[backup] backup tidak ada: {target}")
    if not cmd_verify(name):
        raise SystemExit("[backup] verify gagal -- abort restore")

    # Safety net: pindahin state saat ini ke .backup/pre-restore-<ts>/ dulu
    # supaya bisa rollback kalau restore ternyata bermasalah.
    ts = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    safety = BACKUP_ROOT / f"pre-restore_{ts}"
    safety.mkdir(parents=True, exist_ok=False)
    if DB_FILE.exists():
        shutil.copy2(DB_FILE, safety / DB_BACKUP_NAME)
    if UPLOADS_DIR.exists():
        _zip_uploads(UPLOADS_DIR, safety / UPLOADS_BACKUP_NAME)
    print(f"[restore] state lama disimpan di {safety}")

    # Replace DB.
    shutil.copy2(target / DB_BACKUP_NAME, DB_FILE)
    # WAL / SHM sisa write dari sesi sebelumnya bakal bikin corrupt read
    # kalau tidak dibuang. Aman: mereka cuma cache write-ahead log.
    for suffix in ("-wal", "-shm"):
        stale = DB_FILE.parent / (DB_FILE.name + suffix)
        if stale.exists():
            stale.unlink()

    # Replace uploads: hapus dulu, extract ulang.
    if UPLOADS_DIR.exists():
        shutil.rmtree(UPLOADS_DIR)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target / UPLOADS_BACKUP_NAME, "r") as zf:
        zf.extractall(UPLOADS_DIR)

    print(f"[restore] OK -- DB dan uploads diganti dari {target.name}")
    print(f"[restore] Rollback: pindahkan file dari {safety} ke tempat semula.")
    return target


# ============================================================================
# CLI
# ============================================================================
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create", help="bikin backup baru")
    sub.add_parser("list", help="list backup yg ada")
    v = sub.add_parser("verify", help="verify checksum manifest")
    v.add_argument("name", help="nama folder backup, mis. umar_backup_20260830_120000")
    r = sub.add_parser("restore", help="restore DB+uploads dari backup")
    r.add_argument("name", help="nama folder backup, mis. umar_backup_20260830_120000")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    if args.cmd == "create":
        cmd_create()
    elif args.cmd == "list":
        cmd_list()
    elif args.cmd == "verify":
        cmd_verify(args.name)
    elif args.cmd == "restore":
        cmd_restore(args.name)


if __name__ == "__main__":
    # sys.stdout write() bakal UnicodeEncodeError di Windows cp1252 kalau ada
    # emoji/non-ASCII di message; force UTF-8 stream.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    main()
