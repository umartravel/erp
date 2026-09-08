# Login Background Images

Folder ini berisi gambar background untuk halaman login. Sistem otomatis rotate
gambar berdasarkan waktu (jam sistem client saat page dimuat).

## File yang dibutuhkan

Simpan 4 file berikut dengan nama persis (case-sensitive):

| File | Waktu tampil | Konten disarankan |
|---|---|---|
| `fajar.jpg` | 04:00 - 10:59 | Kabaah/Masjidil Haram saat fajar/pagi (soft orange) |
| `siang.jpg` | 11:00 - 14:59 | Kabaah siang hari (cerah, gold accent) |
| `sore.jpg`  | 15:00 - 17:59 | Masjid Nabawi golden hour (warm orange) |
| `malam.jpg` | 18:00 - 03:59 | Masjid Nabawi malam hari (deep blue + gold) |

## Spesifikasi teknis

- **Format**: `.jpg` atau `.webp` (rename ekstensi jadi `.jpg` supaya code otomatis kenali)
- **Resolusi**: minimum 1600x1000 px (idealnya 1920x1200)
- **Aspect ratio**: landscape 16:10 atau 16:9 (visual panel = 60% viewport width)
- **Ukuran file**: < 500KB per file (kompres via TinyPNG kalau perlu)
- **Fokus komposisi**: subject di tengah / rule of thirds (Ken Burns zoom+pan aktif)

## Fallback

Kalau salah satu file **tidak ada**, sistem otomatis tampilkan **gradient
overlay** dengan warna sesuai waktu (fajar rose-gold / siang amber / sore
coral / malam deep-navy). Aman untuk dikosongkan sementara.

## Sumber gambar yang aman digunakan

- **Foto sendiri** (kantor UMAR, hasil dokumentasi umroh) — paling aman
- **Wikimedia Commons** — public domain, cek license page (mis. CC-BY, CC0)
- **Unsplash** — free untuk komersial, cek `unsplash.com/license`
- **Pexels / Pixabay** — free untuk komersial, no attribution required

**Hindari:**
- Screenshot dari Google Images (bisa jadi hak cipta)
- Foto dari media sosial tanpa izin
- Gambar dari situs iklan/travel lain

## Cara mengganti

1. Simpan/rename gambar ke path exact: `public/assets/login-bg/{nama}.jpg`
2. Refresh halaman login (Ctrl+Shift+R kalau perlu bypass cache)
3. Klik tombol dev tool / hard-reload untuk lihat efek Ken Burns baru
