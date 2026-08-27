"""
Baseline migration -- snapshot skema Umar CRM per 2026-08-28.

Migration ini men-establish state awal. Untuk DB yang sudah punya tabel-tabel
tersebut (production sekarang), semua CREATE/ALTER idempoten (IF NOT EXISTS +
try/except) sehingga tidak ada perubahan side-effect saat migration ini "dijalankan"
untuk kali pertama pada DB existing -- runner cukup mencatat bahwa 001 sudah
applied di tabel schema_migrations.

Untuk fresh DB, migration ini akan membuat semua tabel + kolom + index dari nol.

Sumber tunggal SCHEMA_STATEMENTS dan ALTER_STATEMENTS tetap di db.py supaya kalau
someday kita mau introspect skema baseline, cukup baca db.py. Migration setelah 001
harus menuliskan perubahan mereka SENDIRI (jangan modifikasi 001).
"""
import sqlite3


def up(conn: sqlite3.Connection) -> None:
    # Import lazily supaya tidak ada circular import saat db.py sedang mem-boot runner.
    import db

    for stmt in db.SCHEMA:
        conn.execute(stmt)
    for q in db.ALTER_QUERIES:
        try:
            conn.execute(q)
        except sqlite3.OperationalError:
            # kolom sudah ada / index sudah ada / DROP kalau tabel sudah tidak ada. Aman diabaikan.
            pass


def down(conn: sqlite3.Connection) -> None:
    # Baseline tidak reversible -- kalau butuh rollback fresh DB, delete file DB.
    raise RuntimeError("Baseline tidak reversible. Hapus umar_crm.db untuk reset ke pre-baseline.")
