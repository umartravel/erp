"""
Phase F1 (Finance): Kategorisasi pemasukan + pengeluaran.

Latar: atasan minta setiap pemasukan & pengeluaran ter-kategori (mis. Iklan
Digital, Sewa Kantor) supaya dashboard tahunan/bulanan bisa breakdown per
kategori + export PDF/Excel.

Inspect awal: 281 rows di `transactions` semuanya `type='income'` category
='payment' (backfill jamaah DP -- task #103). Tidak ada expense terekam.
Migration ini bikin:
- Tabel `expense_categories` (parent + subcategory, group income/expense)
- Kolom `transactions.category_id` FK
- Seed default: 2 parent income + 6 parent expense, ~40 subcategory
- Backfill: existing income 'payment' -> "Payment Jamaah" subcategory
"""
import sqlite3


_INCOME_TREE = [
    ("Pendapatan Umroh", [
        "Payment Jamaah",
    ]),
    ("Pendapatan Lain-lain", [
        "Bunga Bank",
        "Refund Vendor",
        "Komisi Vendor",
        "Denda / Late Fee",
    ]),
]

_EXPENSE_TREE = [
    ("HPP / Biaya Operasional Umroh", [
        "Hotel Mekkah", "Hotel Madinah", "Hotel Transit", "Tiket Pesawat",
        "Transportasi Darat", "Visa & Dokumen", "Katering",
        "Muthawif / Tour Leader", "Insurance Jamaah", "Handling Bandara",
        "Extras Jamaah (koper/seragam)", "Refund Jamaah",
    ]),
    ("Marketing & Sales", [
        "Iklan Digital (Meta/Google/TikTok)", "Iklan Offline (banner/brosur)",
        "Event & Pameran", "Konten & Design", "Endorsement / KOL",
        "Komisi Agen", "Komisi Sales",
    ]),
    ("HR & Gaji", [
        "Gaji Pokok + Tunjangan", "BPJS Kesehatan/Ketenagakerjaan",
        "Bonus / THR", "Konsumsi Karyawan", "Rekrutmen", "Training/Sertifikasi",
    ]),
    ("Operasional Kantor", [
        "Sewa Kantor", "Listrik / Air", "Internet & Telpon", "ATK",
        "Pemeliharaan Kantor", "Cleaning & Security",
        "Transport Operasional", "Fotokopi / Printing",
    ]),
    ("Keuangan & Legal", [
        "Biaya Bank / Transfer", "Pajak & Retribusi", "Perizinan (SIUP/TDP)",
        "Konsultan (akuntan/hukum/notaris)", "Bunga Pinjaman",
        "Setoran BPS Umroh (jaminan)",
    ]),
    ("Aset & Lain-lain", [
        "Pembelian Aset (kendaraan/elektronik)", "Renovasi Kantor",
        "Software / Lisensi", "Amal / Zakat / CSR", "Insidentil",
    ]),
]


def _seed_tree(conn, tree, group_type, start_order):
    sub_id_map = {}
    for i, (parent_name, subs) in enumerate(tree):
        cur = conn.execute(
            "INSERT INTO expense_categories (parent_id, name, group_type, sort_order) "
            "VALUES (NULL, ?, ?, ?)",
            (parent_name, group_type, start_order + i * 100),
        )
        parent_id = cur.lastrowid
        for j, sub_name in enumerate(subs):
            cur = conn.execute(
                "INSERT INTO expense_categories (parent_id, name, group_type, sort_order) "
                "VALUES (?, ?, ?, ?)",
                (parent_id, sub_name, group_type, start_order + i * 100 + (j + 1)),
            )
            sub_id_map[sub_name] = cur.lastrowid
    return sub_id_map


def up(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS expense_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id INTEGER REFERENCES expense_categories(id) ON DELETE SET NULL,
            name TEXT NOT NULL,
            group_type TEXT NOT NULL DEFAULT 'expense',
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_expense_cat_parent "
        "ON expense_categories (parent_id, sort_order)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_expense_cat_group "
        "ON expense_categories (group_type, is_active, sort_order)"
    )

    cols = {r[1] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()}
    if "category_id" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN category_id INTEGER")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_transactions_category "
            "ON transactions (category_id, created_at DESC)"
        )

    already = conn.execute("SELECT COUNT(*) FROM expense_categories").fetchone()[0]
    if already > 0:
        return

    income_subs = _seed_tree(conn, _INCOME_TREE, "income", start_order=1000)
    _seed_tree(conn, _EXPENSE_TREE, "expense", start_order=10000)

    payment_id = income_subs.get("Payment Jamaah")
    if payment_id:
        conn.execute(
            "UPDATE transactions SET category_id = ? "
            "WHERE type = 'income' AND (category = 'payment' OR category IS NULL) "
            "AND category_id IS NULL",
            (payment_id,),
        )
