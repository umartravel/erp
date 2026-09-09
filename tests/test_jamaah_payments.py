"""
Phase 13a: Maker-checker Pembayaran Jamaah (Sales submit -> Finance ACC).

Uji:
- Sales submit -> status Pending, jamaah.paid_amount BELUM berubah
- Finance verify -> paid_amount naik, transactions row masuk, submission Verified
- Finance reject -> tidak sentuh paid_amount, submission Rejected + reason
- Guard: submit melebihi sisa tagihan (setelah kurangi Pending) -> 400
- Guard: submit di jamaah yg sama saat masih Pending -> hitung stack sisa
- RBAC: finance tidak boleh submit, sales tidak boleh review
- List queue: finance dapat semua Pending
"""
import datetime

from tests.conftest import bearer

import db


_PID = [1000]


def _mk_jamaah(client, admin_token, name, total_price=30_000_000):
    _PID[0] += 1
    nik = f"9993{_PID[0]:012d}"[:16]
    pkg = client.get("/api/packages", headers=bearer(admin_token)).json()[0]
    price = pkg.get("price_quad") or pkg.get("price") or total_price
    r = client.post("/api/jamaah", json={
        "nik": nik, "name": name,
        "phone": f"0812{_PID[0]:010d}"[:14],
        "package_type": pkg["name"], "total_price": price,
        "status": "Terdaftar",
    }, headers=bearer(admin_token))
    assert r.status_code == 200, r.text
    return r.json()["id"], price


def _submit(client, token, jid, amount, kind="Cicilan"):
    return client.post(f"/api/jamaah/{jid}/payment-submissions", json={
        "amount": amount, "payment_kind": kind,
        "payment_method": "Transfer", "bank_account": "BCA 1234",
        "notes": "test",
    }, headers=bearer(token))


def test_submit_creates_pending_submission(client, sales_token, admin_token):
    jid, price = _mk_jamaah(client, admin_token, "PayFlow Fulan A")
    r = _submit(client, sales_token, jid, 5_000_000, "DP")
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    # Cek submission Pending, jamaah paid_amount TIDAK berubah
    sub = db.query_one("SELECT * FROM jamaah_payment_submissions WHERE id = ?", (sid,))
    assert sub["status"] == "Pending"
    assert sub["amount"] == 5_000_000
    j = db.query_one("SELECT paid_amount FROM jamaah WHERE id = ?", (jid,))
    assert (j["paid_amount"] or 0) == 0


def test_verify_applies_payment(client, sales_token, admin_token, finance_token):
    jid, price = _mk_jamaah(client, admin_token, "PayFlow Fulan B")
    r = _submit(client, sales_token, jid, 10_000_000, "DP")
    sid = r.json()["id"]
    r2 = client.put(f"/api/payment-submissions/{sid}/review",
                    json={"action": "verify"}, headers=bearer(finance_token))
    assert r2.status_code == 200, r2.text
    d = r2.json()
    assert d["new_payment_status"] == "DP"
    j = db.query_one("SELECT paid_amount, payment_status FROM jamaah WHERE id = ?", (jid,))
    assert j["paid_amount"] == 10_000_000
    assert j["payment_status"] == "DP"
    tx = db.query_one("SELECT * FROM transactions WHERE id = ?", (d["transaction_id"],))
    assert tx is not None
    assert tx["type"] == "income"
    assert tx["amount"] == 10_000_000
    sub = db.query_one("SELECT * FROM jamaah_payment_submissions WHERE id = ?", (sid,))
    assert sub["status"] == "Verified"


def test_verify_full_marks_lunas(client, sales_token, admin_token, finance_token):
    jid, price = _mk_jamaah(client, admin_token, "PayFlow Fulan C")
    r = _submit(client, sales_token, jid, price, "Pelunasan")
    sid = r.json()["id"]
    r2 = client.put(f"/api/payment-submissions/{sid}/review",
                    json={"action": "verify"}, headers=bearer(finance_token))
    assert r2.json()["new_payment_status"] == "Lunas"


def test_reject_keeps_paid_amount_unchanged(
        client, sales_token, admin_token, finance_token):
    jid, price = _mk_jamaah(client, admin_token, "PayFlow Fulan D")
    r = _submit(client, sales_token, jid, 8_000_000, "DP")
    sid = r.json()["id"]
    r2 = client.put(f"/api/payment-submissions/{sid}/review",
                    json={"action": "reject", "reason": "Uang belum masuk rek"},
                    headers=bearer(finance_token))
    assert r2.status_code == 200
    sub = db.query_one("SELECT * FROM jamaah_payment_submissions WHERE id = ?", (sid,))
    assert sub["status"] == "Rejected"
    assert "Uang belum masuk rek" in sub["reject_reason"]
    j = db.query_one("SELECT paid_amount FROM jamaah WHERE id = ?", (jid,))
    assert (j["paid_amount"] or 0) == 0


def test_reject_requires_reason(client, sales_token, admin_token, finance_token):
    jid, _ = _mk_jamaah(client, admin_token, "PayFlow Fulan E")
    r = _submit(client, sales_token, jid, 3_000_000)
    sid = r.json()["id"]
    r2 = client.put(f"/api/payment-submissions/{sid}/review",
                    json={"action": "reject"},
                    headers=bearer(finance_token))
    assert r2.status_code == 400


def test_submit_exceeds_remainder_denied(client, sales_token, admin_token):
    jid, price = _mk_jamaah(client, admin_token, "PayFlow Fulan F")
    r = _submit(client, sales_token, jid, price + 1_000_000)
    assert r.status_code == 400


def test_submit_stack_pending_reduces_remainder(
        client, sales_token, admin_token):
    """2 submission Pending sekaligus -> kedua reject kalau melebihi sisa combined."""
    jid, price = _mk_jamaah(client, admin_token, "PayFlow Fulan G")
    r1 = _submit(client, sales_token, jid, price - 1_000_000)
    assert r1.status_code == 200
    # Sisa = 1_000_000, submit lagi 2_000_000 harus 400
    r2 = _submit(client, sales_token, jid, 2_000_000)
    assert r2.status_code == 400


def test_rbac_finance_cannot_submit(client, finance_token, admin_token):
    jid, _ = _mk_jamaah(client, admin_token, "PayFlow Fulan H")
    r = _submit(client, finance_token, jid, 5_000_000)
    assert r.status_code == 403


def test_rbac_sales_cannot_review(
        client, sales_token, admin_token):
    jid, _ = _mk_jamaah(client, admin_token, "PayFlow Fulan I")
    r = _submit(client, sales_token, jid, 5_000_000)
    sid = r.json()["id"]
    r2 = client.put(f"/api/payment-submissions/{sid}/review",
                    json={"action": "verify"},
                    headers=bearer(sales_token))
    assert r2.status_code == 403


def test_list_pending_queue(client, sales_token, admin_token, finance_token):
    jid, _ = _mk_jamaah(client, admin_token, "PayFlow Fulan J")
    _submit(client, sales_token, jid, 4_000_000)
    r = client.get("/api/payment-submissions?status=Pending",
                   headers=bearer(finance_token))
    assert r.status_code == 200
    items = r.json()
    assert any(x["jamaah_name"] == "PayFlow Fulan J" for x in items)
