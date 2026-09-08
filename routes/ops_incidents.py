"""
Router Operasional -- Incidents (laporan insiden lapangan).
- GET   /api/incidents         list dgn filter status/severity/package
- POST  /api/incidents         semua role bisa lapor
- PATCH /api/incidents/{iid}   update status/severity/assignee (admin/ops/mgmt)

Status Open -> InProgress -> Resolved. resolved_at auto-stamp saat Resolved.
"""
import datetime

from fastapi import APIRouter

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
from deps.notifications import notify_role, notify_user  # Phase 8f-2

router = APIRouter(tags=["ops-incidents"])


def _uid_by_name(name: str | None) -> int | None:
    if not name:
        return None
    row = db.query_one("SELECT id FROM users WHERE name = ?", (name,))
    return row["id"] if row else None


def _notify_severity_bump(iid: int, package: str | None, severity: str, text: str | None):
    """Kirim notif ke management + ops kalau severity Critical/High.
    Fire-and-forget."""
    if severity not in ("Critical", "High"):
        return
    body = (text or "")[:200]
    link = "#page-incidents"
    title = f"Insiden {severity}: {package or 'Umum'}"
    notify_role("management", "incident_critical", title, body, link)
    notify_role("ops", "incident_critical", title, body, link)


@router.get("/api/incidents")
async def incidents_list(
    status: str | None = None,
    severity: str | None = None,
    package: str | None = None,
    user=Depends(authenticate_token),
):
    where = []
    params = []
    if status:
        where.append("i.status = ?"); params.append(status)
    if severity:
        where.append("i.severity = ?"); params.append(severity)
    if package:
        where.append("i.package_name = ?"); params.append(package)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    return db.query_all(
        f"SELECT i.*, j.name AS jamaah_name FROM incidents i "
        f"LEFT JOIN jamaah j ON j.id = i.jamaah_id "
        f"{where_sql} ORDER BY "
        "  CASE i.status WHEN 'Open' THEN 0 WHEN 'InProgress' THEN 1 ELSE 2 END, "
        "  CASE i.severity WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END, "
        "  i.created_at DESC",
        tuple(params),
    ) or []


@router.post("/api/incidents")
async def incidents_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    db.execute(
        "INSERT INTO incidents (package_name, reported_by, incident_text, severity, jamaah_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            body.get("package_name") or "Umum",
            user["name"],
            body.get("incident_text"),
            body.get("severity") or "Medium",
            body.get("jamaah_id") or None,
        ),
    )
    log_action(user, "INCIDENT_CREATE", f"pkg={body.get('package_name')} sev={body.get('severity')}")
    notify("data_updated", "incident")
    # Phase 8f-2: bump notif utk incident Critical/High
    new_id = db.query_one("SELECT last_insert_rowid() AS lid")["lid"]
    _notify_severity_bump(new_id, body.get("package_name"),
                          body.get("severity") or "Medium",
                          body.get("incident_text"))
    return {"message": "Laporan insiden berhasil dikirim ke Pusat."}


@router.patch("/api/incidents/{iid}")
async def incidents_update(iid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops", "management")
    row = db.query_one("SELECT * FROM incidents WHERE id = ?", (iid,))
    if not row:
        raise HTTPException(status_code=404, detail="Insiden tidak ditemukan.")
    status = body.get("status") or row["status"]
    severity = body.get("severity") or row["severity"] or "Medium"
    assigned_to = body.get("assigned_to") if "assigned_to" in body else row["assigned_to"]
    resolution_note = body.get("resolution_note") if "resolution_note" in body else row["resolution_note"]
    resolved_at = row["resolved_at"]
    if status == "Resolved" and not resolved_at:
        resolved_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    elif status != "Resolved":
        resolved_at = None
    db.execute(
        "UPDATE incidents SET status=?, severity=?, assigned_to=?, resolution_note=?, resolved_at=? WHERE id=?",
        (status, severity, assigned_to, resolution_note, resolved_at, iid),
    )
    log_action(user, "INCIDENT_UPDATE", f"id={iid} status={status} sev={severity}")
    notify("data_updated", "incident")
    # Phase 8f-2: (a) severity bump ke Critical/High (bila naik dari lebih rendah),
    # (b) assignee baru -> notif user yg di-assign.
    prev_sev = row["severity"] or "Medium"
    rank = {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}
    if severity in ("Critical", "High") and rank.get(severity, 1) > rank.get(prev_sev, 1):
        _notify_severity_bump(iid, row["package_name"], severity, row["incident_text"])
    if assigned_to and assigned_to != (row["assigned_to"] or ""):
        notify_user(
            _uid_by_name(assigned_to), "incident_assigned",
            f"Anda di-assign insiden: {row['package_name'] or 'Umum'}",
            f"Severity {severity}. {(row['incident_text'] or '')[:150]}",
            "#page-incidents",
        )
    return {"message": "Insiden diperbarui."}
