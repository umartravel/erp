"""
Router HR & Onboarding:
- Pendaftaran publik JAMAAH (tanpa login) + review CS (approve manual via
  /api/jamaah POST, atau reject dari sini).
- Pendaftaran publik AGEN (tanpa login) + review admin/CS (approve via
  /api/agents POST dengan source_submission_id, reject dari sini).
- Surat izin karyawan: create (semua role) + list (self scoped) +
  review admin/mgmt (Cuti Tahunan gate 12 hari/tahun).

Semua endpoint dulunya duduk di app.py. Pemindahan tidak mengubah kontrak API.
"""
import datetime
import time

from fastapi import APIRouter, Request

import db
from deps import (
    Depends,
    HTTPException,
    authenticate_token,
    json_body,
    log_action,
    notify,
    require_role,
)

router = APIRouter(tags=["hr"])


# ===========================================================================
# RATE LIMITER (in-memory, per IP) untuk 2 endpoint publik tanpa auth
# ===========================================================================
_public_submit_log = {}  # ip -> [timestamp, ...], in-memory (cukup untuk skala 1 server)
_public_agent_submit_log = {}  # namespace terpisah dari pendaftaran jamaah di atas
RATE_LIMIT_MAX = 3
RATE_LIMIT_WINDOW_SEC = 3600


def _check_rate_limit(ip, log_store=None):
    log_store = _public_submit_log if log_store is None else log_store
    now = time.time()
    recent = [t for t in log_store.get(ip, []) if now - t < RATE_LIMIT_WINDOW_SEC]
    if len(recent) >= RATE_LIMIT_MAX:
        log_store[ip] = recent
        return False
    recent.append(now)
    log_store[ip] = recent
    return True


# ===========================================================================
# PENDAFTARAN PUBLIK JAMAAH (tanpa login) -> antrian validasi CS -> jamaah
# ===========================================================================
@router.post("/api/public/pendaftaran")
async def public_pendaftaran_create(request: Request, body: dict = Depends(json_body)):
    g = body.get

    # Honeypot: field tersembunyi yang cuma bot biasanya isi. Pura-pura sukses
    # (tanpa menyimpan) supaya bot tidak tahu submission-nya ditolak & tidak mencoba lagi.
    if g("website"):
        return {"message": "Data Anda sudah kami terima dan akan diverifikasi tim kami."}

    ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(ip):
        raise HTTPException(
            status_code=429, detail="Terlalu banyak percobaan dari perangkat ini. Silakan coba lagi nanti."
        )

    name = (g("name") or "").strip()
    phone = (g("phone") or "").strip()
    nik = (g("nik") or "").strip()
    if not name or not phone or not nik:
        raise HTTPException(status_code=400, detail="Nama, No. WhatsApp, dan Nomor Identitas wajib diisi.")

    preferred_cs_id = g("preferred_cs_id")
    db.execute(
        "INSERT INTO pendaftaran_publik (name, orderer_name, citizenship, identity_type, nik, "
        "passport_number, passport_issued, passport_issuer_city, gender, birth_place, birth_date, phone, "
        "family_phone, email, address, province, city, subdistrict, village, health_history, mahram, "
        "interest_note, source_ip, preferred_cs_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            name, g("orderer_name"), g("citizenship") or "WNI", g("identity_type") or "NIK", nik,
            g("passport_number"), g("passport_issued"), g("passport_issuer_city"),
            g("gender"), g("birth_place"), g("birth_date"), phone, g("family_phone"), g("email"),
            g("address"), g("province"), g("city"), g("subdistrict"), g("village"),
            g("health_history"), g("mahram"), g("interest_note"), ip,
            int(preferred_cs_id) if preferred_cs_id else None,
        ),
    )
    notify("data_updated", "pendaftaran_publik")
    return {
        "message": "Terima kasih! Data Anda sudah kami terima dan akan diverifikasi tim kami. "
        "Kami akan menghubungi Anda melalui WhatsApp dalam 1x24 jam."
    }


# Daftar CS (role sales) untuk dropdown "Pilih CS" di form publik -- sengaja endpoint
# terbuka (tanpa login) tapi cuma mengembalikan id+nama, tidak ada data sensitif.
@router.get("/api/public/cs-list")
async def public_cs_list():
    return db.query_all("SELECT id, name FROM users WHERE role = 'sales' ORDER BY name ASC", ())


@router.get("/api/pendaftaran-publik")
async def pendaftaran_publik_list(user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")
    rows = db.query_all(
        "SELECT p.*, u.name as preferred_cs_name FROM pendaftaran_publik p "
        "LEFT JOIN users u ON p.preferred_cs_id = u.id ORDER BY p.created_at DESC", ()
    )
    result = []
    for r in rows:
        r = dict(r)
        existing = db.query_one("SELECT id FROM jamaah WHERE nik = ?", (r["nik"],)) if r["nik"] else None
        r["nik_exists"] = bool(existing)
        result.append(r)
    return result


@router.put("/api/pendaftaran-publik/{pid}/reject")
async def pendaftaran_publik_reject(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Alasan penolakan wajib diisi.")

    row = db.query_one("SELECT * FROM pendaftaran_publik WHERE id = ?", (pid,))
    if not row:
        raise HTTPException(status_code=404, detail="Data pendaftaran tidak ditemukan")
    if row["status"] != "Pending":
        raise HTTPException(status_code=400, detail=f"Data ini sudah berstatus '{row['status']}'.")

    db.execute(
        "UPDATE pendaftaran_publik SET status = 'Ditolak', reviewed_by = ?, "
        "reviewed_at = CURRENT_TIMESTAMP, review_note = ? WHERE id = ?",
        (user["name"], reason, pid),
    )
    log_action(user, "REJECT_PUBLIC_REGISTRATION", f"Menolak pendaftaran publik dari {row['name']}: {reason}")
    notify("data_updated", "pendaftaran_publik")
    return {"message": "Pendaftaran publik berhasil ditolak."}


# ===========================================================================
# PENDAFTARAN AGEN/KONSULTAN PUBLIK (tanpa login) -> antrian validasi CS/Admin
# -> agents. Calon agen isi form publik (/daftar-agen) -> tersimpan di
# pendaftaran_agen_publik (bukan tabel agents) -> Admin/CS review di Kotak Masuk
# Keagenan -> Terima (lewat POST /api/agents yang sama seperti tambah agen manual,
# dengan source_submission_id) atau Tolak. Pola identik dengan pendaftaran jamaah.
# ===========================================================================
@router.post("/api/public/agent-registration")
async def public_agent_registration_create(request: Request, body: dict = Depends(json_body)):
    g = body.get

    # Honeypot: sama seperti pendaftaran jamaah -> pura-pura sukses ke bot.
    if g("website"):
        return {"message": "Data Anda sudah kami terima dan akan diverifikasi tim kami."}

    ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(ip, _public_agent_submit_log):
        raise HTTPException(
            status_code=429, detail="Terlalu banyak percobaan dari perangkat ini. Silakan coba lagi nanti."
        )

    name = (g("name") or "").strip()
    phone = (g("phone") or "").strip()
    if not name or not phone:
        raise HTTPException(status_code=400, detail="Nama dan No. WhatsApp wajib diisi.")

    preferred_cs_id = g("preferred_cs_id")
    db.execute(
        "INSERT INTO pendaftaran_agen_publik (name, phone, email, instagram, address, "
        "province, city, subdistrict, village, source_ip, preferred_cs_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            name, phone, g("email"), g("instagram"), g("address"),
            g("province"), g("city"), g("subdistrict"), g("village"), ip,
            int(preferred_cs_id) if preferred_cs_id else None,
        ),
    )
    notify("data_updated", "pendaftaran_agen_publik")
    return {
        "message": "Terima kasih! Pendaftaran Anda sebagai mitra/agen sudah kami terima dan akan "
        "diverifikasi tim kami. Kami akan menghubungi Anda melalui WhatsApp dalam 1x24 jam."
    }


@router.get("/api/pendaftaran-agen")
async def pendaftaran_agen_list(user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")
    return db.query_all(
        "SELECT p.*, u.name as preferred_cs_name FROM pendaftaran_agen_publik p "
        "LEFT JOIN users u ON p.preferred_cs_id = u.id ORDER BY p.created_at DESC", ()
    )


@router.put("/api/pendaftaran-agen/{pid}/reject")
async def pendaftaran_agen_reject(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Alasan penolakan wajib diisi.")

    row = db.query_one("SELECT * FROM pendaftaran_agen_publik WHERE id = ?", (pid,))
    if not row:
        raise HTTPException(status_code=404, detail="Data pendaftaran tidak ditemukan")
    if row["status"] != "Pending":
        raise HTTPException(status_code=400, detail=f"Data ini sudah berstatus '{row['status']}'.")

    db.execute(
        "UPDATE pendaftaran_agen_publik SET status = 'Ditolak', reviewed_by = ?, "
        "reviewed_at = CURRENT_TIMESTAMP, review_note = ? WHERE id = ?",
        (user["name"], reason, pid),
    )
    log_action(user, "REJECT_AGENT_REGISTRATION", f"Menolak pendaftaran agen dari {row['name']}: {reason}")
    notify("data_updated", "pendaftaran_agen_publik")
    return {"message": "Pendaftaran agen berhasil ditolak."}


# ===========================================================================
# SURAT IZIN KARYAWAN: semua role bisa mengajukan (hanya lihat riwayat sendiri),
# Admin/Management yang menyetujui/menolak. Terpisah dari Manajemen Karyawan (HR)
# yang datanya (termasuk gaji pokok) tetap dibatasi admin/management saja.
# ===========================================================================
LEAVE_ANNUAL_QUOTA_DAYS = 12  # jatah Cuti Tahunan per tahun -- HANYA jenis ini yang
# dibatasi kuotanya; Sakit/Izin Keperluan Pribadi/Lainnya tidak memotong kuota apapun.


def _leave_duration_days(start_date, end_date):
    """Durasi inklusif kedua ujung tanggal (mis. 01-03 Agustus = 3 hari) -- cermin
    persis dari leaveDurationDays() di frontend (public/index.html) agar konsisten."""
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    return (end - start).days + 1


@router.post("/api/leave-requests")
async def leave_request_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    g = body.get
    leave_type = (g("leave_type") or "").strip()
    start_date = (g("start_date") or "").strip()
    end_date = (g("end_date") or "").strip()
    reason = (g("reason") or "").strip()
    if not leave_type or not start_date or not end_date or not reason:
        raise HTTPException(
            status_code=400, detail="Jenis izin, tanggal mulai/selesai, dan alasan wajib diisi."
        )
    db.execute(
        "INSERT INTO leave_requests (user_id, user_name, leave_type, start_date, end_date, reason) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user["id"], user["name"], leave_type, start_date, end_date, reason),
    )
    log_action(user, "CREATE_LEAVE_REQUEST", f"Mengajukan {leave_type}: {start_date} s/d {end_date}")
    notify("data_updated", "leave_request")
    return {"message": "Pengajuan izin berhasil dikirim, menunggu persetujuan."}


@router.get("/api/leave-requests")
async def leave_requests_list(user=Depends(authenticate_token)):
    if user["role"] in ("admin", "management"):
        return db.query_all("SELECT * FROM leave_requests ORDER BY requested_at DESC", ())
    return db.query_all(
        "SELECT * FROM leave_requests WHERE user_id = ? ORDER BY requested_at DESC", (user["id"],)
    )


@router.put("/api/leave-requests/{lid}/review")
async def leave_request_review(lid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "management")
    action = body.get("action")
    row = db.query_one("SELECT * FROM leave_requests WHERE id = ?", (lid,))
    if not row:
        raise HTTPException(status_code=404, detail="Pengajuan izin tidak ditemukan")
    if row["status"] != "Pending":
        raise HTTPException(status_code=400, detail=f"Pengajuan ini sudah berstatus '{row['status']}'.")

    if action == "approve":
        # Gatekeeper kuota: hanya Cuti Tahunan yang dibatasi 12 hari/tahun, dan baru
        # dicek saat APPROVAL (bukan submission) -- karena kuota baru benar-benar
        # terpakai begitu disetujui, bukan saat masih Pending.
        if row["leave_type"] == "Cuti Tahunan":
            year = row["start_date"][:4]
            approved_rows = db.query_all(
                "SELECT start_date, end_date FROM leave_requests WHERE user_id = ? AND "
                "leave_type = 'Cuti Tahunan' AND status = 'Disetujui' AND substr(start_date, 1, 4) = ?",
                (row["user_id"], year),
            )
            used_days = sum(_leave_duration_days(r["start_date"], r["end_date"]) for r in approved_rows)
            this_duration = _leave_duration_days(row["start_date"], row["end_date"])
            if used_days + this_duration > LEAVE_ANNUAL_QUOTA_DAYS:
                remaining = max(0, LEAVE_ANNUAL_QUOTA_DAYS - used_days)
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Kuota Cuti Tahunan {year} tidak cukup. Sudah terpakai {used_days} dari "
                        f"{LEAVE_ANNUAL_QUOTA_DAYS} hari (sisa {remaining} hari), sementara pengajuan "
                        f"ini butuh {this_duration} hari."
                    ),
                )
        db.execute(
            "UPDATE leave_requests SET status = 'Disetujui', approved_by = ?, "
            "approved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user["name"], lid),
        )
        msg = "Pengajuan izin disetujui."
    elif action == "reject":
        reject_reason = (body.get("reason") or "").strip()
        if not reject_reason:
            raise HTTPException(status_code=400, detail="Alasan penolakan wajib diisi.")
        db.execute(
            "UPDATE leave_requests SET status = 'Ditolak', approved_by = ?, "
            "approved_at = CURRENT_TIMESTAMP, reject_reason = ? WHERE id = ?",
            (user["name"], reject_reason, lid),
        )
        msg = "Pengajuan izin ditolak."
    else:
        raise HTTPException(status_code=400, detail="Aksi tidak valid.")

    log_action(user, "REVIEW_LEAVE_REQUEST", f"{msg} ({row['user_name']}: {row['leave_type']})")
    notify("data_updated", "leave_request")
    return {"message": msg}
