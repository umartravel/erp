# Umar CRM (Python / FastAPI)

Aplikasi ERP/CRM Travel Umroh untuk PT. Umar Travel. Backend Python
(FastAPI + python-socketio + neonize + Playwright), frontend
`public/index.html` monolitik dengan Tailwind CDN + Chart.js.

- **139 REST endpoint** terbagi ke **22 router modular** (`routes/*.py`).
- SQLite (WAL mode) sebagai satu-satunya storage (`umar_crm.db`).
- Real-time via Socket.IO (event `data_updated` untuk auto-refresh UI).
- RBAC: `admin`, `management`, `sales`, `ops`, `finance`.

> **Arsitektur & konvensi** ada di [ARCHITECTURE.md](ARCHITECTURE.md).

## Menjalankan

```bash
# 1. (sekali) buat virtual environment & install dependensi
python -m venv .venv
.venv\Scripts\activate              # Windows (Git Bash / PowerShell)
# source .venv/bin/activate         # Linux/Mac
pip install -r requirements.txt

# 2. (opsional, sekali) install browser untuk cek visa nyata
python -m playwright install chromium

# 3. jalankan migrations bila DB baru / belum tersinkron
python migrate.py status
python migrate.py up

# 4. jalankan server
python app.py
#   atau: uvicorn app:asgi --host 0.0.0.0 --port 3000
```

Buka `http://localhost:3000`.

## User & role

| Username   | Role         | Landing default        |
|------------|--------------|------------------------|
| `admin`    | `admin`      | Dashboard Super        |
| `manager1` | `management` | Home Management (M-A)  |
| `finance1` | `finance`    | Home Finance (F-A)     |
| `ops1`     | `ops`        | Home Ops               |
| `sales1`   | `sales`      | Home Sales             |
| `lina`     | `sales`      | (sama, PII real)       |
| `titin`    | `sales`      | (sama, PII real)       |
| `farah`    | `sales`      | (sama, PII real)       |

Password default untuk akun demo `password123`. Akun `admin/lina/titin/farah`
adalah **user produksi dengan data PII nyata** — jangan reset di sembarang env.

## Fitur utama

**Sales & Marketing**
- CRM lead → follow-up → closing pipeline.
- Sales targets per bulan + leaderboard.
- Marketing analytics dashboard (peta, tren, demografi, channel breakdown).
- Global search (Cmd+K palette).

**Operasional**
- Kotak Inbox Insiden dengan severity + assignee.
- Checklist pra-keberangkatan per paket (H-90..H-1 auto-schedule).
- Vendor bookings (hotel/airline/bus/muthawif) dengan status Confirmed/Paid.
- Serah terima perlengkapan (SOP Gerbang: min DP % configurable).
- QR check-in boarding + label print.
- Debrief post-trip + feedback jamaah.

**Finance**
- Buku Kas (transactions) dengan Koreksi Admin (cascading rollback ke jamaah).
- Payroll bulanan idempoten.
- Aged Receivable + drill-down per jamaah.
- Cash Flow Forecast H-30/H-60.
- Payment Reconciliation: import CSV mutasi bank (BCA/Mandiri/Generic)
  → match ke `transactions` (auto-suggest amount + tanggal ±3 hari).
- P&L per paket + expense matrix per kategori.
- Vendor Quick Pay (procurement payment inline).

**Management**
- Home Management dengan KPI eksekutif.
- Approval Inbox: refund + commission-claim + expense + procurement.
- Company Targets Setting.
- Monthly Executive PDF Report.
- Risk Register (aggregate cross-modul).

**Master Data**
- Paket umroh dengan 3 tipe kamar + fee komisi default per paket.
- Mitra/Agen dengan komisi khusus per (agen, paket).
- Users + HR (leave request Cuti Tahunan gate 12 hari/tahun).

## Layout storage & keamanan

| Direktori           | Isi                                   | Akses            |
|---------------------|---------------------------------------|------------------|
| `public/`           | Frontend statis (index.html, images)  | Publik           |
| `public/branding/`  | Logo perusahaan (upload lewat admin)  | Publik           |
| `uploads_private/`  | PII: KTP/KK/Paspor/Vaksin + media WA  | Token JWT wajib  |
| `umar_crm.db`       | SQLite storage utama                  | File-system only |
| `neonize_auth.sqlite3` | Sesi WhatsApp (jangan commit)      | File-system only |

Dokumen PII (KTP/KK/Paspor) dan media WA **tidak** disajikan lewat
`StaticFiles`. Route `/uploads/{filename}` di
[routes/static_pages.py](routes/static_pages.py) memvalidasi token JWT
(header atau `?token=`), memblokir path traversal (`os.path.basename`),
lalu membaca dari `uploads_private/`.

## Migrations

Sistem migrations file-based (Alembic-lite). Setiap perubahan skema baru:

```bash
python migrate.py create nama-perubahan     # buat file 0NN_nama-perubahan.py
# ...edit up(conn) di file itu...
python migrate.py up                        # jalankan pending migrations
```

Baseline (kondisi skema per 2026-08) tersimpan di `migrations/001_baseline.py`.
`db.SCHEMA` tidak untuk perubahan schema baru — hanya reference legacy.
Detail: [ARCHITECTURE.md#migrations](ARCHITECTURE.md#migrations).

## WhatsApp integration

Modul `whatsapp.py` memakai **neonize** (whatsmeow/Go binding) sebagai backend
asli. Sesi disimpan `neonize_auth.sqlite3`. Bila `neonize` tidak terpasang atau
gagal, otomatis fallback ke stub (aplikasi tetap jalan, WA gagal graceful).

Mode simulasi (broadcast terhitung sukses tanpa kirim WA nyata):

```bash
WA_SIMULATE=1 python app.py     # Linux
$env:WA_SIMULATE=1; python app.py    # PowerShell
```

Endpoint WA: lihat [routes/wa.py](routes/wa.py).

## Backup DB

Auto-backup dibuat sebelum migrasi (`umar_crm.db.before-migrations-*`)
dan sebelum backfill script (`umar_crm.db.before-backfill-*`). Semua
di-.gitignore via pattern `umar_crm.db*`.

Backup manual sebelum operasi berat:

```bash
cp umar_crm.db "umar_crm.db.before-$(date +%Y%m%d-%H%M%S)"
```

## Kompatibilitas frontend

`public/index.html` monolitik + Tailwind CDN + Chart.js. Kontrak API tidak
berubah sepanjang refactor — semua perubahan backend transparan bagi
frontend. Format error: `{"error": "pesan"}` (frontend baca `data.error`).

Event Socket.IO: `data_updated` (payload nama tabel) untuk trigger UI reload.
