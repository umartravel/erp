"""
Router Daily Task Reports (Phase DT-1a skeleton).

Karyawan (semua role) isi laporan harian: ringkasan free-text + todo list.
Optional link tiap task ke jamaah/paket/incident/refund. Management (via
phase DT-3) baca semua report + kasih feedback.

Konvensi akses:
- Karyawan HANYA bisa akses report miliknya (user_id = current user).
- admin + management akses semua (lihat _can_view_report helper).
- Edit hanya sementara Draft. Setelah Submitted (phase DT-1b) locked.

Scope Phase DT-1a (file ini): CRUD dasar report + task items + get detail.
Submit + feedback + notify hooks = phase DT-1b (extend endpoint yg sama).
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
)

router = APIRouter(tags=["daily-reports"])


# ===========================================================================
# HELPERS
# ===========================================================================
_MOOD_ENUM = {"productive", "neutral", "blocked", "off"}
_STATUS_ITEM = {"Pending", "InProgress", "Done", "Skipped"}
_PRIORITY = {"low", "medium", "high"}
_LINK_TYPES = {"jamaah", "package", "incident", "refund"}


def _today_str() -> str:
    return datetime.date.today().isoformat()


def _is_privileged(user: dict) -> bool:
    """Admin + management bisa lihat semua report (Phase DT-3 mgmt view)."""
    return user.get("role") in ("admin", "management")


def _get_report_or_404(rid: int):
    row = db.query_one("SELECT * FROM daily_reports WHERE id = ?", (rid,))
    if not row:
        raise HTTPException(status_code=404, detail="Laporan tidak ditemukan.")
    return row


def _get_item_or_404(iid: int):
    row = db.query_one("SELECT * FROM daily_task_items WHERE id = ?", (iid,))
    if not row:
        raise HTTPException(status_code=404, detail="Task item tidak ditemukan.")
    return row


def _assert_report_access(report, user, edit=False):
    """Karyawan hanya akses miliknya. Mgmt/admin baca semua tapi TIDAK edit.
    edit=True menolak mgmt/admin ikut ubah content (mereka boleh feedback saja)."""
    is_owner = report["user_id"] == user["id"]
    if edit:
        if not is_owner:
            raise HTTPException(
                status_code=403,
                detail="Hanya pemilik laporan yang bisa mengubah isi.")
    else:
        if not (is_owner or _is_privileged(user)):
            raise HTTPException(status_code=403, detail="Akses ditolak.")


def _assert_editable(report):
    """Laporan hanya editable saat Draft. Setelah Submitted, locked."""
    if report["status"] != "Draft":
        raise HTTPException(
            status_code=400,
            detail="Laporan sudah di-submit dan tidak bisa diubah.")


def _serialize_report(row) -> dict:
    """Dict yg aman utk JSON (translate Row -> dict)."""
    if not row:
        return None
    return dict(row)


def _load_items(report_id):
    return [dict(r) for r in db.query_all(
        "SELECT * FROM daily_task_items "
        "WHERE report_id = ? "
        "ORDER BY sort_order ASC, id ASC", (report_id,))]


def _load_feedback(report_id):
    """Phase DT-1b nanti diaktifkan (bareng endpoint feedback). Skeleton
    di sini supaya GET /{rid} sudah return field 'feedback' kosong."""
    return [dict(r) for r in db.query_all(
        "SELECT f.id, f.report_id, f.user_id, f.comment_text, "
        "f.is_from_management, f.created_at, u.name AS user_name "
        "FROM daily_report_feedback f "
        "LEFT JOIN users u ON u.id = f.user_id "
        "WHERE f.report_id = ? "
        "ORDER BY f.created_at ASC", (report_id,))]


# ===========================================================================
# ENDPOINTS - CRUD REPORT
# ===========================================================================
@router.get("/api/daily-reports/mine")
async def daily_reports_mine(days: int = 30, user=Depends(authenticate_token)):
    """History laporan saya (default 30 hari terakhir). Untuk history panel."""
    days = max(1, min(int(days or 30), 365))  # clamp 1..365
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    rows = db.query_all(
        "SELECT r.*, "
        "  (SELECT COUNT(*) FROM daily_task_items WHERE report_id = r.id) "
        "    AS item_count, "
        "  (SELECT COUNT(*) FROM daily_task_items "
        "    WHERE report_id = r.id AND status = 'Done') AS done_count "
        "FROM daily_reports r "
        "WHERE r.user_id = ? AND r.report_date >= ? "
        "ORDER BY r.report_date DESC",
        (user["id"], since),
    )
    return [dict(r) for r in rows]


@router.post("/api/daily-reports/today")
async def daily_reports_today(body: dict = Depends(json_body),
                              user=Depends(authenticate_token)):
    """Upsert laporan hari ini. Create kalau belum ada, else return existing.
    Body optional: {summary_text, mood}."""
    g = body.get if body else (lambda k, d=None: d)
    today = _today_str()
    summary = g("summary_text")
    mood = g("mood")
    if mood and mood not in _MOOD_ENUM:
        raise HTTPException(status_code=400, detail=f"mood harus salah satu dari {_MOOD_ENUM}.")

    existing = db.query_one(
        "SELECT * FROM daily_reports WHERE user_id = ? AND report_date = ?",
        (user["id"], today),
    )
    if existing:
        # Update summary/mood kalau body kirim value baru (dan masih Draft)
        if existing["status"] != "Draft":
            return _serialize_report(existing)
        updates = []
        params = []
        if summary is not None:
            updates.append("summary_text = ?")
            params.append(summary)
        if mood is not None:
            updates.append("mood = ?")
            params.append(mood)
        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(existing["id"])
            db.execute(
                f"UPDATE daily_reports SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
        return _serialize_report(db.query_one(
            "SELECT * FROM daily_reports WHERE id = ?", (existing["id"],)))

    last_id, _ = db.execute(
        "INSERT INTO daily_reports (user_id, report_date, summary_text, mood, status) "
        "VALUES (?, ?, ?, ?, 'Draft')",
        (user["id"], today, summary or "", mood),
    )
    log_action(user, "CREATE_DAILY_REPORT", f"Laporan {today}")
    notify("data_updated", "daily_report")
    return _serialize_report(db.query_one(
        "SELECT * FROM daily_reports WHERE id = ?", (last_id,)))


@router.put("/api/daily-reports/{rid}")
async def daily_reports_update(rid: int, body: dict = Depends(json_body),
                               user=Depends(authenticate_token)):
    """Update summary/mood. Only owner + only Draft."""
    report = _get_report_or_404(rid)
    _assert_report_access(report, user, edit=True)
    _assert_editable(report)

    g = body.get if body else (lambda k, d=None: d)
    summary = g("summary_text")
    mood = g("mood")
    if mood and mood not in _MOOD_ENUM:
        raise HTTPException(status_code=400, detail=f"mood harus salah satu dari {_MOOD_ENUM}.")

    updates = []
    params = []
    if summary is not None:
        updates.append("summary_text = ?")
        params.append(summary)
    if mood is not None:
        updates.append("mood = ?")
        params.append(mood)
    if not updates:
        return {"message": "Tidak ada perubahan."}

    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.append(rid)
    db.execute(
        f"UPDATE daily_reports SET {', '.join(updates)} WHERE id = ?", tuple(params))
    log_action(user, "UPDATE_DAILY_REPORT", f"Laporan #{rid}")
    notify("data_updated", "daily_report")
    return {"message": "Laporan berhasil diperbarui."}


@router.get("/api/daily-reports/{rid}")
async def daily_reports_detail(rid: int, user=Depends(authenticate_token)):
    """Detail: header + items + feedback thread. Owner or mgmt/admin."""
    report = _get_report_or_404(rid)
    _assert_report_access(report, user, edit=False)
    result = _serialize_report(report)
    result["items"] = _load_items(rid)
    result["feedback"] = _load_feedback(rid)
    return result


# ===========================================================================
# ENDPOINTS - TASK ITEMS (child)
# ===========================================================================
def _validate_link(link_type, link_id):
    """Optional link ke entity. type harus di whitelist, id harus int > 0
    kalau type diisi."""
    if not link_type and not link_id:
        return None, None
    if link_type not in _LINK_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"linked_entity_type harus salah satu {_LINK_TYPES}.")
    try:
        lid = int(link_id) if link_id else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="linked_entity_id harus integer.")
    if lid is None or lid <= 0:
        raise HTTPException(
            status_code=400,
            detail="linked_entity_id wajib diisi dan > 0 kalau type diisi.")
    return link_type, lid


@router.post("/api/daily-reports/{rid}/items")
async def daily_reports_add_item(rid: int, body: dict = Depends(json_body),
                                 user=Depends(authenticate_token)):
    """Tambah task item ke report. Owner + Draft only."""
    report = _get_report_or_404(rid)
    _assert_report_access(report, user, edit=True)
    _assert_editable(report)

    g = body.get if body else (lambda k, d=None: d)
    title = (g("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title task wajib diisi.")
    priority = g("priority") or "medium"
    if priority not in _PRIORITY:
        raise HTTPException(status_code=400, detail=f"priority harus {_PRIORITY}.")
    status = g("status") or "Pending"
    if status not in _STATUS_ITEM:
        raise HTTPException(status_code=400, detail=f"status harus {_STATUS_ITEM}.")
    link_type, link_id = _validate_link(g("linked_entity_type"), g("linked_entity_id"))

    # Auto-assign sort_order: max + 10
    max_order = db.query_one(
        "SELECT COALESCE(MAX(sort_order), 0) AS mo FROM daily_task_items WHERE report_id = ?",
        (rid,))["mo"]
    completed_at = (
        datetime.datetime.utcnow().isoformat(sep=" ", timespec="seconds")
        if status == "Done" else None
    )
    last_id, _ = db.execute(
        "INSERT INTO daily_task_items "
        "(report_id, title, description, status, priority, "
        "linked_entity_type, linked_entity_id, completed_at, sort_order) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (rid, title, g("description") or None, status, priority,
         link_type, link_id, completed_at, (max_order or 0) + 10),
    )
    db.execute(
        "UPDATE daily_reports SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (rid,))
    log_action(user, "CREATE_DAILY_TASK_ITEM", f"Task '{title}' di report #{rid}")
    notify("data_updated", "daily_report")
    return dict(_get_item_or_404(last_id))


@router.put("/api/daily-reports/items/{iid}")
async def daily_reports_update_item(iid: int, body: dict = Depends(json_body),
                                    user=Depends(authenticate_token)):
    """Update task item. Owner + parent Draft only."""
    item = _get_item_or_404(iid)
    report = _get_report_or_404(item["report_id"])
    _assert_report_access(report, user, edit=True)
    _assert_editable(report)

    g = body.get if body else (lambda k, d=None: d)
    updates = []
    params = []

    if "title" in body:
        t = (g("title") or "").strip()
        if not t:
            raise HTTPException(status_code=400, detail="Title tidak boleh kosong.")
        updates.append("title = ?")
        params.append(t)
    if "description" in body:
        updates.append("description = ?")
        params.append(g("description") or None)
    if "priority" in body:
        p = g("priority")
        if p not in _PRIORITY:
            raise HTTPException(status_code=400, detail=f"priority harus {_PRIORITY}.")
        updates.append("priority = ?")
        params.append(p)
    if "status" in body:
        s = g("status")
        if s not in _STATUS_ITEM:
            raise HTTPException(status_code=400, detail=f"status harus {_STATUS_ITEM}.")
        updates.append("status = ?")
        params.append(s)
        # Auto set/clear completed_at
        if s == "Done" and item["status"] != "Done":
            updates.append("completed_at = CURRENT_TIMESTAMP")
        elif s != "Done" and item["status"] == "Done":
            updates.append("completed_at = NULL")
    if "sort_order" in body:
        try:
            updates.append("sort_order = ?")
            params.append(int(g("sort_order") or 0))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="sort_order harus integer.")
    if "linked_entity_type" in body or "linked_entity_id" in body:
        link_type, link_id = _validate_link(
            g("linked_entity_type"), g("linked_entity_id"))
        updates.append("linked_entity_type = ?")
        updates.append("linked_entity_id = ?")
        params.append(link_type)
        params.append(link_id)

    if not updates:
        return {"message": "Tidak ada perubahan."}

    params.append(iid)
    db.execute(
        f"UPDATE daily_task_items SET {', '.join(updates)} WHERE id = ?", tuple(params))
    db.execute(
        "UPDATE daily_reports SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (report["id"],))
    log_action(user, "UPDATE_DAILY_TASK_ITEM", f"Task #{iid}")
    notify("data_updated", "daily_report")
    return dict(_get_item_or_404(iid))


@router.delete("/api/daily-reports/items/{iid}")
async def daily_reports_delete_item(iid: int, user=Depends(authenticate_token)):
    """Hapus task item. Owner + parent Draft only."""
    item = _get_item_or_404(iid)
    report = _get_report_or_404(item["report_id"])
    _assert_report_access(report, user, edit=True)
    _assert_editable(report)

    db.execute("DELETE FROM daily_task_items WHERE id = ?", (iid,))
    db.execute(
        "UPDATE daily_reports SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (report["id"],))
    log_action(user, "DELETE_DAILY_TASK_ITEM", f"Task #{iid}")
    notify("data_updated", "daily_report")
    return {"message": "Task item berhasil dihapus."}
