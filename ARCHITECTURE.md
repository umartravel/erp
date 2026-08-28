# Arsitektur Umar CRM

Dokumen ini adalah acuan bagi developer yang menambah fitur atau memaintain
kode. Untuk cara menjalankan aplikasi, lihat [README_PYTHON.md](README_PYTHON.md).

## Struktur repo (top-level)

```
app.py                    # 170 baris. Bootstrap: FastAPI + lifespan + exception
                          # handler + include_router x 22 + StaticFiles + Socket.IO
                          # wrap. TIDAK ada logika bisnis di sini.

deps.py                   # Shared helpers + konstanta path yang dipakai lintas
                          # router. Semua router import dari sini (bukan app.py)
                          # untuk hindari circular import.

routes/                   # 22 file router, satu domain per file.
  __init__.py             # Package marker.
  reconcile.py            # F-E: bank mutation CSV import + match
  finance.py              # F-A, F-B, F-D: Home Finance, forecast, aged
  finance_tx.py           # Transactions Buku Kas + payroll + reports/pnl
  mgmt.py                 # M-A, M-C, M-D, M-E: Home Mgmt, PDF, targets, risk
  ops.py                  # O-A + incidents + package ops verbs (feedback/debrief/vendor/checklist)
  sales.py                # S-A: Home Sales + targets + performance + followups
  jamaah_actions.py       # Jamaah sub-resource: cancel/comments/activities/refund
  jamaah_read.py          # GET + POST /api/jamaah
  jamaah_write.py         # 7 endpoint write: PUT/{jid} big update + payment + doc + check-visa + ops
  packages.py             # Master Paket CRUD + staff + room-groups + 3 PDF
  inventory.py            # Inventory + company-assets + handover + depart + restock
  marketing.py            # 3 endpoint analytics (summary/filters/rows)
  users.py                # Users CRUD + profil diri + reset-password
  agents.py               # Agent CRUD + package-fees + commission-claims lifecycle
  expense.py              # Expense Reports lifecycle + PDF
  procurement.py          # Vendor B2B kontrak + payment + dummy VA
  hr.py                   # Pendaftaran publik jamaah/agen + leave requests
  settings.py             # /api/settings + /api/settings/logo (branding)
  wa.py                   # WhatsApp: status/connect/logout/send/templates
  dashboard.py            # Login + dashboard/super + tactical + audit + search
  static_pages.py         # /panduan, /daftar, /daftar-agen, /uploads/{filename}

db.py                     # SQLite connection + PRAGMA WAL + SCHEMA legacy (baseline)
                          # + migration runner (dipanggil dari lifespan)
auth.py                   # JWT: create_token, authenticate_token,
                          # authenticate_file_token (untuk /uploads) + bcrypt
realtime.py               # Socket.IO server + notify helper (thread-safe emit)
whatsapp.py               # WA neonize backend + stub fallback + WA_SIMULATE mode
visa_checker.py           # Playwright check visa (dengan fallback offline)
reconcile_importer.py     # Bank CSV parser (BCA/Mandiri/Generic detect) + SHA1
expense_pdf.py            # ReportLab: Expense Report PDF (routes/expense.py)
mgmt_pdf.py               # ReportLab: Monthly Executive Report (routes/mgmt.py)
jamaah_docs_pdf.py        # ReportLab: Manifest/Roomlist/Absensi (routes/packages.py)
migrate.py                # CLI: status | create <name> | up
migrations/               # Migration files versioned by NNN prefix
  001_baseline.py         # Baseline schema; jangan sentuh -- untuk fresh DB
```

## Pola router (routes/*.py)

Setiap file router memuat satu domain bisnis dan mengikuti pola:

```python
"""
Docstring: describe apa yang di-cover router ini.
"""
from fastapi import APIRouter
import db
from deps import (
    Depends, HTTPException,
    authenticate_token, json_body, log_action, notify, require_role,
    # ...helper lain sesuai kebutuhan
)

router = APIRouter(tags=["<domain>"])


@router.get("/api/<domain>/<endpoint>")
async def handler(user=Depends(authenticate_token)):
    require_role(user, "admin", "management")  # RBAC per-endpoint
    # ...query db.query_one / db.query_all / db.execute
    log_action(user, "ACTION_TAG", "detail human-readable")
    notify("data_updated", "<table_name>")     # trigger UI reload
    return {...}
```

**Konvensi:**

1. **Router register di `app.py` dengan `app.include_router(<name>_router)`** --
   satu urutan di block `include_router` (~30 baris). Urutan bebas KECUALI:
   - `routes/static_pages.py` HARUS di-include supaya `/uploads/{filename}`
     tidak di-shadow oleh `StaticFiles` mount di paling akhir.
   - `routes/jamaah_write.py` internal: `PUT /api/jamaah/bulk-ops` didaftarkan
     SEBELUM `PUT /api/jamaah/{jid}` di file yang sama (matching order per
     `@router.*` decoration).

2. **Import dari `deps.py`, BUKAN dari `app.py`.** Circular import risk.

3. **Body parser:** pakai `Depends(json_body)` (bukan Pydantic model) --
   kompatibel dengan pola Express lama, error handling terintegrasi dengan
   konversi `{"error": "..."}` di app.py.

4. **RBAC:** panggil `require_role(user, "role1", "role2")` di awal handler
   atau setelah validasi input dasar. Alternatif untuk sales: `assert_jamaah_access(jid, user)`
   yang cek `sales_id` ownership.

5. **Notify:** setelah tulis DB apapun, panggil `notify("data_updated", "<table>")`.
   Frontend socket handler auto-reload halaman yang menampilkan tabel itu.

6. **Log action:** untuk tiap aksi write yang bermakna bisnis, `log_action(user, "TAG", "detail")`
   -> masuk `audit_logs` yang dilihat di `/audit-logs`.

## Deps.py: shared helpers

Berisi:

**Konstanta path:**
- `BASE_DIR`, `PUBLIC_DIR`, `UPLOAD_DIR`, `BRANDING_DIR`

**HTTP helpers:**
- `json_body(request)` -- body parser longgar (dipakai lewat `Depends`).
- `require_role(user, *roles)` -- RBAC guard.
- `authenticate_token`, `notify` re-export biar router 1x import.

**Audit & settings:**
- `log_action(user, action, details)` -- INSERT ke audit_logs + notify.
- `get_setting(key, default)` -- baca `settings` table (key/value).

**Utility:**
- `parse_int(value, field)` -- raise HTTP 400 jika bukan angka.
- `fmt_id(n)` -- format "1.000.000" (locale ID).
- `fire_and_forget(coro)` -- asyncio.create_task wrapper untuk WA async.

**Jamaah status system (4 dimensi):**
- `_derive_status(pipeline, payment, visa, trip)` -> label legacy string.
- `sync_status_mirror(jid)` -- recompute jamaah.status dari 4 dimensi.
- `status_to_dims(status_legacy, paid, total)` -> 3-tuple pipeline/payment/trip.
- `_field_change(label, old, new)` -- string "Label (X -> Y)" untuk audit trail.
- `assert_jamaah_access(jid, user)` -- sales hanya boleh akses jamaah miliknya.

## Jamaah status system

Table `jamaah` menyimpan **4 dimensi bersih**:
- `pipeline_stage`: Lead / Waitlisted / Registered / Booked / Cancelled
- `payment_status`: Unpaid / DP / Lunas
- `visa_status`: Belum Proses / Proses Kedutaan / Visa Approved / Visa Rejected / ...
- `trip_status`: NotStarted / OnTrip / Completed

Plus **kolom cermin `status`** (legacy label string) yang di-derive otomatis via
`sync_status_mirror(jid)` setiap kali salah satu dimensi berubah. Frontend
membaca `status` legacy -- dimensi tidak disentuh langsung dari UI.

**Aturan penting:**
- Payment endpoint (`PUT /api/jamaah/{jid}/payment`) UPDATE payment_status +
  pipeline_stage saja. Trip & visa tidak disentuh.
- Check-visa endpoint UPDATE visa_status saja. Payment & pipeline tidak tersentuh
  (bug lama: dulu ikut ke-overwrite jadi "Unpaid").
- PUT /api/jamaah/{jid} (big update): boleh geser pipeline HANYA jika masih di
  funnel awal (Lead/Waitlisted/Registered). Yang sudah Booked/On Trip dikunci.

## Migrations

File-based, mirip Alembic tapi minimal. Runner ada di `db._migrate()` yang
dipanggil dari `lifespan`. State di tabel `schema_migrations` (kolom `version`).

**Menambah migration baru:**

```bash
python migrate.py create tambah-kolom-x
# -> generate migrations/002_tambah-kolom-x.py dengan template up(conn)
```

Isi template:

```python
def up(conn):
    conn.execute("ALTER TABLE jamaah ADD COLUMN x TEXT")
    # SQLite tidak bisa DROP COLUMN <SQLite 3.35 -- pakai rename+copy trick
```

**Menjalankan pending migrations:**

```bash
python migrate.py up            # jalankan yang belum applied
python migrate.py status        # cek versi terakhir + pending
```

**Baseline (`migrations/001_baseline.py`):** copy `db.SCHEMA` list untuk
DB kosong. `db.SCHEMA` **tidak lagi untuk perubahan schema baru** -- hanya
dijadikan snapshot per 2026-08. Semua perubahan skema baru masuk sebagai
migration file baru.

## Storage layout

```
umar_crm.db                                # SQLite utama (WAL mode)
umar_crm.db-shm, umar_crm.db-wal           # SQLite runtime files
umar_crm.db.before-migrations-<ts>         # Auto-backup sebelum migrate up
umar_crm.db.before-backfill-<ts>           # Auto-backup script backfill

neonize_auth.sqlite3                       # Sesi WhatsApp neonize
uploads_private/                           # PII + media WA (token-gated)
  doc_ktp_<jid>_<ts>.jpg                   # KTP jamaah
  doc_kk_<jid>_<ts>.jpg                    # Kartu Keluarga
  doc_passport_<jid>_<ts>.jpg              # Paspor
  doc_vaccine_<jid>_<ts>.jpg               # Vaksin
  profile_<uid>_<ts>.jpg                   # Foto profil karyawan
  expline_<rid>_<ts>.jpg                   # Struk expense line
public/                                    # Publik (StaticFiles mount "/")
  index.html                               # Frontend monolitik (Tailwind + Chart.js)
  branding/logo_<ts>.png                   # Logo perusahaan (upload lewat admin)
```

## Alur data khusus (side-effect graph)

**Payment (`PUT /api/jamaah/{jid}/payment`):**
1. UPDATE jamaah.paid_amount + payment_status + pipeline_stage.
2. `sync_status_mirror` -- update kolom `status` cermin.
3. INSERT `transactions (type=income, category=payment)`.
4. **JIKA baru menjadi Lunas + ada agent_id:** INSERT `commission_claims (status=Pending)`.
5. Kirim WA invoice via `fire_and_forget`.
6. Emit `data_updated` untuk `transaction`, `jamaah`, `commission_claim`.

**Refund disburse (`PUT /api/refund-requests/{rid}/disburse`):**
1. UPDATE jamaah.paid_amount - refund.amount + recompute payment_status.
2. INSERT `transactions (type=expense, category=refund)`.
3. Jika refund_request.cancel_booking: UPDATE pipeline_stage='Cancelled'.
4. UPDATE refund_requests.status='Dicairkan'.

**Commission disburse (`PUT /api/commission-claims/{cid}/disburse`):**
1. INSERT `transactions (type=expense, category=commission)`.
2. UPDATE commission_claims.status='Dicairkan' + transaction_id link.

**Transaction delete (`DELETE /api/transactions/{tid}` -- admin only):**
Cascading rollback: kalau category='payment' -> kembalikan
jamaah.paid_amount - amount + recompute payment_status + sync_status_mirror.
Bila tidak, hanya DELETE row transactions.

## Socket.IO events

**Server -> client:**
- `data_updated` payload string `<table_name>` -- trigger UI reload halaman
  yang menampilkan tabel itu (`jamaah`, `transaction`, `procurement`, dst).
- `wa_status` -- perubahan status WA (connected/disconnected/qr).

Emit dari router via `notify("data_updated", "table_name")` (dari `realtime.py`,
thread-safe: schedule ke event loop utama via `set_loop` di lifespan).

## Backup & DR

- **DB backup manual** sebelum operasi berat: `cp umar_crm.db "umar_crm.db.before-$(date +%s)"`
- **Auto-backup pra-migrasi** oleh `migrate.py up` sebelum jalanin migration
  pertama yang pending.
- **Auto-backup pra-backfill** oleh `backfill_transactions.py`.
- **PII** di `uploads_private/` -- backup manual + jangan commit.
- **Sesi WA** (`neonize_auth.sqlite3`) -- backup opsional; hilang = user harus
  scan QR ulang.

## Environment variables

| Variable        | Default | Efek                                                    |
|-----------------|---------|---------------------------------------------------------|
| `PORT`          | `3000`  | Port uvicorn.                                           |
| `WA_SIMULATE`   | unset   | `1` = broadcast dihitung sukses tanpa kirim WA nyata.   |
| `JWT_SECRET`    | (hard-coded) | Rahasia signing JWT.                               |

## Testing

Belum ada test suite formal. Verifikasi manual pola:
1. `.venv/Scripts/python.exe -c "import app; print('OK')"` -- smoke import.
2. `.venv/Scripts/python.exe app.py &` + curl endpoint kritis dengan token.
3. Route registration check via `app.app.routes` introspection.

## Add feature checklist

1. **Butuh kolom baru?** -> tambah migration file (`python migrate.py create`).
2. **Butuh endpoint baru?** -> tambah di router yang paling relevan
   (`routes/<domain>.py`) atau bikin router baru + register di `app.py`.
3. **Perlu helper share antar router?** -> tambah ke `deps.py` (dengan
   `__all__` update).
4. **Perlu side-effect ke tabel lain?** -> cek "Alur data khusus" di atas
   supaya konsisten dengan pola cascading yang sudah ada.
5. **Emit socket notify** untuk setiap tulisan DB yang mempengaruhi tampilan.
6. **Log action** untuk semua aksi bermakna audit.
7. **Cek routing order** kalau path punya kombinasi literal + `{id}`.
