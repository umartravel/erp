"""
Security hardening regression tests (post-Phase-8 audit fixes).
"""
from tests.conftest import bearer

import db


def test_incidents_list_denies_sales(client, sales_token):
    r = client.get("/api/incidents", headers=bearer(sales_token))
    assert r.status_code == 403


def test_incidents_list_allows_ops(client, ops_token):
    r = client.get("/api/incidents", headers=bearer(ops_token))
    assert r.status_code == 200


def test_payment_submission_idor_blocked(client, sales_token, admin_token):
    """Sales tidak boleh submit utk jamaah bukan miliknya (sales_id NULL)."""
    pkg = client.get("/api/packages", headers=bearer(admin_token)).json()[0]
    price = pkg.get("price_quad") or pkg.get("price") or 10_000_000
    r = client.post("/api/jamaah", json={
        "nik": "9995000000009999", "name": "IDOR Test", "phone": "081200000099",
        "package_type": pkg["name"], "total_price": price, "status": "Terdaftar",
    }, headers=bearer(admin_token))
    jid = r.json()["id"]
    r2 = client.post(f"/api/jamaah/{jid}/payment-submissions", json={
        "amount": 1_000_000, "payment_kind": "DP",
        "payment_method": "Transfer", "bank_account": "BCA", "notes": "t",
    }, headers=bearer(sales_token))
    assert r2.status_code == 403


def test_audit_jamaah_idor_blocked(client, sales_token, admin_token):
    """Sales tidak boleh baca audit jamaah bukan miliknya."""
    pkg = client.get("/api/packages", headers=bearer(admin_token)).json()[0]
    price = pkg.get("price_quad") or pkg.get("price") or 10_000_000
    r = client.post("/api/jamaah", json={
        "nik": "9995000000008888", "name": "IDOR Audit", "phone": "081200000088",
        "package_type": pkg["name"], "total_price": price, "status": "Terdaftar",
    }, headers=bearer(admin_token))
    jid = r.json()["id"]
    r2 = client.get(f"/api/audit/jamaah/{jid}", headers=bearer(sales_token))
    assert r2.status_code == 403


def test_audit_details_mask_nik(client, admin_token):
    """audit_logs.details plaintext NIK di-mask di response."""
    pkg = client.get("/api/packages", headers=bearer(admin_token)).json()[0]
    price = pkg.get("price_quad") or pkg.get("price") or 10_000_000
    r = client.post("/api/jamaah", json={
        "nik": "1234567890123456", "name": "Mask Test",
        "phone": "081200000077",
        "package_type": pkg["name"], "total_price": price, "status": "Terdaftar",
    }, headers=bearer(admin_token))
    jid = r.json()["id"]
    db.execute(
        "INSERT INTO audit_logs (user_name, action, details, user_id, role) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Test", "UPDATE_JAMAAH",
         "Mask Test: NIK (0000000000000000 -> 1234567890123456)", 1, "admin"),
    )
    r2 = client.get(f"/api/audit/jamaah/{jid}", headers=bearer(admin_token))
    assert r2.status_code == 200
    dumps = str(r2.json())
    assert "1234567890123456" not in dumps
    assert "XXXXXXXX" in dumps


def test_year_out_of_range_clamped_mgmt(client, admin_token):
    r = client.get("/api/mgmt/home?year=0", headers=bearer(admin_token))
    assert r.status_code == 200


def test_year_out_of_range_clamped_sales(client, admin_token):
    r = client.get("/api/sales/home?year=99999", headers=bearer(admin_token))
    assert r.status_code == 200
