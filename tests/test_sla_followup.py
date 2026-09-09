"""
Phase 9b: SLA Follow-up Lead -- notif ke sales owner ketika ada jamaah stale.

Uji:
- POST /api/sales/sla-followup/check
- Sales dgn 0 jamaah stale -> stale_count=0 tidak notif
- Sales dgn > 0 jamaah stale -> notif dikirim ke user itu
- Call 2x -> dedupe (notified=False dgn reason=already_notified_today)
- Admin/mgmt dapat count preview tanpa notif
- Role finance/ops 403
"""
import datetime

from tests.conftest import bearer

import db


_NIK_COUNTER = [500]


def _seed_stale_jamaah(pkg_name, sales_id, days_since_contact=5):
    """Insert jamaah langsung ke DB dgn last_contact di masa lalu."""
    _NIK_COUNTER[0] += 1
    nik = f"9997{_NIK_COUNTER[0]:012d}"[:16]
    ts = (datetime.datetime.now() - datetime.timedelta(days=days_since_contact)
          ).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "INSERT INTO jamaah (nik, name, phone, package_type, total_price, "
        "status, sales_id, last_contact) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (nik, f"SLA Test {_NIK_COUNTER[0]}", f"0812{_NIK_COUNTER[0]:010d}"[:14],
         pkg_name, 30_000_000, "Terdaftar", sales_id, ts),
    )


def _sales_user_id(username):
    r = db.query_one("SELECT id FROM users WHERE username = ?", (username,))
    return r["id"] if r else None


def _cleanup_notif_today(user_id):
    db.execute(
        "DELETE FROM user_notifications WHERE user_id = ? AND kind LIKE 'sla_followup_alert_%'",
        (user_id,),
    )


def test_check_no_stale_no_notif(client, sales_token, admin_token):
    """Kalau sales tidak punya jamaah stale -> stale_count=0, notified=False."""
    sid = _sales_user_id("sales1")
    _cleanup_notif_today(sid)
    # Bersihkan jamaah stale existing utk sales1 (set last_contact = now)
    db.execute(
        "UPDATE jamaah SET last_contact = datetime('now') "
        "WHERE sales_id = ? AND status IN ('Terdaftar', 'DP Masuk', 'Lead - Follow Up') "
        "AND (last_contact IS NULL OR datetime(last_contact) < datetime('now', '-3 days'))",
        (sid,),
    )
    r = client.post("/api/sales/sla-followup/check", headers=bearer(sales_token))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["stale_count"] == 0
    assert d["notified"] is False


def test_check_with_stale_sends_notif(client, sales_token, admin_token):
    """Sales dgn 3 jamaah stale -> notified=True."""
    sid = _sales_user_id("sales1")
    _cleanup_notif_today(sid)
    pkg = client.get("/api/packages", headers=bearer(admin_token)).json()[0]
    _seed_stale_jamaah(pkg["name"], sid, days_since_contact=5)
    _seed_stale_jamaah(pkg["name"], sid, days_since_contact=7)
    _seed_stale_jamaah(pkg["name"], sid, days_since_contact=10)

    before_count = client.get("/api/notifications/count",
                              headers=bearer(sales_token)).json()["unread"]
    r = client.post("/api/sales/sla-followup/check", headers=bearer(sales_token))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["stale_count"] >= 3, f"stale_count={d['stale_count']}"
    assert d["notified"] is True
    after_count = client.get("/api/notifications/count",
                             headers=bearer(sales_token)).json()["unread"]
    assert after_count > before_count


def test_check_dedupes_same_day(client, sales_token, admin_token):
    """Call 2x -> call kedua notified=False."""
    sid = _sales_user_id("sales1")
    _cleanup_notif_today(sid)
    pkg = client.get("/api/packages", headers=bearer(admin_token)).json()[0]
    _seed_stale_jamaah(pkg["name"], sid, days_since_contact=6)

    r1 = client.post("/api/sales/sla-followup/check", headers=bearer(sales_token))
    assert r1.json()["notified"] is True
    r2 = client.post("/api/sales/sla-followup/check", headers=bearer(sales_token))
    d2 = r2.json()
    assert d2["notified"] is False
    assert d2.get("reason") == "already_notified_today"


def test_check_admin_gets_preview_no_notif(client, admin_token):
    """Admin akses -> count aggregate, tidak insert notif."""
    admin_id = _sales_user_id("admin")
    _cleanup_notif_today(admin_id)
    r = client.post("/api/sales/sla-followup/check", headers=bearer(admin_token))
    assert r.status_code == 200
    d = r.json()
    assert "stale_count" in d
    assert d["notified"] is False


def test_check_rbac_denies_finance(client, finance_token):
    r = client.post("/api/sales/sla-followup/check", headers=bearer(finance_token))
    assert r.status_code == 403
