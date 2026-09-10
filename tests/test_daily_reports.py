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

from tests.conftest import bearer


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
