"""
Phase F1b: verify category_id linkage terpasang di flow transaksi.

F1b-1: auto-tag via resolve_cat_id di 5 flow (payment/refund/payroll/komisi/extras).
F1b-2: expense_reports.category_id -> transactions.category_id on Pay.
F1b-3: procurement.category_id -> transactions.category_id on each payment.

Cover:
- POST /api/transactions/income + expense pakai category_id yg valid
- Payroll -> transactions.category_id = id "Gaji Pokok + Tunjangan"
- Expense report create dgn category_id, Pay -> tx.category_id sama
- Procurement create dgn category_id, Payment -> tx.category_id sama
- Guard: category_id invalid -> 400
- Guard: category_id group_type='income' dipakai utk expense -> 400
"""
import db
from tests.conftest import bearer


def _cat_id_by_name(name):
    row = db.query_one(
        "SELECT id FROM expense_categories WHERE name = ? AND is_active = 1", (name,))
    assert row, f"kategori '{name}' harus ada (dari migration 011 seed)"
    return row["id"]


def test_expense_manual_uses_category_id(client, admin_token):
    """POST /api/transactions/expense (manual) accept category_id + persist."""
    cid = _cat_id_by_name("Iklan Digital (Meta/Google/TikTok)")
    r = client.post("/api/transactions/expense", json={
        "category": "iklan", "amount": 500_000, "description": "Test IG Ads",
        "category_id": cid,
    }, headers=bearer(admin_token))
    assert r.status_code == 200, r.text
    row = db.query_one(
        "SELECT category_id FROM transactions WHERE description = 'Test IG Ads' "
        "ORDER BY id DESC LIMIT 1")
    assert row["category_id"] == cid


def test_income_uses_category_id(client, admin_token):
    """POST /api/transactions/income accept category_id group=income."""
    parent_income = db.query_one(
        "SELECT id FROM expense_categories WHERE group_type='income' "
        "AND is_active=1 ORDER BY id LIMIT 1")
    assert parent_income
    r = client.post("/api/transactions/income", json={
        "category": "other", "amount": 100_000, "description": "Test income",
        "category_id": parent_income["id"],
    }, headers=bearer(admin_token))
    assert r.status_code == 200, r.text


def test_expense_rejects_income_category(client, admin_token):
    """Guard: expense endpoint tolak category_id group='income'."""
    income_id = db.query_one(
        "SELECT id FROM expense_categories WHERE group_type='income' LIMIT 1")["id"]
    r = client.post("/api/transactions/expense", json={
        "category": "iklan", "amount": 100_000, "description": "salah kategori",
        "category_id": income_id,
    }, headers=bearer(admin_token))
    assert r.status_code == 400


def test_expense_rejects_invalid_category(client, admin_token):
    """Guard: category_id yg tidak ada -> 400."""
    r = client.post("/api/transactions/expense", json={
        "category": "iklan", "amount": 100_000, "description": "kategori palsu",
        "category_id": 999_999,
    }, headers=bearer(admin_token))
    assert r.status_code == 400


def test_payroll_auto_tags_gaji_category(client, admin_token):
    """Phase F1b-1: /api/payroll -> tx.category_id = 'Gaji Pokok + Tunjangan'."""
    gaji_id = _cat_id_by_name("Gaji Pokok + Tunjangan")

    payroll_users = db.query_all("SELECT id FROM users WHERE base_salary > 0")
    if not payroll_users:
        db.execute("INSERT INTO users (name, username, password, role, base_salary) "
                   "VALUES (?, ?, ?, ?, ?)",
                   ("F1b Test User", "f1b_test", "x", "sales", 3_000_000))

    db.execute("DELETE FROM transactions WHERE category='payroll' AND "
               "strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')")

    r = client.post("/api/payroll", json={}, headers=bearer(admin_token))
    assert r.status_code == 200, r.text

    tagged = db.query_all(
        "SELECT category_id FROM transactions WHERE category='payroll' "
        "AND strftime('%Y-%m', created_at)=strftime('%Y-%m', 'now')")
    assert tagged
    assert all(t["category_id"] == gaji_id for t in tagged), \
        f"semua row payroll bulan ini harus tagged ke {gaji_id}, dapat {tagged}"


def test_expense_report_pay_propagates_category_id(
    client, admin_token, management_token, finance_token
):
    """Phase F1b-2: expense_reports.category_id -> transactions.category_id on Pay."""
    from tests.test_expense_lifecycle import _setup_project, _get_approver_id

    cid = _cat_id_by_name("Transport Operasional")
    hdr = bearer(admin_token)
    pid = _setup_project(client, admin_token, name="F1b Test Project")
    aid = _get_approver_id(client, admin_token)

    r = client.post("/api/expense-reports", json={
        "project_id": pid, "period_from": "2026-09-01", "period_to": "2026-09-30",
        "approver_id": aid, "category_id": cid, "note": "F1b test",
    }, headers=hdr)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]

    got = client.get(f"/api/expense-reports/{rid}", headers=hdr).json()
    assert got["category_id"] == cid
    assert got["category_name"] == "Transport Operasional"

    r = client.post(f"/api/expense-reports/{rid}/lines", json={
        "category": "Transport", "description": "Grab lapangan",
        "unit_price_net": 50_000, "qty": 1, "tax_percent": 0,
        "date": "2026-09-15",
    }, headers=hdr)
    assert r.status_code == 200, r.text

    client.post(f"/api/expense-reports/{rid}/submit", headers=hdr)
    client.put(f"/api/expense-reports/{rid}/review",
               json={"action": "approve"}, headers=bearer(management_token))
    r = client.put(f"/api/expense-reports/{rid}/pay",
                   headers=bearer(finance_token))
    assert r.status_code == 200, r.text

    tx = db.query_one(
        "SELECT category_id FROM transactions WHERE category='expense_report' "
        "AND reference_id = ?", (rid,))
    assert tx, "harus ada 1 tx expense_report utk report ini"
    assert tx["category_id"] == cid


def test_procurement_payment_propagates_category_id(
    client, admin_token, management_token, finance_token
):
    """Phase F1b-3: procurement.category_id -> transactions.category_id on payment."""
    cid = _cat_id_by_name("Hotel Mekkah")

    r = client.post("/api/procurement", json={
        "vendor_name": "F1b Hotel Test", "service_type": "Hotel Mekkah/Madinah",
        "total_stock": 10, "total_price": 5_000_000, "category_id": cid,
    }, headers=bearer(admin_token))
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    lst = client.get("/api/procurement", headers=bearer(admin_token)).json()
    row = next(x for x in lst if x["id"] == pid)
    assert row["category_id"] == cid
    assert row["category_name"] == "Hotel Mekkah"

    client.put(f"/api/procurement/{pid}/review",
               json={"action": "approve"}, headers=bearer(management_token))

    r = client.post(f"/api/procurement/{pid}/payment",
                    json={"amount": 1_000_000}, headers=bearer(finance_token))
    assert r.status_code == 200, r.text

    tx = db.query_one(
        "SELECT category_id FROM transactions WHERE category='procurement_payment' "
        "AND reference_id = ?", (pid,))
    assert tx
    assert tx["category_id"] == cid
