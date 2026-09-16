"""
Sprint AK-3: Test Month-End Closing engine + endpoint.
"""
import db
import month_end_closer as mec
from tests.conftest import bearer


# --- Migration 018 ------------------------------------------------------


def test_ak3_month_end_closings_table_exists(client):
    cols = [r["name"] for r in db.query_all("PRAGMA table_info(month_end_closings)")]
    assert "year" in cols
    assert "month" in cols
    assert "closed_at" in cols
    assert "entries_count" in cols
    assert "revenue_realized" in cols
    assert "cogs_recognized" in cols


# --- Service -> COGS mapping -------------------------------------------


def test_ak3_service_type_mapping():
    assert mec._map_service_to_cogs("Tiket Pesawat") == "5101"
    assert mec._map_service_to_cogs("Hotel Mekkah") == "5102"
    assert mec._map_service_to_cogs("Visa Umrah") == "5103"
    assert mec._map_service_to_cogs("Bus Ziarah") == "5104"
    assert mec._map_service_to_cogs("Katering Saudi") == "5105"
    assert mec._map_service_to_cogs("Handling Bandara") == "5106"
    assert mec._map_service_to_cogs("Koper Seragam") == "5107"
    assert mec._map_service_to_cogs("Muthawif") == "5108"
    assert mec._map_service_to_cogs("Random unknown") == "5101"


# --- Month bounds -----------------------------------------------------


def test_ak3_month_bounds():
    assert mec._month_bounds(2026, 2) == ("2026-02-01", "2026-02-28")
    assert mec._month_bounds(2024, 2) == ("2024-02-01", "2024-02-29")
    assert mec._month_bounds(2026, 12) == ("2026-12-01", "2026-12-31")


# --- Test helpers -------------------------------------------------------


def _seed_test_data(package_name, year, month):
    dep = f"{year}-{month:02d}-15"
    pkg_id, _ = db.execute(
        "INSERT INTO packages (name, price, departure_date, quota) "
        "VALUES (?, ?, ?, ?)",
        (package_name, 30_000_000, dep, 40),
    )
    j1, _ = db.execute(
        "INSERT INTO jamaah (name, package_type, total_price, paid_amount, "
        "  payment_status, pipeline_stage) "
        "VALUES ('AK3 Jamaah A', ?, 30000000, 15000000, 'DP', 'Booked')",
        (package_name,),
    )
    j2, _ = db.execute(
        "INSERT INTO jamaah (name, package_type, total_price, paid_amount, "
        "  payment_status, pipeline_stage) "
        "VALUES ('AK3 Jamaah B', ?, 30000000, 30000000, 'Lunas', 'Booked')",
        (package_name,),
    )
    p1, _ = db.execute(
        "INSERT INTO procurement (vendor_name, service_type, total_stock, "
        "  total_price, deposit_paid, package_name, status) "
        "VALUES ('Vendor Hotel Mekkah', 'Hotel Mekkah', 40, 20000000, "
        " 5000000, ?, 'Aktif')",
        (package_name,),
    )
    return pkg_id, [j1, j2], p1


def _cleanup(pkg_id, jamaah_ids, proc_id, closing_id=None):
    if closing_id:
        ids_str = ",".join(str(x) for x in jamaah_ids)
        txs = db.query_all(
            f"SELECT id FROM transactions WHERE type='closing' "
            f"AND (reference_id IN ({ids_str}) OR reference_id = ?)", (proc_id,))
        for t in txs:
            db.execute("DELETE FROM journal_lines WHERE transaction_id = ?",
                       (t["id"],))
        db.execute(
            f"DELETE FROM transactions WHERE type='closing' "
            f"AND (reference_id IN ({ids_str}) OR reference_id = ?)", (proc_id,))
        db.execute("DELETE FROM month_end_closings WHERE id = ?", (closing_id,))
    for jid in jamaah_ids:
        db.execute("DELETE FROM jamaah WHERE id = ?", (jid,))
    db.execute("DELETE FROM procurement WHERE id = ?", (proc_id,))
    db.execute("DELETE FROM packages WHERE id = ?", (pkg_id,))


# --- preview_month ------------------------------------------------------


def test_ak3_preview_shows_counts(client):
    pkg_id, jamaah_ids, proc_id = _seed_test_data("Test AK3 Preview", 2099, 6)
    try:
        prev = mec.preview_month(2099, 6)
        assert prev["jamaah_count"] >= 2
        assert prev["procurement_count"] >= 1
        assert prev["revenue_to_realize"] >= 45_000_000
        assert prev["cogs_to_recognize"] >= 5_000_000
        assert "5102" in prev["cogs_by_account"]
        assert prev["already_closed"] is False
    finally:
        _cleanup(pkg_id, jamaah_ids, proc_id)


def test_ak3_close_month_amortizes(client):
    pkg_id, jamaah_ids, proc_id = _seed_test_data("Test AK3 Close", 2099, 7)
    try:
        result = mec.close_month(2099, 7, closed_by="pytest")
        assert result["jamaah_amortized"] >= 2
        assert result["procurement_recognized"] >= 1
        assert result["revenue_realized"] >= 45_000_000
        assert result["cogs_recognized"] >= 5_000_000

        tx_row = db.query_one(
            "SELECT id FROM transactions "
            "WHERE type='closing' AND category='revenue_realized' AND reference_id = ?",
            (jamaah_ids[0],))
        assert tx_row is not None
        lines = db.query_all(
            "SELECT jl.debit, jl.credit, ca.account_code FROM journal_lines jl "
            "JOIN chart_of_accounts ca ON ca.id = jl.account_id "
            "WHERE jl.transaction_id = ?", (tx_row["id"],))
        codes = {l["account_code"]: l for l in lines}
        assert codes["2101"]["debit"] == 15_000_000
        assert codes["4101"]["credit"] == 15_000_000

        cogs_tx = db.query_one(
            "SELECT id FROM transactions "
            "WHERE type='closing' AND category='cogs_recognized' AND reference_id = ?",
            (proc_id,))
        assert cogs_tx is not None
        cogs_lines = db.query_all(
            "SELECT jl.debit, jl.credit, ca.account_code FROM journal_lines jl "
            "JOIN chart_of_accounts ca ON ca.id = jl.account_id "
            "WHERE jl.transaction_id = ?", (cogs_tx["id"],))
        cogs_codes = {l["account_code"]: l for l in cogs_lines}
        assert cogs_codes["5102"]["debit"] == 5_000_000
        assert cogs_codes["1108"]["credit"] == 5_000_000

        _cleanup(pkg_id, jamaah_ids, proc_id, closing_id=result["closing_id"])
    except Exception:
        _cleanup(pkg_id, jamaah_ids, proc_id)
        raise


def test_ak3_close_month_idempotent(client):
    pkg_id, jamaah_ids, proc_id = _seed_test_data("Test AK3 Idempotent", 2099, 8)
    closing_id = None
    try:
        result = mec.close_month(2099, 8, closed_by="pytest")
        closing_id = result["closing_id"]
        try:
            mec.close_month(2099, 8, closed_by="pytest")
            assert False, "Harusnya raise ValueError"
        except ValueError as e:
            assert "sudah ditutup" in str(e)
    finally:
        _cleanup(pkg_id, jamaah_ids, proc_id, closing_id=closing_id)


def test_ak3_close_month_zero_sum(client):
    pkg_id, jamaah_ids, proc_id = _seed_test_data("Test AK3 ZeroSum", 2099, 9)
    try:
        result = mec.close_month(2099, 9, closed_by="pytest")
        unbalanced = db.query_all(
            "SELECT jl.transaction_id FROM journal_lines jl "
            "JOIN transactions t ON t.id = jl.transaction_id "
            "WHERE t.type = 'closing' "
            "GROUP BY jl.transaction_id "
            "HAVING SUM(jl.debit) != SUM(jl.credit)"
        )
        assert len(unbalanced) == 0
        _cleanup(pkg_id, jamaah_ids, proc_id, closing_id=result["closing_id"])
    except Exception:
        _cleanup(pkg_id, jamaah_ids, proc_id)
        raise


def test_ak3_invalid_month():
    try:
        mec.preview_month(2026, 13)
        assert False
    except ValueError:
        pass
    try:
        mec.close_month(2026, 0)
        assert False
    except ValueError:
        pass


# --- HTTP endpoints ---------------------------------------------------


def test_ak3_preview_endpoint_finance_ok(client, finance_token):
    r = client.get("/api/finance/close-month/preview?year=2099&month=10",
                   headers=bearer(finance_token))
    assert r.status_code == 200
    data = r.json()
    assert "jamaah_count" in data
    assert "cogs_by_account" in data


def test_ak3_preview_endpoint_sales_denied(client, sales_token):
    r = client.get("/api/finance/close-month/preview?year=2099&month=10",
                   headers=bearer(sales_token))
    assert r.status_code == 403


def test_ak3_close_endpoint_finance_denied(client, finance_token):
    r = client.post("/api/finance/close-month",
                    headers=bearer(finance_token),
                    json={"year": 2099, "month": 11})
    assert r.status_code == 403


def test_ak3_close_endpoint_admin_ok(client, admin_token):
    pkg_id, jamaah_ids, proc_id = _seed_test_data("Test AK3 HTTP", 2099, 11)
    closing_id = None
    try:
        r = client.post("/api/finance/close-month",
                        headers=bearer(admin_token),
                        json={"year": 2099, "month": 11, "notes": "test HTTP"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["jamaah_amortized"] >= 2
        closing_id = data["closing_id"]
    finally:
        _cleanup(pkg_id, jamaah_ids, proc_id, closing_id=closing_id)


def test_ak3_history_endpoint(client, finance_token):
    r = client.get("/api/finance/close-month/history?limit=5",
                   headers=bearer(finance_token))
    assert r.status_code == 200
    assert "closings" in r.json()
