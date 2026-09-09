"""
Router Audit Trail (Phase 10a) -- baca audit_logs untuk 1 entity.

Endpoint:
- GET /api/audit/jamaah/{jid}   riwayat perubahan 1 jamaah
- GET /api/audit/package/{pid}  riwayat perubahan 1 paket
- GET /api/audit/boq/{boq_id}   riwayat perubahan 1 BOQ

Karena audit_logs.details adalah free-text tulisan tangan (contoh:
"Update data jamaah CECEP: Status (DP Masuk -> Terdaftar); Tipe Kamar (QUAD -> -)"),
filter by nama pakai LIKE. Nama jamaah/paket cukup unique di produksi.

RBAC:
- Jamaah: admin, management, sales, finance, ops -- semua role kerja yg butuh audit
- Package/BOQ: admin, management, ops -- yg berwenang lihat operasional detail
"""
from fastapi import APIRouter

import db
from deps import Depends, HTTPException, authenticate_token, require_role

router = APIRouter(tags=["audit-trail"])


# Action yg dianggap "menyentuh 1 entity". Details field free-text -- filter by
# nama akan match. Kalau nanti audit_logs punya entity_type + entity_id, ganti
# ke exact match.
JAMAAH_ACTIONS = (
    "CREATE_JAMAAH", "UPDATE_JAMAAH", "DELETE_JAMAAH",
    "PAYMENT", "APPROVE_REFUND", "REJECT_REFUND",
    "TRANSFER_AGENT", "BOARDING_CHECKIN", "BOARDING_CHECKIN_UNDO",
    "JAMAAH_FEEDBACK", "REJECT_PUBLIC_REGISTRATION",
)
PACKAGE_ACTIONS = (
    "CREATE_PACKAGE", "UPDATE_PACKAGE", "DELETE_PACKAGE",
    "VENDOR_CREATE", "VENDOR_UPDATE", "VENDOR_DELETE",
    "CHECKLIST_UPDATE", "INCIDENT_CREATE", "INCIDENT_UPDATE",
    "DEBRIEF_UPDATE",
)
BOQ_ACTIONS = (
    "BOQ_CREATE", "BOQ_UPDATE", "BOQ_DELETE",
    "BOQ_APPROVE", "BOQ_REJECT", "BOQ_CONVERT",
    "BOQ_TEMPLATE_CREATE", "BOQ_TEMPLATE_UPDATE", "BOQ_TEMPLATE_DELETE",
)


def _fetch_audit(actions: tuple, needle: str, limit: int = 100):
    """Query audit_logs dgn action IN (...) AND details LIKE '%needle%'."""
    if not needle:
        return []
    ph_actions = ",".join(["?"] * len(actions))
    return db.query_all(
        f"SELECT id, user_name, action, details, created_at, role "
        f"FROM audit_logs "
        f"WHERE action IN ({ph_actions}) AND details LIKE ? "
        f"ORDER BY created_at DESC, id DESC LIMIT ?",
        (*actions, f"%{needle}%", limit),
    ) or []


@router.get("/api/audit/jamaah/{jid}")
async def audit_jamaah(jid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "management", "sales", "finance", "ops")
    row = db.query_one("SELECT id, name, orderer_name FROM jamaah WHERE id = ?", (jid,))
    if not row:
        raise HTTPException(status_code=404, detail="Jamaah tidak ditemukan.")
    # Cari log dgn nama jamaah (fuzzy), fallback ke orderer_name kalau ada.
    logs = _fetch_audit(JAMAAH_ACTIONS, row["name"])
    if row["orderer_name"] and row["orderer_name"] != row["name"]:
        extra = _fetch_audit(JAMAAH_ACTIONS, row["orderer_name"])
        # Merge unique by id, hindari duplikat.
        seen = {l["id"] for l in logs}
        for l in extra:
            if l["id"] not in seen:
                logs.append(l)
        logs.sort(key=lambda l: (l["created_at"] or "", l["id"] or 0), reverse=True)
    return {"entity": {"type": "jamaah", "id": jid, "name": row["name"]},
            "logs": logs, "count": len(logs)}


@router.get("/api/audit/package/{pid}")
async def audit_package(pid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "management", "ops")
    row = db.query_one("SELECT id, name FROM packages WHERE id = ?", (pid,))
    if not row:
        raise HTTPException(status_code=404, detail="Paket tidak ditemukan.")
    logs = _fetch_audit(PACKAGE_ACTIONS, row["name"])
    return {"entity": {"type": "package", "id": pid, "name": row["name"]},
            "logs": logs, "count": len(logs)}


@router.get("/api/audit/boq/{boq_id}")
async def audit_boq(boq_id: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "management", "ops")
    row = db.query_one("SELECT id, name FROM package_boq WHERE id = ?", (boq_id,))
    if not row:
        raise HTTPException(status_code=404, detail="BOQ tidak ditemukan.")
    # BOQ actions logged dgn "BOQ #<id>" pattern -- cari by id atau name.
    needle_id = f"#{boq_id}"
    needle_name = row["name"] or ""
    logs = _fetch_audit(BOQ_ACTIONS, needle_id)
    if needle_name:
        extra = _fetch_audit(BOQ_ACTIONS, needle_name)
        seen = {l["id"] for l in logs}
        for l in extra:
            if l["id"] not in seen:
                logs.append(l)
        logs.sort(key=lambda l: (l["created_at"] or "", l["id"] or 0), reverse=True)
    return {"entity": {"type": "boq", "id": boq_id, "name": row["name"]},
            "logs": logs, "count": len(logs)}
