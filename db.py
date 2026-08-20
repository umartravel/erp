"""
Konfigurasi Database SQLite untuk Umar CRM (versi Python).
Setara dengan database.js pada versi Node.js.

Memakai ulang file umar_crm.db yang sama, sehingga seluruh data jamaah,
transaksi, paket, dan riwayat WhatsApp tetap utuh setelah migrasi.
"""
import os
import sqlite3
import threading

import bcrypt

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "umar_crm.db")

# check_same_thread=False agar koneksi bisa dipakai dari beberapa thread
# (FastAPI threadpool + background thread WhatsApp). Semua tulis dilindungi lock.
_conn = sqlite3.connect(DB_FILE, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.execute("PRAGMA journal_mode=WAL")
_conn.execute("PRAGMA foreign_keys=ON")
_lock = threading.RLock()


# ---------------------------------------------------------------------------
# Helper akses data (meniru gaya db.all / db.get / db.run di Node)
# ---------------------------------------------------------------------------
def query_all(sql, params=()):
    """Ambil banyak baris -> list[dict]."""
    with _lock:
        cur = _conn.execute(sql, params)
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def query_one(sql, params=()):
    """Ambil satu baris -> dict | None."""
    with _lock:
        cur = _conn.execute(sql, params)
        row = cur.fetchone()
    return dict(row) if row else None


def execute(sql, params=()):
    """Jalankan INSERT/UPDATE/DELETE. Mengembalikan (lastrowid, rowcount)."""
    with _lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur.lastrowid, cur.rowcount


def executemany(sql, seq_of_params):
    with _lock:
        cur = _conn.executemany(sql, seq_of_params)
        _conn.commit()
        return cur.rowcount


# ---------------------------------------------------------------------------
# Inisialisasi skema, data dummy, dan migrasi kolom
# ---------------------------------------------------------------------------
SCHEMA = [
    # Tabel Pengguna (Role: admin, sales, finance, ops)
    """CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        name TEXT,
        role TEXT,
        base_salary INTEGER DEFAULT 0,
        phone TEXT,
        personal_email TEXT,
        address TEXT,
        nik TEXT,
        birth_date TEXT,
        photo_url TEXT,
        last_education TEXT,
        education_major TEXT,
        education_institution TEXT
    )""",
    # Tabel Jamaah
    """CREATE TABLE IF NOT EXISTS jamaah (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nik TEXT UNIQUE,
        name TEXT,
        phone TEXT,
        package_type TEXT,
        status TEXT,
        total_price INTEGER,
        paid_amount INTEGER DEFAULT 0,
        notes TEXT,
        room_type TEXT,
        room_number TEXT,
        visa_status TEXT DEFAULT 'Belum Proses',
        health_history TEXT,
        mahram TEXT,
        doc_ktp TEXT,
        doc_kk TEXT,
        doc_passport TEXT,
        doc_vaccine TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Tabel Paket Umroh
    """CREATE TABLE IF NOT EXISTS packages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        price INTEGER,
        departure_date TEXT,
        duration INTEGER,
        quota INTEGER DEFAULT 45,
        default_commission_fee INTEGER DEFAULT 0
    )""",
    # Fasilitas tambahan per paket, dikelompokkan ke 3 kategori tetap (category:
    # 'country' / 'citytour' / 'extra') -- daftar dinamis per kategori, jadi anak tabel
    # tersendiri (bisa berapa pun baris per paket) bukan kolom tetap di packages.
    # `label` dipakai sebagai isian tunggal per baris (mis. "Turki"); `description`
    # tidak lagi ditulis untuk baris baru, dipertahankan hanya untuk kompatibilitas mundur.
    """CREATE TABLE IF NOT EXISTS package_extras (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        package_id INTEGER,
        category TEXT DEFAULT 'extra',
        label TEXT,
        description TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Tabel Transaksi Keuangan (Buku Besar)
    """CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT,
        category TEXT,
        amount INTEGER,
        description TEXT,
        reference_id INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Tabel Master Inventory Perlengkapan. category membedakan barang habis pakai
    # untuk jamaah (bisa diserahterimakan lewat form Serah Terima) dari aset tetap
    # milik perusahaan (laptop, HP, dll -- tidak diserahkan ke jamaah, cuma dipantau stoknya).
    """CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_name TEXT UNIQUE,
        stock INTEGER DEFAULT 0,
        category TEXT DEFAULT 'Barang Jamaah',
        min_stock_threshold INTEGER DEFAULT 20
    )""",
    # Tabel Aset Perusahaan (laptop, HP, dll) -- per-unit, terpisah dari inventory
    # (stok agregat barang jamaah). Tiap unit dilacak siapa pemegangnya sekarang,
    # bukan cuma jumlah -- beda kebutuhan dari barang jamaah yang habis terpakai.
    """CREATE TABLE IF NOT EXISTS company_assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_name TEXT,
        serial_number TEXT,
        condition TEXT DEFAULT 'Baik',
        assigned_to TEXT,
        assigned_at DATETIME,
        notes TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Riwayat perpindahan tangan aset perusahaan -- terpisah dari Audit Trail umum
    # supaya bisa ditelusuri rapi per-aset (linimasa from_holder -> to_holder) tanpa
    # perlu menyaring teks log manual. Tercatat setiap kali assigned_to berubah,
    # baik lewat "Pindah Tangan" maupun "Edit Aset".
    """CREATE TABLE IF NOT EXISTS asset_transfer_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        asset_id INTEGER,
        from_holder TEXT,
        to_holder TEXT,
        transferred_by TEXT,
        transferred_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Tabel Riwayat Serah Terima Perlengkapan
    """CREATE TABLE IF NOT EXISTS jamaah_inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        jamaah_id INTEGER,
        item_name TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Tabel Keagenan (Mitra/Reseller)
    """CREATE TABLE IF NOT EXISTS agents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        phone TEXT,
        commission_fee INTEGER DEFAULT 1000000,
        province TEXT,
        city TEXT,
        subdistrict TEXT,
        village TEXT,
        address TEXT,
        instagram TEXT,
        email TEXT,
        agreement_number TEXT,
        kit_banner_wide INTEGER DEFAULT 0,
        kit_x_banner INTEGER DEFAULT 0,
        kit_kartu_nama INTEGER DEFAULT 0,
        kit_id_card INTEGER DEFAULT 0,
        legacy_code TEXT,
        registered_at TEXT,
        handler_cs_id INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Tabel Audit Trail (Pengawasan Admin)
    """CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_name TEXT,
        action TEXT,
        details TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Tabel Manajemen Vendor / Procurement -- alur maker-checker:
    # Finance/Admin input kontrak (Pending, belum potong Kas) -> Admin/Management setujui
    # (jadi Aktif) -> baru pembayaran (termasuk deposit awal) bisa dicatat & memotong Kas.
    """CREATE TABLE IF NOT EXISTS procurement (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vendor_name TEXT,
        service_type TEXT,
        total_stock INTEGER,
        total_price INTEGER,
        deposit_paid INTEGER DEFAULT 0,
        package_name TEXT,
        status TEXT DEFAULT 'Pending',
        reviewed_by TEXT,
        reviewed_at DATETIME,
        reject_reason TEXT,
        created_by TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Tabel Komentar Internal Antar Divisi
    """CREATE TABLE IF NOT EXISTS jamaah_comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        jamaah_id INTEGER,
        user_name TEXT,
        comment TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Tabel Laporan Insiden / Darurat Lapangan
    """CREATE TABLE IF NOT EXISTS incidents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        package_name TEXT,
        reported_by TEXT,
        incident_text TEXT,
        status TEXT DEFAULT 'Open',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Tabel WA Templates (Quick Replies)
    """CREATE TABLE IF NOT EXISTS wa_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, content TEXT
    )""",
    # Tabel Pengaturan Aplikasi (key-value) - logo, nama perusahaan, ambang DP, dst.
    """CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )""",
    # Tabel Aktivitas Lead (CRM Sales): riwayat follow-up tiap calon/jamaah
    """CREATE TABLE IF NOT EXISTS lead_activities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        jamaah_id INTEGER,
        user_name TEXT,
        activity_type TEXT,
        note TEXT,
        next_follow_up TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Tabel Project/Label Anggaran Expense Report (mis. "Expense Marketing 2026")
    # Dikelola oleh finance/admin/management sebagai kategori pengelompokan laporan.
    """CREATE TABLE IF NOT EXISTS expense_projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        is_active INTEGER DEFAULT 1,
        created_by TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Header Expense Report: alur maker-checker
    # Draft -> Submitted -> (Approved oleh admin/management | Rejected) -> Paid oleh admin/finance
    """CREATE TABLE IF NOT EXISTS expense_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ref TEXT UNIQUE,
        project_id INTEGER,
        user_id INTEGER,
        user_name TEXT,
        period_from TEXT,
        period_to TEXT,
        approver_id INTEGER,
        approver_name TEXT,
        note TEXT,
        note_visibility TEXT DEFAULT 'public',
        status TEXT DEFAULT 'Draft',
        validation_date DATETIME,
        reviewed_by TEXT,
        review_note TEXT,
        paid_by TEXT,
        paid_at DATETIME,
        transaction_id INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Baris item pengeluaran per Expense Report, dengan breakdown pajak per baris.
    """CREATE TABLE IF NOT EXISTS expense_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id INTEGER,
        date TEXT,
        category TEXT,
        description TEXT,
        unit_price_net INTEGER,
        tax_percent REAL DEFAULT 11,
        qty INTEGER DEFAULT 1,
        receipt_url TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Data mentah dari form pendaftaran publik (tanpa login), menunggu validasi CS.
    # Terpisah dari tabel jamaah -> tidak pernah masuk data produksi tanpa direview manusia.
    """CREATE TABLE IF NOT EXISTS pendaftaran_publik (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        status TEXT DEFAULT 'Pending',
        reviewed_by TEXT,
        reviewed_at DATETIME,
        review_note TEXT,
        jamaah_id INTEGER,
        name TEXT,
        orderer_name TEXT,
        citizenship TEXT,
        identity_type TEXT,
        nik TEXT,
        passport_number TEXT,
        passport_issued TEXT,
        passport_issuer_city TEXT,
        gender TEXT,
        birth_place_date TEXT,
        birth_place TEXT,
        birth_date TEXT,
        phone TEXT,
        family_phone TEXT,
        email TEXT,
        address TEXT,
        province TEXT,
        city TEXT,
        subdistrict TEXT,
        village TEXT,
        health_history TEXT,
        mahram TEXT,
        interest_note TEXT,
        source_ip TEXT,
        preferred_cs_id INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Permintaan refund: CS ajukan -> Manajemen setujui/tolak -> Finance cairkan.
    """CREATE TABLE IF NOT EXISTS refund_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        jamaah_id INTEGER,
        amount INTEGER,
        reason TEXT,
        status TEXT DEFAULT 'Pending',
        requested_by TEXT,
        requested_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        approved_by TEXT,
        approved_at DATETIME,
        reject_reason TEXT,
        disbursed_by TEXT,
        disbursed_at DATETIME,
        transaction_id INTEGER,
        cancel_booking INTEGER DEFAULT 0
    )""",
    # Klaim komisi agen: dibuat otomatis (status Pending) saat jamaah pertama kali Lunas ->
    # Manajemen setuju/tolak -> Finance cairkan. Menggantikan pencairan otomatis langsung
    # ke Buku Kas yang dipakai sebelumnya (lihat jamaah_payment() di app.py).
    """CREATE TABLE IF NOT EXISTS commission_claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id INTEGER,
        jamaah_id INTEGER,
        amount INTEGER,
        status TEXT DEFAULT 'Pending',
        requested_by TEXT,
        requested_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        approved_by TEXT,
        approved_at DATETIME,
        reject_reason TEXT,
        disbursed_by TEXT,
        disbursed_at DATETIME,
        transaction_id INTEGER
    )""",
    # Pengecualian fee komisi per (agen, paket) -- opsional, hanya diisi kalau ada nego
    # khusus. Kalau tidak ada baris yang cocok, sistem jatuh balik ke agents.commission_fee
    # (fee default agen, sudah ada sejak awal).
    """CREATE TABLE IF NOT EXISTS agent_package_fees (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id INTEGER,
        package_name TEXT,
        commission_fee INTEGER,
        UNIQUE(agent_id, package_name)
    )""",
    # Surat Izin karyawan: siapa saja (semua role) bisa mengajukan, Admin/Management
    # yang menyetujui atau menolak. Tidak seperti tabel Karyawan (HR) yang dibatasi
    # admin/management, pengajuan izin ini sengaja terbuka untuk semua role -- mereka
    # hanya bisa mengajukan & melihat riwayat sendiri, bukan data karyawan lain.
    """CREATE TABLE IF NOT EXISTS leave_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        user_name TEXT,
        leave_type TEXT,
        start_date TEXT,
        end_date TEXT,
        reason TEXT,
        status TEXT DEFAULT 'Pending',
        requested_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        approved_by TEXT,
        approved_at DATETIME,
        reject_reason TEXT
    )""",
    # Pendaftaran agen/konsultan dari form publik (tanpa login) -> antrian
    # validasi CS/Admin -> baris baru di tabel agents. Meniru pola pendaftaran_publik
    # untuk jamaah: data mentah tidak pernah langsung masuk agents tanpa direview.
    """CREATE TABLE IF NOT EXISTS pendaftaran_agen_publik (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        status TEXT DEFAULT 'Pending',
        reviewed_by TEXT,
        reviewed_at DATETIME,
        review_note TEXT,
        agent_id INTEGER,
        name TEXT,
        phone TEXT,
        email TEXT,
        instagram TEXT,
        address TEXT,
        province TEXT,
        city TEXT,
        subdistrict TEXT,
        village TEXT,
        source_ip TEXT,
        preferred_cs_id INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Target bulanan per sales -- di-set oleh admin/management, dilihat sales sendiri
    # untuk gauge Home Sales. Satu baris per (user, bulan) -- UNIQUE via index di ALTER.
    """CREATE TABLE IF NOT EXISTS sales_targets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        month TEXT NOT NULL,
        target_closing INTEGER DEFAULT 0,
        target_omzet INTEGER DEFAULT 0,
        set_by TEXT,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
]

# Migrasi kolom untuk DB lama (abaikan error bila kolom sudah ada)
ALTER_QUERIES = [
    "ALTER TABLE jamaah ADD COLUMN health_history TEXT",
    "ALTER TABLE jamaah ADD COLUMN mahram TEXT",
    "ALTER TABLE jamaah ADD COLUMN doc_ktp TEXT",
    "ALTER TABLE jamaah ADD COLUMN doc_kk TEXT",
    "ALTER TABLE jamaah ADD COLUMN doc_passport TEXT",
    "ALTER TABLE jamaah ADD COLUMN doc_vaccine TEXT",
    "ALTER TABLE jamaah ADD COLUMN room_type TEXT",
    "ALTER TABLE jamaah ADD COLUMN room_number TEXT",
    "ALTER TABLE jamaah ADD COLUMN visa_status TEXT DEFAULT 'Belum Proses'",
    "ALTER TABLE jamaah ADD COLUMN agent_id INTEGER",
    "ALTER TABLE transactions ADD COLUMN package_name TEXT",
    "ALTER TABLE jamaah ADD COLUMN city TEXT",
    "ALTER TABLE packages ADD COLUMN tour_leader TEXT",
    "ALTER TABLE packages ADD COLUMN mutawwif TEXT",
    "ALTER TABLE jamaah ADD COLUMN doc_status TEXT DEFAULT 'Pending'",
    "ALTER TABLE jamaah ADD COLUMN passport_expiry TEXT",
    "ALTER TABLE jamaah ADD COLUMN passport_location TEXT DEFAULT 'Di Jamaah'",
    "ALTER TABLE jamaah ADD COLUMN bus_group TEXT",
    "ALTER TABLE jamaah ADD COLUMN cancel_reason TEXT",
    "ALTER TABLE packages ADD COLUMN quota INTEGER DEFAULT 45",
    # Cleanup modul Live Chat WhatsApp -- dihapus 2026-08-17 (tim UMAR chat via WA
    # HP masing-masing; ERP hanya untuk broadcast+auto-remind via wa_templates).
    "DROP TABLE IF EXISTS wa_conversations",
    "DROP TABLE IF EXISTS wa_messages",
    "ALTER TABLE jamaah ADD COLUMN orderer_name TEXT",
    "ALTER TABLE jamaah ADD COLUMN gender TEXT",
    "ALTER TABLE jamaah ADD COLUMN birth_place_date TEXT",
    "ALTER TABLE jamaah ADD COLUMN citizenship TEXT",
    "ALTER TABLE jamaah ADD COLUMN identity_type TEXT",
    "ALTER TABLE jamaah ADD COLUMN family_phone TEXT",
    "ALTER TABLE jamaah ADD COLUMN email TEXT",
    "ALTER TABLE jamaah ADD COLUMN father_name TEXT",
    "ALTER TABLE jamaah ADD COLUMN education TEXT",
    "ALTER TABLE jamaah ADD COLUMN job TEXT",
    "ALTER TABLE jamaah ADD COLUMN marital_status TEXT",
    "ALTER TABLE jamaah ADD COLUMN relation TEXT",
    "ALTER TABLE jamaah ADD COLUMN address TEXT",
    "ALTER TABLE jamaah ADD COLUMN province TEXT",
    "ALTER TABLE jamaah ADD COLUMN subdistrict TEXT",
    "ALTER TABLE jamaah ADD COLUMN village TEXT",
    "ALTER TABLE jamaah ADD COLUMN passport_number TEXT",
    "ALTER TABLE jamaah ADD COLUMN passport_issued TEXT",
    "ALTER TABLE jamaah ADD COLUMN passport_issuer_city TEXT",
    "ALTER TABLE jamaah ADD COLUMN submitted_documents TEXT",
    "ALTER TABLE jamaah ADD COLUMN equipment_package TEXT",
    "ALTER TABLE packages ADD COLUMN price_quad INTEGER",
    "ALTER TABLE packages ADD COLUMN price_triple INTEGER",
    "ALTER TABLE packages ADD COLUMN price_double INTEGER",
    "ALTER TABLE jamaah ADD COLUMN sales_id INTEGER",
    "ALTER TABLE jamaah ADD COLUMN lead_source TEXT",
    "ALTER TABLE jamaah ADD COLUMN next_follow_up TEXT",
    "ALTER TABLE jamaah ADD COLUMN last_contact DATETIME",
    # Dimensi status terpisah (menggantikan konflasi 1 kolom `status`).
    # `status` lama dipertahankan sebagai cermin turunan demi kompatibilitas frontend.
    "ALTER TABLE jamaah ADD COLUMN pipeline_stage TEXT",   # Lead/Waitlisted/Registered/Booked/Cancelled
    "ALTER TABLE jamaah ADD COLUMN payment_status TEXT",   # Unpaid/DP/Lunas/Refunded
    "ALTER TABLE jamaah ADD COLUMN trip_status TEXT",      # NotStarted/OnTrip/Completed
    # Identitas pelaku di audit log disimpan sebagai id + role (bukan cuma nama),
    # agar tetap presisi walau ada nama yang sama atau role user berubah di kemudian hari.
    "ALTER TABLE audit_logs ADD COLUMN user_id INTEGER",
    "ALTER TABLE audit_logs ADD COLUMN role TEXT",
    "ALTER TABLE pendaftaran_publik ADD COLUMN city TEXT",
    "ALTER TABLE refund_requests ADD COLUMN cancel_booking INTEGER DEFAULT 0",
    # birth_place_date (gabungan, bebas ketik) dipecah jadi birth_place + birth_date (tanggal asli)
    # agar AGE bisa dihitung akurat untuk Manifest. Data lama TIDAK dimigrasikan otomatis (format
    # bebas ketik terlalu tidak konsisten untuk diparsing aman) -- kosong sampai diisi ulang manual.
    "ALTER TABLE jamaah ADD COLUMN birth_place TEXT",
    "ALTER TABLE jamaah ADD COLUMN birth_date TEXT",
    "ALTER TABLE pendaftaran_publik ADD COLUMN birth_place TEXT",
    "ALTER TABLE pendaftaran_publik ADD COLUMN birth_date TEXT",
    # Fee komisi kini berbasis PAKET (bukan per-agen) -- setiap paket punya fee default
    # sendiri; agents.commission_fee dibiarkan ada di skema (tidak dihapus) tapi TIDAK
    # dipakai lagi oleh kode manapun. Pengecualian per (agen, paket) tetap lewat
    # agent_package_fees seperti sebelumnya.
    "ALTER TABLE packages ADD COLUMN default_commission_fee INTEGER DEFAULT 0",
    # Profil agen diperluas mengikuti struktur data konsultan lama di Google Sheet
    # (rencana migrasi historis) -- alamat pakai nama kolom yang sama dengan jamaah
    # (province/city/subdistrict/village/address) agar konsisten & bisa reuse API wilayah.
    "ALTER TABLE agents ADD COLUMN province TEXT",
    "ALTER TABLE agents ADD COLUMN city TEXT",
    "ALTER TABLE agents ADD COLUMN subdistrict TEXT",
    "ALTER TABLE agents ADD COLUMN village TEXT",
    "ALTER TABLE agents ADD COLUMN address TEXT",
    "ALTER TABLE agents ADD COLUMN instagram TEXT",
    "ALTER TABLE agents ADD COLUMN email TEXT",
    "ALTER TABLE agents ADD COLUMN agreement_number TEXT",
    "ALTER TABLE agents ADD COLUMN kit_banner_wide INTEGER DEFAULT 0",
    "ALTER TABLE agents ADD COLUMN kit_x_banner INTEGER DEFAULT 0",
    "ALTER TABLE agents ADD COLUMN kit_kartu_nama INTEGER DEFAULT 0",
    "ALTER TABLE agents ADD COLUMN kit_id_card INTEGER DEFAULT 0",
    # Kode agen lama dari sistem sebelumnya (mis. ATRV-060120), disimpan sebagai referensi
    # historis saja saat migrasi -- BUKAN primary key, ERP tetap pakai id autoincrement sendiri.
    "ALTER TABLE agents ADD COLUMN legacy_code TEXT",
    # Tanggal pendaftaran ASLI (bisa jauh di masa lalu bila diisi lewat migrasi data lama),
    # dipisah dari created_at yang selalu ke-stamp saat baris ini benar-benar dibuat di ERP.
    "ALTER TABLE agents ADD COLUMN registered_at TEXT",
    # Fondasi Logistik & Ops: kategori barang (Barang Jamaah vs Aset Perusahaan) + ambang
    # minimum stok per item (dulu hardcode <=20 untuk semua barang, kini bisa beda tiap item --
    # laptop butuh ambang 1-2, sementara mukena wajar di 50).
    "ALTER TABLE inventory ADD COLUMN category TEXT DEFAULT 'Barang Jamaah'",
    "ALTER TABLE inventory ADD COLUMN min_stock_threshold INTEGER DEFAULT 20",
    # Calon jamaah bisa memilih CS tujuan sendiri saat mendaftar lewat form publik --
    # opsional; kalau kosong, sales_id tetap default ke CS yang klik Terima (perilaku lama).
    "ALTER TABLE pendaftaran_publik ADD COLUMN preferred_cs_id INTEGER",
    # Sama seperti di atas tapi untuk atribusi kinerja rekrut mitra/agen (bukan jamaah).
    "ALTER TABLE agents ADD COLUMN handler_cs_id INTEGER",
    "ALTER TABLE pendaftaran_agen_publik ADD COLUMN preferred_cs_id INTEGER",
    # Profil diri karyawan (self-service) -- diisi sendiri oleh karyawan, terlihat
    # oleh Admin/Management di Kelola Pengguna. Terpisah dari data akun (username/role).
    "ALTER TABLE users ADD COLUMN phone TEXT",
    "ALTER TABLE users ADD COLUMN personal_email TEXT",
    "ALTER TABLE users ADD COLUMN address TEXT",
    "ALTER TABLE users ADD COLUMN nik TEXT",
    "ALTER TABLE users ADD COLUMN birth_date TEXT",
    "ALTER TABLE users ADD COLUMN photo_url TEXT",
    "ALTER TABLE users ADD COLUMN last_education TEXT",
    "ALTER TABLE users ADD COLUMN education_major TEXT",
    "ALTER TABLE users ADD COLUMN education_institution TEXT",
    # Perbaikan modul Manajemen Vendor: alur approval + status siklus hidup + link paket.
    "ALTER TABLE procurement ADD COLUMN package_name TEXT",
    "ALTER TABLE procurement ADD COLUMN status TEXT DEFAULT 'Pending'",
    "ALTER TABLE procurement ADD COLUMN reviewed_by TEXT",
    "ALTER TABLE procurement ADD COLUMN reviewed_at DATETIME",
    "ALTER TABLE procurement ADD COLUMN reject_reason TEXT",
    "ALTER TABLE procurement ADD COLUMN created_by TEXT",
    # Kode aset internal untuk fitur scan/label QR -- terpisah dari serial_number pabrik
    # yang bisa kosong/duplikat. CREATE UNIQUE INDEX idempoten sendiri lewat IF NOT EXISTS,
    # dimasukkan ke daftar ini juga supaya jalan lewat mekanisme migrasi yang sama.
    "ALTER TABLE company_assets ADD COLUMN asset_code TEXT",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_company_assets_code ON company_assets(asset_code)",
    # Detail paket: hotel, maskapai, rute -- harga SENGAJA tidak disentuh, akan dikelola
    # lewat modul Simulasi Paket terpisah nanti.
    "ALTER TABLE packages ADD COLUMN hotel_mekkah TEXT",
    "ALTER TABLE packages ADD COLUMN hotel_madinah TEXT",
    "ALTER TABLE packages ADD COLUMN airline TEXT",
    "ALTER TABLE packages ADD COLUMN route_type TEXT DEFAULT 'Direct'",
    "ALTER TABLE packages ADD COLUMN transit_city TEXT",
    # `airline` (satu field) digantikan 3 field per arah -- kolom lama dibiarkan ada
    # (tidak dihapus, konvensi yang sama dengan `price` lama di packages) tapi tidak
    # dibaca/ditulis lagi oleh kode baru.
    "ALTER TABLE packages ADD COLUMN airline_depart TEXT",
    "ALTER TABLE packages ADD COLUMN airline_return TEXT",
    "ALTER TABLE packages ADD COLUMN airline_transit TEXT",
    "ALTER TABLE package_extras ADD COLUMN category TEXT DEFAULT 'extra'",
    # Tanggal Pulang: field sungguhan (bukan sekadar dihitung dari Durasi) supaya bisa
    # ditimpa manual kalau jadwal riil beda dari hitungan kalender biasa (reschedule,
    # penerbangan malam, dll). Bandara Transit mendampingi transit_city yang sudah ada.
    "ALTER TABLE packages ADD COLUMN return_date TEXT",
    "ALTER TABLE packages ADD COLUMN transit_airport TEXT",
    # Cleanup obsolete marketing tables (data sekarang dari jamaah/agents/users/packages).
    "DROP TABLE IF EXISTS marketing_closings",
    "DROP TABLE IF EXISTS marketing_agents",
    # Cleanup tabel reimbursements lama (pra-Expense Report, sudah tidak direferensikan kode).
    "DROP TABLE IF EXISTS reimbursements",
    # Tanggal transaksi asli dari migrasi web-umar (TANGGAL ORDER CSV). Dipakai halaman
    # Marketing Analytics untuk tren bulanan yang akurat -- created_at hanya menandai
    # waktu row masuk ke ERP (import batch), bukan waktu closing sebenarnya.
    "ALTER TABLE jamaah ADD COLUMN order_date TEXT",
    # Usia jamaah (dari kolom USIA CSV). Dipakai chart demografi Marketing Analytics.
    # Integer -- disimpan sebagai snapshot saat closing, bukan dihitung dari birth_date
    # (untuk konsistensi dengan sumber CSV historis; birth_date sendiri sering kosong).
    "ALTER TABLE jamaah ADD COLUMN age INTEGER",
    # Index tambahan untuk halaman Marketing Analytics yang sekarang query jamaah+agents.
    "CREATE INDEX IF NOT EXISTS idx_jamaah_province ON jamaah(province)",
    "CREATE INDEX IF NOT EXISTS idx_jamaah_city ON jamaah(city)",
    "CREATE INDEX IF NOT EXISTS idx_jamaah_sales_id ON jamaah(sales_id)",
    "CREATE INDEX IF NOT EXISTS idx_jamaah_agent_id ON jamaah(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_jamaah_package_type ON jamaah(package_type)",
    "CREATE INDEX IF NOT EXISTS idx_jamaah_lead_source ON jamaah(lead_source)",
    "CREATE INDEX IF NOT EXISTS idx_jamaah_order_date ON jamaah(order_date)",
    "CREATE INDEX IF NOT EXISTS idx_jamaah_age ON jamaah(age)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_sales_targets_user_month ON sales_targets(user_id, month)",
    # Perluasan tabel incidents (schema dasar cuma package_name/reported_by/text) --
    # untuk manajemen ops butuh severity + assignee + resolution + link ke jamaah.
    "ALTER TABLE incidents ADD COLUMN severity TEXT DEFAULT 'Medium'",
    "ALTER TABLE incidents ADD COLUMN assigned_to TEXT",
    "ALTER TABLE incidents ADD COLUMN resolved_at DATETIME",
    "ALTER TABLE incidents ADD COLUMN resolution_note TEXT",
    "ALTER TABLE incidents ADD COLUMN jamaah_id INTEGER",
    "CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_package ON incidents(package_name)",
]


def init_db():
    with _lock:
        for stmt in SCHEMA:
            _conn.execute(stmt)
        _conn.commit()
        for q in ALTER_QUERIES:
            try:
                _conn.execute(q)
                _conn.commit()
            except sqlite3.OperationalError:
                pass  # kolom sudah ada

    _seed_users()
    _seed_packages()
    _seed_inventory()
    _seed_agents()
    _seed_settings()
    _backfill_status_dimensions()
    _backfill_procurement_status()
    _backfill_asset_codes()
    print("Berhasil terhubung ke SQLite database Umar CRM.")


def next_asset_code():
    """Kode aset internal unik (AST-0001 dst) untuk fitur scan/label QR -- disimpan sebagai
    counter monoton di tabel settings (BUKAN MAX() dari baris yang masih ada), supaya kode
    dari aset yang sudah dihapus tidak pernah dipakai ulang untuk aset lain (mencegah barcode
    lama yang masih tertempel fisik di suatu barang tiba-tiba menunjuk ke aset yang salah)."""
    row = query_one("SELECT value FROM settings WHERE key = 'asset_code_seq'")
    seq = (int(row["value"]) if row and row["value"] else 0) + 1
    execute(
        "INSERT INTO settings (key, value) VALUES ('asset_code_seq', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(seq),),
    )
    return f"AST-{seq:04d}"


def _backfill_asset_codes():
    # Angkat counter ke kode tertinggi yang sudah terpakai (mis. baris yang sempat dibuat
    # sebelum counter ini ada) -- supaya next_asset_code() tidak pernah menabrak kode lama.
    rows = query_all("SELECT asset_code FROM company_assets WHERE asset_code IS NOT NULL")
    max_seq = 0
    for r in rows:
        try:
            max_seq = max(max_seq, int(r["asset_code"].split("-")[1]))
        except (IndexError, ValueError):
            pass
    if max_seq:
        current = query_one("SELECT value FROM settings WHERE key = 'asset_code_seq'")
        if not current or int(current["value"] or 0) < max_seq:
            execute(
                "INSERT INTO settings (key, value) VALUES ('asset_code_seq', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(max_seq),),
            )
    missing = query_all("SELECT id FROM company_assets WHERE asset_code IS NULL ORDER BY id ASC")
    for r in missing:
        execute("UPDATE company_assets SET asset_code = ? WHERE id = ?", (next_asset_code(), r["id"]))


def _backfill_procurement_status():
    # Kontrak vendor lama (sebelum alur approval ada) sudah terlanjur memotong Kas saat
    # dibuat -- kalau dibiarkan default 'Pending' dari migrasi ALTER, datanya jadi
    # inkonsisten (sudah bayar tapi berstatus belum disetujui). Samakan ke 'Aktif'.
    execute(
        "UPDATE procurement SET status = 'Aktif' WHERE deposit_paid > 0 AND status = 'Pending'"
    )


def _backfill_status_dimensions():
    """
    Isi dimensi status baru dari data lama (1x jalan untuk baris yang belum diisi).
    payment_status diturunkan dari paid_amount/total_price (andal), sehingga info
    'Lunas' yang dulu hilang saat status ditimpa 'Visa Approved' kini pulih.
    """
    rows = query_all(
        "SELECT id, status, paid_amount, total_price FROM jamaah WHERE pipeline_stage IS NULL"
    )
    for r in rows:
        s = r["status"] or "Lead - Follow Up"
        paid = r["paid_amount"] or 0
        total = r["total_price"] or 0
        if paid > 0 and total > 0 and paid >= total:
            payment = "Lunas"
        elif paid > 0:
            payment = "DP"
        else:
            payment = "Unpaid"
        trip = "OnTrip" if s == "On Trip" else "NotStarted"
        if s == "Cancelled":
            pipeline = "Cancelled"
        elif s == "Lead - Follow Up":
            pipeline = "Lead"
        elif s == "Waitlisted":
            pipeline = "Waitlisted"
        elif s == "Terdaftar":
            pipeline = "Registered"
        else:  # DP Masuk / Lunas / Visa Approved / Proses Kedutaan / On Trip
            pipeline = "Booked"
        execute(
            "UPDATE jamaah SET pipeline_stage = ?, payment_status = ?, trip_status = ? WHERE id = ?",
            (pipeline, payment, trip, r["id"]),
        )


# Pengaturan default. INSERT OR IGNORE -> tidak menimpa nilai yang sudah diubah admin.
DEFAULT_SETTINGS = {
    "company_name": "Umar Travel",
    "company_tagline": "Sistem Manajemen Terpadu",
    "logo_url": "/umar-Logo.png",
    "equipment_min_dp_percent": "75",
    # Data legal perusahaan untuk kop surat dokumen resmi (mis. PDF Expense Report)
    "company_legal_name": "PT. UMAR SINERGI BERSAMA",
    "company_address": "Jl. Raya Hankam no. 1/11, 17431 BEKASI",
    "company_email": "info@u-mar.id",
    "company_website": "u-mar.id",
}


def _seed_settings():
    for k, v in DEFAULT_SETTINGS.items():
        execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))


def _seed_users():
    row = query_one("SELECT count(*) as count FROM users")
    if row and row["count"] == 0:
        default_password = bcrypt.hashpw(b"password123", bcrypt.gensalt(10)).decode()
        seed = [
            ("admin", default_password, "Administrator", "admin", 10000000),
            ("sales1", default_password, "Tim Sales", "sales", 4000000),
            ("finance1", default_password, "Tim Keuangan", "finance", 5000000),
            ("ops1", default_password, "Tim Operasional", "ops", 4500000),
            ("manager1", default_password, "Tim Management", "management", 6000000),
        ]
        executemany(
            "INSERT INTO users (username, password, name, role, base_salary) VALUES (?, ?, ?, ?, ?)",
            seed,
        )
        print("Data user default berhasil dibuat.")


def _seed_packages():
    row = query_one("SELECT count(*) as count FROM packages")
    if row and row["count"] == 0:
        seed = [
            ("Paket Hemat Awal Desember 2025", 25900000, "2025-12-06", 9),
            ("Paket Hemat Januari 2026", 26900000, "2026-01-27", 9),
            ("Paket Awal Ramadhan Plus Hainan", 27900000, "2026-02-22", 11),
            ("Paket Hemat Syawal 2026", 25900000, "2026-03-24", 9),
        ]
        executemany(
            "INSERT INTO packages (name, price, departure_date, duration) VALUES (?, ?, ?, ?)",
            seed,
        )
        print("Data paket Umroh default berhasil dibuat.")


def _seed_inventory():
    row = query_one("SELECT count(*) as count FROM inventory")
    if row and row["count"] == 0:
        seed = [
            ("Koper Besar", 50),
            ("Koper Kabin", 50),
            ("Kain Ihram", 100),
            ("Mukena", 100),
            ("Buku Panduan", 200),
        ]
        executemany("INSERT INTO inventory (item_name, stock) VALUES (?, ?)", seed)


def _seed_agents():
    row = query_one("SELECT count(*) as count FROM agents")
    if row and row["count"] == 0:
        seed = [
            ("Ustadz Fulan (Freelance)", "08123456789", 1000000),
            ("Kantor Cabang Banten", "08987654321", 1500000),
        ]
        executemany(
            "INSERT INTO agents (name, phone, commission_fee) VALUES (?, ?, ?)", seed
        )
