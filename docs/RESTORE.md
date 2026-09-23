# Restore UMAR ERP DB dari Cloudflare R2 (Litestream)

Runbook disaster recovery untuk mengembalikan `umar_crm.db` dari backup R2.

## Kapan pakai runbook ini

- File `umar_crm.db` corrupt/rusak (SQLite error saat open)
- File `umar_crm.db` terhapus tidak sengaja
- Migrasi ke laptop/server baru
- Rollback ke titik waktu spesifik dalam 24 jam terakhir (mis. sebelum bug atau perubahan salah)
- Audit — snapshot state pada tanggal tertentu

## Prasyarat

- File `litestream.yml` di root project (berisi R2 credentials — gitignored)
- Binary `tools/litestream.exe` v0.5.17+
- Akses ke R2 bucket `umar-erp-backup` (via credentials di `litestream.yml`)

## Skenario A: Restore ke state TERBARU (paling umum)

Kalau `umar_crm.db` hilang/corrupt dan ingin state paling baru:

```powershell
# 1. STOP Litestream dulu (biar tidak overwrite selama restore)
Stop-ScheduledTask -TaskName "LitestreamUmarERP"
Get-Process litestream -ErrorAction SilentlyContinue | Stop-Process -Force

# 2. Backup file yg rusak (jaga-jaga)
cd "C:\09 UGS2\SC ERP\ERP UMAR"
if (Test-Path umar_crm.db) { Move-Item umar_crm.db umar_crm.db.corrupt-$(Get-Date -Format yyyyMMddHHmmss) }

# 3. Restore dari R2
.\tools\litestream.exe restore -config litestream.yml "C:\09 UGS2\SC ERP\ERP UMAR\umar_crm.db"

# 4. Verify file terbuat
sqlite3 umar_crm.db "SELECT COUNT(*) FROM jamaah;"

# 5. Start Litestream lagi
Start-ScheduledTask -TaskName "LitestreamUmarERP"
```

## Skenario B: Restore ke titik waktu spesifik (point-in-time recovery)

Misal: mau state jamaah tanggal `2026-09-23 10:00 WIB` (03:00 UTC).

```powershell
Stop-ScheduledTask -TaskName "LitestreamUmarERP"

# Restore ke timestamp UTC (bukan WIB!)
.\tools\litestream.exe restore -config litestream.yml `
    -timestamp "2026-09-23T03:00:00Z" `
    -o "C:\09 UGS2\SC ERP\ERP UMAR\umar_crm.db.restore-1000wib"

# Inspeksi hasil (jangan overwrite production dulu)
sqlite3 "umar_crm.db.restore-1000wib" "SELECT COUNT(*) FROM jamaah;"

# Kalau OK dan mau pakai sebagai production:
Move-Item umar_crm.db umar_crm.db.pre-rollback-$(Get-Date -Format yyyyMMddHHmmss)
Move-Item umar_crm.db.restore-1000wib umar_crm.db

Start-ScheduledTask -TaskName "LitestreamUmarERP"
```

Catatan waktu:
- Litestream simpan timestamp dalam **UTC**
- WIB (Indonesia) = UTC+7
- Contoh: WIB 10:00 → UTC 03:00, WIB 15:30 → UTC 08:30
- Format harus ISO 8601 dengan `Z` di akhir (UTC marker)

## Skenario C: List semua snapshot yang ada di R2

Untuk lihat titik waktu apa saja yang bisa di-restore:

```powershell
.\tools\litestream.exe ltx -config litestream.yml "C:\09 UGS2\SC ERP\ERP UMAR\umar_crm.db"
```

Output menampilkan level, min_txid, max_txid, size, dan created (UTC).

## Skenario D: Migrasi ke laptop/server baru

Setup di laptop baru:
1. Clone repo UMAR ERP dari GitHub
2. Copy `litestream.yml` (dari secure backup — jangan lewat email/chat plaintext)
3. Download binary `tools/litestream.exe` dari GitHub releases benbjohnson/litestream (versi 0.5.17+)
4. Restore DB:
   ```powershell
   cd "C:\path\ke\umar-erp"
   .\tools\litestream.exe restore -config litestream.yml "C:\path\ke\umar-erp\umar_crm.db"
   ```
5. Register scheduled task dengan `Register-ScheduledTask` PowerShell cmdlet (contoh command di setup guide sesi Litestream)
6. Start Litestream via `Start-ScheduledTask -TaskName "LitestreamUmarERP"`

## Verifikasi post-restore

Setelah restore, pastikan data intact:

```powershell
python -c "import sqlite3; c=sqlite3.connect('umar_crm.db'); print('jamaah:', c.execute('SELECT COUNT(*) FROM jamaah').fetchone()[0]); print('journal_lines:', c.execute('SELECT COUNT(*) FROM journal_lines').fetchone()[0]); print('packages:', c.execute('SELECT COUNT(*) FROM packages').fetchone()[0]); print('tx:', c.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]); row = c.execute('SELECT SUM(debit)-SUM(credit) FROM journal_lines').fetchone(); print('zero-sum:', row[0], '(harus 0)')"
```

Kalau `zero-sum: 0` = akrual PSAK intact. Kalau bukan 0, ada corruption partial.

## Troubleshooting

**Error: "no snapshots found"**
- Cek `litestream.yml` — pastikan `path` sesuai dengan lokasi DB waktu di-replikasi
- Cek R2 bucket via Cloudflare dashboard — pastikan folder `umar_crm/` ada isi

**Error: "access denied" saat connect R2**
- Access Key / Secret di `litestream.yml` mungkin expired atau revoked
- Cek Cloudflare dashboard → R2 → API tokens → status `litestream-umar-erp`

**Restore selesai tapi DB kosong / cuma schema**
- Kemungkinan restore ke timestamp sebelum initial data ada
- Coba tanpa `-timestamp` untuk state terbaru
- Atau list snapshots dulu (Skenario C) untuk lihat rentang waktu tersedia

**Windows: "sync restore output dir: sync .: Access is denied" saat restore**
- Bisa diabaikan — file `.db` sudah ter-restore benar. Error cuma flush dir metadata yang Windows tolak (kuirk NTFS + non-elevated process).
- Verify via COUNT(*) query untuk pastikan data lengkap.

## Kontak

Kalau step di atas gagal:
- Cek log Litestream: `logs/litestream-YYYYMMDD.log`
- Dokumentasi Litestream: https://litestream.io/reference/restore/
- R2 dashboard: https://dash.cloudflare.com/ → R2 → `umar-erp-backup`
