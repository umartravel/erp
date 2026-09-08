"""
Phase 7b-1: Transfer Agen Antar CS.

Cover endpoint PUT /api/agents/{aid}/handler (admin only) yg memindahkan
handler_cs_id 1 agen ke CS lain. Guard resign akan di-test terpisah di
Phase 7b-3.

Kasus:
- Happy path admin transfer agen ke sales lain
- RBAC: sales/mgmt/finance/ops -> 403
- 404 kalau agent tidak ada
- 404 kalau target user tidak ada
- 400 kalau target user role != 'sales'
- 400 kalau agen sudah dihandle CS itu (no-op prevention)
- 400 kalau handler_cs_id tidak dikirim / bukan int
- log_action tersimpan dgn detail from -> to (+ note kalau ada)
"""
from tests.conftest import bearer

import db


def _make_sales_user(client, admin_token, username, name):
    """Bikin user role sales via /api/users, return user id."""
    r = client.post(
        "/api/users",
        json={"username": username, "password": "password123", "name": name, "role": "sales"},
        headers=bearer(admin_token),
    )
    assert r.status_code == 200, r.text
    row = db.query_one("SELECT id FROM users WHERE username = ?", (username,))
    assert row is not None
    return row["id"]


def _make_agent(client, admin_token, name, phone_suffix):
    """Bikin agen baru (default handler = admin yg klik terima). Return agent id."""
    r = client.post(
        "/api/agents",
        json={
            "name": name,
            "phone": "0812" + phone_suffix,
            "province": "Jawa Barat",
            "city": "Bandung",
        },
        headers=bearer(admin_token),
    )
    assert r.status_code == 200, r.text
    agent = next(
        a for a in client.get("/api/agents", headers=bearer(admin_token)).json()
        if a["name"] == name
    )
    return agent["id"]


def test_transfer_agent_happy_path(client, admin_token):
    """Admin transfer agen dari CS-A ke CS-B; response benar + DB ter-update."""
    # sales1 dari conftest.py fixture. Buat sales user kedua.
    cs_b_id = _make_sales_user(client, admin_token, "cs_transfer_b", "CS Transfer B")
    cs_a = db.query_one("SELECT id FROM users WHERE username = 'sales1'")
    assert cs_a is not None
    cs_a_id = cs_a["id"]

    aid = _make_agent(client, admin_token, "TEST AgentTransfer1", "70000001")
    # Set handler_cs_id awal = cs_a supaya transfer dari cs_a -> cs_b jelas.
    db.execute("UPDATE agents SET handler_cs_id = ? WHERE id = ?", (cs_a_id, aid))

    r = client.put(
        f"/api/agents/{aid}/handler",
        json={"handler_cs_id": cs_b_id, "note": "load re-balance"},
        headers=bearer(admin_token),
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["agent_id"] == aid
    assert payload["from_cs_id"] == cs_a_id
    assert payload["to_cs_id"] == cs_b_id

    # DB harus tersimpan handler_cs_id baru.
    row = db.query_one("SELECT handler_cs_id FROM agents WHERE id = ?", (aid,))
    assert row["handler_cs_id"] == cs_b_id

    # audit log tercatat dgn detail from -> to + catatan.
    log = db.query_one(
        "SELECT details FROM audit_logs WHERE action = 'TRANSFER_AGENT' "
        "ORDER BY id DESC LIMIT 1"
    )
    assert log is not None
    assert "TEST AgentTransfer1" in log["details"]
    assert "load re-balance" in log["details"]


def test_transfer_agent_rbac(client, admin_token, sales_token, management_token, finance_token, ops_token):
    """Cuma admin. Sales/mgmt/finance/ops semua 403."""
    cs_x_id = _make_sales_user(client, admin_token, "cs_transfer_rbac", "CS Transfer RBAC")
    aid = _make_agent(client, admin_token, "TEST AgentRbac", "70000002")

    for name, tok in [
        ("sales", sales_token),
        ("management", management_token),
        ("finance", finance_token),
        ("ops", ops_token),
    ]:
        r = client.put(
            f"/api/agents/{aid}/handler",
            json={"handler_cs_id": cs_x_id},
            headers=bearer(tok),
        )
        assert r.status_code == 403, f"{name} seharusnya 403, dapat {r.status_code}"


def test_transfer_agent_missing_agent(client, admin_token):
    """Agen tidak ada -> 404."""
    cs_id = _make_sales_user(client, admin_token, "cs_transfer_missing_a", "CS Missing A")
    r = client.put(
        "/api/agents/999999/handler",
        json={"handler_cs_id": cs_id},
        headers=bearer(admin_token),
    )
    assert r.status_code == 404
    assert "Mitra/Agen" in r.json()["error"]


def test_transfer_agent_missing_target_user(client, admin_token):
    """Target CS tidak ada -> 404."""
    aid = _make_agent(client, admin_token, "TEST AgentMissingTarget", "70000003")
    r = client.put(
        f"/api/agents/{aid}/handler",
        json={"handler_cs_id": 999999},
        headers=bearer(admin_token),
    )
    assert r.status_code == 404
    assert "tujuan" in r.json()["error"].lower()


def test_transfer_agent_target_wrong_role(client, admin_token):
    """Target user bukan role sales (mis. admin) -> 400."""
    aid = _make_agent(client, admin_token, "TEST AgentWrongRole", "70000004")
    admin_row = db.query_one("SELECT id FROM users WHERE username = 'admin'")
    r = client.put(
        f"/api/agents/{aid}/handler",
        json={"handler_cs_id": admin_row["id"]},
        headers=bearer(admin_token),
    )
    assert r.status_code == 400
    body = r.json()["error"].lower()
    assert "sales" in body or "cs" in body


def test_transfer_agent_same_cs_noop(client, admin_token):
    """Coba transfer ke CS yg sudah handle agen itu -> 400."""
    cs_id = _make_sales_user(client, admin_token, "cs_transfer_noop", "CS Transfer Noop")
    aid = _make_agent(client, admin_token, "TEST AgentNoop", "70000005")
    db.execute("UPDATE agents SET handler_cs_id = ? WHERE id = ?", (cs_id, aid))

    r = client.put(
        f"/api/agents/{aid}/handler",
        json={"handler_cs_id": cs_id},
        headers=bearer(admin_token),
    )
    assert r.status_code == 400
    assert "sudah" in r.json()["error"].lower()


def test_transfer_agent_missing_handler_cs_id(client, admin_token):
    """Body tanpa handler_cs_id -> 400 dari parse_int."""
    aid = _make_agent(client, admin_token, "TEST AgentMissingBody", "70000006")
    r = client.put(
        f"/api/agents/{aid}/handler",
        json={"note": "lupa CS-nya"},
        headers=bearer(admin_token),
    )
    assert r.status_code == 400
