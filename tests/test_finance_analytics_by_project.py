"""
Phase EX-5: Test summary/month + summary/year expose `by_project` breakdown.
Fixtures (client, admin_token, finance_token) datang dari conftest.py.
"""
import datetime

import pytest

import db
from tests.conftest import bearer


def _first_expense_category_id():
    row = db.query_one(
        "SELECT id FROM expense_categories WHERE group_type='expense' "
        "AND parent_id IS NOT NULL AND is_active=1 ORDER BY sort_order LIMIT 1")
    return row["id"]


def _get_approver_id(client, admin_token):
    users = client.get("/api/expense-approvers", headers=bearer(admin_token)).json()
    manager = next(u for u in users if u["role"] == "management")
    return manager["id"]


def _create_and_pay_expense(client, admin_token, project_id, amount):
    cid = _first_expense_category_id()
    aid = _get_approver_id(client, admin_token)
    r = client.post(
        "/api/expense-reports",
        json={"project_id": project_id, "category_id": cid,
              "approver_id": aid,
              "period_from": "2026-09-01", "period_to": "2026-09-30"},
        headers=bearer(admin_token),
    )
    assert r.status_code == 200, r.text
    rid = r.json()["id"]

    r = client.post(
        f"/api/expense-reports/{rid}/lines",
        json={"category_id": cid, "description": "test",
              "unit_price_net": amount, "qty": 1, "tax_percent": 0,
              "date": "2026-09-15"},
        headers=bearer(admin_token),
    )
    assert r.status_code == 200, r.text

    r = client.post(f"/api/expense-reports/{rid}/submit", headers=bearer(admin_token))
    assert r.status_code == 200, r.text

    r = client.put(
        f"/api/expense-reports/{rid}/review",
        json={"action": "approve", "note": "ok"},
        headers=bearer(admin_token),
    )
    assert r.status_code == 200, r.text

    r = client.put(
        f"/api/expense-reports/{rid}/pay",
        json={"paid_date": "2026-09-16", "bank_account_id": None},
        headers=bearer(admin_token),
    )
    assert r.status_code == 200, r.text
    return rid


def _create_project(client, admin_token, name):
    """POST /api/expense-projects tidak return id, jadi query by name."""
    r = client.post("/api/expense-projects",
                    json={"name": name},
                    headers=bearer(admin_token))
    assert r.status_code == 200, r.text
    row = db.query_one("SELECT id FROM expense_projects WHERE name = ?", (name,))
    return row["id"]


def test_ex5_by_project_present_in_month(client, admin_token, finance_token):
    pid_a = _create_project(client, admin_token, "Test EX5 Project A")
    pid_b = _create_project(client, admin_token, "Test EX5 Project B")

    _create_and_pay_expense(client, admin_token, pid_a, 100_000)
    _create_and_pay_expense(client, admin_token, pid_a, 150_000)
    _create_and_pay_expense(client, admin_token, pid_b, 100_000)

    now = datetime.datetime.now()
    r = client.get(
        f"/api/finance/summary/month?year={now.year}&month={now.month}",
        headers=bearer(finance_token),
    )
    assert r.status_code == 200
    data = r.json()
    assert "by_project" in data, f"Response missing by_project"
    assert isinstance(data["by_project"], list)

    projects = {p["project_id"]: p for p in data["by_project"]}
    assert pid_a in projects
    assert pid_b in projects
    assert projects[pid_a]["total"] >= 250_000
    assert projects[pid_b]["total"] >= 100_000
    idx_a = next(i for i, p in enumerate(data["by_project"]) if p["project_id"] == pid_a)
    idx_b = next(i for i, p in enumerate(data["by_project"]) if p["project_id"] == pid_b)
    assert idx_a < idx_b


def test_ex5_by_project_category_breakdown_shape(client, finance_token):
    now = datetime.datetime.now()
    r = client.get(
        f"/api/finance/summary/month?year={now.year}&month={now.month}",
        headers=bearer(finance_token),
    )
    data = r.json()
    if not data["by_project"]:
        pytest.skip("No projects yet")
    first = data["by_project"][0]
    assert "category_breakdown" in first
    assert isinstance(first["category_breakdown"], list)
    if first["count"] > 0:
        assert len(first["category_breakdown"]) >= 1
        assert "cat_name" in first["category_breakdown"][0]


def test_ex5_by_project_present_in_year(client, finance_token):
    now = datetime.datetime.now()
    r = client.get(
        f"/api/finance/summary/year?year={now.year}",
        headers=bearer(finance_token),
    )
    assert r.status_code == 200
    data = r.json()
    assert "by_project" in data
    assert isinstance(data["by_project"], list)


def test_ex5_by_project_only_expense_type(client, finance_token):
    now = datetime.datetime.now()
    r = client.get(
        f"/api/finance/summary/month?year={now.year}&month={now.month}",
        headers=bearer(finance_token),
    )
    data = r.json()
    for p in data["by_project"]:
        assert p["total"] > 0
        assert p["count"] > 0
