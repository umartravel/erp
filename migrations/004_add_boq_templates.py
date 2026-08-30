"""
BOQ Template preset -- Phase 3b.

Tabel baru untuk menyimpan template line items standar (mis. "Umroh Reguler
9 Hari") supaya user tidak perlu re-input item hotel/tiket/visa/muthawwif
dari nol setiap bikin BOQ baru. Sekali klik "Load from Template" -> semua
line items ter-populate ke BOQ Draft.

Template hanya bisa dibuat/diubah oleh mgmt/admin (governance harga preset).
Sales/ops boleh baca + apply.

Tabel baru:
- boq_templates        header/nama preset
- boq_template_items   line items preset

Design note: TIDAK ada FK ke package_boq -- template independen dari BOQ.
Apply-template = copy items (bukan referensi hidup) supaya BOQ yg sudah
dibuat tidak berubah kalau template kemudian di-edit.
"""
import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS boq_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS boq_template_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER NOT NULL REFERENCES boq_templates(id) ON DELETE CASCADE,
            -- Kategori & unit valid = superset yg sama dengan package_boq_items
            -- (hotel_mekkah | hotel_madinah | tiket | ...)
            category TEXT NOT NULL,
            item_name TEXT NOT NULL,
            -- per_pax | per_room_per_night | per_group | per_pax_per_day
            unit TEXT NOT NULL DEFAULT 'per_pax',
            quantity REAL NOT NULL DEFAULT 1,
            unit_price INTEGER NOT NULL DEFAULT 0,
            vendor_name TEXT,
            note TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_boq_tmpl_items_tmpl ON boq_template_items(template_id);
        """
    )


def down(conn: sqlite3.Connection) -> None:
    raise NotImplementedError("Manual rollback: DROP TABLE boq_template_items; boq_templates;")
