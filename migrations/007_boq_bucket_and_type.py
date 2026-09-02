"""
BOQ Bucket & Boq Type -- Phase 6a.

Tambah 3 kolom untuk mendukung breakdown harga jual dengan 5 kategori (bucket)
dan tipe paket BOQ. Berbasis analisa Template BOQ Excel (Include TL / Exclude
TL / UTS / Itikaf) yang tidak bisa direpresentasikan dengan schema lama.

Perubahan schema:

- package_boq_items.bucket TEXT NOT NULL DEFAULT 'hpp'
  Bucket enum: hpp | prorate_tl | fee_agen | fee_referal | margin
  * hpp         -- biaya nyata (hotel, tiket, visa, transport, muthawwif
                   kalau paket "TL include").
  * prorate_tl  -- biaya TL yang diprorate ke jamaah (paket "TL exclude").
                   Item punya quantity = jumlah TL, subtotal auto-dibagi
                   target_pax saat kalkulasi.
  * fee_agen    -- komisi jaringan penjual (per pax).
  * fee_referal -- komisi personal referal/sponsor (per pax).
  * margin      -- profit UMAR.

- boq_template_items.bucket TEXT NOT NULL DEFAULT 'hpp'
  Sama seperti package_boq_items -- supaya template siap-pakai juga bucket-
  aware.

- package_boq.boq_type TEXT NOT NULL DEFAULT 'umar_reguler'
  Tipe paket enum: umar_reguler | umar_ramadhan | uts_partner | itikaf
  Metadata + flag untuk validasi/UI logic berbeda per tipe.

Backfill:
- Item existing dengan category='margin' -> bucket='margin' (mapping trivial).
- Sisanya default 'hpp' (paling aman, sales bisa reklasifikasi lewat UI).
- Item category='muthawwif' TIDAK auto-migrate ke prorate_tl -- sales harus
  konfirmasi apakah paket tersebut "TL include" (tetap hpp) atau "TL exclude"
  (pindah ke prorate_tl).

Backward compat:
- BOQ existing tetap valid: semua kolom baru punya default.
- Harga jual per pax lama = sum(subtotal semua items) / target_pax masih
  benar karena default bucket=hpp (backfill sudah pisahkan margin).
- boq_type default 'umar_reguler' aman untuk semua BOQ historis UMAR.
"""
import sqlite3


def up(conn: sqlite3.Connection) -> None:
    # package_boq_items.bucket
    cols = {r[1] for r in conn.execute("PRAGMA table_info(package_boq_items)").fetchall()}
    if "bucket" not in cols:
        conn.execute(
            "ALTER TABLE package_boq_items ADD COLUMN bucket TEXT NOT NULL DEFAULT 'hpp'"
        )

    # boq_template_items.bucket
    cols = {r[1] for r in conn.execute("PRAGMA table_info(boq_template_items)").fetchall()}
    if "bucket" not in cols:
        conn.execute(
            "ALTER TABLE boq_template_items ADD COLUMN bucket TEXT NOT NULL DEFAULT 'hpp'"
        )

    # package_boq.boq_type
    cols = {r[1] for r in conn.execute("PRAGMA table_info(package_boq)").fetchall()}
    if "boq_type" not in cols:
        conn.execute(
            "ALTER TABLE package_boq ADD COLUMN boq_type TEXT NOT NULL DEFAULT 'umar_reguler'"
        )

    # Backfill: category='margin' -> bucket='margin'.
    # Idempotent: WHERE bucket='hpp' AND category='margin' -- kalau sudah pernah
    # dijalankan, tidak akan re-update baris yang sudah bucket='margin'.
    conn.execute(
        "UPDATE package_boq_items SET bucket='margin' "
        "WHERE bucket='hpp' AND category='margin'"
    )
    conn.execute(
        "UPDATE boq_template_items SET bucket='margin' "
        "WHERE bucket='hpp' AND category='margin'"
    )


def down(conn: sqlite3.Connection) -> None:
    raise NotImplementedError(
        "SQLite DROP COLUMN memerlukan table rebuild. Manual rollback:\n"
        "  1. Backup DB dulu.\n"
        "  2. Rebuild tiap tabel tanpa kolom baru:\n"
        "     - CREATE TABLE package_boq_items_new (...tanpa bucket...);\n"
        "     - INSERT INTO ...new SELECT ...(exclude bucket) FROM ...items;\n"
        "     - DROP TABLE package_boq_items; ALTER RENAME.\n"
        "     - Ulang utk boq_template_items + package_boq.\n"
        "Sebaiknya JANGAN rollback -- kolom nullable dengan default, "
        "backward-compat sepenuhnya."
    )
