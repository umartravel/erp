"""
Phase 9c: Cashflow negatif alert.

Uji:
- POST /api/finance/cashflow-alert/check
- Saldo cukup + net > 0 -> alerted=False, reason=cashflow_ok
- Saldo kecil + vendor payment besar segera -> alerted=True (min_balance <= 0)
- Panggil 2x -> dedupe (reason=already_notified_today)
- Notif dikirim ke role finance + management (bell counter naik)
- RBAC: sales 403
"""
import datetime

from tests.conftest import bearer

import db


def _clear_cashflow_test_state():
    """Reset kondisi test dgn hapus notif cashflow + tx test + vendor test."""
    db.execute("DELETE FROM user_notifications WHERE kind LIKE 'cashflow_negatif_%'")
    db.execute("DELETE FROM transactions WHERE description LIKE '%CASHFLOW TEST%'")
    db.execute("DELETE FROM vendor_bookings WHERE vendor_name LIKE 'CASHFLOW TEST%'")


def test_cashflow_alert_response_shape(client, admin_token):
    """Endpoint respond dgn struktur benar (summary + alerted + kind/reason)."""
    _clear_cashflow_test_state()
    r = client.post("/api/finance/cashflow-alert/check", headers=bearer(admin_token))
    assert r.status_code == 200
    d = r.json()
    assert "alerted" in d
    assert isinstance(d["alerted"], bool)
    assert "summary" in d
    assert "net_30d" in d["summary"]
    assert "min_balance" in d["summary"]
    assert "min_balance_date" in d["summary"]


def test_cashflow_negatif_triggers_alert(
        client, admin_token, finance_token, management_token):
    """Vendor huge out due tomorrow + saldo minim -> min_balance <= 0 -> alert."""
    _clear_cashflow_test_state()
    db.execute("DELETE FROM transactions WHERE description LIKE 'CASHFLOW TEST%'")
    db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('income', 'test', ?, 'CASHFLOW TEST saldo kecil')",
        (100_000,),
    )
    pkg = client.get("/api/packages", headers=bearer(admin_token)).json()[0]
    due = (datetime.date.today() + datetime.timedelta(days=5)).strftime("%Y-%m-%d")
    db.execute(
        "INSERT INTO vendor_bookings (package_id, vendor_type, vendor_name, "
        "status, deposit_amount, total_amount, due_date, created_by) "
        "VALUES (?, 'hotel_mekkah', 'CASHFLOW TEST Vendor', 'Booked', 0, ?, ?, 'test')",
        (pkg["id"], 500_000_000, due),
    )

    before_fin = client.get("/api/notifications/count",
                            headers=bearer(finance_token)).json()["unread"]
    before_mgr = client.get("/api/notifications/count",
                            headers=bearer(management_token)).json()["unread"]

    r = client.post("/api/finance/cashflow-alert/check", headers=bearer(admin_token))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["alerted"] is True, f"Expected alerted, got: {d}"
    assert d["summary"]["min_balance"] <= 0

    after_fin = client.get("/api/notifications/count",
                           headers=bearer(finance_token)).json()["unread"]
    after_mgr = client.get("/api/notifications/count",
                           headers=bearer(management_token)).json()["unread"]
    assert after_fin > before_fin, "Finance belum dpt notif cashflow"
    assert after_mgr > before_mgr, "Management belum dpt notif cashflow"


def test_cashflow_alert_dedupes_same_day(client, admin_token):
    """Call 2x -> kedua reason=already_notified_today."""
    _clear_cashflow_test_state()
    db.execute(
        "INSERT INTO transactions (type, category, amount, description) "
        "VALUES ('income', 'test', ?, 'CASHFLOW TEST dedup')",
        (100_000,),
    )
    pkg = client.get("/api/packages", headers=bearer(admin_token)).json()[0]
    due = (datetime.date.today() + datetime.timedelta(days=3)).strftime("%Y-%m-%d")
    db.execute(
        "INSERT INTO vendor_bookings (package_id, vendor_type, vendor_name, "
        "status, deposit_amount, total_amount, due_date, created_by) "
        "VALUES (?, 'hotel_madinah', 'CASHFLOW TEST Dedup', 'Booked', 0, ?, ?, 'test')",
        (pkg["id"], 500_000_000, due),
    )
    r1 = client.post("/api/finance/cashflow-alert/check", headers=bearer(admin_token))
    assert r1.json()["alerted"] is True

    r2 = client.post("/api/finance/cashflow-alert/check", headers=bearer(admin_token))
    d2 = r2.json()
    assert d2["alerted"] is False
    assert d2.get("reason") == "already_notified_today"


def test_cashflow_alert_rbac_denies_sales(client, sales_token):
    r = client.post("/api/finance/cashflow-alert/check", headers=bearer(sales_token))
    assert r.status_code == 403


def test_cashflow_alert_ops_denied(client, ops_token):
    """Ops tidak akses cashflow alert."""
    r = client.post("/api/finance/cashflow-alert/check", headers=bearer(ops_token))
    assert r.status_code == 403
