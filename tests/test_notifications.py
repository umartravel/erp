"""
Phase 8c: In-app notification bell.

Verifikasi:
- Endpoint list, count, read-one, read-all
- Scope: user hanya lihat/tandai notif miliknya sendiri (404 utk notif user lain)
- Trigger insert saat refund pending (notif ke management role)
"""
from tests.conftest import bearer

import db


def _insert_notif(user_id, kind="test", title="Halo", body=None, link=None):
    db.execute(
        "INSERT INTO user_notifications (user_id, kind, title, body, link) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, kind, title, body, link),
    )


def test_list_and_count_own_notifs(client, admin_token):
    admin = db.query_one("SELECT id FROM users WHERE username = 'admin'")
    _insert_notif(admin["id"], "expense_pending", "Test A")
    _insert_notif(admin["id"], "refund_pending", "Test B")

    r = client.get("/api/notifications", headers=bearer(admin_token))
    assert r.status_code == 200
    items = r.json()
    titles = [x["title"] for x in items]
    assert "Test A" in titles and "Test B" in titles

    r = client.get("/api/notifications/count", headers=bearer(admin_token))
    assert r.json()["unread"] >= 2


def test_read_one_scoped_to_owner(client, admin_token, sales_token):
    admin = db.query_one("SELECT id FROM users WHERE username = 'admin'")
    _insert_notif(admin["id"], "test", "Only admin can see me")
    last = db.query_one(
        "SELECT id FROM user_notifications WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (admin["id"],),
    )
    # Sales coba mark-read notif admin -> 404 (bukan 403 supaya tidak leak keberadaan)
    r = client.put(f"/api/notifications/{last['id']}/read",
                   headers=bearer(sales_token))
    assert r.status_code == 404
    # Admin sendiri OK.
    r = client.put(f"/api/notifications/{last['id']}/read",
                   headers=bearer(admin_token))
    assert r.status_code == 200
    row = db.query_one("SELECT is_read FROM user_notifications WHERE id = ?",
                       (last["id"],))
    assert row["is_read"] == 1


def test_read_all_hides_from_count(client, admin_token):
    admin = db.query_one("SELECT id FROM users WHERE username = 'admin'")
    _insert_notif(admin["id"], "test", "will be read 1")
    _insert_notif(admin["id"], "test", "will be read 2")
    before = client.get("/api/notifications/count", headers=bearer(admin_token)).json()["unread"]

    r = client.put("/api/notifications/read-all", headers=bearer(admin_token))
    assert r.status_code == 200

    after = client.get("/api/notifications/count", headers=bearer(admin_token)).json()["unread"]
    assert after == 0
    assert before >= 2


def test_refund_creates_notif_for_management(client, admin_token, sales_token, management_token):
    """Ajukan refund -> user management dapat notif."""
    mgr = db.query_one("SELECT id FROM users WHERE role = 'management' LIMIT 1")
    assert mgr is not None
    before = client.get("/api/notifications/count",
                        headers=bearer(management_token)).json()["unread"]

    pkg = client.get("/api/packages", headers=bearer(admin_token)).json()[0]
    price = pkg.get("price_quad") or pkg.get("price") or 10_000_000
    r = client.post("/api/jamaah", json={
        "nik": "9999999999830001", "name": "TEST Refund Notif",
        "phone": "081200030001", "package_type": pkg["name"],
        "total_price": price, "status": "Terdaftar",
    }, headers=bearer(admin_token))
    jid = r.json()["id"]
    fin_token = client.post("/api/login", json={"username": "finance1", "password": "password123"}).json()["token"]
    client.put(f"/api/jamaah/{jid}/payment",
               json={"paid_amount": price},
               headers=bearer(fin_token))

    # Admin ajukan refund (bukan sales -- sales scope filter jamaah)
    r = client.post(f"/api/jamaah/{jid}/refund-requests",
                    json={"amount": 500_000, "reason": "test refund utk cek notif"},
                    headers=bearer(admin_token))
    assert r.status_code == 200, r.text

    after = client.get("/api/notifications/count",
                       headers=bearer(management_token)).json()["unread"]
    assert after > before, "Manager belum menerima notif refund_pending"
