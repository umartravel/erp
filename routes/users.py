"""
Router Users & Profil: manajemen akun karyawan (admin) + profil diri (semua).

- /api/users, /api/users/directory                         (list)
- /api/users/me + PUT profile + POST photo + PUT password  (self-serve)
- /api/users POST + PUT + DELETE                           (admin)
- /api/users/{id}/reset-password                           (admin bypass)
"""
import asyncio
import base64
import os

from fastapi import APIRouter

import db
from auth import hash_password, verify_password
from deps import (
    Depends,
    HTTPException,
    UPLOAD_DIR,
    authenticate_token,
    json_body,
    log_action,
    notify,
    require_role,
)

router = APIRouter(tags=["users"])


@router.get("/api/users")
async def users_list(user=Depends(authenticate_token)):
    require_role(user, "admin", "management")
    return db.query_all(
        "SELECT id, username, name, role, base_salary, phone, personal_email, address, nik, "
        "birth_date, photo_url, last_education, education_major, education_institution, "
        "last_login_at FROM users", ()
    )


@router.get("/api/users/directory")
async def users_directory(user=Depends(authenticate_token)):
    # Versi ringan dari /api/users (tanpa data HR sensitif) -- dipakai untuk menampilkan
    # foto profil di tempat yang menyebut nama karyawan (mis. PIC Live Chat), yang bisa
    # diakses semua role, bukan cuma admin/management.
    return db.query_all("SELECT name, photo_url FROM users", ())


# ===========================================================================
# PROFIL SAYA: setiap karyawan mendata dirinya sendiri (kontak, foto, pendidikan)
# -- terpisah dari data akun (username/role/gaji) yang tetap hanya admin yang atur.
# Ganti password: karyawan bisa ganti sendiri (wajib password lama), Admin bisa
# reset password siapa saja tanpa password lama (lihat /api/users/{uid}/reset-password).
# ===========================================================================
@router.get("/api/users/me")
async def users_me(user=Depends(authenticate_token)):
    row = db.query_one(
        "SELECT id, username, name, role, phone, personal_email, address, nik, birth_date, "
        "photo_url, last_education, education_major, education_institution FROM users WHERE id = ?",
        (user["id"],),
    )
    if not row:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")
    return row


@router.put("/api/users/me/profile")
async def users_me_profile_update(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    g = body.get
    db.execute(
        "UPDATE users SET phone = ?, personal_email = ?, address = ?, nik = ?, birth_date = ?, "
        "last_education = ?, education_major = ?, education_institution = ? WHERE id = ?",
        (
            g("phone"), g("personal_email"), g("address"), g("nik"), g("birth_date"),
            g("last_education"), g("education_major"), g("education_institution"), user["id"],
        ),
    )
    log_action(user, "UPDATE_OWN_PROFILE", "Memperbarui data profil diri")
    return {"message": "Profil berhasil disimpan."}


@router.post("/api/users/me/photo")
async def users_me_photo_upload(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    file_base64 = body.get("fileBase64", "")
    ext = (body.get("ext") or "jpg").lower().lstrip(".")
    if ext not in ("png", "jpg", "jpeg", "webp"):
        raise HTTPException(status_code=400, detail="Format foto harus gambar (png/jpg/webp).")
    if not file_base64:
        raise HTTPException(status_code=400, detail="Tidak ada file foto.")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    file_name = f"profile_{user['id']}_{int(asyncio.get_event_loop().time() * 1000)}.{ext}"
    b64 = file_base64.split(",")[1] if "," in file_base64 else file_base64
    try:
        with open(os.path.join(UPLOAD_DIR, file_name), "wb") as f:
            f.write(base64.b64decode(b64))
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Data foto tidak valid.")

    photo_url = f"/uploads/{file_name}"
    db.execute("UPDATE users SET photo_url = ? WHERE id = ?", (photo_url, user["id"]))
    log_action(user, "UPDATE_OWN_PROFILE", "Mengunggah foto profil baru")
    return {"message": "Foto profil berhasil diperbarui.", "photo_url": photo_url}


@router.put("/api/users/me/password")
async def users_me_password_change(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    old_password = body.get("old_password") or ""
    new_password = body.get("new_password") or ""
    if not new_password or len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Password baru minimal 6 karakter.")

    row = db.query_one("SELECT password FROM users WHERE id = ?", (user["id"],))
    if not row or not verify_password(old_password, row["password"]):
        raise HTTPException(status_code=400, detail="Password lama salah.")

    db.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(new_password), user["id"]))
    log_action(user, "CHANGE_OWN_PASSWORD", "Mengganti password akun sendiri")
    return {"message": "Password berhasil diganti."}


@router.put("/api/users/{uid}/reset-password")
async def users_reset_password(uid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin")
    new_password = body.get("new_password") or ""
    if not new_password or len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Password baru minimal 6 karakter.")

    target = db.query_one("SELECT username FROM users WHERE id = ?", (uid,))
    if not target:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")

    db.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(new_password), uid))
    log_action(user, "RESET_USER_PASSWORD", f"Reset password untuk akun: {target['username']}")
    return {"message": "Password berhasil direset."}


@router.post("/api/users")
async def users_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin")
    g = body.get
    try:
        hashed = hash_password(g("password") or "")
        db.execute(
            "INSERT INTO users (username, password, name, role, base_salary) VALUES (?, ?, ?, ?, ?)",
            (g("username"), hashed, g("name"), g("role"), g("base_salary") or 0),
        )
    except Exception as e:  # noqa: BLE001
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=400, detail="Username ini sudah digunakan oleh karyawan lain.")
        raise HTTPException(status_code=500, detail=str(e))
    log_action(user, "CREATE_USER", f"Membuat akun karyawan baru: {g('username')} (role: {g('role')})")
    notify("data_updated", "user")
    return {"message": "User berhasil ditambahkan."}


@router.put("/api/users/{uid}")
async def users_update(uid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin")
    g = body.get
    target = db.query_one("SELECT username FROM users WHERE id = ?", (uid,))
    if not target:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")
    try:
        db.execute(
            "UPDATE users SET username = ?, name = ?, role = ?, base_salary = ?, phone = ?, "
            "personal_email = ?, address = ?, nik = ?, birth_date = ?, last_education = ?, "
            "education_major = ?, education_institution = ? WHERE id = ?",
            (
                g("username"), g("name"), g("role"), g("base_salary") or 0, g("phone"),
                g("personal_email"), g("address"), g("nik"), g("birth_date"), g("last_education"),
                g("education_major"), g("education_institution"), uid,
            ),
        )
    except Exception as e:  # noqa: BLE001
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=400, detail="Username ini sudah digunakan oleh karyawan lain.")
        raise HTTPException(status_code=500, detail=str(e))
    log_action(user, "UPDATE_USER", f"Mengubah data karyawan: {target['username']} -> {g('username')}")
    notify("data_updated", "user")
    return {"message": "Data karyawan berhasil diperbarui."}


@router.delete("/api/users/{uid}")
async def users_delete(uid: int, user=Depends(authenticate_token)):
    require_role(user, "admin")
    if uid == user["id"]:
        raise HTTPException(
            status_code=400, detail="Akses Ditolak: Anda tidak dapat menghapus akun Anda sendiri."
        )
    target = db.query_one("SELECT username, role FROM users WHERE id = ?", (uid,))
    # Phase 7b-3: Guard resign -- kalau user CS ini masih pegang agen, tolak
    # sampai admin handoff dulu. Cegah agen orphan (handler_cs_id = dangling FK)
    # per keputusan di project_edit_jamaah_agent_transfer_plan.md.
    agent_count_row = db.query_one(
        "SELECT COUNT(*) as c FROM agents WHERE handler_cs_id = ?", (uid,)
    )
    agent_count = agent_count_row["c"] if agent_count_row else 0
    if agent_count > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"User ini masih pegang {agent_count} agen. Pindahkan dulu ke CS lain "
                f"lewat Keagenan (Transfer Agen) atau bulk handoff sebelum hapus."
            ),
        )
    db.execute("DELETE FROM users WHERE id = ?", (uid,))
    if target:
        log_action(user, "DELETE_USER", f"Menghapus akun karyawan: {target['username']} (role: {target['role']})")
    notify("data_updated", "user")
    return {"message": "User dihapus."}
