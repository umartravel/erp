"""
Phase SS-1.6 (2026-09-21): Drop UNIQUE constraint dari jamaah.nik.

Root cause: NIK UNIQUE bikin sync Supabase fail 120x karena:
1. 53 rows di Supabase closings punya NIK="" (empty string)
2. 14+ rows punya NIK="3,27512E+15" (scientific notation dari Excel bug)
3. Real cases: 1 jamaah booking 2 paket = 2 closings dgn NIK sama.

Semua legitimate untuk sync. UMAR treat jamaah row per booking, bukan
per orang -- jadi NIK duplicate normal. UNIQUE constraint sisa dari
CSV import baseline lama yg assume 1 person = 1 row.

SQLite tidak support DROP CONSTRAINT langsung -- harus recreate table.
Idempotent: cek CREATE TABLE sql apakah masih ada 'UNIQUE' di nik line.
"""
import sqlite3


def up(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jamaah'"
    ).fetchone()
    if not row or not row[0]:
        return
    sql = row[0]
    has_unique = False
    for line in sql.split(","):
        norm = line.strip().lower()
        if norm.startswith("nik ") and "unique" in norm:
            has_unique = True
            break
    if not has_unique:
        return

    cols_info = conn.execute("PRAGMA table_info(jamaah)").fetchall()
    col_names = [c[1] for c in cols_info]
    col_list = ", ".join(col_names)

    lines = sql.split(",")
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("nik "):
            fixed = line.replace(" UNIQUE", "").replace(" unique", "")
            new_lines.append(fixed)
        else:
            new_lines.append(line)
    new_sql = ",".join(new_lines)
    new_sql = new_sql.replace("CREATE TABLE jamaah", "CREATE TABLE jamaah_new_temp")
    new_sql = new_sql.replace("CREATE TABLE IF NOT EXISTS jamaah",
                              "CREATE TABLE jamaah_new_temp")

    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute(new_sql)
        conn.execute(f"INSERT INTO jamaah_new_temp ({col_list}) "
                     f"SELECT {col_list} FROM jamaah")
        conn.execute("DROP TABLE jamaah")
        conn.execute("ALTER TABLE jamaah_new_temp RENAME TO jamaah")
    finally:
        conn.execute("PRAGMA foreign_keys=ON")

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_jamaah_external_id "
        "ON jamaah(external_id) WHERE external_id IS NOT NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jamaah_supabase_missing "
        "ON jamaah(supabase_missing_since) WHERE supabase_missing_since IS NOT NULL"
    )
