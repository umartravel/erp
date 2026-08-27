"""
CLI helper untuk migration system (dipanggil manual dari terminal).

Usage:
    python migrate.py status              # list applied + pending migrations
    python migrate.py create <name>       # scaffold file 00N_<name>.py
    python migrate.py up                  # apply pending migrations

Runner utamanya ada di db._migrate() -- CLI ini hanya wrapper untuk operasi
terminal (baseline sudah otomatis apply saat FastAPI boot lewat init_db()).
"""
import pathlib
import re
import sys

BASE_DIR = pathlib.Path(__file__).parent
MIG_DIR = BASE_DIR / "migrations"

SCAFFOLD = '''"""
{description}
"""
import sqlite3


def up(conn: sqlite3.Connection) -> None:
    # TODO: tulis DDL di sini. Contoh:
    # conn.execute("ALTER TABLE users ADD COLUMN xxx TEXT")
    # conn.execute("CREATE INDEX IF NOT EXISTS idx_xxx ON users(xxx)")
    pass


def down(conn: sqlite3.Connection) -> None:
    # Optional: reverse migration untuk keperluan rollback dev.
    # Runner tidak invoke down otomatis -- ini stub untuk keperluan manual.
    raise NotImplementedError("Migration ini belum punya down().")
'''


def _next_version() -> str:
    existing = sorted(MIG_DIR.glob("[0-9][0-9][0-9]_*.py"))
    if not existing:
        return "001"
    last = existing[-1].stem[:3]
    return f"{int(last) + 1:03d}"


def cmd_status():
    import db
    applied, pending = db.migrations_status()
    print(f"Applied ({len(applied)}):")
    if not applied:
        print("  (kosong -- migration belum pernah jalan)")
    else:
        for r in applied:
            print(f"  {r['version']}  {r['applied_at']}")
    print()
    print(f"Pending ({len(pending)}):")
    if not pending:
        print("  (semua up-to-date)")
    else:
        for v in pending:
            print(f"  {v}")


def cmd_create(name: str):
    if not name:
        print("Usage: python migrate.py create <name>", file=sys.stderr)
        sys.exit(2)
    slug = re.sub(r"[^a-z0-9_]+", "_", name.lower().strip("_"))
    if not slug:
        print("Nama tidak valid.", file=sys.stderr)
        sys.exit(2)
    version = _next_version()
    filename = MIG_DIR / f"{version}_{slug}.py"
    if filename.exists():
        print(f"File sudah ada: {filename}", file=sys.stderr)
        sys.exit(2)
    filename.write_text(SCAFFOLD.format(description=slug.replace("_", " ")))
    print(f"Created: {filename.relative_to(BASE_DIR)}")


def cmd_up():
    import db
    db._migrate()
    print("Migration selesai.")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    cmd = sys.argv[1]
    if cmd == "status":
        cmd_status()
    elif cmd == "create":
        cmd_create(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "up":
        cmd_up()
    else:
        print(f"Unknown command: {cmd}\n")
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
