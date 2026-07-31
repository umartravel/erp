# Umar CRM — Migrasi Node.js → Python (FastAPI)

Aplikasi CRM Travel Umroh ini telah dimigrasikan **secara menyeluruh** dari
Node.js (Express + Socket.io + Baileys + Playwright) ke **Python**
(FastAPI + python-socketio + neonize + Playwright). Frontend (`public/index.html`)
**tidak berubah** kecuali satu baris sumber socket.io client.

## Pemetaan file (Node → Python)

| Node.js (lama)   | Python (baru)      | Keterangan |
|------------------|--------------------|------------|
| `server.js`      | `app.py`           | FastAPI + semua ~50 endpoint REST, RBAC, gatekeeper |
| `database.js`    | `db.py`            | SQLite: skema, seeding, migrasi kolom (pakai ulang `umar_crm.db` yang sama) |
| `whatsapp.js`    | `whatsapp.py`      | Bot WhatsApp (Baileys → **neonize**, dengan fallback stub) |
| `visa_checker.js`| `visa_checker.py`  | Automasi cek visa (Playwright Node → Playwright Python) |
| (socket.io di server.js) | `realtime.py` | Server Socket.IO + helper emit thread-safe |
| (jwt/bcrypt di server.js) | `auth.py`  | JWT + bcrypt + dependency RBAC |

File `.js` lama **dibiarkan sebagai referensi** dan tidak lagi dipakai.

## Cara menjalankan

```bash
# 1. (sekali) buat virtual environment & pasang dependensi
python -m venv .venv
.venv\Scripts\activate              # Windows
# source .venv/bin/activate          # Linux/Mac
pip install -r requirements.txt

# 2. (opsional) pasang browser untuk cek visa nyata
python -m playwright install chromium

# 3. jalankan server
python app.py
#   atau: uvicorn app:asgi --host 0.0.0.0 --port 3000
```

Buka `http://localhost:3000`. Login default (sama seperti versi Node):

| Username  | Password      | Role     |
|-----------|---------------|----------|
| `admin`   | `password123` | admin    |
| `sales1`  | `password123` | sales    |
| `finance1`| `password123` | finance  |
| `ops1`    | `password123` | ops      |

## Catatan WhatsApp (penting)

Baileys (Node) tidak punya padanan resmi di Python. Modul `whatsapp.py` memakai
**neonize** (berbasis whatsmeow/Go) sebagai backend asli, dengan beberapa perilaku:

- **Jika `neonize` terpasang** → WhatsApp asli aktif. Login lewat menu di UI
  (status → QR muncul → scan). Sesi disimpan di `neonize_auth.sqlite3`.
  Sesi `baileys_auth_info` lama **tidak kompatibel** (format berbeda), jadi perlu
  scan QR ulang sekali.
- **Jika `neonize` tidak terpasang** → otomatis pakai backend **stub** agar
  aplikasi tetap berjalan penuh; pengiriman WA dilewati (mengembalikan gagal).
- **Mode simulasi**: jalankan dengan `WA_SIMULATE=1` agar stub menganggap
  pengiriman "berhasil" untuk menguji alur CRM tanpa WhatsApp asli.
  ```bash
  set WA_SIMULATE=1 && python app.py        # Windows
  ```

Detail API neonize (penangkapan QR & struktur objek pesan) dapat berbeda antar
versi; seluruh akses sudah dibungkus guard agar kegagalan tidak menjatuhkan
aplikasi. Sesuaikan bagian `NeonizeBackend` di `whatsapp.py` dengan versi neonize
terpasang bila diperlukan.

## Kompatibilitas yang dijaga

- **Endpoint & payload** identik dengan versi Node (frontend tidak perlu diubah).
- **Format error** dikembalikan sebagai `{"error": "..."}` (frontend membaca `data.error`).
- **Event Socket.IO** sama: `wa_status`, `wa_new_message`, `wa_conversation_updated`, `data_updated`.
- **Database** memakai file `umar_crm.db` yang sama — seluruh data lama tetap utuh.
- **Hash password** bcrypt dari Node ($2b$) kompatibel penuh dengan paket `bcrypt` Python.

## Keamanan dokumen PII

Dokumen sensitif jamaah (KTP, KK, **Paspor**, sertifikat vaksin) dan media WhatsApp
**tidak lagi** disimpan di direktori publik. File fisik kini berada di folder
**`uploads_private/`** (di luar direktori statis) dan hanya dapat diakses melalui
route ber-autentikasi:

```
GET /uploads/{filename}?token=<JWT>
```

- Token boleh dikirim via header `Authorization: Bearer ...` atau query `?token=`
  (query diperlukan agar `<img>`/`<a>` di frontend bisa mengaksesnya).
- Tanpa token → 401, token tidak valid → 403, path-traversal (`../`) diblok.
- Frontend memakai helper `fileUrl(path)` yang otomatis menambahkan token.

> Catatan: token berada di query-string saat membuka file di tab baru. Untuk
> produksi sebaiknya beralih ke **signed URL** berumur pendek per-file. Ini sudah
> menutup celah utama (akses publik tanpa autentikasi), penguatan lanjutan opsional.

### Perubahan frontend (`public/index.html`)
1. Sumber socket.io client diarahkan ke CDN
   (`https://cdn.socket.io/4.8.1/socket.io.min.js`), karena python-socketio tidak
   melayani `/socket.io/socket.io.js` di path lokal seperti server Node.
2. Ditambahkan helper `fileUrl(path)` + penggunaannya pada link dokumen & media WA,
   agar akses file menyertakan token (lihat bagian Keamanan dokumen PII).

## Status verifikasi

Diuji end-to-end dan lulus: login + JWT, RBAC per-role (admin/sales/finance/ops),
gatekeeper harga & kuota, NIK unik, pembayaran + pencairan komisi otomatis,
cek visa (fallback simulasi Playwright), penyajian file statis & `/panduan`,
serta handshake Socket.IO v4.
