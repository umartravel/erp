"""
Integration test: procurement (kontrak vendor B2B).
Lifecycle: Pending -> Aktif -> multiple payments (deposit_paid += amount)
-> Selesai/Dibatalkan.

Yang di-cover:
- Finance create -> Pending (belum potong kas)
- Management approve -> Aktif
- Finance register payment -> tx expense procurement_payment + deposit_paid update
- Guard: payment melebihi sisa ditolak
- Guard: payment sebelum Aktif ditolak
- Guard: delete kontrak yang sudah ada pembayaran ditolak
- Reject wajib reason
- RBAC create/review/pay/delete
"""
from tests.conftest import bearer


def _make_procurement(client, finance_token, vendor_name, total_price=10_000_000, stock=100):
    r = client.post("/api/procurement", json={
        "vendor_name": vendor_name, "service_type": "hotel_mekkah",
        "total_stock": stock, "total_price": total_price,
        "package_name": None,
    }, headers=bearer(finance_token))
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_procurement_full_flow(client, finance_token, management_token, admin_token):
    """create Pending -> approve Aktif -> payment -> tx expense."""
    hdr = bearer(admin_token)
    pid = _make_procurement(client, finance_token, "TEST Vendor Full")

    rows = client.get("/api/procurement", headers=hdr).json()
    row = next(r for r in rows if r["id"] == pid)
    assert row["status"] == "Pending"
    assert (row["deposit_paid"] or 0) == 0

    r = client.put(f"/api/procurement/{pid}/review",
                   json={"action": "approve"}, headers=bearer(management_token))
    assert r.status_code == 200

    rows = client.get("/api/procurement", headers=hdr).json()
    row = next(r for r in rows if r["id"] == pid)
    assert row["status"] == "Aktif"

    r = client.post(f"/api/procurement/{pid}/payment",
                    json={"amount": 3_000_000}, headers=bearer(finance_token))
    assert r.status_code == 200, r.text

    rows = client.get("/api/procurement", headers=hdr).json()
    row = next(r for r in rows if r["id"] == pid)
    assert row["deposit_paid"] == 3_000_000

    r = client.post(f"/api/procurement/{pid}/payment",
                    json={"amount": 2_000_000}, headers=bearer(finance_token))
    assert r.status_code == 200
    rows = client.get("/api/procurement", headers=hdr).json()
    row = next(r for r in rows if r["id"] == pid)
    assert row["deposit_paid"] == 5_000_000

    txs = client.get("/api/transactions", headers=hdr).json()
    my_txs = [t for t in txs if t["category"] == "procurement_payment" and t["reference_id"] == pid]
    assert len(my_txs) == 2
    assert sum(t["amount"] for t in my_txs) == 5_000_000


def test_procurement_payment_exceeds_remainder(client, finance_token, management_token):
    """Payment > sisa tagihan -> 400."""
    pid = _make_procurement(client, finance_token, "TEST Vendor Overshoot", total_price=1_000_000)
    client.put(f"/api/procurement/{pid}/review",
               json={"action": "approve"}, headers=bearer(management_token))

    r = client.post(f"/api/procurement/{pid}/payment",
                    json={"amount": 2_000_000}, headers=bearer(finance_token))
    assert r.status_code == 400
    assert "melebihi" in r.json()["error"].lower()


def test_procurement_payment_needs_active(client, finance_token):
    """Pending (belum approve) tidak bisa dibayar."""
    pid = _make_procurement(client, finance_token, "TEST Vendor Pending Pay")
    r = client.post(f"/api/procurement/{pid}/payment",
                    json={"amount": 500_000}, headers=bearer(finance_token))
    assert r.status_code == 400
    assert "Aktif" in r.json()["error"]


def test_procurement_delete_with_payment_blocked(
    client, admin_token, finance_token, management_token
):
    """Kontrak yang sudah ada pembayaran tidak bisa dihapus."""
    pid = _make_procurement(client, finance_token, "TEST Vendor NoDelete")
    client.put(f"/api/procurement/{pid}/review",
               json={"action": "approve"}, headers=bearer(management_token))
    client.post(f"/api/procurement/{pid}/payment",
                json={"amount": 100_000}, headers=bearer(finance_token))

    r = client.delete(f"/api/procurement/{pid}", headers=bearer(admin_token))
    assert r.status_code == 400
    assert "tidak bisa" in r.json()["error"].lower()


def test_procurement_reject_requires_reason(client, finance_token, management_token):
    """action=reject tanpa reason -> 400."""
    pid = _make_procurement(client, finance_token, "TEST Vendor RejectReason")
    r = client.put(f"/api/procurement/{pid}/review",
                   json={"action": "reject"}, headers=bearer(management_token))
    assert r.status_code == 400
    assert "penolakan" in r.json()["error"].lower()


def test_procurement_rbac_create(client, sales_token, ops_token):
    """Sales/ops tidak boleh create (hanya admin/finance)."""
    payload = {"vendor_name": "X", "service_type": "y", "total_stock": 1, "total_price": 1}
    r = client.post("/api/procurement", json=payload, headers=bearer(sales_token))
    assert r.status_code == 403
    r = client.post("/api/procurement", json=payload, headers=bearer(ops_token))
    assert r.status_code == 403


def test_procurement_rbac_review(client, finance_token, sales_token):
    """Finance/sales tidak boleh review (hanya admin/management)."""
    r = client.put("/api/procurement/999/review",
                   json={"action": "approve"}, headers=bearer(finance_token))
    assert r.status_code == 403
    r = client.put("/api/procurement/999/review",
                   json={"action": "approve"}, headers=bearer(sales_token))
    assert r.status_code == 403


def test_procurement_rbac_delete_admin_only(client, finance_token, management_token):
    """Finance/mgmt tidak boleh delete (hanya admin)."""
    r = client.delete("/api/procurement/999", headers=bearer(finance_token))
    assert r.status_code == 403
    r = client.delete("/api/procurement/999", headers=bearer(management_token))
    assert r.status_code == 403
