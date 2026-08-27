"""Backfill 1 baris income di `transactions` untuk setiap jamaah dengan
`paid_amount > 0` yang belum punya baris backfill. Diperlukan sekali karena
data historis (321 jamaah, ~7.39M cashflow) diimport langsung ke tabel
jamaah via wipe_and_migrate.py, tidak melewati endpoint
PUT /api/jamaah/{id}/payment yang seharusnya INSERT ke transactions.

Description bertanda "[BACKFILL]" supaya bisa dibedakan dari transaksi
riil ke depan. reference_id = jamaah.id (sama seperti endpoint payment)
supaya audit trail lengkap. created_at diset ke jamaah.order_date (fallback
jamaah.created_at) supaya aged buckets & cash flow historical konsisten.

Default: DRY-RUN. Cetak preview, jangan tulis DB. Jalankan dengan
`--confirm` untuk benar-benar tulis.

Idempoten: cek dulu apakah sudah ada backfill row untuk jamaah_id ybs
(via reference_id + LIKE '[BACKFILL]%'), skip kalau sudah ada.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "umar_crm.db"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true",
                        help="Aktifkan mode tulis. Tanpa flag ini = dry-run.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Batasi jumlah baris (untuk testing).")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"[ERROR] DB tidak ditemukan: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, name, package_type, payment_status, "
        "  COALESCE(paid_amount, 0) AS paid_amount, "
        "  COALESCE(total_price, 0) AS total_price, "
        "  COALESCE(order_date, created_at) AS anchor_date "
        "FROM jamaah "
        "WHERE COALESCE(paid_amount, 0) > 0 "
        "  AND status NOT IN ('Cancelled', 'Lead - Follow Up') "
        "ORDER BY anchor_date ASC, id ASC"
    ).fetchall()

    if args.limit:
        rows = rows[: args.limit]

    total_amount = sum(r["paid_amount"] for r in rows)
    print(f"Kandidat: {len(rows)} jamaah, total Rp {total_amount:,.0f}")
    print(f"Mode: {'WRITE (--confirm)' if args.confirm else 'DRY-RUN (default)'}")
    print("-" * 78)

    inserted = 0
    skipped = 0
    for r in rows:
        existing = conn.execute(
            "SELECT id FROM transactions "
            "WHERE reference_id = ? AND category = 'payment' AND description LIKE '[BACKFILL]%'",
            (r["id"],),
        ).fetchone()
        if existing:
            skipped += 1
            continue

        desc = f"[BACKFILL] Pembayaran Umroh: {r['name']} ({r['payment_status']})"
        if args.confirm:
            conn.execute(
                "INSERT INTO transactions (type, category, amount, description, "
                "  reference_id, package_name, created_at) "
                "VALUES ('income', 'payment', ?, ?, ?, ?, ?)",
                (r["paid_amount"], desc, r["id"], r["package_type"], r["anchor_date"]),
            )
        inserted += 1

        if inserted <= 5 or inserted % 50 == 0:
            print(f"  #{r['id']:4d} {r['name'][:30]:30s} {r['payment_status']:6s} "
                  f"Rp {r['paid_amount']:>14,}  {r['anchor_date']}")

    if args.confirm:
        conn.commit()
        print("-" * 78)
        print(f"[OK] {inserted} baris ditulis, {skipped} sudah ada (skip).")
        cash = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE -amount END), 0) AS c "
            "FROM transactions"
        ).fetchone()["c"]
        n = conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"]
        print(f"[VERIFY] transactions rows = {n}, saldo kas = Rp {cash:,}")
    else:
        print("-" * 78)
        print(f"[DRY-RUN] Akan menulis {inserted} baris, skip {skipped} (sudah ada).")
        print("Jalankan lagi dengan flag `--confirm` untuk eksekusi.")

    conn.close()


if __name__ == "__main__":
    main()
