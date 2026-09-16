"""
Sprint AK-1: Chart of Accounts + extend expense_categories.

Fondasi arsitektur akuntansi akrual (PSAK-compliant) sesuai PDF Modul Akuntansi
ERP UMAR:
- Table baru `chart_of_accounts` (COA) dgn kode 4-digit:
    1xxx Aset, 2xxx Liabilitas, 3xxx Ekuitas,
    4xxx Pendapatan, 5xxx COGS, 6xxx OPEX/Lain-lain
- Setiap akun punya `normal_balance` (DEBIT/CREDIT).
- Extend `expense_categories`:
    * `default_account_id` FK ke chart_of_accounts.id -- auto-map subkategori
      ke akun COA saat penjurnalan.
    * `report_type` (BALANCE_SHEET | PROFIT_LOSS) di parent category.
  Semua kategori existing di-set PROFIT_LOSS.
"""
import sqlite3


_COA_SEED = [
    # 1xxx Aset Lancar
    ("1101", "Kas Kecil Kantor", "ASSET", "DEBIT"),
    ("1102", "Bank Mandiri", "ASSET", "DEBIT"),
    ("1103", "Bank Syariah Indonesia (BSI)", "ASSET", "DEBIT"),
    ("1108", "Beban Dibayar Dimuka - Umrah", "ASSET", "DEBIT"),
    ("1109", "Kas Kliring / Transit Antar-Akun", "ASSET", "DEBIT"),
    # 1200 Aset Tetap
    ("1201", "Aset Tetap (Kendaraan/Elektronik/Furniture)", "ASSET", "DEBIT"),
    ("1202", "Akumulasi Penyusutan", "ASSET", "CREDIT"),
    # 2xxx Liabilitas
    ("2101", "Pendapatan Diterima Dimuka - Umrah", "LIABILITY", "CREDIT"),
    ("2102", "Hutang Usaha Vendor / Mitra", "LIABILITY", "CREDIT"),
    ("2103", "Biaya Yang Masih Harus Dibayar", "LIABILITY", "CREDIT"),
    # 3xxx Ekuitas
    ("3101", "Modal Pemilik", "EQUITY", "CREDIT"),
    ("3102", "Prive Pemilik Usaha (Owner Draw)", "EQUITY", "DEBIT"),
    # 4xxx Pendapatan Usaha
    ("4101", "Pendapatan Paket Umrah Reguler", "REVENUE", "CREDIT"),
    ("4102", "Pendapatan Layanan Haji Khusus", "REVENUE", "CREDIT"),
    ("4103", "Pendapatan Jasa Badal & Wakaf", "REVENUE", "CREDIT"),
    ("4104", "Pendapatan Penjualan Tiket & Visa", "REVENUE", "CREDIT"),
    # 5xxx Beban Pokok Penjualan
    ("5101", "HPP - Tiket Penerbangan Internasional", "EXPENSE", "DEBIT"),
    ("5102", "HPP - Hotel Makkah & Madinah", "EXPENSE", "DEBIT"),
    ("5103", "HPP - Visa Umrah, BRN & Asuransi", "EXPENSE", "DEBIT"),
    ("5104", "HPP - Transportasi Bus Ziarah Saudi", "EXPENSE", "DEBIT"),
    ("5105", "HPP - Konsumsi & Katering di Saudi", "EXPENSE", "DEBIT"),
    ("5106", "HPP - Airport Handling & Lounge VIP", "EXPENSE", "DEBIT"),
    ("5107", "HPP - Koper, Seragam & Bahan Promosi", "EXPENSE", "DEBIT"),
    ("5108", "HPP - Pembimbing Ibadah & Tour Leader", "EXPENSE", "DEBIT"),
    ("5201", "HPP - Logistik & Distribusi", "EXPENSE", "DEBIT"),
    # 6xxx Beban Operasional & Lain
    ("6101", "Beban Gaji Staf & Manajemen", "EXPENSE", "DEBIT"),
    ("6102", "Beban Komisi Penjualan Mitra Agen", "EXPENSE", "DEBIT"),
    ("6103", "Beban Komunikasi, Internet & Wifi", "EXPENSE", "DEBIT"),
    ("6104", "Beban Utilitas Listrik & Air Kantor", "EXPENSE", "DEBIT"),
    ("6105", "Beban Perlengkapan & Keperluan Kantor", "EXPENSE", "DEBIT"),
    ("6106", "Beban Percetakan & Alat Tulis Kantor", "EXPENSE", "DEBIT"),
    ("6107", "Beban Amortisasi Sewa Gedung Kantor", "EXPENSE", "DEBIT"),
    ("6108", "Beban Penyusutan Aset Tetap", "EXPENSE", "DEBIT"),
    ("6201", "Beban Administrasi Transaksi Bank", "EXPENSE", "DEBIT"),
    ("6202", "Kerugian / (Keuntungan) Selisih Kurs", "EXPENSE", "DEBIT"),
]


# Auto-map subkategori existing -> COA code via keyword match.
_SUB_TO_COA = [
    ("payment jamaah", "4101"),
    ("refund jamaah", "2101"),
    ("hotel mekkah", "5102"),
    ("hotel madinah", "5102"),
    ("hotel transit", "5102"),
    ("tiket pesawat", "5101"),
    ("transportasi darat", "5104"),
    ("visa & dokumen", "5103"),
    ("katering", "5105"),
    ("muthawif", "5108"),
    ("tour leader", "5108"),
    ("insurance jamaah", "5103"),
    ("handling bandara", "5106"),
    ("extras jamaah", "5107"),
    ("refund vendor", "5201"),
    ("iklan digital", "6102"),
    ("iklan offline", "6102"),
    ("event & pameran", "6102"),
    ("konten & design", "6102"),
    ("endorsement", "6102"),
    ("komisi agen", "6102"),
    ("komisi sales", "6102"),
    ("gaji pokok", "6101"),
    ("bpjs", "6101"),
    ("bonus", "6101"),
    ("konsumsi karyawan", "6105"),
    ("rekrutmen", "6101"),
    ("training", "6101"),
    ("sewa kantor", "6107"),
    ("listrik", "6104"),
    ("air", "6104"),
    ("internet", "6103"),
    ("telpon", "6103"),
    ("atk", "6106"),
    ("pemeliharaan", "6105"),
    ("cleaning", "6105"),
    ("security", "6105"),
    ("transport operasional", "6105"),
    ("fotokopi", "6106"),
    ("biaya bank", "6201"),
    ("pajak", "6201"),
    ("perizinan", "6201"),
    ("konsultan", "6201"),
    ("bunga pinjaman", "6201"),
    ("setoran bps", "6201"),
    ("pembelian aset", "1201"),
    ("renovasi", "6107"),
    ("software", "6106"),
    ("lisensi", "6106"),
    ("amal", "6201"),
    ("zakat", "6201"),
    ("csr", "6201"),
    ("insidentil", "6201"),
    ("bunga bank", "4104"),
    ("komisi vendor", "4104"),
    ("denda", "4104"),
    ("late fee", "4104"),
]


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chart_of_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_code TEXT UNIQUE NOT NULL,
            account_name TEXT NOT NULL,
            account_group TEXT NOT NULL CHECK(account_group IN
                ('ASSET','LIABILITY','EQUITY','REVENUE','EXPENSE')),
            normal_balance TEXT NOT NULL CHECK(normal_balance IN ('DEBIT','CREDIT')),
            parent_id INTEGER REFERENCES chart_of_accounts(id) ON DELETE SET NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_coa_group_code "
        "ON chart_of_accounts (account_group, account_code)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_coa_active "
        "ON chart_of_accounts (is_active, account_group)"
    )

    for code, name, grp, nb in _COA_SEED:
        exist = conn.execute(
            "SELECT id FROM chart_of_accounts WHERE account_code = ?", (code,)
        ).fetchone()
        if not exist:
            conn.execute(
                "INSERT INTO chart_of_accounts "
                "(account_code, account_name, account_group, normal_balance) "
                "VALUES (?, ?, ?, ?)",
                (code, name, grp, nb),
            )

    cols = {r[1] for r in conn.execute("PRAGMA table_info(expense_categories)").fetchall()}
    if "default_account_id" not in cols:
        conn.execute(
            "ALTER TABLE expense_categories ADD COLUMN default_account_id INTEGER "
            "REFERENCES chart_of_accounts(id) ON DELETE SET NULL"
        )
    if "report_type" not in cols:
        conn.execute(
            "ALTER TABLE expense_categories ADD COLUMN report_type TEXT "
            "CHECK(report_type IN ('BALANCE_SHEET','PROFIT_LOSS'))"
        )

    conn.execute(
        "UPDATE expense_categories SET report_type = 'PROFIT_LOSS' "
        "WHERE parent_id IS NULL AND report_type IS NULL"
    )

    subs = conn.execute(
        "SELECT c.id, c.name FROM expense_categories c "
        "WHERE c.parent_id IS NOT NULL AND c.default_account_id IS NULL "
        "AND c.is_active = 1"
    ).fetchall()
    for sub in subs:
        sub_name_lower = (sub[1] or "").lower()
        matched_code = None
        for keyword, coa_code in _SUB_TO_COA:
            if keyword in sub_name_lower:
                matched_code = coa_code
                break
        if matched_code:
            coa_row = conn.execute(
                "SELECT id FROM chart_of_accounts WHERE account_code = ?",
                (matched_code,),
            ).fetchone()
            if coa_row:
                conn.execute(
                    "UPDATE expense_categories SET default_account_id = ? WHERE id = ?",
                    (coa_row[0], sub[0]),
                )
