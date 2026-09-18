"""
Phase DT-1a: Daily Task Reports skeleton -- CRUD + auth guard tests.

Cover:
- POST /today idempotent (create pertama kali, update summary/mood setelah)
- GET /mine return history user (bukan user lain)
- POST/PUT/DELETE items dengan validation (enum, link_type, priority)
- Access control: sales1 tidak bisa akses report sales2/finance/mgmt
- admin + management punya read-access ke semua report (bird's-eye)
- Draft-only edit rule (skeleton -- endpoint submit di DT-1b)
"""
import datetime

import db

from tests.conftest import bearer


def _wipe_today_report(user_id):
    """Purge today report + items + feedback utk user tsb -- gunanya biar test
    yg butuh 'meja bersih' bebas dari state test lain (client scope=session)."""
    today = datetime.date.today().isoformat()
    rows = db.query_all(
        "SELECT id FROM daily_reports WHERE user_id = ? AND report_date = ?",
        (user_id, today))
    for r in rows:
        db.execute("DELETE FROM daily_task_items WHERE report_id = ?", (r["id"],))
        db.execute("DELETE FROM daily_report_feedback WHERE report_id = ?", (r["id"],))
        db.execute("DELETE FROM daily_reports WHERE id = ?", (r["id"],))


def _user_id_by_username(username):
    row = db.query_one("SELECT id FROM users WHERE username = ?", (username,))
    return row["id"] if row else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _today():
    return datetime.date.today().isoformat()


def _create_today(client, token, summary=None, mood=None):
    body = {}
    if summary is not None:
        body["summary_text"] = summary
    if mood is not None:
        body["mood"] = mood
    r = client.post("/api/daily-reports/today", json=body, headers=bearer(token))
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# POST /today upsert
# ---------------------------------------------------------------------------
def test_today_creates_draft_report(client, sales_token):
    r = _create_today(client, sales_token, summary="Halo hari ini", mood="productive")
    assert r["status"] == "Draft"
    assert r["report_date"] == _today()
    assert r["summary_text"] == "Halo hari ini"
    assert r["mood"] == "productive"
    assert r["submitted_at"] is None


def test_today_idempotent_same_user_same_day(client, ops_token):
    """Panggil 2x utk user yg sama, harus balik row yg sama (bukan bikin duplikat)."""
    a = _create_today(client, ops_token, summary="draft 1")
    b = _create_today(client, ops_token, summary="draft 2 timpa")
    assert a["id"] == b["id"]
    assert b["summary_text"] == "draft 2 timpa"


def test_today_rejects_invalid_mood(client, finance_token):
    r = client.post("/api/daily-reports/today",
                    json={"mood": "on-fire"},
                    headers=bearer(finance_token))
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# GET /mine
# ---------------------------------------------------------------------------
def test_mine_returns_only_my_reports(client, sales_token, finance_token):
    _create_today(client, sales_token, summary="sales laporan")
    _create_today(client, finance_token, summary="finance laporan")

    r_sales = client.get("/api/daily-reports/mine",
                         headers=bearer(sales_token)).json()
    r_finance = client.get("/api/daily-reports/mine",
                           headers=bearer(finance_token)).json()

    sales_summaries = [x["summary_text"] for x in r_sales]
    finance_summaries = [x["summary_text"] for x in r_finance]
    assert "sales laporan" in sales_summaries
    assert "sales laporan" not in finance_summaries
    assert "finance laporan" in finance_summaries


def test_mine_includes_item_counts(client, sales_token):
    """Panel history karyawan butuh item_count + done_count utk render badge."""
    rep = _create_today(client, sales_token)
    client.post(f"/api/daily-reports/{rep['id']}/items",
                json={"title": "T1"}, headers=bearer(sales_token))
    client.post(f"/api/daily-reports/{rep['id']}/items",
                json={"title": "T2", "status": "Done"},
                headers=bearer(sales_token))
    rows = client.get("/api/daily-reports/mine",
                      headers=bearer(sales_token)).json()
    today_row = next(r for r in rows if r["id"] == rep["id"])
    assert today_row["item_count"] == 2
    assert today_row["done_count"] == 1


# ---------------------------------------------------------------------------
# PUT /{rid} update
# ---------------------------------------------------------------------------
def test_update_report_summary(client, sales_token):
    rep = _create_today(client, sales_token, summary="awal")
    r = client.put(f"/api/daily-reports/{rep['id']}",
                   json={"summary_text": "diperbaiki", "mood": "neutral"},
                   headers=bearer(sales_token))
    assert r.status_code == 200
    detail = client.get(f"/api/daily-reports/{rep['id']}",
                        headers=bearer(sales_token)).json()
    assert detail["summary_text"] == "diperbaiki"
    assert detail["mood"] == "neutral"


# ---------------------------------------------------------------------------
# POST /{rid}/items
# ---------------------------------------------------------------------------
def test_add_task_item_default(client, sales_token):
    rep = _create_today(client, sales_token)
    r = client.post(f"/api/daily-reports/{rep['id']}/items",
                    json={"title": "Follow-up Pak Budi"},
                    headers=bearer(sales_token))
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["title"] == "Follow-up Pak Budi"
    assert item["status"] == "Pending"
    assert item["priority"] == "medium"
    assert item["sort_order"] > 0
    assert item["linked_entity_type"] is None


def test_add_task_item_with_valid_link(client, sales_token):
    rep = _create_today(client, sales_token)
    r = client.post(f"/api/daily-reports/{rep['id']}/items",
                    json={"title": "Follow-up jamaah",
                          "linked_entity_type": "jamaah",
                          "linked_entity_id": 1,
                          "priority": "high"},
                    headers=bearer(sales_token))
    assert r.status_code == 200
    item = r.json()
    assert item["linked_entity_type"] == "jamaah"
    assert item["linked_entity_id"] == 1
    assert item["priority"] == "high"


def test_add_task_item_rejects_invalid_link_type(client, sales_token):
    rep = _create_today(client, sales_token)
    r = client.post(f"/api/daily-reports/{rep['id']}/items",
                    json={"title": "X", "linked_entity_type": "hacker",
                          "linked_entity_id": 99},
                    headers=bearer(sales_token))
    assert r.status_code == 400


def test_add_task_item_rejects_empty_title(client, sales_token):
    rep = _create_today(client, sales_token)
    r = client.post(f"/api/daily-reports/{rep['id']}/items",
                    json={"title": "   "},
                    headers=bearer(sales_token))
    assert r.status_code == 400


def test_add_task_item_rejects_invalid_priority(client, sales_token):
    rep = _create_today(client, sales_token)
    r = client.post(f"/api/daily-reports/{rep['id']}/items",
                    json={"title": "X", "priority": "urgent"},
                    headers=bearer(sales_token))
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# PUT /items/{iid}
# ---------------------------------------------------------------------------
def test_update_task_item_status_to_done_sets_completed_at(client, sales_token):
    rep = _create_today(client, sales_token)
    item = client.post(f"/api/daily-reports/{rep['id']}/items",
                       json={"title": "X"},
                       headers=bearer(sales_token)).json()
    assert item["completed_at"] is None

    r = client.put(f"/api/daily-reports/items/{item['id']}",
                   json={"status": "Done"},
                   headers=bearer(sales_token))
    assert r.status_code == 200
    updated = r.json()
    assert updated["status"] == "Done"
    assert updated["completed_at"] is not None


def test_update_task_item_from_done_to_pending_clears_completed_at(client, sales_token):
    rep = _create_today(client, sales_token)
    item = client.post(f"/api/daily-reports/{rep['id']}/items",
                       json={"title": "X", "status": "Done"},
                       headers=bearer(sales_token)).json()
    assert item["completed_at"] is not None

    r = client.put(f"/api/daily-reports/items/{item['id']}",
                   json={"status": "Pending"},
                   headers=bearer(sales_token))
    updated = r.json()
    assert updated["status"] == "Pending"
    assert updated["completed_at"] is None


# ---------------------------------------------------------------------------
# DELETE /items/{iid}
# ---------------------------------------------------------------------------
def test_delete_task_item(client, sales_token):
    rep = _create_today(client, sales_token)
    item = client.post(f"/api/daily-reports/{rep['id']}/items",
                       json={"title": "hapus aja"},
                       headers=bearer(sales_token)).json()

    r = client.delete(f"/api/daily-reports/items/{item['id']}",
                      headers=bearer(sales_token))
    assert r.status_code == 200

    detail = client.get(f"/api/daily-reports/{rep['id']}",
                        headers=bearer(sales_token)).json()
    assert not any(i["id"] == item["id"] for i in detail["items"])


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------
def test_sales_cannot_edit_finance_report(client, sales_token, finance_token):
    """sales1 mencoba edit report finance1 -> 403."""
    finance_rep = _create_today(client, finance_token, summary="finance private")
    r = client.put(f"/api/daily-reports/{finance_rep['id']}",
                   json={"summary_text": "hacked"},
                   headers=bearer(sales_token))
    assert r.status_code == 403


def test_sales_cannot_read_finance_report_detail(client, sales_token, finance_token):
    finance_rep = _create_today(client, finance_token, summary="finance private")
    r = client.get(f"/api/daily-reports/{finance_rep['id']}",
                   headers=bearer(sales_token))
    assert r.status_code == 403


def test_management_can_read_any_report(client, sales_token, management_token):
    """Bird's-eye view -- mgmt boleh baca semua utk phase DT-3."""
    sales_rep = _create_today(client, sales_token, summary="sales info")
    r = client.get(f"/api/daily-reports/{sales_rep['id']}",
                   headers=bearer(management_token))
    assert r.status_code == 200
    assert r.json()["summary_text"] == "sales info"


def test_management_cannot_edit_someone_elses_report(client, sales_token,
                                                     management_token):
    """Mgmt bisa BACA tapi tidak edit isi. Feedback endpoint = phase DT-1b."""
    sales_rep = _create_today(client, sales_token)
    r = client.put(f"/api/daily-reports/{sales_rep['id']}",
                   json={"summary_text": "mgmt intruder"},
                   headers=bearer(management_token))
    assert r.status_code == 403


def test_admin_can_read_any_report(client, sales_token, admin_token):
    sales_rep = _create_today(client, sales_token, summary="admin view")
    r = client.get(f"/api/daily-reports/{sales_rep['id']}",
                   headers=bearer(admin_token))
    assert r.status_code == 200


def test_unauth_denied(client):
    r = client.get("/api/daily-reports/mine")
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Phase DT-1b: Submit endpoint
# ---------------------------------------------------------------------------
def _has_notif_with_kind(client, token, kind_prefix):
    """Helper: cek user login punya minimal 1 notif dgn kind starting kind_prefix."""
    r = client.get("/api/notifications", headers=bearer(token))
    if r.status_code != 200:
        return False
    return any(str(n.get("kind", "")).startswith(kind_prefix) for n in r.json())


def test_submit_empty_report_rejected(client, ops_token):
    """Report tanpa summary & tanpa item -> submit ditolak.
    Wipe state ops1 dulu -- session-scoped fixture, prior tests bisa isi."""
    _wipe_today_report(_user_id_by_username("ops1"))
    rep = _create_today(client, ops_token)  # no summary
    # Kadang _create_today balik row lama (idempotent); tapi wipe di atas
    # ensured tidak ada row -> ini baru create empty. Force clear summary:
    db.execute(
        "UPDATE daily_reports SET summary_text = '' WHERE id = ?", (rep["id"],))
    r = client.post(f"/api/daily-reports/{rep['id']}/submit",
                    headers=bearer(ops_token))
    assert r.status_code == 400


def test_submit_with_summary_only_ok(client, sales_token):
    rep = _create_today(client, sales_token, summary="Sudah follow-up 3 jamaah.")
    r = client.post(f"/api/daily-reports/{rep['id']}/submit",
                    headers=bearer(sales_token))
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "Submitted"

    detail = client.get(f"/api/daily-reports/{rep['id']}",
                        headers=bearer(sales_token)).json()
    assert detail["status"] == "Submitted"
    assert detail["submitted_at"] is not None


def test_submit_with_items_only_ok(client, finance_token):
    """Boleh submit tanpa summary asalkan ada task item."""
    rep = _create_today(client, finance_token)
    client.post(f"/api/daily-reports/{rep['id']}/items",
                json={"title": "Reconcile BCA"},
                headers=bearer(finance_token))
    r = client.post(f"/api/daily-reports/{rep['id']}/submit",
                    headers=bearer(finance_token))
    assert r.status_code == 200


def test_submit_twice_rejected(client, sales_token):
    rep = _create_today(client, sales_token, summary="draft")
    client.post(f"/api/daily-reports/{rep['id']}/submit",
                headers=bearer(sales_token))
    r = client.post(f"/api/daily-reports/{rep['id']}/submit",
                    headers=bearer(sales_token))
    assert r.status_code == 400


def test_submit_locks_edit(client, sales_token):
    """After submit, PUT summary + POST items harus 400 (report tidak editable)."""
    rep = _create_today(client, sales_token, summary="draft")
    client.post(f"/api/daily-reports/{rep['id']}/submit",
                headers=bearer(sales_token))

    r = client.put(f"/api/daily-reports/{rep['id']}",
                   json={"summary_text": "coba edit"},
                   headers=bearer(sales_token))
    assert r.status_code == 400

    r2 = client.post(f"/api/daily-reports/{rep['id']}/items",
                     json={"title": "coba tambah"},
                     headers=bearer(sales_token))
    assert r2.status_code == 400


def test_non_owner_cannot_submit(client, sales_token, finance_token):
    sales_rep = _create_today(client, sales_token, summary="draft")
    r = client.post(f"/api/daily-reports/{sales_rep['id']}/submit",
                    headers=bearer(finance_token))
    assert r.status_code == 403


def test_submit_notifies_management(client, sales_token, management_token):
    """notify_role('management', 'daily_report_submitted', ...) harus jalan.
    Wipe dulu supaya submit BARU jalan (idempotent create bisa return row
    yg sudah Submitted dari test lain -> submit-nya tolak jadi tidak ada notif)."""
    _wipe_today_report(_user_id_by_username("sales1"))
    rep = _create_today(client, sales_token, summary="notif test")
    r = client.post(f"/api/daily-reports/{rep['id']}/submit",
                    headers=bearer(sales_token))
    assert r.status_code == 200

    assert _has_notif_with_kind(client, management_token, "daily_report_submitted"), \
        "Management harus dapat notif daily_report_submitted setelah sales submit."


# ---------------------------------------------------------------------------
# Phase DT-1b: Feedback endpoint (2-way thread)
# ---------------------------------------------------------------------------
def test_management_can_comment_on_submitted_report(client, sales_token,
                                                    management_token):
    rep = _create_today(client, sales_token, summary="isi")
    client.post(f"/api/daily-reports/{rep['id']}/submit",
                headers=bearer(sales_token))

    r = client.post(f"/api/daily-reports/{rep['id']}/feedback",
                    json={"comment_text": "Bagus, lanjutkan!"},
                    headers=bearer(management_token))
    assert r.status_code == 200
    comment = r.json()
    assert comment["comment_text"] == "Bagus, lanjutkan!"
    assert comment["is_from_management"] == 1


def test_owner_can_reply_after_mgmt_comment(client, sales_token, management_token):
    rep = _create_today(client, sales_token, summary="isi")
    client.post(f"/api/daily-reports/{rep['id']}/submit",
                headers=bearer(sales_token))
    client.post(f"/api/daily-reports/{rep['id']}/feedback",
                json={"comment_text": "gimana progress?"},
                headers=bearer(management_token))

    r = client.post(f"/api/daily-reports/{rep['id']}/feedback",
                    json={"comment_text": "sudah on-track pak"},
                    headers=bearer(sales_token))
    assert r.status_code == 200
    comment = r.json()
    assert comment["is_from_management"] == 0
    assert comment["comment_text"] == "sudah on-track pak"


def test_feedback_thread_ordered_ascending(client, sales_token, management_token):
    """Wipe state utk cek urutan dari nol -- test lain bisa sudah kasih
    feedback ke report sales1 hari ini."""
    _wipe_today_report(_user_id_by_username("sales1"))
    rep = _create_today(client, sales_token, summary="isi")
    client.post(f"/api/daily-reports/{rep['id']}/submit",
                headers=bearer(sales_token))
    client.post(f"/api/daily-reports/{rep['id']}/feedback",
                json={"comment_text": "MGMT pertama"},
                headers=bearer(management_token))
    client.post(f"/api/daily-reports/{rep['id']}/feedback",
                json={"comment_text": "SALES kedua"},
                headers=bearer(sales_token))
    client.post(f"/api/daily-reports/{rep['id']}/feedback",
                json={"comment_text": "MGMT ketiga"},
                headers=bearer(management_token))

    detail = client.get(f"/api/daily-reports/{rep['id']}",
                        headers=bearer(sales_token)).json()
    texts = [f["comment_text"] for f in detail["feedback"]]
    assert texts == ["MGMT pertama", "SALES kedua", "MGMT ketiga"]


def test_feedback_rejects_empty(client, sales_token, management_token):
    rep = _create_today(client, sales_token, summary="isi")
    client.post(f"/api/daily-reports/{rep['id']}/submit",
                headers=bearer(sales_token))
    r = client.post(f"/api/daily-reports/{rep['id']}/feedback",
                    json={"comment_text": "   "},
                    headers=bearer(management_token))
    assert r.status_code == 400


def test_random_role_cannot_comment(client, sales_token, finance_token,
                                    management_token):
    """finance1 bukan mgmt/admin dan bukan owner -> 403."""
    rep = _create_today(client, sales_token, summary="isi")
    client.post(f"/api/daily-reports/{rep['id']}/submit",
                headers=bearer(sales_token))
    r = client.post(f"/api/daily-reports/{rep['id']}/feedback",
                    json={"comment_text": "outsider"},
                    headers=bearer(finance_token))
    assert r.status_code == 403


def test_feedback_notifies_owner_on_mgmt_comment(client, sales_token,
                                                 management_token):
    rep = _create_today(client, sales_token, summary="isi")
    client.post(f"/api/daily-reports/{rep['id']}/submit",
                headers=bearer(sales_token))
    client.post(f"/api/daily-reports/{rep['id']}/feedback",
                json={"comment_text": "review please"},
                headers=bearer(management_token))
    assert _has_notif_with_kind(client, sales_token, "daily_report_feedback"), \
        "Sales owner harus dapat notif setelah mgmt komen."


def test_feedback_on_draft_also_allowed(client, sales_token, management_token):
    """Mgmt boleh komen sebelum karyawan submit (nudge)."""
    rep = _create_today(client, sales_token, summary="masih draft")
    r = client.post(f"/api/daily-reports/{rep['id']}/feedback",
                    json={"comment_text": "sudah isi belum?"},
                    headers=bearer(management_token))
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Phase DT-3a: Mgmt-scope endpoints (/team + /team/summary) + attention chip
# ---------------------------------------------------------------------------
def test_team_endpoint_requires_privileged(client, sales_token):
    r = client.get("/api/daily-reports/team", headers=bearer(sales_token))
    assert r.status_code == 403


def test_team_endpoint_returns_users_with_status_today(client, sales_token,
                                                        finance_token,
                                                        management_token):
    """Grid Mgmt hari ini: sales1 punya draft, finance1 tidak submit."""
    _wipe_today_report(_user_id_by_username("sales1"))
    _wipe_today_report(_user_id_by_username("finance1"))
    _create_today(client, sales_token, summary="hari ini")

    r = client.get("/api/daily-reports/team", headers=bearer(management_token))
    assert r.status_code == 200
    data = r.json()
    assert data["date"] == _today()
    assert "totals" in data and "users" in data
    users_map = {u["username"]: u for u in data["users"]}
    assert "sales1" in users_map
    assert users_map["sales1"]["status"] == "Draft"
    assert "finance1" in users_map
    assert users_map["finance1"]["status"] == "NotSubmitted"
    assert users_map["finance1"]["item_count"] == 0
    assert users_map["finance1"]["done_count"] == 0


def test_team_endpoint_filter_role(client, management_token):
    r = client.get("/api/daily-reports/team?role=sales",
                   headers=bearer(management_token))
    assert r.status_code == 200
    data = r.json()
    assert data["role_filter"] == "sales"
    for u in data["users"]:
        assert u["role"] == "sales"


def test_team_endpoint_default_excludes_admin(client, management_token):
    """Default (tanpa role filter) exclude admin (bukan karyawan operasional)."""
    r = client.get("/api/daily-reports/team", headers=bearer(management_token))
    for u in r.json()["users"]:
        assert u["role"] != "admin"


def test_team_endpoint_rejects_invalid_date(client, management_token):
    r = client.get("/api/daily-reports/team?date=2026-99-99",
                   headers=bearer(management_token))
    assert r.status_code == 400


def test_team_endpoint_rejects_invalid_role(client, management_token):
    r = client.get("/api/daily-reports/team?role=hacker",
                   headers=bearer(management_token))
    assert r.status_code == 400


def test_team_endpoint_admin_can_access(client, admin_token):
    """Admin sama seperti mgmt — bird's-eye view."""
    r = client.get("/api/daily-reports/team", headers=bearer(admin_token))
    assert r.status_code == 200


def test_team_summary_default_30_days(client, management_token):
    r = client.get("/api/daily-reports/team/summary",
                   headers=bearer(management_token))
    assert r.status_code == 200
    data = r.json()
    assert data["total_days"] == 30
    assert isinstance(data["users"], list)
    for u in data["users"]:
        assert "submitted_days" in u
        assert "submit_rate_pct" in u
        assert "done_pct" in u
        assert "total_days" in u


def test_team_summary_requires_privileged(client, sales_token):
    r = client.get("/api/daily-reports/team/summary",
                   headers=bearer(sales_token))
    assert r.status_code == 403


def test_team_summary_single_day_math(client, sales_token, management_token):
    """Range 1 hari, sales1 submit -> submitted_days=1, submit_rate=100."""
    _wipe_today_report(_user_id_by_username("sales1"))
    rep = _create_today(client, sales_token, summary="isi")
    r_sub = client.post(f"/api/daily-reports/{rep['id']}/submit",
                        headers=bearer(sales_token))
    assert r_sub.status_code == 200

    today = _today()
    r = client.get(
        f"/api/daily-reports/team/summary?date_from={today}&date_to={today}",
        headers=bearer(management_token))
    assert r.status_code == 200
    data = r.json()
    assert data["total_days"] == 1
    sales_row = next(u for u in data["users"] if u["username"] == "sales1")
    assert sales_row["submitted_days"] == 1
    assert sales_row["submit_rate_pct"] == 100.0


def test_team_summary_reject_from_after_to(client, management_token):
    r = client.get(
        "/api/daily-reports/team/summary?date_from=2026-09-15&date_to=2026-09-10",
        headers=bearer(management_token))
    assert r.status_code == 400


def test_team_summary_reject_range_too_large(client, management_token):
    r = client.get(
        "/api/daily-reports/team/summary?date_from=2020-01-01&date_to=2026-09-11",
        headers=bearer(management_token))
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Phase DT-4: Reminder cron endpoint + dedupe
# ---------------------------------------------------------------------------
def _wipe_today_reminders_for_user(user_id):
    """Purge notif reminder hari ini utk user tsb -- gunanya biar test dedup
    bebas dari state test lain."""
    today_key = datetime.date.today().strftime("%Y%m%d")
    db.execute(
        "DELETE FROM user_notifications "
        "WHERE user_id = ? AND kind = ?",
        (user_id, f"daily_report_reminder_{today_key}"))


def test_reminders_check_requires_privileged(client, sales_token):
    r = client.post("/api/daily-reports/reminders/check",
                    headers=bearer(sales_token))
    assert r.status_code == 403


def test_reminders_check_admin_can_call(client, admin_token):
    r = client.post("/api/daily-reports/reminders/check",
                    headers=bearer(admin_token))
    assert r.status_code == 200
    data = r.json()
    assert "sent" in data
    assert "candidates" in data
    assert data["kind"].startswith("daily_report_reminder_")


def test_reminders_check_notifies_user_who_has_not_submitted(client,
                                                             sales_token,
                                                             management_token):
    """Sales1 belum submit hari ini -> setelah /check dipanggil, sales1
    harus punya notif kind daily_report_reminder_*."""
    _wipe_today_report(_user_id_by_username("sales1"))
    _wipe_today_reminders_for_user(_user_id_by_username("sales1"))

    r = client.post("/api/daily-reports/reminders/check",
                    headers=bearer(management_token))
    assert r.status_code == 200
    assert _has_notif_with_kind(client, sales_token, "daily_report_reminder_"), \
        "Sales1 (belum submit) harus dapat notif reminder."


def test_reminders_check_skips_user_who_submitted_today(client, sales_token,
                                                        management_token):
    """Sales1 sudah Submitted hari ini -> tidak dapat notif reminder."""
    _wipe_today_report(_user_id_by_username("sales1"))
    _wipe_today_reminders_for_user(_user_id_by_username("sales1"))
    rep = _create_today(client, sales_token, summary="sudah lapor")
    r_sub = client.post(f"/api/daily-reports/{rep['id']}/submit",
                        headers=bearer(sales_token))
    assert r_sub.status_code == 200

    r = client.post("/api/daily-reports/reminders/check",
                    headers=bearer(management_token))
    assert r.status_code == 200
    # sales1 sudah submit -> tidak boleh dapat reminder
    assert not _has_notif_with_kind(client, sales_token, "daily_report_reminder_"), \
        "Sales1 (sudah submit) tidak boleh dapat notif reminder."


def test_reminders_check_dedupe_second_call_zero_sent(client, sales_token,
                                                       management_token):
    """Panggil 2x, 2nd call harus sent=0 (semua sudah di-notify di call pertama)."""
    _wipe_today_report(_user_id_by_username("sales1"))
    _wipe_today_reminders_for_user(_user_id_by_username("sales1"))

    r1 = client.post("/api/daily-reports/reminders/check",
                     headers=bearer(management_token))
    assert r1.status_code == 200
    sent1 = r1.json()["sent"]
    assert sent1 > 0, "Call pertama harus kirim minimal 1 notif"

    r2 = client.post("/api/daily-reports/reminders/check",
                     headers=bearer(management_token))
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["sent"] == 0, \
        f"Call kedua harus dedup semua, tapi sent={data2['sent']}"
    assert data2["skipped"] >= sent1, \
        f"Semua kandidat call pertama harus di-skip di call kedua"


def test_reminders_check_draft_still_gets_reminder(client, sales_token,
                                                    management_token):
    """User yg punya Draft (belum submit) tetap masuk daftar reminder."""
    _wipe_today_report(_user_id_by_username("sales1"))
    _wipe_today_reminders_for_user(_user_id_by_username("sales1"))
    _create_today(client, sales_token, summary="masih draft, belum submit")

    r = client.post("/api/daily-reports/reminders/check",
                    headers=bearer(management_token))
    assert r.status_code == 200
    assert _has_notif_with_kind(client, sales_token, "daily_report_reminder_"), \
        "User dgn Draft (belum submit) tetap harus dapat reminder."


# ---------------------------------------------------------------------------
# Phase DT-5: Digest PDF + Excel export
# ---------------------------------------------------------------------------
def _assert_pdf_response(resp):
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/pdf")
    body = resp.content
    assert body[:5] == b"%PDF-", f"bukan PDF: {body[:20]!r}"
    assert len(body) > 500
    assert resp.headers.get("content-disposition", "").startswith("inline")


def _assert_xlsx_response(resp):
    assert resp.status_code == 200, resp.text
    assert "spreadsheetml.sheet" in resp.headers["content-type"]
    body = resp.content
    # XLSX = ZIP archive => magic PK\x03\x04
    assert body[:4] == b"PK\x03\x04", f"bukan XLSX: {body[:8]!r}"
    assert len(body) > 500
    assert resp.headers.get("content-disposition", "").startswith("attachment")


def test_export_pdf_via_query_token(client, management_token):
    """Klik dari <a target=_blank> pakai ?token= query -- authenticate_file_token."""
    r = client.get(
        f"/api/daily-reports/export.pdf?token={management_token}")
    _assert_pdf_response(r)


def test_export_xlsx_via_query_token(client, management_token):
    r = client.get(
        f"/api/daily-reports/export.xlsx?token={management_token}")
    _assert_xlsx_response(r)


def test_export_pdf_via_bearer_header(client, admin_token):
    """authenticate_file_token juga terima Bearer header."""
    r = client.get("/api/daily-reports/export.pdf",
                   headers=bearer(admin_token))
    _assert_pdf_response(r)


def test_export_pdf_denied_without_token(client):
    r = client.get("/api/daily-reports/export.pdf")
    assert r.status_code == 401


def test_export_pdf_denied_for_sales(client, sales_token):
    """Sales role tidak boleh export -- restricted admin+mgmt saja."""
    r = client.get(f"/api/daily-reports/export.pdf?token={sales_token}")
    assert r.status_code == 403


def test_export_xlsx_denied_for_finance(client, finance_token):
    r = client.get(f"/api/daily-reports/export.xlsx?token={finance_token}")
    assert r.status_code == 403


def test_export_pdf_with_date_range_and_role(client, management_token):
    """Test lengkap dgn filter date + role, konfirmasi payload non-empty."""
    today = _today()
    r = client.get(
        f"/api/daily-reports/export.pdf"
        f"?date_from={today}&date_to={today}&role=sales"
        f"&token={management_token}")
    _assert_pdf_response(r)


def test_export_reject_invalid_date_range(client, management_token):
    """date_from > date_to -> 400."""
    r = client.get(
        f"/api/daily-reports/export.pdf"
        f"?date_from=2026-09-15&date_to=2026-09-10&token={management_token}")
    assert r.status_code == 400


def test_reopen_by_admin_ok(client, sales_token, admin_token):
    sales1_id = _user_id_by_username("sales1")
    _wipe_today_report(sales1_id)
    r = _create_today(client, sales_token, summary="AK reopen test")
    rid = r["id"]
    r = client.post(f"/api/daily-reports/{rid}/submit", headers=bearer(sales_token))
    assert r.status_code == 200

    r = client.post(f"/api/daily-reports/{rid}/reopen",
                    headers=bearer(admin_token),
                    json={"reason": "Salah task, minta revisi"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "Draft"

    row = db.query_one("SELECT status FROM daily_reports WHERE id = ?", (rid,))
    assert row["status"] == "Draft"
    _wipe_today_report(sales1_id)


def test_reopen_reason_required(client, sales_token, admin_token):
    sales1_id = _user_id_by_username("sales1")
    _wipe_today_report(sales1_id)
    r = _create_today(client, sales_token, summary="reason req")
    rid = r["id"]
    client.post(f"/api/daily-reports/{rid}/submit", headers=bearer(sales_token))

    r = client.post(f"/api/daily-reports/{rid}/reopen",
                    headers=bearer(admin_token),
                    json={"reason": "abc"})
    assert r.status_code == 400
    assert "5 karakter" in r.json()["error"]
    _wipe_today_report(sales1_id)


def test_reopen_status_wrong(client, sales_token, admin_token):
    sales1_id = _user_id_by_username("sales1")
    _wipe_today_report(sales1_id)
    r = _create_today(client, sales_token, summary="not submitted yet")
    rid = r["id"]

    r = client.post(f"/api/daily-reports/{rid}/reopen",
                    headers=bearer(admin_token),
                    json={"reason": "test wrong status"})
    assert r.status_code == 400
    _wipe_today_report(sales1_id)


def test_reopen_owner_within_window_ok(client, sales_token):
    sales1_id = _user_id_by_username("sales1")
    _wipe_today_report(sales1_id)
    r = _create_today(client, sales_token, summary="owner window test")
    rid = r["id"]
    client.post(f"/api/daily-reports/{rid}/submit", headers=bearer(sales_token))

    r = client.post(f"/api/daily-reports/{rid}/reopen",
                    headers=bearer(sales_token),
                    json={"reason": "misklik submit"})
    assert r.status_code == 200, r.text
    _wipe_today_report(sales1_id)


def test_reopen_owner_past_window_denied(client, sales_token):
    sales1_id = _user_id_by_username("sales1")
    _wipe_today_report(sales1_id)
    r = _create_today(client, sales_token, summary="past window")
    rid = r["id"]
    client.post(f"/api/daily-reports/{rid}/submit", headers=bearer(sales_token))
    past = (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).strftime(
        "%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE daily_reports SET submitted_at = ? WHERE id = ?",
               (past, rid))

    r = client.post(f"/api/daily-reports/{rid}/reopen",
                    headers=bearer(sales_token),
                    json={"reason": "coba past window"})
    assert r.status_code == 403
    assert "window" in r.json()["error"].lower()
    _wipe_today_report(sales1_id)


def test_reopen_owner_corrupt_submitted_at_denied(client, sales_token):
    """Fail-closed: kalau submitted_at DB corrupt/tidak parseable, owner
    HARUS ditolak (bukan silent bypass window guard)."""
    sales1_id = _user_id_by_username("sales1")
    _wipe_today_report(sales1_id)
    r = _create_today(client, sales_token, summary="corrupt at test")
    rid = r["id"]
    client.post(f"/api/daily-reports/{rid}/submit", headers=bearer(sales_token))
    # Corrupt the submitted_at value to simulate DB data issue.
    db.execute("UPDATE daily_reports SET submitted_at = ? WHERE id = ?",
               ("not-a-valid-datetime", rid))

    r = client.post(f"/api/daily-reports/{rid}/reopen",
                    headers=bearer(sales_token),
                    json={"reason": "coba dgn submitted_at corrupt"})
    assert r.status_code == 403, r.text
    assert "tidak dapat divalidasi" in r.json()["error"].lower()
    _wipe_today_report(sales1_id)


def test_reopen_stranger_denied(client, sales_token, finance_token):
    sales1_id = _user_id_by_username("sales1")
    _wipe_today_report(sales1_id)
    r = _create_today(client, sales_token, summary="stranger test")
    rid = r["id"]
    client.post(f"/api/daily-reports/{rid}/submit", headers=bearer(sales_token))

    r = client.post(f"/api/daily-reports/{rid}/reopen",
                    headers=bearer(finance_token),
                    json={"reason": "coba akses"})
    assert r.status_code == 403
    _wipe_today_report(sales1_id)


def test_mgmt_home_attention_daily_report_overdue(client, management_token):
    """Chip 'daily_report_overdue' muncul kalau ada user sales/ops/finance
    yg tidak submit dalam 3 hari terakhir. Kita clear submitted history sales1
    supaya minimal 1 karyawan overdue -> chip harus muncul."""
    sales1_id = _user_id_by_username("sales1")
    cutoff = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
    db.execute(
        "UPDATE daily_reports SET status = 'Draft', submitted_at = NULL "
        "WHERE user_id = ? AND report_date >= ? AND status = 'Submitted'",
        (sales1_id, cutoff))

    r = client.get("/api/mgmt/home", headers=bearer(management_token))
    assert r.status_code == 200, r.text
    keys = [a["key"] for a in r.json().get("attention", [])]
    assert "daily_report_overdue" in keys
