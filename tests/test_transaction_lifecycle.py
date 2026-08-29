"""
Integration test: cascading rollback saat transaksi payment dihapus.

Skenario kritis (Koreksi Admin di Buku Kas):
- Admin hapus tx pembayaran jamaah -> paid_amount jamaah HARUS dikurangi
- payment_status recompute (Lunas -> DP -> Unpaid tergantung sisa)
- status legacy jamaah ikut ke-sync via sync_status_mirror

Kalau ini regressi, koreksi buku kas bikin jamaah "Lunas" padahal duitnya
sudah ditarik keluar dari kas.
"""
from tests.conftest import bearer


def _make_jamaah(client, admin_token, nik, name):
    hdr = bearer(admin_token)
    packages = client.get("/api/packages", headers=hdr).json()
    pkg = packages[0]
    price = pkg.get("price_quad") or pkg.get("price") or 0
    r = client.post("/api/jamaah", json={
        "nik": nik, "name": name, "phone": "0811" + nik[-8:],
        "package_type": pkg["name"], "total_price": price,
        "room_type": "QUAD" if pkg.get("price_quad") else None,
        "status": "Terdaftar",
    }, headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()["id"], price


def _find_payment_tx(client, admin_token, jid):
    txs = client.get("/api/transactions", headers=bearer(admin_token)).json()
    return [t for t in txs if t["category"] == "payment" and t["reference_id"] == jid]


def test_delete_full_payment_rolls_back_to_unpaid(client, admin_token, finance_token):
    """Bayar full -> Lunas. Hapus tx-nya -> paid_amount kembali 0 + status Unpaid."""
    jid, price = _make_jamaah(client, admin_token, "9999999999998001", "TEST TxRollback Full")

    r = client.put(f"/api/jamaah/{jid}/payment", json={"paid_amount": price},
                   headers=bearer(finance_token))
    assert r.status_code == 200
    listing = client.get("/api/jamaah", headers=bearer(admin_token)).json()
    j = next(x for x in listing if x["id"] == jid)
    assert j["payment_status"] == "Lunas"
    assert j["paid_amount"] == price

    txs = _find_payment_tx(client, admin_token, jid)
    assert len(txs) == 1
    tid = txs[0]["id"]

    r = client.delete(f"/api/transactions/{tid}", headers=bearer(admin_token))
    assert r.status_code == 200, r.text

    listing = client.get("/api/jamaah", headers=bearer(admin_token)).json()
    j = next(x for x in listing if x["id"] == jid)
    assert j["paid_amount"] == 0, f"paid_amount tidak di-rollback: {j['paid_amount']}"
    assert j["payment_status"] == "Unpaid", f"payment_status masih {j['payment_status']}"


def test_delete_partial_payment_downgrades_lunas_to_dp(client, admin_token, finance_token):
    """DP1 + DP2 (full lunas) -> hapus DP2 -> harusnya turun jadi DP."""
    jid, price = _make_jamaah(client, admin_token, "9999999999998002", "TEST TxRollback Partial")
    dp1 = price // 3
    dp2 = price - dp1

    client.put(f"/api/jamaah/{jid}/payment", json={"paid_amount": dp1},
               headers=bearer(finance_token))
    client.put(f"/api/jamaah/{jid}/payment", json={"paid_amount": dp2},
               headers=bearer(finance_token))

    listing = client.get("/api/jamaah", headers=bearer(admin_token)).json()
    j = next(x for x in listing if x["id"] == jid)
    assert j["payment_status"] == "Lunas"

    txs = _find_payment_tx(client, admin_token, jid)
    assert len(txs) == 2
    tx_dp2 = next(t for t in txs if t["amount"] == dp2)
    r = client.delete(f"/api/transactions/{tx_dp2['id']}", headers=bearer(admin_token))
    assert r.status_code == 200

    listing = client.get("/api/jamaah", headers=bearer(admin_token)).json()
    j = next(x for x in listing if x["id"] == jid)
    assert j["paid_amount"] == dp1, f"expected paid_amount={dp1}, got {j['paid_amount']}"
    assert j["payment_status"] == "DP", f"expected DP, got {j['payment_status']}"


def test_delete_non_payment_tx_no_error(client, admin_token, finance_token):
    """Hapus tx expense (bukan payment) -> tidak sentuh jamaah manapun."""
    r = client.post("/api/transactions/expense", json={
        "category": "lainnya", "amount": 50000,
        "description": "TEST expense manual", "package_name": None,
    }, headers=bearer(finance_token))
    assert r.status_code == 200

    txs = client.get("/api/transactions", headers=bearer(admin_token)).json()
    my_tx = next(t for t in txs if t["description"] == "TEST expense manual")

    r = client.delete(f"/api/transactions/{my_tx['id']}", headers=bearer(admin_token))
    assert r.status_code == 200


def test_delete_transaction_admin_only(client, sales_token, finance_token):
    """Endpoint DELETE tx guarded ke admin saja (bukan finance)."""
    r = client.delete("/api/transactions/999999", headers=bearer(finance_token))
    assert r.status_code == 403, f"finance seharusnya ditolak: {r.status_code} {r.text}"
    r = client.delete("/api/transactions/999999", headers=bearer(sales_token))
    assert r.status_code == 403


def test_delete_nonexistent_transaction_404(client, admin_token):
    r = client.delete("/api/transactions/999999999", headers=bearer(admin_token))
    assert r.status_code == 404
