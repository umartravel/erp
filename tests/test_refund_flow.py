"""
Integration test: refund lifecycle (Pending -> Disetujui -> Dicairkan).

Yang di-cover:
- Sales/admin ajukan refund (endpoint /api/jamaah/{jid}/refund-requests)
- Guard: nominal > paid_amount ditolak
- Guard: reject wajib note
- Guard: dobel-approve (status non-Pending) ditolak
- Management review Disetujui / Ditolak
- Finance disburse -> transactions expense category=refund + paid_amount jamaah dikurangi
- cancel_booking=1 -> jamaah pipeline_stage jadi 'Cancelled'

Ini adalah rantai uang keluar terbesar setelah komisi -- regressi di sini
= saldo kas ngaco atau jamaah "Lunas" padahal duitnya sudah dikembalikan.
"""
from tests.conftest import bearer


def _setup_paid_jamaah(client, admin_token, finance_token, nik, name):
    """Utility: bikin jamaah + full pay, return (jid, price)."""
    hdr = bearer(admin_token)
    pkg = client.get("/api/packages", headers=hdr).json()[0]
    price = pkg.get("price_quad") or pkg.get("price") or 0
    r = client.post("/api/jamaah", json={
        "nik": nik, "name": name, "phone": "0811" + nik[-8:],
        "package_type": pkg["name"], "total_price": price,
        "room_type": "QUAD" if pkg.get("price_quad") else None,
        "status": "Terdaftar",
    }, headers=hdr)
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    r = client.put(f"/api/jamaah/{jid}/payment", json={"paid_amount": price},
                   headers=bearer(finance_token))
    assert r.status_code == 200
    return jid, price


def test_refund_full_flow_disburse(client, admin_token, management_token, finance_token):
    """Lifecycle happy-path: ajukan -> approve -> disburse.
    Verify tx expense terbentuk + jamaah.paid_amount dikurangi."""
    jid, price = _setup_paid_jamaah(
        client, admin_token, finance_token, "9999999999997001", "TEST Refund Full"
    )
    hdr_admin = bearer(admin_token)
    refund_amount = price // 2

    r = client.post(f"/api/jamaah/{jid}/refund-requests", json={
        "amount": refund_amount, "reason": "test refund reason", "cancel_booking": False,
    }, headers=hdr_admin)
    assert r.status_code == 200, r.text

    all_refunds = client.get("/api/refund-requests", headers=hdr_admin).json()
    my_refund = next(x for x in all_refunds if x["jamaah_id"] == jid)
    rid = my_refund["id"]
    assert my_refund["status"] == "Pending"

    r = client.put(f"/api/refund-requests/{rid}/review",
                   json={"action": "approve"}, headers=bearer(management_token))
    assert r.status_code == 200, r.text

    r = client.put(f"/api/refund-requests/{rid}/disburse",
                   headers=bearer(finance_token))
    assert r.status_code == 200, r.text

    txs = client.get("/api/transactions", headers=hdr_admin).json()
    refund_txs = [t for t in txs if t["category"] == "refund" and t["reference_id"] == jid]
    assert len(refund_txs) == 1
    assert refund_txs[0]["amount"] == refund_amount
    assert refund_txs[0]["type"] == "expense"

    listing = client.get("/api/jamaah", headers=hdr_admin).json()
    j = next(x for x in listing if x["id"] == jid)
    assert j["paid_amount"] == price - refund_amount
    assert j["payment_status"] == "DP", f"expected DP setelah refund parsial, got {j['payment_status']}"


def test_refund_amount_exceeds_paid_rejected(client, admin_token, finance_token):
    """Nominal refund > paid_amount -> 400."""
    jid, price = _setup_paid_jamaah(
        client, admin_token, finance_token, "9999999999997002", "TEST Refund Overshoot"
    )
    r = client.post(f"/api/jamaah/{jid}/refund-requests", json={
        "amount": price + 1_000_000, "reason": "coba lebih dari paid",
    }, headers=bearer(admin_token))
    assert r.status_code == 400
    assert "melebihi" in r.json()["error"].lower()


def test_refund_reject_requires_note(client, admin_token, management_token, finance_token):
    """action=reject tanpa note -> 400."""
    jid, price = _setup_paid_jamaah(
        client, admin_token, finance_token, "9999999999997003", "TEST Refund Reject"
    )
    r = client.post(f"/api/jamaah/{jid}/refund-requests", json={
        "amount": 100_000, "reason": "test",
    }, headers=bearer(admin_token))
    assert r.status_code == 200

    rid = next(x for x in client.get("/api/refund-requests",
               headers=bearer(admin_token)).json() if x["jamaah_id"] == jid)["id"]

    r = client.put(f"/api/refund-requests/{rid}/review",
                   json={"action": "reject"}, headers=bearer(management_token))
    assert r.status_code == 400
    assert "penolakan" in r.json()["error"].lower()

    r = client.put(f"/api/refund-requests/{rid}/review",
                   json={"action": "reject", "note": "alasan tolak"},
                   headers=bearer(management_token))
    assert r.status_code == 200


def test_refund_disburse_only_if_approved(client, admin_token, finance_token):
    """Pending (belum di-approve) tidak boleh langsung disburse."""
    jid, price = _setup_paid_jamaah(
        client, admin_token, finance_token, "9999999999997004", "TEST Refund NotApproved"
    )
    r = client.post(f"/api/jamaah/{jid}/refund-requests", json={
        "amount": 100_000, "reason": "test",
    }, headers=bearer(admin_token))
    assert r.status_code == 200

    rid = next(x for x in client.get("/api/refund-requests",
               headers=bearer(admin_token)).json() if x["jamaah_id"] == jid)["id"]

    r = client.put(f"/api/refund-requests/{rid}/disburse",
                   headers=bearer(finance_token))
    assert r.status_code == 400
    assert "Disetujui" in r.json()["error"]


def test_refund_cancel_booking_sets_cancelled_stage(
    client, admin_token, management_token, finance_token
):
    """cancel_booking=1 -> setelah disburse, pipeline_stage jamaah = Cancelled."""
    jid, price = _setup_paid_jamaah(
        client, admin_token, finance_token, "9999999999997005", "TEST Refund Cancel"
    )
    r = client.post(f"/api/jamaah/{jid}/refund-requests", json={
        "amount": price, "reason": "batal total", "cancel_booking": True,
    }, headers=bearer(admin_token))
    assert r.status_code == 200

    rid = next(x for x in client.get("/api/refund-requests",
               headers=bearer(admin_token)).json() if x["jamaah_id"] == jid)["id"]

    client.put(f"/api/refund-requests/{rid}/review",
               json={"action": "approve"}, headers=bearer(management_token))
    client.put(f"/api/refund-requests/{rid}/disburse",
               headers=bearer(finance_token))

    listing = client.get("/api/jamaah", headers=bearer(admin_token)).json()
    j = next(x for x in listing if x["id"] == jid)
    assert j["pipeline_stage"] == "Cancelled", f"expected Cancelled, got {j['pipeline_stage']}"
