"""
Phase EX-2: Expense Lines category_id FK + backfill.

Sebelumnya `expense_lines.category` adalah TEXT bebas dengan 6 hard-coded Type
di frontend (Transportasi/Akomodasi/Konsumsi/Komunikasi/ATK/Lainnya). Tidak
terhubung ke master `expense_categories` -- akibatnya field kategori di line
paralel tapi tidak sync dengan `expense_reports.category_id` (yang mengalir ke
transactions.category_id saat Paid).

Migration ini:
1. Tambah kolom `expense_lines.category_id` FK ke expense_categories.
2. Backfill 6 Type hard-coded ke subkategori existing (best-effort).
3. Kolom `category` (TEXT) tetap ada untuk backward-compat + fallback display
   kalau kelak sistem membaca line lama tanpa category_id.
4. Idempotent lewat PRAGMA table_info.
"""
import sqlite3


# Map 6 Type frontend lama -> nama subkategori di expense_categories seed.
# Best-effort match dari _EXPENSE_TREE di migrations/011_expense_categories.py.
_BACKFILL_MAP = {
    "Transportasi": "Transport Operasional",
    "Akomodasi": "Hotel Transit",
    "Konsumsi": "Konsumsi Karyawan",
    "Komunikasi": "Internet & Telpon",
    "ATK": "ATK",
    "Lainnya": "Insidentil",
}


def up(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(expense_lines)").fetchall()}
    if "category_id" not in cols:
        conn.execute("ALTER TABLE expense_lines ADD COLUMN category_id INTEGER")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_expense_lines_category "
            "ON expense_lines (category_id)"
        )

    # Backfill: cocokkan text lama ke subkategori master (case-insensitive).
    for old_text, subcat_name in _BACKFILL_MAP.items():
        row = conn.execute(
            "SELECT id FROM expense_categories "
            "WHERE name = ? AND is_active = 1 LIMIT 1",
            (subcat_name,)).fetchone()
        if not row:
            continue
        cid = row[0]
        conn.execute(
            "UPDATE expense_lines SET category_id = ? "
            "WHERE category_id IS NULL "
            "AND LOWER(TRIM(category)) = LOWER(?)",
            (cid, old_text))

    # Fallback: line dengan text `category` di luar 6 Type standar
    # -> map ke "Insidentil" supaya tidak orphan.
    fallback_row = conn.execute(
        "SELECT id FROM expense_categories "
        "WHERE name = 'Insidentil' AND is_active = 1 LIMIT 1"
    ).fetchone()
    if fallback_row:
        conn.execute(
            "UPDATE expense_lines SET category_id = ? "
            "WHERE category_id IS NULL "
            "AND category IS NOT NULL AND TRIM(category) != ''",
            (fallback_row[0],))
