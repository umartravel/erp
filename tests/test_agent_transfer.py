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


# ==========================================================================
# Phase 7b-2: Bulk handoff (POST /api/users/{uid}/handoff-agents)
# ==========================================================================


def _setup_source_cs_with_agents(client, admin_token, source_username, agent_count, id_prefix):
    """Bikin CS sumber + N agen milik dia. Return (source_uid, agent_ids)."""
    source_uid = _make_sales_user(client, admin_token, source_username, source_username.upper())
    agent_ids = []
    for i in range(agent_count):
        aid = _make_agent(client, admin_token, f"TEST BulkAgent {id_prefix}{i}", f"7100{id_prefix}{i:02d}")
        db.execute("UPDATE agents SET handler_cs_id = ? WHERE id = ?", (source_uid, aid))
        agent_ids.append(aid)
    return source_uid, agent_ids


def test_handoff_all_agents_default(client, admin_token):
    """POST tanpa agent_ids -> semua agen sumber pindah ke tujuan."""
    src_uid, src_agents = _setup_source_cs_with_agents(client, admin_token, "cs_handoff_src1", 3, "01")
    tgt_uid = _make_sales_user(client, admin_token, "cs_handoff_tgt1", "CS Handoff TGT1")

    r = client.post(
        f"/api/users/{src_uid}/handoff-agents",
        json={"to_cs_id": tgt_uid},
        headers=bearer(admin_token),
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["moved_count"] == 3
    assert set(payload["moved_agent_ids"]) == set(src_agents)
    assert payload["from_cs_id"] == src_uid
    assert payload["to_cs_id"] == tgt_uid

    # DB: 3 agen sekarang handled tgt.
    rows = db.query_all(
        "SELECT id FROM agents WHERE handler_cs_id = ? ORDER BY id", (tgt_uid,)
    )
    moved_now = [r["id"] for r in rows]
    for aid in src_agents:
        assert aid in moved_now

    # Log ringkasan.
    log = db.query_one(
        "SELECT details FROM audit_logs WHERE action = 'HANDOFF_AGENTS' ORDER BY id DESC LIMIT 1"
    )
    assert log is not None
    assert "3 agen" in log["details"]


def test_handoff_subset_agents(client, admin_token):
    """agent_ids: [subset] -> hanya subset yg pindah, sisanya tetap di sumber."""
    src_uid, src_agents = _setup_source_cs_with_agents(client, admin_token, "cs_handoff_src2", 4, "02")
    tgt_uid = _make_sales_user(client, admin_token, "cs_handoff_tgt2", "CS Handoff TGT2")

    subset = src_agents[:2]
    r = client.post(
        f"/api/users/{src_uid}/handoff-agents",
        json={"to_cs_id": tgt_uid, "agent_ids": subset},
        headers=bearer(admin_token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["moved_count"] == 2

    # Sisanya masih di sumber.
    remaining = db.query_all(
        "SELECT id FROM agents WHERE handler_cs_id = ? ORDER BY id", (src_uid,)
    )
    remaining_ids = [r["id"] for r in remaining]
    for aid in src_agents[2:]:
        assert aid in remaining_ids


def test_handoff_rbac(client, admin_token, sales_token, management_token, finance_token, ops_token):
    """Non-admin -> 403."""
    src_uid, _ = _setup_source_cs_with_agents(client, admin_token, "cs_handoff_rbac_src", 1, "03")
    tgt_uid = _make_sales_user(client, admin_token, "cs_handoff_rbac_tgt", "CS Handoff RBAC TGT")

    for name, tok in [
        ("sales", sales_token),
        ("management", management_token),
        ("finance", finance_token),
        ("ops", ops_token),
    ]:
        r = client.post(
            f"/api/users/{src_uid}/handoff-agents",
            json={"to_cs_id": tgt_uid},
            headers=bearer(tok),
        )
        assert r.status_code == 403, f"{name} seharusnya 403, dapat {r.status_code}"


def test_handoff_same_user_forbidden(client, admin_token):
    """Sumber = tujuan -> 400."""
    src_uid, _ = _setup_source_cs_with_agents(client, admin_token, "cs_handoff_same", 1, "04")
    r = client.post(
        f"/api/users/{src_uid}/handoff-agents",
        json={"to_cs_id": src_uid},
        headers=bearer(admin_token),
    )
    assert r.status_code == 400
    assert "sama" in r.json()["error"].lower()


def test_handoff_target_wrong_role(client, admin_token):
    """Target user bukan sales -> 400."""
    src_uid, _ = _setup_source_cs_with_agents(client, admin_token, "cs_handoff_wrole", 1, "05")
    admin_row = db.query_one("SELECT id FROM users WHERE username = 'admin'")
    r = client.post(
        f"/api/users/{src_uid}/handoff-agents",
        json={"to_cs_id": admin_row["id"]},
        headers=bearer(admin_token),
    )
    assert r.status_code == 400
    assert "sales" in r.json()["error"].lower() or "cs" in r.json()["error"].lower()


def test_handoff_source_no_agents(client, admin_token):
    """Sumber tidak punya agen -> 400."""
    src_uid = _make_sales_user(client, admin_token, "cs_handoff_empty", "CS Empty")
    tgt_uid = _make_sales_user(client, admin_token, "cs_handoff_empty_tgt", "CS Empty TGT")
    r = client.post(
        f"/api/users/{src_uid}/handoff-agents",
        json={"to_cs_id": tgt_uid},
        headers=bearer(admin_token),
    )
    assert r.status_code == 400
    assert "tidak ada agen" in r.json()["error"].lower()


def test_handoff_missing_source_user(client, admin_token):
    """Source user tidak ada -> 404."""
    tgt_uid = _make_sales_user(client, admin_token, "cs_handoff_no_src_tgt", "CS No Src TGT")
    r = client.post(
        "/api/users/999999/handoff-agents",
        json={"to_cs_id": tgt_uid},
        headers=bearer(admin_token),
    )
    assert r.status_code == 404
    assert "sumber" in r.json()["error"].lower()


def test_handoff_empty_agent_ids_list(client, admin_token):
    """agent_ids=[] (kosong) -> 400 karena list non-empty required."""
    src_uid, _ = _setup_source_cs_with_agents(client, admin_token, "cs_handoff_empty_list", 1, "06")
    tgt_uid = _make_sales_user(client, admin_token, "cs_handoff_empty_list_tgt", "CS Empty List TGT")
    r = client.post(
        f"/api/users/{src_uid}/handoff-agents",
        json={"to_cs_id": tgt_uid, "agent_ids": []},
        headers=bearer(admin_token),
    )
    assert r.status_code == 400


# ==========================================================================
# Phase 7b-3: Guard DELETE /api/users/{uid} kalau user masih pegang agen
# ==========================================================================


def test_delete_user_blocked_when_holds_agents(client, admin_token):
    """User CS punya agen -> DELETE 409."""
    uid = _make_sales_user(client, admin_token, "cs_delete_blocked", "CS Delete Blocked")
    aid = _make_agent(client, admin_token, "TEST DeleteBlockedAgent", "72000001")
    db.execute("UPDATE agents SET handler_cs_id = ? WHERE id = ?", (uid, aid))

    r = client.delete(f"/api/users/{uid}", headers=bearer(admin_token))
    assert r.status_code == 409
    error = r.json()["error"]
    assert "1 agen" in error
    assert "Pindahkan" in error or "handoff" in error.lower()

    # User masih ada -- tidak jadi dihapus.
    still = db.query_one("SELECT id FROM users WHERE id = ?", (uid,))
    assert still is not None


def test_delete_user_succeeds_after_handoff(client, admin_token):
    """Setelah handoff semua agen, DELETE user lolos."""
    src_uid, src_agents = _setup_source_cs_with_agents(client, admin_token, "cs_delete_ok_src", 2, "07")
    tgt_uid = _make_sales_user(client, admin_token, "cs_delete_ok_tgt", "CS Delete OK TGT")

    # 1. Coba delete dulu -> 409 karena masih pegang agen.
    r = client.delete(f"/api/users/{src_uid}", headers=bearer(admin_token))
    assert r.status_code == 409

    # 2. Handoff semua agen.
    r = client.post(
        f"/api/users/{src_uid}/handoff-agents",
        json={"to_cs_id": tgt_uid},
        headers=bearer(admin_token),
    )
    assert r.status_code == 200
    assert r.json()["moved_count"] == 2

    # 3. Sekarang delete boleh.
    r = client.delete(f"/api/users/{src_uid}", headers=bearer(admin_token))
    assert r.status_code == 200, r.text

    # User beneran hilang.
    gone = db.query_one("SELECT id FROM users WHERE id = ?", (src_uid,))
    assert gone is None

    # Agen tetap ada + handler ke tgt.
    for aid in src_agents:
        row = db.query_one("SELECT handler_cs_id FROM agents WHERE id = ?", (aid,))
        assert row["handler_cs_id"] == tgt_uid


def test_delete_user_without_agents_still_works(client, admin_token):
    """User tanpa agen -> DELETE tetap bisa (backward compat)."""
    uid = _make_sales_user(client, admin_token, "cs_delete_noagent", "CS Delete NoAgent")
    r = client.delete(f"/api/users/{uid}", headers=bearer(admin_token))
    assert r.status_code == 200
    gone = db.query_one("SELECT id FROM users WHERE id = ?", (uid,))
    assert gone is None
