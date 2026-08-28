"""
Konstanta path storage. Dipakai lintas router untuk file upload/serve.

- BASE_DIR       : root repo (dihitung dari lokasi file ini).
- PUBLIC_DIR     : file publik (StaticFiles mount). index.html, images, dst.
- UPLOAD_DIR     : PII jamaah + media WA. Hanya dilayani via /uploads/{filename}
                   yang wajib token JWT.
- BRANDING_DIR   : logo perusahaan. Publik karena harus tampil di layar login.
"""
import os

# deps/ ada di sub-folder repo -> BASE_DIR = parent-nya
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
# Dokumen PII (KTP/KK/Paspor/vaksin) & media WA disimpan di folder PRIVAT --
# di luar direktori statis publik -- dan hanya dilayani lewat route ber-token.
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads_private")
# Logo BUKAN PII & harus tampil di layar login (pra-autentikasi), jadi disimpan
# di direktori publik.
BRANDING_DIR = os.path.join(PUBLIC_DIR, "branding")

__all__ = ["BASE_DIR", "PUBLIC_DIR", "UPLOAD_DIR", "BRANDING_DIR"]
