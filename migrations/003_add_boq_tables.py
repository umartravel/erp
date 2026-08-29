"""
Simulasi Paket (BOQ) module -- schema baru.

Konsep: BOQ = Bill of Quantities. Setiap paket bisa punya beberapa BOQ /
skenario harga dengan breakdown biaya per item (hotel, tiket, visa, muthawwif,
transport, konsumsi, dll). BOQ Approved bisa dipakai untuk quote jamaah.

Alur:
- Sales bikin BOQ Draft -> Submit -> Pending Approval -> Mgmt Approve/Reject
- Mgmt/Admin bikin BOQ -> langsung Approved (auto-skip pending)
- BOQ Approved yg tidak link ke package_id bisa di-'convert' jadi packages row
- Multiple BOQ Approved per paket (mis: Reguler, Bintang 5 Upgrade, Promo)

Tabel baru:
- package_boq        header/skenario
- package_boq_items  line items breakdown
"""
import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS package_boq (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            -- NULL = BOQ utk paket baru (belum di-convert jadi packages row);
            -- ada nilai = BOQ utk paket existing (skenario alternatif).
            package_id INTEGER REFERENCES packages(id) ON DELETE SET NULL,
            name TEXT NOT NULL,
            -- Draft | Pending Approval | Approved | Rejected
            status TEXT NOT NULL DEFAULT 'Draft',
            target_pax INTEGER DEFAULT 45,
            -- JSON split kamar: {"quad": 20, "triple": 15, "double": 10}
            room_split TEXT,
            target_margin_pct REAL DEFAULT 15,
            notes TEXT,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            submitted_at TEXT,
            reviewed_by INTEGER REFERENCES users(id),
            reviewed_at TEXT,
            review_note TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_boq_package ON package_boq(package_id);
        CREATE INDEX IF NOT EXISTS idx_boq_status ON package_boq(status);

        CREATE TABLE IF NOT EXISTS package_boq_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            boq_id INTEGER NOT NULL REFERENCES package_boq(id) ON DELETE CASCADE,
            -- hotel_mekkah | hotel_madinah | tiket | visa | muthawwif |
            -- transport | konsumsi | ziyarah | handling | perlengkapan |
            -- vaksin | asuransi | margin | lain
            category TEXT NOT NULL,
            item_name TEXT NOT NULL,
            -- per_pax | per_room_per_night | per_group | per_pax_per_day
            unit TEXT NOT NULL DEFAULT 'per_pax',
            quantity REAL NOT NULL DEFAULT 1,
            unit_price INTEGER NOT NULL DEFAULT 0,
            -- Cached subtotal (quantity * unit_price). Ditulis router saat
            -- insert/update supaya list endpoint tidak perlu recompute.
            subtotal INTEGER NOT NULL DEFAULT 0,
            vendor_name TEXT,
            note TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_boq_items_boq ON package_boq_items(boq_id);
        """
    )


def down(conn: sqlite3.Connection) -> None:
    # Runner tidak invoke down otomatis. Manual rollback:
    #   DROP TABLE package_boq_items; DROP TABLE package_boq;
    raise NotImplementedError("Manual rollback: DROP TABLE package_boq_items; package_boq;")
