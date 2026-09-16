"""
Sprint AK-2: Test suite jurnal engine (double-entry).

Cakupan:
1. post_journal: zero-sum invariant + fail-fast + POSTED status.
2. reverse_journal: swap Dr<->Cr + REVERSED status + immutable.
3. Wrapper flow: post_jamaah_dp / refund / expense / procurement / payroll /
   commission / prive.
4. Backfill 017: memastikan tx historis pre-AK2 di-cover.
"""
import db
import journal_engine as je
from tests.conftest import bearer


# --- post_journal --------------------------------------------------------


def test_ak2_post_journal_zero_sum_pass(client, admin_token):
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('income','test',100000,'AK2 test post_journal balanced')", (),
    )
    kas_id = je.coa_id("1102")
    unearned_id = je.coa_id("2101")
    je.post_journal(tx_id, [
        (kas_id, 100000, 0, "test dr"),
        (unearned_id, 0, 100000, "test cr"),
    ])
    lines = db.query_all(
        "SELECT debit, credit FROM journal_lines WHERE transaction_id = ?", (tx_id,))
    assert len(lines) == 2
    assert sum(l["debit"] for l in lines) == 100000
    assert sum(l["credit"] for l in lines) == 100000
    db.execute("DELETE FROM journal_lines WHERE transaction_id = ?", (tx_id,))
    db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))


def test_ak2_post_journal_unbalanced_rejected(client):
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('income','test',999,'unbalanced test')", (),
    )
    kas_id = je.coa_id("1102")
    unearned_id = je.coa_id("2101")
    try:
        je.post_journal(tx_id, [
            (kas_id, 100000, 0, "dr"),
            (unearned_id, 0, 99999, "cr - off by 1"),
        ])
        assert False, "Harusnya raise ValueError"
    except ValueError as e:
        assert "ZERO-SUM GAGAL" in str(e)
    lines = db.query_all(
        "SELECT id FROM journal_lines WHERE transaction_id = ?", (tx_id,))
    assert len(lines) == 0
    db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))


def test_ak2_post_journal_debit_and_credit_same_line_rejected(client):
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('income','test',1,'d+c same')", (),
    )
    kas_id = je.coa_id("1102")
    try:
        je.post_journal(tx_id, [
            (kas_id, 100, 100, "invalid"),
            (je.coa_id("2101"), 0, 0, "empty"),
        ])
        assert False, "Harusnya raise"
    except ValueError:
        pass
    db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))


def test_ak2_post_journal_empty_rejected(client):
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('income','test',1,'empty')", (),
    )
    try:
        je.post_journal(tx_id, [])
        assert False
    except ValueError as e:
        assert "wajib" in str(e).lower()
    db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))


def test_ak2_post_journal_sets_status_posted(client):
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('income','test',5000,'status set test')", (),
    )
    db.execute("UPDATE transactions SET status = NULL WHERE id = ?", (tx_id,))
    je.post_journal(tx_id, [
        (je.coa_id("1102"), 5000, 0, "dr"),
        (je.coa_id("2101"), 0, 5000, "cr"),
    ])
    row = db.query_one("SELECT status FROM transactions WHERE id = ?", (tx_id,))
    assert row["status"] == "POSTED"
    db.execute("DELETE FROM journal_lines WHERE transaction_id = ?", (tx_id,))
    db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))


# --- Wrappers per flow --------------------------------------------------


def test_ak2_wrapper_jamaah_dp(client):
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('income','payment',7500000,'AK2 wrapper DP test')", (),
    )
    je.post_jamaah_dp(tx_id, 7500000, "Testi Jamaah")
    lines = db.query_all(
        "SELECT jl.debit, jl.credit, ca.account_code FROM journal_lines jl "
        "JOIN chart_of_accounts ca ON ca.id = jl.account_id "
        "WHERE jl.transaction_id = ? ORDER BY ca.account_code", (tx_id,))
    assert len(lines) == 2
    codes = {l["account_code"]: l for l in lines}
    assert codes["1102"]["debit"] == 7500000
    assert codes["2101"]["credit"] == 7500000
    db.execute("DELETE FROM journal_lines WHERE transaction_id = ?", (tx_id,))
    db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))


def test_ak2_wrapper_jamaah_refund(client):
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('expense','refund',2000000,'AK2 wrapper refund')", (),
    )
    je.post_jamaah_refund(tx_id, 2000000, "Testi Jamaah")
    lines = db.query_all(
        "SELECT jl.debit, jl.credit, ca.account_code FROM journal_lines jl "
        "JOIN chart_of_accounts ca ON ca.id = jl.account_id "
        "WHERE jl.transaction_id = ?", (tx_id,))
    codes = {l["account_code"]: l for l in lines}
    assert codes["2101"]["debit"] == 2000000
    assert codes["1102"]["credit"] == 2000000
    db.execute("DELETE FROM journal_lines WHERE transaction_id = ?", (tx_id,))
    db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))


def test_ak2_wrapper_expense_paid_with_fallback(client):
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('expense','expense_report',100000,'AK2 expense fallback')", (),
    )
    je.post_expense_paid(tx_id, 100000, None, "Test fallback")
    lines = db.query_all(
        "SELECT jl.debit, jl.credit, ca.account_code FROM journal_lines jl "
        "JOIN chart_of_accounts ca ON ca.id = jl.account_id "
        "WHERE jl.transaction_id = ?", (tx_id,))
    codes = {l["account_code"]: l for l in lines}
    assert codes["6201"]["debit"] == 100000
    assert codes["1102"]["credit"] == 100000
    db.execute("DELETE FROM journal_lines WHERE transaction_id = ?", (tx_id,))
    db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))


def test_ak2_wrapper_procurement_prepaid_vs_cogs(client):
    tx1, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('expense','procurement_payment',5000000,'proc future')", (),
    )
    je.post_procurement_paid(tx1, 5000000, "Vendor A",
                             departure_date="2099-12-31",
                             today_iso="2026-09-16")
    lines1 = db.query_all(
        "SELECT jl.debit, ca.account_code FROM journal_lines jl "
        "JOIN chart_of_accounts ca ON ca.id = jl.account_id "
        "WHERE jl.transaction_id = ? AND jl.debit > 0", (tx1,))
    assert lines1[0]["account_code"] == "1108"

    tx2, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('expense','procurement_payment',3000000,'proc past')", (),
    )
    je.post_procurement_paid(tx2, 3000000, "Vendor B",
                             departure_date="2020-01-01",
                             today_iso="2026-09-16")
    lines2 = db.query_all(
        "SELECT jl.debit, ca.account_code FROM journal_lines jl "
        "JOIN chart_of_accounts ca ON ca.id = jl.account_id "
        "WHERE jl.transaction_id = ? AND jl.debit > 0", (tx2,))
    assert lines2[0]["account_code"] == "5101"

    for t in (tx1, tx2):
        db.execute("DELETE FROM journal_lines WHERE transaction_id = ?", (t,))
        db.execute("DELETE FROM transactions WHERE id = ?", (t,))


def test_ak2_wrapper_payroll(client):
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('expense','payroll',6000000,'AK2 payroll')", (),
    )
    je.post_payroll(tx_id, 6000000, "Karyawan A")
    lines = db.query_all(
        "SELECT jl.debit, jl.credit, ca.account_code FROM journal_lines jl "
        "JOIN chart_of_accounts ca ON ca.id = jl.account_id "
        "WHERE jl.transaction_id = ?", (tx_id,))
    codes = {l["account_code"]: l for l in lines}
    assert codes["6101"]["debit"] == 6000000
    assert codes["1102"]["credit"] == 6000000
    db.execute("DELETE FROM journal_lines WHERE transaction_id = ?", (tx_id,))
    db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))


def test_ak2_wrapper_commission(client):
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('expense','commission',500000,'AK2 komisi')", (),
    )
    je.post_commission(tx_id, 500000, "Agen X", "Jamaah Y")
    lines = db.query_all(
        "SELECT ca.account_code FROM journal_lines jl "
        "JOIN chart_of_accounts ca ON ca.id = jl.account_id "
        "WHERE jl.transaction_id = ? AND jl.debit > 0", (tx_id,))
    assert lines[0]["account_code"] == "6102"
    db.execute("DELETE FROM journal_lines WHERE transaction_id = ?", (tx_id,))
    db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))


def test_ak2_wrapper_prive(client):
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('expense','prive',10000000,'AK2 prive')", (),
    )
    je.post_prive(tx_id, 10000000, "Prive pemilik Sep 2026")
    lines = db.query_all(
        "SELECT ca.account_code FROM journal_lines jl "
        "JOIN chart_of_accounts ca ON ca.id = jl.account_id "
        "WHERE jl.transaction_id = ? AND jl.debit > 0", (tx_id,))
    assert lines[0]["account_code"] == "3102"
    db.execute("DELETE FROM journal_lines WHERE transaction_id = ?", (tx_id,))
    db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))


# --- reverse_journal ----------------------------------------------------


def test_ak2_reverse_journal_creates_swapped_entry(client):
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('income','payment',3000000,'AK2 reverse test')", (),
    )
    je.post_jamaah_dp(tx_id, 3000000, "Reverse Test")

    new_tx = je.reverse_journal(tx_id, "Test reversal")
    row = db.query_one("SELECT status FROM transactions WHERE id = ?", (tx_id,))
    assert row["status"] == "REVERSED"

    new_row = db.query_one(
        "SELECT status, reversal_of FROM transactions WHERE id = ?", (new_tx,))
    assert new_row["status"] == "REVERSED"
    assert new_row["reversal_of"] == tx_id

    lines = db.query_all(
        "SELECT jl.debit, jl.credit, ca.account_code FROM journal_lines jl "
        "JOIN chart_of_accounts ca ON ca.id = jl.account_id "
        "WHERE jl.transaction_id = ?", (new_tx,))
    codes = {l["account_code"]: l for l in lines}
    assert codes["2101"]["debit"] == 3000000
    assert codes["1102"]["credit"] == 3000000
    for t in (tx_id, new_tx):
        db.execute("DELETE FROM journal_lines WHERE transaction_id = ?", (t,))
        db.execute("DELETE FROM transactions WHERE id = ?", (t,))


def test_ak2_reverse_journal_double_reverse_rejected(client):
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('income','payment',1000000,'AK2 double reverse test')", (),
    )
    je.post_jamaah_dp(tx_id, 1000000, "Double Rev")
    new_tx = je.reverse_journal(tx_id, "First reversal")
    try:
        je.reverse_journal(tx_id, "Second attempt")
        assert False, "Harusnya raise"
    except ValueError as e:
        assert "sudah di-reverse" in str(e) or "REVERSED" in str(e)
    for t in (tx_id, new_tx):
        db.execute("DELETE FROM journal_lines WHERE transaction_id = ?", (t,))
        db.execute("DELETE FROM transactions WHERE id = ?", (t,))


# --- Migration 017 backfill (idempotent) --------------------------------


def test_ak2_migration_017_zero_sum_invariant(client):
    """Setelah migration 017 jalan, semua tx yg punya journal_lines balanced."""
    unbalanced = db.query_all(
        "SELECT transaction_id, SUM(debit) d, SUM(credit) c "
        "FROM journal_lines GROUP BY transaction_id "
        "HAVING SUM(debit) != SUM(credit)"
    )
    assert len(unbalanced) == 0, \
        f"Setelah backfill, {len(unbalanced)} tx unbalanced: {unbalanced[:3]}"
