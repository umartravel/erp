"""
BOQ Room Split -- Phase 4a.

Tambah 2 kolom di package_boq untuk selisih harga per pax vs base QUAD:
- extra_triple: Rp/pax utk TRIPLE (occupancy 3 vs 4 -> hotel cost naik)
- extra_double: Rp/pax utk DOUBLE (occupancy 2 vs 4 -> hotel cost naik lagi)

Default 0 => semua room type = base price (flat, backward compat untuk BOQ lama).

Base price di BOQ dianggap = QUAD. Sales boleh input manual selisih Rp untuk
TRIPLE/DOUBLE di form BOQ header. Kalkulasi backend:
  price_quad   = _compute_totals()["price_per_pax"]     (existing)
  price_triple = price_quad + extra_triple
  price_double = price_quad + extra_double

Alasan manual override (bukan auto dari hotel items): fleksibel + transparan +
tidak asumsi tentang unit hotel item (per_pax vs per_room_per_night).
"""
import sqlite3


def up(conn: sqlite3.Connection) -> None:
    # ALTER kolom -- kalau sudah ada (idempoten), skip.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(package_boq)").fetchall()}
    if "extra_triple" not in cols:
        conn.execute("ALTER TABLE package_boq ADD COLUMN extra_triple INTEGER DEFAULT 0")
    if "extra_double" not in cols:
        conn.execute("ALTER TABLE package_boq ADD COLUMN extra_double INTEGER DEFAULT 0")


def down(conn: sqlite3.Connection) -> None:
    raise NotImplementedError(
        "SQLite ALTER TABLE DROP COLUMN memerlukan table rebuild. Manual rollback:\n"
        "  1. CREATE TABLE package_boq_new AS SELECT (kolom lama) FROM package_boq;\n"
        "  2. DROP TABLE package_boq; ALTER TABLE package_boq_new RENAME TO package_boq;\n"
        "Sebaiknya JANGAN rollback -- kolom default 0 tidak breaking backward compat."
    )
