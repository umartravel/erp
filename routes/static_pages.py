"""
Router Halaman Statis & Media:
- 2 form HTML publik pendaftaran jamaah + agen.
- /uploads/{filename}: penyajian file PII/media WA dengan authenticate_file_token.
  Wajib DIDAFTARKAN SEBELUM StaticFiles mount di app.py supaya /uploads/* tidak
  pernah dilayani publik (file fisik tersimpan di folder privat di luar public/).

Catatan cleanup 2026-09-20: route /panduan (Panduan.html) + /dokumentasi-teknis
(Dokumentasi-Teknis.html) di-hapus. Kedua HTML tsb Jun 2026 pakai palet lama
(hijau-biru), tidak sesuai standar Luxury UMAR sekarang, dan tidak ada link
dari UI. Panduan modern per-role sekarang di-publish sebagai Artifact.
"""
import os

from fastapi import APIRouter
from fastapi.responses import FileResponse

from auth import authenticate_file_token
from deps import (
    BASE_DIR,
    Depends,
    HTTPException,
    UPLOAD_DIR,
)

router = APIRouter(tags=["static-pages"])


@router.get("/daftar")
async def daftar_publik():
    return FileResponse(os.path.join(BASE_DIR, "Pendaftaran-Publik.html"))


@router.get("/daftar-agen")
async def daftar_agen_publik():
    return FileResponse(os.path.join(BASE_DIR, "Pendaftaran-Agen-Publik.html"))


# Penyajian file PII/media WA dengan autentikasi (token via header atau ?token=).
# Route ini didefinisikan SEBELUM mount statis (di app.py) sehingga /uploads/*
# TIDAK pernah dilayani publik. File fisik tersimpan di folder privat di luar public/.
@router.get("/uploads/{filename}")
async def serve_upload(filename: str, _user=Depends(authenticate_file_token)):
    safe = os.path.basename(filename)  # cegah path traversal (../)
    path = os.path.join(UPLOAD_DIR, safe)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File tidak ditemukan")
    return FileResponse(path)
