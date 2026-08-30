"""
Wire BOQ ke Jamaah -- Phase 4b.

Tambah 3 kolom di jamaah untuk melacak BOQ yang dipakai saat register jamaah
+ snapshot harganya (immutable audit trail):

- boq_id             pointer ke package_boq(id). NULL utk jamaah lama atau
                     paket tanpa BOQ Approved.
- boq_snapshot_price harga/pax yang di-quote saat register. Immutable meskipun
                     BOQ mgmt kemudian diubah -- jamaah lama pegang harga yang
                     mereka setujui.
- boq_snapshot_at    timestamp snapshot terakhir (di-update kalau BOQ_id
                     diubah lewat edit jamaah = re-snapshot).

Backward compat:
- Jamaah lama: boq_id NULL -> flow harga existing tetap (packages.price_*
  sesuai room_type). Zero-touch.
- Paket tanpa BOQ Approved: register jalan seperti dulu.
- Paket dengan BOQ Approved: register WAJIB pilih boq_id (gate di endpoint,
  bukan schema).
"""
import sqlite3


def up(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jamaah)").fetchall()}
    if "boq_id" not in cols:
        conn.execute("ALTER TABLE jamaah ADD COLUMN boq_id INTEGER REFERENCES package_boq(id)")
    if "boq_snapshot_price" not in cols:
        conn.execute("ALTER TABLE jamaah ADD COLUMN boq_snapshot_price INTEGER")
    if "boq_snapshot_at" not in cols:
        conn.execute("ALTER TABLE jamaah ADD COLUMN boq_snapshot_at TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jamaah_boq ON jamaah(boq_id)")


def down(conn: sqlite3.Connection) -> None:
    raise NotImplementedError(
        "SQLite DROP COLUMN memerlukan table rebuild. Manual rollback:\n"
        "  DROP INDEX idx_jamaah_boq;\n"
        "  (table rebuild dgn SELECT kolom-non-BOQ jika perlu)\n"
        "Sebaiknya JANGAN rollback -- kolom nullable + tidak breaking."
    )
