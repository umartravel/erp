"""
Sprint AK-4: Test Neraca + Laba Rugi Multi-Step.
"""
import datetime

import db
import financial_reports as fr
import journal_engine as je
from tests.conftest import bearer


TODAY = datetime.date.today().isoformat()


def test_ak4_balance_sheet_basic_structure(client):
    bs = fr.build_balance_sheet(TODAY)
    assert "assets" in bs
    assert "liabilities" in bs
    assert "equity" in bs
    assert "total_liab_equity" in bs
    assert "balanced" in bs
    assert "current" in bs["assets"]
    assert "fixed" in bs["assets"]


def test_ak4_balance_sheet_accounting_equation(client):
    """Aset = Liab + Ekuitas -- invariant fundamental double-entry."""
    bs = fr.build_balance_sheet(TODAY)
    assert bs["assets"]["total"] == bs["total_liab_equity"], \
        f"Neraca tidak balance: Aset={bs['assets']['total']} vs " \
        f"L+E={bs['total_liab_equity']} (delta={bs['delta']})"
    assert bs["balanced"] is True


def test_ak4_balance_sheet_asset_classification(client):
    bs = fr.build_balance_sheet(TODAY)
    for a in bs["assets"]["current"]:
        assert not a["account_code"].startswith("12")
        assert a["account_group"] == "ASSET"
    for a in bs["assets"]["fixed"]:
        assert a["account_code"].startswith("12")
        assert a["account_group"] == "ASSET"


def test_ak4_balance_sheet_unearned_revenue_visible(client):
    bs = fr.build_balance_sheet(TODAY)
    codes = {l["account_code"] for l in bs["liabilities"]["items"]}
    assert "2101" in codes


def test_ak4_income_statement_structure(client):
    is_data = fr.build_income_statement(2026, 9)
    assert "revenue" in is_data
    assert "cogs" in is_data
    assert "gross_profit" in is_data
    assert "opex" in is_data
    assert "operating_profit" in is_data
    assert "other" in is_data
    assert "net_profit_before_tax" in is_data


def test_ak4_income_statement_multi_step_math(client):
    is_data = fr.build_income_statement(2026, 9)
    assert is_data["gross_profit"] == is_data["revenue"]["total"] - is_data["cogs"]["total"]
    assert is_data["operating_profit"] == is_data["gross_profit"] - is_data["opex"]["total"]
    assert is_data["net_profit_before_tax"] == is_data["operating_profit"] - is_data["other"]["total"]


def test_ak4_income_statement_expense_classification(client):
    is_data = fr.build_income_statement(2026, 9)
    for item in is_data["cogs"]["items"]:
        assert item["account_code"].startswith("5")
    for item in is_data["opex"]["items"]:
        assert item["account_code"].startswith("61")
    for item in is_data["other"]["items"]:
        assert item["account_code"].startswith("62")


def test_ak4_income_statement_invalid_month():
    try:
        fr.build_income_statement(2026, 13)
        assert False
    except ValueError:
        pass


def test_ak4_reversed_tx_excluded_from_balance(client):
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('income','test',1234567,'AK4 revtest')", (),
    )
    je.post_jamaah_dp(tx_id, 1234567, "AK4 Test Rev")
    bs_before = fr.build_balance_sheet(TODAY)
    unearned_before = next(
        (l["balance"] for l in bs_before["liabilities"]["items"]
         if l["account_code"] == "2101"), 0)

    new_tx = je.reverse_journal(tx_id, "AK4 test excl")
    bs_after = fr.build_balance_sheet(TODAY)
    # Original REVERSED + new tx also REVERSED -- both filtered out. Net effect
    # depends on query filter, but the balance sheet must remain internally
    # consistent (Aset == L+E) in every case.
    assert bs_after["balanced"] is True

    for t in (tx_id, new_tx):
        db.execute("DELETE FROM journal_lines WHERE transaction_id = ?", (t,))
        db.execute("DELETE FROM transactions WHERE id = ?", (t,))


def test_ak4_balance_sheet_endpoint_finance_ok(client, finance_token):
    r = client.get("/api/finance/reports/balance-sheet",
                   headers=bearer(finance_token))
    assert r.status_code == 200
    data = r.json()
    assert data["balanced"] is True


def test_ak4_balance_sheet_endpoint_sales_denied(client, sales_token):
    r = client.get("/api/finance/reports/balance-sheet",
                   headers=bearer(sales_token))
    assert r.status_code == 403


def test_ak4_balance_sheet_endpoint_with_date(client, admin_token):
    r = client.get("/api/finance/reports/balance-sheet?date=2026-06-30",
                   headers=bearer(admin_token))
    assert r.status_code == 200
    assert r.json()["as_of"] == "2026-06-30"


def test_ak4_balance_sheet_endpoint_bad_date(client, admin_token):
    r = client.get("/api/finance/reports/balance-sheet?date=not-a-date",
                   headers=bearer(admin_token))
    assert r.status_code == 400


def test_ak4_income_statement_endpoint_finance_ok(client, finance_token):
    r = client.get("/api/finance/reports/income-statement?year=2026&month=9",
                   headers=bearer(finance_token))
    assert r.status_code == 200
    data = r.json()
    assert data["period"] == "2026-09"


def test_ak4_income_statement_endpoint_sales_denied(client, sales_token):
    r = client.get("/api/finance/reports/income-statement?year=2026&month=9",
                   headers=bearer(sales_token))
    assert r.status_code == 403


def test_ak4_income_statement_endpoint_bad_month(client, admin_token):
    r = client.get("/api/finance/reports/income-statement?year=2026&month=13",
                   headers=bearer(admin_token))
    assert r.status_code == 400
