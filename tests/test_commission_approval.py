"""
Integration test: commission_claim lifecycle (Pending -> Disetujui -> Dicairkan).

test_jamaah_lifecycle sudah cover auto-create Pending saat jamaah Lunas.
Yang di-cover di sini adalah LANJUTAN-nya: management review + finance disburse.

Alur:
- Auto-create Pending (dari full payment jamaah + agent_id)
- PUT /review approve -> Disetujui
- PUT /disburse -> Dicairkan, transactions expense category=commission muncul
- PUT /review reject tanpa note -> 400
- PUT /disburse pada Pending (belum di-approve) -> 400
- PUT /review pada claim yang sudah non-Pending -> 400
"""
from tests.conftest import bearer


def _make_agent_and_paid_jamaah(client, admin_token, finance_token, nik, agent_name):
    """Bikin agent + jamaah -> full-pay -> return (aid, jid, claim_id, fee)."""
    hdr = bearer(admin_token)

    r = client.post("/api/agents", json={
        "name": agent_name, "phone": "0812" + nik[-8:],
        "province": "Jawa Barat", "city": "Bandung",
    }, headers=hdr)
    assert r.status_code == 200
    agent = next(a for a in client.get("/api/agents", headers=hdr).json() if a["name"] == agent_name)
    aid = agent["id"]

    pkg = client.get("/api/packages", headers=hdr).json()[0]
    price = pkg.get("price_quad") or pkg.get("price") or 0
    r = client.post("/api/jamaah", json={
        "nik": nik, "name": f"TEST J {agent_name}",
        "phone": "0811" + nik[-8:],
        "package_type": pkg["name"], "total_price": price,
        "room_type": "QUAD" if pkg.get("price_quad") else None,
        "status": "Terdaftar", "agent_id": aid,
    }, headers=hdr)
    assert r.status_code == 200
    jid = r.json()["id"]

    r = client.put(f"/api/jamaah/{jid}/payment", json={"paid_amount": price},
                   headers=bearer(finance_token))
    assert r.status_code == 200

    claim = next(c for c in client.get("/api/commission-claims", headers=hdr).json()
                 if c["jamaah_id"] == jid)
    return aid, jid, claim["id"], claim["amount"]


def test_commission_approve_then_disburse(
    client, admin_token, management_token, finance_token
):
    """Happy path: Pending -> Disetujui -> Dicairkan (dgn tx expense)."""
    aid, jid, cid, fee = _make_agent_and_paid_jamaah(
        client, admin_token, finance_token, "9999999999996001", "TEST AgentApproveDisburse"
    )
    hdr = bearer(admin_token)

    r = client.put(f"/api/commission-claims/{cid}/review",
                   json={"action": "approve"}, headers=bearer(management_token))
    assert r.status_code == 200

    claims = client.get("/api/commission-claims", headers=hdr).json()
    my_claim = next(c for c in claims if c["id"] == cid)
    assert my_claim["status"] == "Disetujui"

    r = client.put(f"/api/commission-claims/{cid}/disburse",
                   headers=bearer(finance_token))
    assert r.status_code == 200

    my_claim = next(c for c in client.get("/api/commission-claims", headers=hdr).json()
                    if c["id"] == cid)
    assert my_claim["status"] == "Dicairkan"

    txs = client.get("/api/transactions", headers=hdr).json()
    commission_txs = [t for t in txs if t["category"] == "commission" and t["reference_id"] == aid]
    assert len(commission_txs) == 1
    assert commission_txs[0]["amount"] == fee
    assert commission_txs[0]["type"] == "expense"


def test_commission_reject_requires_note(
    client, admin_token, management_token, finance_token
):
    """reject tanpa note -> 400. Dgn note -> lolos, status Ditolak."""
    aid, jid, cid, fee = _make_agent_and_paid_jamaah(
        client, admin_token, finance_token, "9999999999996002", "TEST AgentReject"
    )

    r = client.put(f"/api/commission-claims/{cid}/review",
                   json={"action": "reject"}, headers=bearer(management_token))
    assert r.status_code == 400
    assert "penolakan" in r.json()["error"].lower()

    r = client.put(f"/api/commission-claims/{cid}/review",
                   json={"action": "reject", "note": "fee salah paket"},
                   headers=bearer(management_token))
    assert r.status_code == 200

    my_claim = next(c for c in client.get("/api/commission-claims",
                    headers=bearer(admin_token)).json() if c["id"] == cid)
    assert my_claim["status"] == "Ditolak"


def test_commission_disburse_only_if_approved(
    client, admin_token, finance_token
):
    """Pending -> tidak boleh langsung disburse."""
    aid, jid, cid, fee = _make_agent_and_paid_jamaah(
        client, admin_token, finance_token, "9999999999996003", "TEST AgentPending"
    )

    r = client.put(f"/api/commission-claims/{cid}/disburse",
                   headers=bearer(finance_token))
    assert r.status_code == 400
    assert "Disetujui" in r.json()["error"]


def test_commission_double_review_blocked(
    client, admin_token, management_token, finance_token
):
    """Sudah Disetujui -> tidak bisa direview ulang."""
    aid, jid, cid, fee = _make_agent_and_paid_jamaah(
        client, admin_token, finance_token, "9999999999996004", "TEST AgentDoubleReview"
    )

    client.put(f"/api/commission-claims/{cid}/review",
               json={"action": "approve"}, headers=bearer(management_token))
    r = client.put(f"/api/commission-claims/{cid}/review",
                   json={"action": "reject", "note": "coba ubah"},
                   headers=bearer(management_token))
    assert r.status_code == 400
    assert "direview ulang" in r.json()["error"]


def test_commission_review_rbac(client, sales_token, finance_token):
    """Sales & finance tidak boleh review (hanya admin/mgmt)."""
    r = client.put("/api/commission-claims/999/review",
                   json={"action": "approve"}, headers=bearer(sales_token))
    assert r.status_code == 403
    r = client.put("/api/commission-claims/999/review",
                   json={"action": "approve"}, headers=bearer(finance_token))
    assert r.status_code == 403


def test_commission_disburse_rbac(client, sales_token, management_token):
    """Sales & management tidak boleh disburse (hanya admin/finance)."""
    r = client.put("/api/commission-claims/999/disburse",
                   headers=bearer(sales_token))
    assert r.status_code == 403
    r = client.put("/api/commission-claims/999/disburse",
                   headers=bearer(management_token))
    assert r.status_code == 403
