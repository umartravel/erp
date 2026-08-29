"""
Integration test: expense_reports lifecycle.
Draft -> Submitted -> Approved -> Paid (+ tx expense category=expense_report).

Yang di-cover:
- Owner create Draft + tambah line + submit
- Manager approve (RBAC: admin/management)
- Finance pay -> INSERT tx expense_report + status Paid
- Guard: submit tanpa line/approver ditolak
- Guard: pay tanpa approve ditolak
- Reject wajib note
- RBAC review + pay
"""
from tests.conftest import bearer


def _setup_project(client, admin_token, name="TEST Project"):
    hdr = bearer(admin_token)
    projects = client.get("/api/expense-projects", headers=hdr).json()
    existing = next((p for p in projects if p["name"] == name), None)
    if existing:
        return existing["id"]
    r = client.post("/api/expense-projects", json={"name": name}, headers=hdr)
    assert r.status_code == 200, r.text
    projects = client.get("/api/expense-projects", headers=hdr).json()
    return next(p for p in projects if p["name"] == name)["id"]


def _get_approver_id(client, admin_token):
    users = client.get("/api/expense-approvers", headers=bearer(admin_token)).json()
    manager = next(u for u in users if u["role"] == "management")
    return manager["id"]


def test_expense_full_flow_draft_to_paid(
    client, admin_token, management_token, finance_token
):
    """Happy path: create -> add line -> submit -> approve -> pay."""
    hdr = bearer(admin_token)
    pid = _setup_project(client, admin_token)
    aid = _get_approver_id(client, admin_token)

    r = client.post("/api/expense-reports", json={
        "project_id": pid, "period_from": "2026-08-01", "period_to": "2026-08-31",
        "approver_id": aid, "note": "test expense",
    }, headers=hdr)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]

    r = client.post(f"/api/expense-reports/{rid}/lines", json={
        "category": "Transport", "description": "Grab", "unit_price_net": 100000,
        "qty": 2, "tax_percent": 11.0, "date": "2026-08-15",
    }, headers=hdr)
    assert r.status_code == 200, r.text

    r = client.post(f"/api/expense-reports/{rid}/submit", headers=hdr)
    assert r.status_code == 200, r.text

    r = client.put(f"/api/expense-reports/{rid}/review",
                   json={"action": "approve"}, headers=bearer(management_token))
    assert r.status_code == 200, r.text

    r = client.put(f"/api/expense-reports/{rid}/pay", headers=bearer(finance_token))
    assert r.status_code == 200, r.text

    report = client.get(f"/api/expense-reports/{rid}", headers=hdr).json()
    assert report["status"] == "Paid"

    txs = client.get("/api/transactions", headers=hdr).json()
    my_txs = [t for t in txs if t["category"] == "expense_report" and t["reference_id"] == rid]
    assert len(my_txs) == 1
    # net = 200_000, tax = 22_000, gross = 222_000
    assert my_txs[0]["amount"] == 222_000


def test_expense_submit_needs_line(client, admin_token):
    """Submit tanpa line -> 400."""
    hdr = bearer(admin_token)
    pid = _setup_project(client, admin_token, name="TEST Proj NoLine")
    aid = _get_approver_id(client, admin_token)

    r = client.post("/api/expense-reports", json={
        "project_id": pid, "period_from": "2026-08-01", "period_to": "2026-08-31",
        "approver_id": aid,
    }, headers=hdr)
    rid = r.json()["id"]

    r = client.post(f"/api/expense-reports/{rid}/submit", headers=hdr)
    assert r.status_code == 400
    assert "item pengeluaran" in r.json()["error"]


def test_expense_submit_needs_approver(client, admin_token):
    """Submit tanpa approver -> 400."""
    hdr = bearer(admin_token)
    pid = _setup_project(client, admin_token, name="TEST Proj NoApprv")

    r = client.post("/api/expense-reports", json={
        "project_id": pid, "period_from": "2026-08-01", "period_to": "2026-08-31",
    }, headers=hdr)
    rid = r.json()["id"]

    client.post(f"/api/expense-reports/{rid}/lines", json={
        "category": "x", "unit_price_net": 1000, "qty": 1, "tax_percent": 0,
    }, headers=hdr)
    r = client.post(f"/api/expense-reports/{rid}/submit", headers=hdr)
    assert r.status_code == 400
    assert "approval" in r.json()["error"].lower()


def test_expense_reject_requires_note(client, admin_token, management_token):
    hdr = bearer(admin_token)
    pid = _setup_project(client, admin_token, name="TEST Proj Reject")
    aid = _get_approver_id(client, admin_token)

    r = client.post("/api/expense-reports", json={
        "project_id": pid, "period_from": "2026-08-01", "period_to": "2026-08-31",
        "approver_id": aid,
    }, headers=hdr)
    rid = r.json()["id"]
    client.post(f"/api/expense-reports/{rid}/lines", json={
        "category": "x", "unit_price_net": 1000, "qty": 1, "tax_percent": 0,
    }, headers=hdr)
    client.post(f"/api/expense-reports/{rid}/submit", headers=hdr)

    r = client.put(f"/api/expense-reports/{rid}/review",
                   json={"action": "reject"}, headers=bearer(management_token))
    assert r.status_code == 400
    assert "penolakan" in r.json()["error"].lower()


def test_expense_pay_needs_approve(client, admin_token, finance_token):
    """Draft/Submitted tidak bisa langsung dibayar."""
    hdr = bearer(admin_token)
    pid = _setup_project(client, admin_token, name="TEST Proj NoAppr")
    aid = _get_approver_id(client, admin_token)

    r = client.post("/api/expense-reports", json={
        "project_id": pid, "period_from": "2026-08-01", "period_to": "2026-08-31",
        "approver_id": aid,
    }, headers=hdr)
    rid = r.json()["id"]

    r = client.put(f"/api/expense-reports/{rid}/pay",
                   headers=bearer(finance_token))
    assert r.status_code == 400
    assert "Approved" in r.json()["error"]


def test_expense_review_rbac(client, sales_token, finance_token):
    """Sales/finance tidak boleh approve (hanya admin/management)."""
    r = client.put("/api/expense-reports/999/review",
                   json={"action": "approve"}, headers=bearer(sales_token))
    assert r.status_code == 403
    r = client.put("/api/expense-reports/999/review",
                   json={"action": "approve"}, headers=bearer(finance_token))
    assert r.status_code == 403


def test_expense_pay_rbac(client, sales_token, management_token):
    """Sales/management tidak boleh pay (hanya admin/finance)."""
    r = client.put("/api/expense-reports/999/pay", headers=bearer(sales_token))
    assert r.status_code == 403
    r = client.put("/api/expense-reports/999/pay", headers=bearer(management_token))
    assert r.status_code == 403
