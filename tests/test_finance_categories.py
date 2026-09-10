"""Phase F2: /api/finance/categories + summary + expense/income transaksi."""
import datetime

from tests.conftest import bearer

import db


def test_categories_tree(client, admin_token):
    r = client.get("/api/finance/categories", headers=bearer(admin_token))
    assert r.status_code == 200
    d = r.json()
    assert "income" in d and "expense" in d
    assert len(d["income"]) == 2
    assert len(d["expense"]) == 6
    hpp = next(p for p in d["expense"] if p["name"].startswith("HPP"))
    assert len(hpp["subcategories"]) >= 10


def test_categories_denies_sales(client, sales_token):
    r = client.get("/api/finance/categories", headers=bearer(sales_token))
    assert r.status_code == 403


def test_category_create_and_update(client, admin_token):
    marketing_parent = db.query_one(
        "SELECT id FROM expense_categories WHERE name = 'Marketing & Sales'"
    )["id"]
    r = client.post("/api/finance/categories", json={
        "name": "Sponsorship Konten YouTube",
        "group_type": "expense", "parent_id": marketing_parent,
    }, headers=bearer(admin_token))
    assert r.status_code == 200
    cid = r.json()["id"]
    r2 = client.put(f"/api/finance/categories/{cid}", json={
        "name": "Sponsorship YouTube (updated)",
    }, headers=bearer(admin_token))
    assert r2.status_code == 200
    row = db.query_one("SELECT name FROM expense_categories WHERE id = ?", (cid,))
    assert row["name"] == "Sponsorship YouTube (updated)"


def test_category_create_denies_non_admin(client, finance_token):
    r = client.post("/api/finance/categories", json={
        "name": "Test", "group_type": "expense",
    }, headers=bearer(finance_token))
    assert r.status_code == 403


def test_expense_with_category_id(client, admin_token):
    sewa = db.query_one(
        "SELECT id FROM expense_categories WHERE name = 'Sewa Kantor'"
    )["id"]
    r = client.post("/api/transactions/expense", json={
        "category": "Sewa Kantor", "category_id": sewa,
        "amount": 5_000_000, "description": "Sewa bulan Sep 2026",
    }, headers=bearer(admin_token))
    assert r.status_code == 200
    row = db.query_one(
        "SELECT * FROM transactions WHERE category = 'Sewa Kantor' "
        "ORDER BY id DESC LIMIT 1"
    )
    assert row["category_id"] == sewa
    assert row["type"] == "expense"


def test_expense_wrong_group_type_rejected(client, admin_token):
    payment_jamaah = db.query_one(
        "SELECT id FROM expense_categories WHERE name = 'Payment Jamaah'"
    )["id"]
    r = client.post("/api/transactions/expense", json={
        "category": "Bad", "category_id": payment_jamaah,
        "amount": 1_000_000, "description": "salah kategori",
    }, headers=bearer(admin_token))
    assert r.status_code == 400


def test_income_endpoint_with_category(client, admin_token):
    bunga = db.query_one(
        "SELECT id FROM expense_categories WHERE name = 'Bunga Bank'"
    )["id"]
    r = client.post("/api/transactions/income", json={
        "category_id": bunga, "amount": 500_000,
        "description": "Bunga BCA Sep 2026",
    }, headers=bearer(admin_token))
    assert r.status_code == 200
    row = db.query_one(
        "SELECT * FROM transactions WHERE category_id = ? ORDER BY id DESC LIMIT 1",
        (bunga,))
    assert row["type"] == "income"
    assert row["amount"] == 500_000


def test_summary_year_shape(client, admin_token):
    r = client.get("/api/finance/summary/year", headers=bearer(admin_token))
    assert r.status_code == 200
    d = r.json()
    assert "monthly" in d and len(d["monthly"]) == 12
    assert "by_category" in d
    assert "total_income" in d and "total_expense" in d and "net_saldo" in d


def test_summary_month_shape(client, admin_token):
    now = datetime.datetime.now()
    r = client.get(
        f"/api/finance/summary/month?year={now.year}&month={now.month}",
        headers=bearer(admin_token),
    )
    assert r.status_code == 200
    d = r.json()
    assert d["year"] == now.year
    assert d["month"] == now.month
    assert "by_category" in d
    assert "transactions" in d
