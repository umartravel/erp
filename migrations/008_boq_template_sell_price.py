"""
BOQ Template Enhancement -- Phase 6d-a.

Menambah 3 kolom di boq_template_items untuk mendukung workflow template yg
lebih kaya (dari analisa Excel Template BOQ UMAR):

- sell_price INTEGER (nullable)
  Harga jual per item. NULL = tidak set (template hanya simpan HPP, harga jual
  di-derive dari margin BOQ). Kalau ada value > unit_price, saat apply template
  ke BOQ akan otomatis create 2 items:
    - bucket=hpp,    unit_price=unit_price          (HPP cost)
    - bucket=margin, unit_price=sell_price - unit_price (margin selisih)
  Ini merepresentasikan konsep Excel "harga jual per item".

- variant TEXT (nullable)
  Subcategory untuk grouping report. Contoh: 'Minimalis', 'Full Set',
  'Koper Only'. Category tetap enum (perlengkapan/hotel/tiket/dll) supaya
  report per kategori tidak pecah. Variant free text.

- optional INTEGER DEFAULT 0
  0 = required (auto ter-copy saat template di-apply).
  1 = optional (user pilih via checklist di apply modal). Contoh: Bukhur,
  Al Baik yang tidak semua paket dapat.

Backward compat:
- Template items legacy: semua kolom baru NULL/0 -> apply behavior identik
  dgn Phase 3b (1 item per template item, semua required, tidak ada margin
  auto-generated).
- Endpoint template CRUD (create/update) accept field baru (Phase 6d-a),
  tanpa merusak caller lama yg tidak kirim field ini.
"""
import sqlite3


def up(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(boq_template_items)").fetchall()}
    if "sell_price" not in cols:
        conn.execute(
            "ALTER TABLE boq_template_items ADD COLUMN sell_price INTEGER"
        )
    if "variant" not in cols:
        conn.execute(
            "ALTER TABLE boq_template_items ADD COLUMN variant TEXT"
        )
    if "optional" not in cols:
        conn.execute(
            "ALTER TABLE boq_template_items ADD COLUMN optional INTEGER NOT NULL DEFAULT 0"
        )


def down(conn: sqlite3.Connection) -> None:
    raise NotImplementedError(
        "SQLite DROP COLUMN memerlukan table rebuild. Manual rollback:\n"
        "  1. Backup DB dulu.\n"
        "  2. CREATE TABLE boq_template_items_new (...tanpa 3 kolom baru...);\n"
        "  3. INSERT INTO ...new SELECT (kolom lama) FROM boq_template_items;\n"
        "  4. DROP TABLE boq_template_items; ALTER RENAME.\n"
        "Sebaiknya JANGAN rollback -- semua kolom baru nullable/default, "
        "zero breaking change."
    )
