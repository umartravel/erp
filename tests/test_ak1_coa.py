"""
Sprint AK-1: Test Chart of Accounts seed + endpoint + schema extend.
"""
import db
from tests.conftest import bearer


# --- Migration 015 --------------------------------------------------------


def test_ak1_coa_table_exists(client):
    cols = [r["name"] for r in db.query_all("PRAGMA table_info(chart_of_accounts)")]
    assert "account_code" in cols
    assert "account_name" in cols
    assert "account_group" in cols
    assert "normal_balance" in cols
    assert "parent_id" in cols
    assert "is_active" in cols


def test_ak1_coa_seed_count(client):
    total = db.query_one("SELECT COUNT(*) c FROM chart_of_accounts")["c"]
    assert total >= 34, f"Expected >= 34 COA seeded, got {total}"


def test_ak1_coa_key_accounts_present(client):
    expected = {
        "1101": ("Kas Kecil Kantor", "ASSET", "DEBIT"),
        "1102": ("Bank Mandiri", "ASSET", "DEBIT"),
        "1108": ("Beban Dibayar Dimuka - Umrah", "ASSET", "DEBIT"),
        "1109": ("Kas Kliring / Transit Antar-Akun", "ASSET", "DEBIT"),
        "2101": ("Pendapatan Diterima Dimuka - Umrah", "LIABILITY", "CREDIT"),
        "3102": ("Prive Pemilik Usaha (Owner Draw)", "EQUITY", "DEBIT"),
        "4101": ("Pendapatan Paket Umrah Reguler", "REVENUE", "CREDIT"),
        "5101": ("HPP - Tiket Penerbangan Internasional", "EXPENSE", "DEBIT"),
        "6101": ("Beban Gaji Staf & Manajemen", "EXPENSE", "DEBIT"),
        "6202": ("Kerugian / (Keuntungan) Selisih Kurs", "EXPENSE", "DEBIT"),
    }
    for code, (name, grp, nb) in expected.items():
        row = db.query_one(
            "SELECT account_name, account_group, normal_balance "
            "FROM chart_of_accounts WHERE account_code = ?", (code,))
        assert row is not None, f"COA {code} tidak ada di seed"
        assert row["account_name"] == name
        assert row["account_group"] == grp
        assert row["normal_balance"] == nb


def test_ak1_expense_categories_extended(client):
    cols = [r["name"] for r in db.query_all("PRAGMA table_info(expense_categories)")]
    assert "default_account_id" in cols
    assert "report_type" in cols


def test_ak1_report_type_backfill(client):
    parents = db.query_all(
        "SELECT id, name, report_type FROM expense_categories "
        "WHERE parent_id IS NULL"
    )
    for p in parents:
        assert p["report_type"] == "PROFIT_LOSS", \
            f"Parent {p['name']} report_type = {p['report_type']}"


def test_ak1_subcategory_default_account_mapped(client):
    sub = db.query_one(
        "SELECT c.default_account_id, ca.account_code "
        "FROM expense_categories c "
        "LEFT JOIN chart_of_accounts ca ON ca.id = c.default_account_id "
        "WHERE c.name = 'Hotel Mekkah' AND c.parent_id IS NOT NULL"
    )
    assert sub is not None
    assert sub["account_code"] == "5102"


# --- Migration 016 --------------------------------------------------------


def test_ak1_journal_lines_table_exists(client):
    cols = [r["name"] for r in db.query_all("PRAGMA table_info(journal_lines)")]
    assert "transaction_id" in cols
    assert "account_id" in cols
    assert "debit" in cols
    assert "credit" in cols
    assert "memo" in cols


def test_ak1_transactions_status_column(client):
    cols = [r["name"] for r in db.query_all("PRAGMA table_info(transactions)")]
    assert "status" in cols
    assert "transaction_no" in cols
    assert "reversal_of" in cols


def test_ak1_transactions_backfill_status(client):
    unset = db.query_one(
        "SELECT COUNT(*) c FROM transactions WHERE status IS NULL"
    )["c"]
    assert unset == 0, f"{unset} row masih status NULL setelah migration"


# --- API endpoints --------------------------------------------------------


def test_ak1_get_coa_semua_role(client, sales_token, finance_token, admin_token):
    for tok in [sales_token, finance_token, admin_token]:
        r = client.get("/api/coa", headers=bearer(tok))
        assert r.status_code == 200
        data = r.json()
        assert "accounts" in data
        assert len(data["accounts"]) >= 34


def test_ak1_get_coa_tree(client, admin_token):
    r = client.get("/api/coa/tree", headers=bearer(admin_token))
    assert r.status_code == 200
    tree = r.json()["groups"]
    for grp in ("ASSET", "LIABILITY", "EQUITY", "REVENUE", "EXPENSE"):
        assert grp in tree
        assert len(tree[grp]) >= 1


def test_ak1_coa_create_admin_only(client, admin_token, finance_token):
    payload = {
        "account_code": "9999",
        "account_name": "Test Account AK1",
        "account_group": "EXPENSE",
        "normal_balance": "DEBIT",
    }
    r = client.post("/api/coa", json=payload, headers=bearer(finance_token))
    assert r.status_code == 403, f"finance should be denied: {r.text}"

    r = client.post("/api/coa", json=payload, headers=bearer(admin_token))
    assert r.status_code == 200
    r = client.post("/api/coa", json=payload, headers=bearer(admin_token))
    assert r.status_code == 400


def test_ak1_coa_create_validates_group(client, admin_token):
    r = client.post("/api/coa",
                    json={"account_code": "9998", "account_name": "X",
                          "account_group": "INVALID", "normal_balance": "DEBIT"},
                    headers=bearer(admin_token))
    assert r.status_code == 400


def test_ak1_coa_soft_delete(client, admin_token):
    row = db.query_one("SELECT id FROM chart_of_accounts WHERE account_code = '9999'")
    if not row:
        client.post("/api/coa",
                    json={"account_code": "9999", "account_name": "Test AK1",
                          "account_group": "EXPENSE", "normal_balance": "DEBIT"},
                    headers=bearer(admin_token))
        row = db.query_one("SELECT id FROM chart_of_accounts WHERE account_code = '9999'")
    aid = row["id"]
    r = client.delete(f"/api/coa/{aid}", headers=bearer(admin_token))
    assert r.status_code == 200
    after = db.query_one("SELECT is_active FROM chart_of_accounts WHERE id = ?", (aid,))
    assert after["is_active"] == 0


def test_ak1_journal_by_tx_empty_ok(client, finance_token):
    import pytest
    tx = db.query_one("SELECT id FROM transactions LIMIT 1")
    if not tx:
        pytest.skip("No transactions in test DB")
    r = client.get(f"/api/journal/{tx['id']}", headers=bearer(finance_token))
    assert r.status_code == 200
    data = r.json()
    assert "lines" in data
    assert data["total_debit"] == 0
    assert data["total_credit"] == 0
    assert data["balanced"] is True


def test_ak1_journal_by_tx_404(client, finance_token):
    r = client.get("/api/journal/999999", headers=bearer(finance_token))
    assert r.status_code == 404


def test_ak1_journal_denied_for_sales(client, sales_token):
    r = client.get("/api/journal/1", headers=bearer(sales_token))
    assert r.status_code == 403
