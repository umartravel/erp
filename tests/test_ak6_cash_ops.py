"""
Sprint AK-6: Test setor tunai 2-leg + prive pemilik.
"""
import db
from tests.conftest import bearer


def _cleanup_tx(tx_ids):
    for t in tx_ids:
        db.execute("DELETE FROM journal_lines WHERE transaction_id = ?", (t,))
        db.execute("DELETE FROM transactions WHERE id = ?", (t,))


def _seed_kas_kecil(amount):
    """Seed opening balance 1101 Kas Kecil dgn contra 3101 Modal Awal.
    Diperlukan sejak audit 2026-09-20: setor_tunai_create sekarang cek
    saldo sufficient sebelum accept. Return tx_id utk cleanup.
    """
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description, status) "
        "VALUES ('opening','seed_kas_kecil',?,'Test seed opening balance','POSTED')",
        (amount,),
    )
    kas = db.query_one("SELECT id FROM chart_of_accounts WHERE account_code = '1101'")
    equity = db.query_one("SELECT id FROM chart_of_accounts WHERE account_code = '3101'")
    db.execute(
        "INSERT INTO journal_lines (transaction_id, account_id, debit, credit, memo) "
        "VALUES (?, ?, ?, 0, 'Seed opening Kas Kecil')",
        (tx_id, kas["id"], amount),
    )
    db.execute(
        "INSERT INTO journal_lines (transaction_id, account_id, debit, credit, memo) "
        "VALUES (?, ?, 0, ?, 'Contra Modal Awal')",
        (tx_id, equity["id"], amount),
    )
    return tx_id


def test_ak6_setor_tunai_leg1_ok(client, finance_token):
    seed_id = _seed_kas_kecil(5000000)
    r = client.post("/api/finance/setor-tunai",
                    headers=bearer(finance_token),
                    json={"amount": 5000000, "to_bank": "1102",
                          "note": "AK6 test setor"})
    assert r.status_code == 200, r.text
    data = r.json()
    tx_id = data["tx_id"]
    assert data["amount"] == 5000000

    lines = db.query_all(
        "SELECT jl.debit, jl.credit, ca.account_code FROM journal_lines jl "
        "JOIN chart_of_accounts ca ON ca.id = jl.account_id "
        "WHERE jl.transaction_id = ?", (tx_id,))
    codes = {l["account_code"]: l for l in lines}
    assert codes["1109"]["debit"] == 5000000
    assert codes["1101"]["credit"] == 5000000
    _cleanup_tx([tx_id, seed_id])


def test_ak6_setor_tunai_insufficient_kas_kecil(client, finance_token):
    """Audit 2026-09-20: setor tunai HARUS ditolak kalau saldo Kas Kecil
    (1101) tidak cukup -- prevent negative cash (physically impossible)."""
    seed_id = _seed_kas_kecil(1000000)
    r = client.post("/api/finance/setor-tunai",
                    headers=bearer(finance_token),
                    json={"amount": 5000000, "to_bank": "1102"})
    assert r.status_code == 400, r.text
    assert "kas kecil" in r.json()["error"].lower()
    _cleanup_tx([seed_id])


def test_ak6_setor_tunai_leg1_sales_denied(client, sales_token):
    r = client.post("/api/finance/setor-tunai",
                    headers=bearer(sales_token),
                    json={"amount": 1000, "to_bank": "1102"})
    assert r.status_code == 403


def test_ak6_setor_tunai_invalid_bank(client, finance_token):
    r = client.post("/api/finance/setor-tunai",
                    headers=bearer(finance_token),
                    json={"amount": 1000, "to_bank": "9999"})
    assert r.status_code == 400


def test_ak6_setor_tunai_zero_amount(client, finance_token):
    r = client.post("/api/finance/setor-tunai",
                    headers=bearer(finance_token),
                    json={"amount": 0, "to_bank": "1102"})
    assert r.status_code == 400


def test_ak6_setor_tunai_confirm_ok(client, finance_token):
    seed_id = _seed_kas_kecil(3000000)
    r = client.post("/api/finance/setor-tunai",
                    headers=bearer(finance_token),
                    json={"amount": 3000000, "to_bank": "1102"})
    leg1_id = r.json()["tx_id"]

    r = client.post(f"/api/finance/setor-tunai/{leg1_id}/confirm",
                    headers=bearer(finance_token),
                    json={"to_bank": "1102"})
    assert r.status_code == 200
    leg2_id = r.json()["leg2_tx_id"]

    lines = db.query_all(
        "SELECT jl.debit, jl.credit, ca.account_code FROM journal_lines jl "
        "JOIN chart_of_accounts ca ON ca.id = jl.account_id "
        "WHERE jl.transaction_id = ?", (leg2_id,))
    codes = {l["account_code"]: l for l in lines}
    assert codes["1102"]["debit"] == 3000000
    assert codes["1109"]["credit"] == 3000000
    _cleanup_tx([leg1_id, leg2_id, seed_id])


def test_ak6_setor_tunai_confirm_double_rejected(client, finance_token):
    seed_id = _seed_kas_kecil(500000)
    r = client.post("/api/finance/setor-tunai",
                    headers=bearer(finance_token),
                    json={"amount": 500000, "to_bank": "1102"})
    leg1_id = r.json()["tx_id"]

    r = client.post(f"/api/finance/setor-tunai/{leg1_id}/confirm",
                    headers=bearer(finance_token),
                    json={"to_bank": "1102"})
    leg2_id = r.json()["leg2_tx_id"]

    r = client.post(f"/api/finance/setor-tunai/{leg1_id}/confirm",
                    headers=bearer(finance_token),
                    json={"to_bank": "1102"})
    assert r.status_code == 400
    assert "sudah dikonfirmasi" in r.json()["error"].lower()
    _cleanup_tx([leg1_id, leg2_id, seed_id])


def test_ak6_setor_tunai_confirm_wrong_category(client, finance_token):
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description, status) "
        "VALUES ('income','payment',100,'AK6 wrong cat','POSTED')", (),
    )
    r = client.post(f"/api/finance/setor-tunai/{tx_id}/confirm",
                    headers=bearer(finance_token),
                    json={"to_bank": "1102"})
    assert r.status_code == 400
    db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))


def test_ak6_setor_tunai_pending_list(client, finance_token):
    seed_id = _seed_kas_kecil(1500000)
    r = client.post("/api/finance/setor-tunai",
                    headers=bearer(finance_token),
                    json={"amount": 1500000, "to_bank": "1102",
                          "note": "AK6 pending test"})
    leg1_id = r.json()["tx_id"]

    r = client.get("/api/finance/setor-tunai/pending",
                   headers=bearer(finance_token))
    assert r.status_code == 200
    data = r.json()
    pending_ids = [p["id"] for p in data["pending"]]
    assert leg1_id in pending_ids
    _cleanup_tx([leg1_id, seed_id])


def test_ak6_prive_ok(client, admin_token):
    r = client.post("/api/finance/prive",
                    headers=bearer(admin_token),
                    json={"amount": 10000000, "from_bank": "1102",
                          "note": "AK6 prive test Sep 2026"})
    assert r.status_code == 200
    tx_id = r.json()["tx_id"]

    lines = db.query_all(
        "SELECT jl.debit, jl.credit, ca.account_code FROM journal_lines jl "
        "JOIN chart_of_accounts ca ON ca.id = jl.account_id "
        "WHERE jl.transaction_id = ?", (tx_id,))
    codes = {l["account_code"]: l for l in lines}
    assert codes["3102"]["debit"] == 10000000
    assert codes["1102"]["credit"] == 10000000
    _cleanup_tx([tx_id])


def test_ak6_prive_finance_denied(client, finance_token):
    r = client.post("/api/finance/prive",
                    headers=bearer(finance_token),
                    json={"amount": 1000, "note": "hello"})
    assert r.status_code == 403


def test_ak6_prive_sales_denied(client, sales_token):
    r = client.post("/api/finance/prive",
                    headers=bearer(sales_token),
                    json={"amount": 1000, "note": "hello"})
    assert r.status_code == 403


def test_ak6_prive_management_ok(client, management_token):
    r = client.post("/api/finance/prive",
                    headers=bearer(management_token),
                    json={"amount": 2000000, "note": "AK6 mgmt prive"})
    assert r.status_code == 200
    _cleanup_tx([r.json()["tx_id"]])


def test_ak6_prive_note_required(client, admin_token):
    r = client.post("/api/finance/prive",
                    headers=bearer(admin_token),
                    json={"amount": 500000, "note": ""})
    assert r.status_code == 400


def test_ak6_prive_history(client, admin_token, finance_token):
    r = client.post("/api/finance/prive",
                    headers=bearer(admin_token),
                    json={"amount": 750000, "note": "AK6 history seed"})
    tx_id = r.json()["tx_id"]

    r = client.get("/api/finance/prive/history?limit=5",
                   headers=bearer(finance_token))
    assert r.status_code == 200
    data = r.json()
    assert "prive" in data
    tx_ids = [p["id"] for p in data["prive"]]
    assert tx_id in tx_ids
    _cleanup_tx([tx_id])


def test_ak6_setor_tunai_full_flow_balance_ok(client, finance_token):
    """Setor + confirm = Neraca balanced sepanjang flow."""
    import datetime
    import financial_reports as fr
    today = datetime.date.today().isoformat()

    seed_id = _seed_kas_kecil(999000)
    bs_before = fr.build_balance_sheet(today)
    assert bs_before["balanced"] is True

    r = client.post("/api/finance/setor-tunai",
                    headers=bearer(finance_token),
                    json={"amount": 999000, "to_bank": "1102"})
    leg1_id = r.json()["tx_id"]
    bs_mid = fr.build_balance_sheet(today)
    assert bs_mid["balanced"] is True

    r = client.post(f"/api/finance/setor-tunai/{leg1_id}/confirm",
                    headers=bearer(finance_token),
                    json={"to_bank": "1102"})
    leg2_id = r.json()["leg2_tx_id"]
    bs_after = fr.build_balance_sheet(today)
    assert bs_after["balanced"] is True

    _cleanup_tx([leg1_id, leg2_id, seed_id])
