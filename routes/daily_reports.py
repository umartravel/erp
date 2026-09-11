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
from deps.notifications import notify_role, notify_user

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


# ===========================================================================
# ENDPOINTS - MGMT SCOPE (Phase DT-3a) -- registered BEFORE /{rid} routes
# supaya /team dan /team/summary tidak nyangkut ke {rid: int} matcher.
# ===========================================================================
_ALL_ROLES = ("admin", "sales", "finance", "ops", "management")
_DEFAULT_TEAM_ROLES = ("sales", "finance", "ops", "management")


def _parse_date_or_400(s, field_name="date"):
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} harus format YYYY-MM-DD.")


@router.get("/api/daily-reports/team")
async def daily_reports_team(date: str | None = None,
                             role: str | None = None,
                             user=Depends(authenticate_token)):
    """Mgmt bird's-eye view: semua user + status lapor untuk 1 tanggal.

    Karyawan tanpa report di tanggal itu tetap muncul dgn status='NotSubmitted'
    (biar Mgmt langsung tahu siapa yg belum). Filter `role` optional; kalau
    kosong, exclude 'admin' by default (bukan karyawan operasional).
    """
    if not _is_privileged(user):
        raise HTTPException(status_code=403, detail="Hanya management/admin.")

    target_date = date or _today_str()
    _parse_date_or_400(target_date, "date")
    if role and role not in _ALL_ROLES:
        raise HTTPException(
            status_code=400, detail=f"role harus salah satu dari {_ALL_ROLES}.")

    if role:
        where = "u.role = ?"
        params: list = [role]
    else:
        where = "u.role IN ({})".format(",".join(["?"] * len(_DEFAULT_TEAM_ROLES)))
        params = list(_DEFAULT_TEAM_ROLES)

    rows = db.query_all(
        "SELECT u.id AS user_id, u.name AS user_name, u.username, u.role, "
        "  r.id AS report_id, r.status AS report_status, r.mood, "
        "  r.summary_text, r.submitted_at, r.updated_at, "
        "  (SELECT COUNT(*) FROM daily_task_items WHERE report_id = r.id) "
        "    AS item_count, "
        "  (SELECT COUNT(*) FROM daily_task_items "
        "    WHERE report_id = r.id AND status = 'Done') AS done_count "
        "FROM users u "
        "LEFT JOIN daily_reports r "
        "  ON r.user_id = u.id AND r.report_date = ? "
        f"WHERE {where} "
        "ORDER BY u.role, u.name",
        (target_date, *params),
    )

    result = []
    for r in rows:
        d = dict(r)
        if d.get("report_id") is None:
            d["status"] = "NotSubmitted"
        else:
            d["status"] = d.get("report_status") or "Draft"
        d.pop("report_status", None)
        d["item_count"] = d.get("item_count") or 0
        d["done_count"] = d.get("done_count") or 0
        result.append(d)

    total_users = len(result)
    submitted = sum(1 for x in result if x["status"] == "Submitted")
    drafts = sum(1 for x in result if x["status"] == "Draft")
    not_submitted = sum(1 for x in result if x["status"] == "NotSubmitted")
    return {
        "date": target_date,
        "role_filter": role,
        "totals": {
            "users": total_users,
            "submitted": submitted,
            "drafts": drafts,
            "not_submitted": not_submitted,
            "submit_rate_pct": (
                round((submitted / total_users) * 100, 1) if total_users else 0),
        },
        "users": result,
    }


def _already_notified_user_today(uid: int, kind: str) -> bool:
    """True kalau (uid, kind) sudah punya row hari ini. Cocok utk dedup
    per-user (beda dari _already_notified_today di reminders.py yang global)."""
    row = db.query_one(
        "SELECT 1 x FROM user_notifications "
        "WHERE user_id = ? AND kind = ? AND date(created_at) = date('now') LIMIT 1",
        (uid, kind),
    )
    return bool(row)


@router.post("/api/daily-reports/reminders/check")
async def daily_reminders_check(user=Depends(authenticate_token)):
    """Sweep karyawan yg belum submit report hari ini, kirim reminder halus
    (kind per-user per-hari: 'daily_report_reminder_YYYYMMDD'). Aman dipanggil
    berkali-kali -- dedupe garansi 1 notif per user per hari.

    Pattern reuse: routes/reminders.py:102-110 (kind encode date). Bedanya di
    sini per-user dedup, bukan global -- karena notify_user() bikin 1 row per
    user, dan setiap user butuh cek sendiri-sendiri.

    Guard: admin + management (sama seperti /team endpoint di DT-3a).
    Dipanggil eksternal cron scheduler jam 16:30 lokal, atau manual dari UI."""
    if not _is_privileged(user):
        raise HTTPException(status_code=403, detail="Hanya management/admin.")

    today_str = _today_str()
    kind = f"daily_report_reminder_{today_str.replace('-', '')}"

    # Karyawan operasional yg belum ada Submitted report hari ini. Draft
    # tetap masuk daftar reminder -- artinya sudah mulai tapi belum kirim.
    overdue = db.query_all(
        "SELECT u.id, u.name, u.role FROM users u "
        "WHERE u.role IN ('sales', 'ops', 'finance', 'management') "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM daily_reports r "
        "  WHERE r.user_id = u.id "
        "  AND r.report_date = ? AND r.status = 'Submitted')",
        (today_str,),
    )

    sent = 0
    skipped = 0
    for u in overdue:
        uid = u["id"]
        if _already_notified_user_today(uid, kind):
            skipped += 1
            continue
        notify_user(
            uid, kind,
            "Reminder: Laporan Harian",
            body=(f"Kamu belum submit laporan hari ini ({today_str}). "
                  "Buka menu Laporan Harian utk isi sebelum pulang."),
            link="#page-daily-mine",
        )
        sent += 1

    return {"date": today_str, "kind": kind, "candidates": len(overdue),
            "sent": sent, "skipped": skipped}


@router.get("/api/daily-reports/team/summary")
async def daily_reports_team_summary(date_from: str | None = None,
                                     date_to: str | None = None,
                                     role: str | None = None,
                                     user=Depends(authenticate_token)):
    """Agregat range per user: hari submit / total hari + total task + % done.

    Default range: 30 hari terakhir (date_to = hari ini). Filter role optional.
    """
    if not _is_privileged(user):
        raise HTTPException(status_code=403, detail="Hanya management/admin.")

    if not date_to:
        date_to = _today_str()
    d_to = _parse_date_or_400(date_to, "date_to")
    if not date_from:
        date_from = (d_to - datetime.timedelta(days=29)).isoformat()
    d_from = _parse_date_or_400(date_from, "date_from")
    if d_from > d_to:
        raise HTTPException(status_code=400, detail="date_from harus <= date_to.")
    if role and role not in _ALL_ROLES:
        raise HTTPException(
            status_code=400, detail=f"role harus salah satu dari {_ALL_ROLES}.")
    if (d_to - d_from).days > 365:
        raise HTTPException(status_code=400, detail="Rentang maksimal 365 hari.")

    if role:
        where = "u.role = ?"
        params: list = [role]
    else:
        where = "u.role IN ({})".format(",".join(["?"] * len(_DEFAULT_TEAM_ROLES)))
        params = list(_DEFAULT_TEAM_ROLES)

    total_days = (d_to - d_from).days + 1

    rows = db.query_all(
        "SELECT u.id AS user_id, u.name AS user_name, u.username, u.role, "
        "  COUNT(DISTINCT CASE WHEN r.status = 'Submitted' "
        "    THEN r.report_date END) AS submitted_days, "
        "  COUNT(DISTINCT CASE WHEN r.status = 'Draft' "
        "    THEN r.report_date END) AS draft_days, "
        "  COALESCE(SUM(("
        "    SELECT COUNT(*) FROM daily_task_items t "
        "    WHERE t.report_id = r.id)), 0) AS total_tasks, "
        "  COALESCE(SUM(("
        "    SELECT COUNT(*) FROM daily_task_items t "
        "    WHERE t.report_id = r.id AND t.status = 'Done')), 0) AS done_tasks "
        "FROM users u "
        "LEFT JOIN daily_reports r "
        "  ON r.user_id = u.id AND r.report_date BETWEEN ? AND ? "
        f"WHERE {where} "
        "GROUP BY u.id "
        "ORDER BY u.role, u.name",
        (date_from, date_to, *params),
    )

    result = []
    for r in rows:
        d = dict(r)
        d["total_days"] = total_days
        d["submit_rate_pct"] = (
            round((d["submitted_days"] / total_days) * 100, 1)
            if total_days else 0)
        d["done_pct"] = (
            round((d["done_tasks"] / d["total_tasks"]) * 100, 1)
            if d["total_tasks"] else 0)
        result.append(d)

    return {
        "date_from": date_from,
        "date_to": date_to,
        "total_days": total_days,
        "role_filter": role,
        "users": result,
    }


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


# ===========================================================================
# ENDPOINTS - SUBMIT + FEEDBACK (Phase DT-1b)
# ===========================================================================
@router.post("/api/daily-reports/{rid}/submit")
async def daily_reports_submit(rid: int, user=Depends(authenticate_token)):
    """Lock laporan (Draft -> Submitted). Owner-only. Butuh minimal 1 item
    ATAU summary_text ke-isi supaya submit tidak "kosong"."""
    report = _get_report_or_404(rid)
    _assert_report_access(report, user, edit=True)

    if report["status"] != "Draft":
        raise HTTPException(
            status_code=400,
            detail="Laporan sudah di-submit sebelumnya.")

    summary = (report["summary_text"] or "").strip()
    item_count = db.query_one(
        "SELECT COUNT(*) AS c FROM daily_task_items WHERE report_id = ?", (rid,))["c"]
    if not summary and item_count == 0:
        raise HTTPException(
            status_code=400,
            detail="Isi minimal ringkasan atau 1 task sebelum submit.")

    db.execute(
        "UPDATE daily_reports SET status = 'Submitted', "
        "submitted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
        "WHERE id = ?", (rid,))
    log_action(user, "SUBMIT_DAILY_REPORT", f"Laporan #{rid} ({report['report_date']})")
    notify("data_updated", "daily_report")

    # Notif ke management + admin -- ada laporan baru siap direview.
    label = user.get("name") or user.get("username") or "Seseorang"
    title = f"Laporan harian dari {label} ({user.get('role', '-')})"
    body_note = f"Tanggal {report['report_date']}. Buka Laporan Tim untuk review."
    link = f"#page-daily-team?date={report['report_date']}"
    notify_role("management", "daily_report_submitted", title, body=body_note, link=link)
    notify_role("admin", "daily_report_submitted", title, body=body_note, link=link)

    return {"message": "Laporan berhasil di-submit.",
            "id": rid, "status": "Submitted"}


@router.post("/api/daily-reports/{rid}/feedback")
async def daily_reports_feedback(rid: int, body: dict = Depends(json_body),
                                 user=Depends(authenticate_token)):
    """Post comment ke thread feedback. 2 arah:
    - Mgmt/admin komen -> notify owner.
    - Owner (karyawan) reply -> notify management + admin.
    Feedback boleh di Draft maupun Submitted (biar mgmt bisa nudge Draft).
    """
    report = _get_report_or_404(rid)

    is_owner = report["user_id"] == user["id"]
    is_mgmt = _is_privileged(user)
    if not (is_owner or is_mgmt):
        raise HTTPException(status_code=403, detail="Akses ditolak.")

    g = body.get if body else (lambda k, d=None: d)
    text = (g("comment_text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Isi komentar tidak boleh kosong.")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="Komentar maksimal 2000 karakter.")

    is_from_mgmt = 1 if is_mgmt and not is_owner else 0
    # Edge case: user yg BOTH owner AND mgmt (admin yg lapor sendiri).
    # Prefer "owner" perspective supaya tidak kirim notif ke diri sendiri.
    if is_owner and is_mgmt:
        is_from_mgmt = 0

    last_id, _ = db.execute(
        "INSERT INTO daily_report_feedback "
        "(report_id, user_id, comment_text, is_from_management) "
        "VALUES (?, ?, ?, ?)",
        (rid, user["id"], text, is_from_mgmt))
    log_action(user, "FEEDBACK_DAILY_REPORT", f"Report #{rid}")
    notify("data_updated", "daily_report")

    author = user.get("name") or user.get("username") or "Seseorang"
    link = (f"#page-daily-team?rid={rid}"
            if is_from_mgmt else f"#page-daily-mine?rid={rid}")
    snippet = (text[:80] + "...") if len(text) > 80 else text

    if is_from_mgmt:
        # Mgmt komen -> notify owner karyawan
        notify_user(
            report["user_id"], "daily_report_feedback",
            f"Feedback dari {author} pada laporan {report['report_date']}",
            body=snippet, link=f"#page-daily-mine?rid={rid}")
    else:
        # Owner reply -> notify mgmt + admin (broadcast)
        notify_role(
            "management", "daily_report_feedback",
            f"Balasan {author} di laporan {report['report_date']}",
            body=snippet, link=link)
        notify_role(
            "admin", "daily_report_feedback",
            f"Balasan {author} di laporan {report['report_date']}",
            body=snippet, link=link)

    # Return the new comment row (utk render langsung di UI thread)
    return dict(db.query_one(
        "SELECT f.id, f.report_id, f.user_id, f.comment_text, "
        "f.is_from_management, f.created_at, u.name AS user_name "
        "FROM daily_report_feedback f "
        "LEFT JOIN users u ON u.id = f.user_id "
        "WHERE f.id = ?", (last_id,)))
