"""
Router Pengaturan Aplikasi (branding, ambang DP, dll.):
- GET  /api/settings         : whitelist kunci tampilan publik (tanpa auth)
- PUT  /api/settings         : update kunci settings (admin)
- POST /api/settings/logo    : upload logo perusahaan (admin, base64 -> BRANDING_DIR)
"""
import asyncio
import base64
import os

from fastapi import APIRouter

import db
from deps import (
    BRANDING_DIR,
    Depends,
    HTTPException,
    authenticate_token,
    json_body,
    log_action,
    notify,
    require_role,
)

router = APIRouter(tags=["settings"])


@router.get("/api/settings")
async def settings_get():
    # Publik (tanpa auth) karena dibutuhkan layar login untuk menampilkan branding.
    # Hanya kunci tampilan non-sensitif yang dikembalikan (whitelist), sehingga
    # setting sensitif apa pun yang ditambahkan di masa depan tidak ikut bocor.
    public_keys = (
        "company_name", "company_tagline", "logo_url", "equipment_min_dp_percent",
        "company_legal_name", "company_address", "company_email", "company_website",
    )
    placeholders = ", ".join("?" * len(public_keys))
    rows = db.query_all(
        f"SELECT key, value FROM settings WHERE key IN ({placeholders})", public_keys
    )
    return {r["key"]: r["value"] for r in rows}


@router.put("/api/settings")
async def settings_update(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin")
    allowed = {
        "company_name", "company_tagline", "logo_url", "equipment_min_dp_percent",
        "company_legal_name", "company_address", "company_email", "company_website",
    }
    saved = []
    for key, value in body.items():
        if key not in allowed:
            continue
        if key == "equipment_min_dp_percent":
            # Validasi angka 0-100
            try:
                pct = float(value)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Minimum DP harus berupa angka.")
            if pct < 0 or pct > 100:
                raise HTTPException(status_code=400, detail="Minimum DP harus antara 0 dan 100 persen.")
            value = str(pct.is_integer() and int(pct) or pct)
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        saved.append(key)
    log_action(user, "UPDATE_SETTINGS", f"Mengubah pengaturan: {', '.join(saved)}")
    notify("data_updated", "settings")
    return {"message": "Pengaturan berhasil disimpan.", "saved": saved}


@router.post("/api/settings/logo")
async def settings_logo(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin")
    file_base64 = body.get("fileBase64", "")
    ext = (body.get("ext") or "png").lower().lstrip(".")
    if ext not in ("png", "jpg", "jpeg", "webp", "gif", "svg"):
        raise HTTPException(status_code=400, detail="Format logo harus gambar (png/jpg/webp/gif/svg).")
    if not file_base64:
        raise HTTPException(status_code=400, detail="Tidak ada file logo.")

    os.makedirs(BRANDING_DIR, exist_ok=True)
    file_name = f"logo_{int(asyncio.get_event_loop().time() * 1000)}.{ext}"
    b64 = file_base64.split(",")[1] if "," in file_base64 else file_base64
    try:
        with open(os.path.join(BRANDING_DIR, file_name), "wb") as f:
            f.write(base64.b64decode(b64))
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Data logo tidak valid.")

    logo_url = f"/branding/{file_name}"
    db.execute(
        "INSERT INTO settings (key, value) VALUES ('logo_url', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (logo_url,),
    )
    log_action(user, "UPDATE_LOGO", "Mengunggah logo baru perusahaan")
    notify("data_updated", "settings")
    return {"message": "Logo berhasil diperbarui.", "logo_url": logo_url}
