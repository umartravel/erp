# data/marketing — Data Historis Marketing Analytics

Folder ini berisi CSV historis yang menjadi sumber halaman **Marketing Analytics** di ERP.
Data mengalir: CSV → `marketing_importer.py` → tabel `marketing_closings` &
`marketing_agents` di SQLite → API `/api/marketing/*` → halaman `page-marketing` di UI.

## Sumber & Anonimisasi

- **Sumber asli**: repositori publik [`ABEEE-666/web-umar`](https://github.com/ABEEE-666/web-umar)
  file `dummy_databaseumar.csv`, `dummy_listagen.csv`, dan
  `Dummy-Paket-UMAR-26_27_upd130826.csv` (branch `main`).
- **PII yang dihapus / diganti** sebelum di-commit ke repo ini (lihat script
  `tools/anonymize_marketing_csv.py`):

  | Kolom asli | Menjadi |
  |---|---|
  | `NAMA JAMAAH`, `NAMA PEMESAN` | `JAMAAH-001..321`, `PEMESAN-001..N` |
  | `NAMA_AGEN`, `NAMA AGEN` | `AGEN-001..070` (mapping via `ID_AGEN`) |
  | `NAMA AYAH`, `TEMPAT LAHIR`, `TANGGAL LAHIR` | dikosongkan |
  | `NO. TELP JAMAAH`, `NO. TELP KELUARGA`, `EMAIL JAMAAH` | dikosongkan |
  | `NOMOR IDENTITAS` (NIK) | dikosongkan |
  | `ALAMAT`, `KELURAHAN`, `DETAIL ALAMAT` | dikosongkan |
  | `NO PASPORT`, `ISSUED`, `EXPIRY` | dikosongkan |
  | `NOMOR WA`, `AKUN IG`, `EMAIL` (agents) | dikosongkan |

- **Field agregat yang DIPERTAHANKAN** (dibutuhkan chart & heatmap):
  `PROVINSI`, `KAB/KOTA`, `KECAMATAN`, `PAKET`, `HARGA`, `TOTAL BAYAR`, `DP`, `KURANG`,
  `STATPAY`, `ADMIN MARKETING`, `CHANNEL`, `SUB CHANNEL`, `TANGGAL ORDER`, `USIA`,
  `JENIS KELAMIN`, `PENDIDIKAN`, `PEKERJAAN`, `IS_TRANSAKSI_AGEN`, `ID_AGEN`,
  `ID JAMAAH` (identifier internal, bukan PII).

Konsekuensi: dashboard menampilkan analisis marketing (heatmap, tren, leaderboard admin/agen,
distribusi paket, breakdown channel) secara **agregat**. Drill-down ke identitas jamaah
individu tidak lagi mungkin dari CSV yang di-commit — sengaja, sesuai prinsip
data minimization.

## Cara Refresh Data

1. Update CSV di folder ini (atau salin ulang dari sumber asli).
2. Kalau CSV baru mengandung PII, jalankan anonymizer dulu:
   ```bash
   python tools/anonymize_marketing_csv.py
   ```
3. Import ulang ke DB:
   - **Otomatis**: hapus row lama (`DELETE FROM marketing_closings;`) lalu restart
     server — auto-import akan jalan.
   - **Manual**: login sebagai admin, klik tombol **Re-import CSV** di header
     halaman Marketing Analytics (memanggil `POST /api/marketing/import`).

## File

| File | Isi | Baris |
|---|---|---:|
| `closings.csv` | Transaksi jamaah historis (agregat) | 321 |
| `agents.csv` | Direktori agen aktif (anon) | 70 |
| `packages_2026_27.csv` | Katalog paket 2026/27 (referensi harga & kursi) | 9 |
