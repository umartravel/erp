"""
Formalize jamaah.external_id column.

BACKGROUND: kolom `jamaah.external_id` sudah ada di production umar_crm.db
(hasil script CSV import lama, ~2026-08) tetapi TIDAK pernah tercatat di
db.SCHEMA atau migration manapun. Dipakai oleh routes/marketing.py
(SELECT + LIKE search di /api/marketing/rows).

Schema drift ini kedeteksi oleh smoke test yang jalan pada fresh test DB:
kolom tidak ada -> query marketing_summary gagal dgn 500.

Fix: tambahkan sebagai migration formal. Existing production sudah punya
kolom -> ADD COLUMN akan raise "duplicate column" dan di-skip idempoten.
Fresh DB (test/dev baru) akan dapat kolom via migration ini.
"""
import sqlite3


def up(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ALTER TABLE jamaah ADD COLUMN external_id TEXT")
    except sqlite3.OperationalError as e:
        # Existing DB (production) sudah punya kolom -> abaikan.
        if "duplicate column name" not in str(e).lower():
            raise


def down(conn: sqlite3.Connection) -> None:
    # SQLite < 3.35 tidak support DROP COLUMN natively. Manual: rename table +
    # copy without external_id + drop old. Runner tidak invoke down otomatis.
    raise NotImplementedError("Rollback external_id butuh table rename manual.")
