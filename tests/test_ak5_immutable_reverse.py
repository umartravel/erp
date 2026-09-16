"""
Sprint AK-5: Test immutable posting + reverse endpoint + accrual KPI di Home Finance.
"""
import db
import journal_engine as je
from tests.conftest import bearer


def test_ak5_delete_posted_tx_rejected(client, admin_token):
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('income','test',500000,'AK5 immutable test')", (),
    )
    je.post_jamaah_dp(tx_id, 500000, "AK5 Immutable")
    r = client.delete(f"/api/transactions/{tx_id}", headers=bearer(admin_token))
    assert r.status_code == 400
    assert "immutable" in r.json()["error"].lower()
    row = db.query_one("SELECT status FROM transactions WHERE id = ?", (tx_id,))
    assert row is not None
    assert row["status"] == "POSTED"
    db.execute("DELETE FROM journal_lines WHERE transaction_id = ?", (tx_id,))
    db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))


def test_ak5_delete_draft_tx_allowed(client, admin_token):
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('income','test',100000,'AK5 legacy delete')", (),
    )
    db.execute("UPDATE transactions SET status = NULL WHERE id = ?", (tx_id,))
    r = client.delete(f"/api/transactions/{tx_id}", headers=bearer(admin_token))
    assert r.status_code == 200
    row = db.query_one("SELECT id FROM transactions WHERE id = ?", (tx_id,))
    assert row is None


def test_ak5_delete_reversed_tx_rejected(client, admin_token):
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('income','test',200000,'AK5 reversed delete test')", (),
    )
    je.post_jamaah_dp(tx_id, 200000, "AK5 Reversed")
    new_tx = je.reverse_journal(tx_id, "AK5 test")
    r = client.delete(f"/api/transactions/{tx_id}", headers=bearer(admin_token))
    assert r.status_code == 400
    for t in (tx_id, new_tx):
        db.execute("DELETE FROM journal_lines WHERE transaction_id = ?", (t,))
        db.execute("DELETE FROM transactions WHERE id = ?", (t,))


def test_ak5_reverse_endpoint_ok(client, admin_token, finance_token):
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('income','test',750000,'AK5 reverse endpoint test')", (),
    )
    je.post_jamaah_dp(tx_id, 750000, "AK5 Rev EP")

    r = client.post(f"/api/finance/reverse/{tx_id}",
                    headers=bearer(finance_token),
                    json={"reason": "Salah kategori"})
    assert r.status_code == 200
    data = r.json()
    assert data["original_tx_id"] == tx_id
    new_tx = data["reversing_tx_id"]

    row = db.query_one("SELECT status FROM transactions WHERE id = ?", (tx_id,))
    assert row["status"] == "REVERSED"
    lines = db.query_all(
        "SELECT jl.debit, jl.credit, ca.account_code FROM journal_lines jl "
        "JOIN chart_of_accounts ca ON ca.id = jl.account_id "
        "WHERE jl.transaction_id = ?", (new_tx,))
    codes = {l["account_code"]: l for l in lines}
    assert codes["2101"]["debit"] == 750000
    assert codes["1102"]["credit"] == 750000

    for t in (tx_id, new_tx):
        db.execute("DELETE FROM journal_lines WHERE transaction_id = ?", (t,))
        db.execute("DELETE FROM transactions WHERE id = ?", (t,))


def test_ak5_reverse_endpoint_reason_required(client, finance_token):
    r = client.post("/api/finance/reverse/1",
                    headers=bearer(finance_token),
                    json={"reason": "abc"})
    assert r.status_code == 400
    assert "5 karakter" in r.json()["error"]


def test_ak5_reverse_endpoint_sales_denied(client, sales_token):
    r = client.post("/api/finance/reverse/1",
                    headers=bearer(sales_token),
                    json={"reason": "hello world"})
    assert r.status_code == 403


def test_ak5_reverse_endpoint_404_ok(client, finance_token):
    r = client.post("/api/finance/reverse/999999",
                    headers=bearer(finance_token),
                    json={"reason": "test 404"})
    assert r.status_code == 400
    assert "tidak ditemukan" in r.json()["error"].lower()


def test_ak5_reverse_endpoint_double_rejected(client, finance_token):
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('income','test',300000,'AK5 double reverse test')", (),
    )
    je.post_jamaah_dp(tx_id, 300000, "AK5 Double")
    r = client.post(f"/api/finance/reverse/{tx_id}",
                    headers=bearer(finance_token),
                    json={"reason": "First reversal"})
    assert r.status_code == 200
    new_tx = r.json()["reversing_tx_id"]
    r = client.post(f"/api/finance/reverse/{tx_id}",
                    headers=bearer(finance_token),
                    json={"reason": "Second attempt"})
    assert r.status_code == 400
    for t in (tx_id, new_tx):
        db.execute("DELETE FROM journal_lines WHERE transaction_id = ?", (t,))
        db.execute("DELETE FROM transactions WHERE id = ?", (t,))


def test_ak5_ledger_recent_ok(client, finance_token):
    r = client.get("/api/finance/ledger/recent?limit=10",
                   headers=bearer(finance_token))
    assert r.status_code == 200
    data = r.json()
    assert "entries" in data
    for e in data["entries"]:
        assert "journal_lines" in e
        assert e["status"] != "REVERSED"


def test_ak5_ledger_recent_sales_denied(client, sales_token):
    r = client.get("/api/finance/ledger/recent",
                   headers=bearer(sales_token))
    assert r.status_code == 403


def test_ak5_finance_home_includes_accrual_kpi(client, finance_token):
    r = client.get("/api/finance/home", headers=bearer(finance_token))
    assert r.status_code == 200
    data = r.json()
    assert "accrual_kpi" in data
    kpi = data["accrual_kpi"]
    if kpi is not None:
        assert "saldo_kas_bank" in kpi
        assert "unearned_liab" in kpi
        assert "net_profit_mtd" in kpi
        assert "balance_sheet_balanced" in kpi
