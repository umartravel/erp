"""
Phase F1b: Wiring transactions.category_id ke seluruh flow.

Sebelumnya Phase F1 (migration 011) hanya kasih category_id ke:
- 281 rows historis 'payment' (backfill jamaah DP) -> Payment Jamaah

Migration 012 ini:
1. Tambah kolom `category_id` di expense_reports + procurement supaya user
   bisa pilih kategori saat submit. Dipakai routes untuk propagate ke
   transactions saat Paid. Nullable, di-populate via UI Phase F1b-2/F1b-3.
2. Backfill retroactive rows di transactions berdasar text category yg lama.
   'expense_report' dan 'procurement_payment' tidak di-backfill di sini --
   akan diisi setelah user pilih kategori di UI Phase F1b-2 & F1b-3.
"""
import sqlite3


_BACKFILL_MAP = {
    "payment": "Payment Jamaah",
    "refund": "Refund Jamaah",
    "payroll": "Gaji Pokok + Tunjangan",
    "commission": "Komisi Agen",  # nama actual di code, bukan "komisi"
    "operational": "Extras Jamaah (koper/seragam)",
}


def up(conn: sqlite3.Connection) -> None:
    er_cols = {r[1] for r in conn.execute("PRAGMA table_info(expense_reports)").fetchall()}
    if "category_id" not in er_cols:
        conn.execute("ALTER TABLE expense_reports ADD COLUMN category_id INTEGER")

    pc_cols = {r[1] for r in conn.execute("PRAGMA table_info(procurement)").fetchall()}
    if "category_id" not in pc_cols:
        conn.execute("ALTER TABLE procurement ADD COLUMN category_id INTEGER")

    for text_cat, subcat_name in _BACKFILL_MAP.items():
        row = conn.execute(
            "SELECT id FROM expense_categories WHERE name = ? LIMIT 1",
            (subcat_name,)).fetchone()
        if not row:
            continue
        cid = row[0]
        conn.execute(
            "UPDATE transactions SET category_id = ? "
            "WHERE category = ? AND category_id IS NULL",
            (cid, text_cat))
