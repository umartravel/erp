"""
Router Operasional -- Checklist Pra-Keberangkatan per Paket.
- GET  /api/packages/{pid}/checklist  list item + progress (LEFT JOIN template)
- POST /api/packages/{pid}/checklist  upsert satu item (pending/done/na)

Item master ada di tabel checklist_templates (di-seed di db.init_db()).
Progress per (package_id, item_key) di package_checklist_progress -- upsert
via ON CONFLICT.
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

router = APIRouter(tags=["ops-checklist"])


@router.get("/api/packages/{pid}/checklist")
async def package_checklist_get(pid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "ops", "management")
    pkg = db.query_one(
        "SELECT id, name, departure_date, CAST(julianday(departure_date) - julianday('now') AS INTEGER) AS days_to_go "
        "FROM packages WHERE id = ?",
        (pid,),
    )
    if not pkg:
        raise HTTPException(status_code=404, detail="Paket tidak ditemukan.")
    items = db.query_all(
        "SELECT t.item_key, t.label, t.category, t.default_offset_days, t.sort_order, "
        "  COALESCE(p.status, 'pending') AS status, "
        "  p.completed_by, p.completed_at, p.note "
        "FROM checklist_templates t "
        "LEFT JOIN package_checklist_progress p ON p.item_key = t.item_key AND p.package_id = ? "
        "ORDER BY t.sort_order ASC",
        (pid,),
    )
    done = sum(1 for i in items if i["status"] in ("done", "na"))
    total = len(items)
    return {
        "package": pkg,
        "items": items,
        "summary": {"done": done, "total": total, "pct": round((done/total)*100) if total else 0},
    }


@router.post("/api/packages/{pid}/checklist")
async def package_checklist_upsert(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops", "management")
    item_key = body.get("item_key")
    status = body.get("status") or "pending"
    if not item_key:
        raise HTTPException(status_code=400, detail="item_key wajib diisi.")
    if status not in ("pending", "done", "na"):
        raise HTTPException(status_code=400, detail="status harus pending/done/na.")
    if not db.query_one("SELECT id FROM checklist_templates WHERE item_key = ?", (item_key,)):
        raise HTTPException(status_code=400, detail="item_key tidak dikenal.")
    if not db.query_one("SELECT id FROM packages WHERE id = ?", (pid,)):
        raise HTTPException(status_code=404, detail="Paket tidak ditemukan.")
    completed_by = user["name"] if status in ("done", "na") else None
    completed_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") if status in ("done", "na") else None
    note = body.get("note") if "note" in body else None
    db.execute(
        "INSERT INTO package_checklist_progress (package_id, item_key, status, completed_by, completed_at, note) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(package_id, item_key) DO UPDATE SET "
        "  status = excluded.status, "
        "  completed_by = excluded.completed_by, "
        "  completed_at = excluded.completed_at, "
        "  note = COALESCE(excluded.note, package_checklist_progress.note)",
        (pid, item_key, status, completed_by, completed_at, note),
    )
    log_action(user, "CHECKLIST_UPDATE", f"pkg={pid} item={item_key} status={status}")
    notify("data_updated", "package")
    return {"message": "Checklist diperbarui."}
