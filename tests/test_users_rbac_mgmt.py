"""
Phase 8e: RBAC management edit/hapus karyawan.

Management sekarang boleh:
- Edit karyawan yg bukan admin (200)
- Hapus karyawan yg bukan admin (200)
Management TIDAK boleh:
- Edit karyawan admin (403)
- Hapus karyawan admin (403)

Admin tetap full akses.
Sales/Finance/Ops tetap 403 (unchanged).
"""
from tests.conftest import bearer

import db


def _create_user(client, admin_token, username, name, role="sales"):
    r = client.post("/api/users", json={
        "username": username, "password": "password123",
        "name": name, "role": role,
    }, headers=bearer(admin_token))
    assert r.status_code == 200, r.text
    row = db.query_one("SELECT id FROM users WHERE username = ?", (username,))
    return row["id"]


def test_mgmt_can_edit_non_admin(client, admin_token, management_token):
    uid = _create_user(client, admin_token, "test_mgmt_edit_1", "Test Edit 1", role="sales")
    r = client.put(f"/api/users/{uid}", json={
        "username": "test_mgmt_edit_1", "name": "Test Edit 1 Renamed",
        "role": "sales", "base_salary": 5000000, "phone": "081200000001",
        "personal_email": "", "address": "", "nik": "",
        "birth_date": "", "last_education": "",
        "education_major": "", "education_institution": "",
    }, headers=bearer(management_token))
    assert r.status_code == 200, r.text
    row = db.query_one("SELECT name FROM users WHERE id = ?", (uid,))
    assert row["name"] == "Test Edit 1 Renamed"


def test_mgmt_cannot_edit_admin(client, management_token):
    admin_row = db.query_one("SELECT id FROM users WHERE username = 'admin'")
    r = client.put(f"/api/users/{admin_row['id']}", json={
        "username": "admin", "name": "Hacked",
        "role": "admin", "base_salary": 999999999,
    }, headers=bearer(management_token))
    assert r.status_code == 403
    assert "admin" in r.json()["error"].lower()


def test_mgmt_can_delete_non_admin(client, admin_token, management_token):
    uid = _create_user(client, admin_token, "test_mgmt_del_1", "Test Del 1")
    r = client.delete(f"/api/users/{uid}", headers=bearer(management_token))
    assert r.status_code == 200
    gone = db.query_one("SELECT id FROM users WHERE id = ?", (uid,))
    assert gone is None


def test_mgmt_cannot_delete_admin(client, management_token):
    admin_row = db.query_one("SELECT id FROM users WHERE username = 'admin'")
    r = client.delete(f"/api/users/{admin_row['id']}",
                      headers=bearer(management_token))
    assert r.status_code == 403


def test_sales_finance_ops_still_forbidden(client, admin_token, sales_token, finance_token, ops_token):
    """Ubah RBAC ke mgmt tidak boleh loosen role lain."""
    uid = _create_user(client, admin_token, "test_rbac_other_1", "Test Other")
    for name, tok in [("sales", sales_token), ("finance", finance_token), ("ops", ops_token)]:
        r = client.put(f"/api/users/{uid}", json={
            "username": "test_rbac_other_1", "name": "X",
            "role": "sales", "base_salary": 0,
        }, headers=bearer(tok))
        assert r.status_code == 403, f"{name} PUT should be 403"
        r = client.delete(f"/api/users/{uid}", headers=bearer(tok))
        assert r.status_code == 403, f"{name} DELETE should be 403"


def test_admin_still_full_access(client, admin_token):
    uid = _create_user(client, admin_token, "test_admin_full_1", "Test Full")
    r = client.put(f"/api/users/{uid}", json={
        "username": "test_admin_full_1", "name": "Renamed by admin",
        "role": "sales", "base_salary": 0,
    }, headers=bearer(admin_token))
    assert r.status_code == 200
    r = client.delete(f"/api/users/{uid}", headers=bearer(admin_token))
    assert r.status_code == 200
