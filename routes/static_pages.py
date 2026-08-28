"""
Router Halaman Statis & Media:
- 4 halaman HTML publik (panduan, form pendaftaran jamaah/agen, dokumentasi).
- /uploads/{filename}: penyajian file PII/media WA dengan authenticate_file_token.
  Wajib DIDAFTARKAN SEBELUM StaticFiles mount di app.py supaya /uploads/* tidak
  pernah dilayani publik (file fisik tersimpan di folder privat di luar public/).
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


@router.get("/panduan")
async def panduan():
    return FileResponse(os.path.join(BASE_DIR, "Panduan.html"))


@router.get("/daftar")
async def daftar_publik():
    return FileResponse(os.path.join(BASE_DIR, "Pendaftaran-Publik.html"))


@router.get("/daftar-agen")
async def daftar_agen_publik():
    return FileResponse(os.path.join(BASE_DIR, "Pendaftaran-Agen-Publik.html"))


@router.get("/dokumentasi-teknis")
async def dokumentasi_teknis():
    return FileResponse(os.path.join(BASE_DIR, "Dokumentasi-Teknis.html"))


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
