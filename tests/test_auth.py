"""
Test login flow + token gating dasar.
"""
from tests.conftest import bearer


def test_login_admin_ok(client):
    r = client.post("/api/login", json={"username": "admin", "password": "password123"})
    assert r.status_code == 200
    data = r.json()
    assert "token" in data
    assert data["user"]["name"] == "Administrator"
    assert data["user"]["role"] == "admin"


def test_login_wrong_password(client):
    r = client.post("/api/login", json={"username": "admin", "password": "salah"})
    assert r.status_code == 401
    assert r.json() == {"error": "Password salah"}


def test_login_unknown_user(client):
    r = client.post("/api/login", json={"username": "ghost-xxx", "password": "x"})
    assert r.status_code == 404
    assert r.json() == {"error": "User tidak ditemukan"}


def test_endpoint_needs_token(client):
    """Endpoint auth-guarded tanpa token -> 401."""
    r = client.get("/api/jamaah")
    assert r.status_code == 401


def test_users_admin_only(client, sales_token, admin_token):
    """/api/users terbuka utk admin + management, TIDAK utk sales."""
    r_sales = client.get("/api/users", headers=bearer(sales_token))
    assert r_sales.status_code == 403

    r_admin = client.get("/api/users", headers=bearer(admin_token))
    assert r_admin.status_code == 200
    assert isinstance(r_admin.json(), list)


def test_settings_unauth(client):
    """/api/settings terbuka tanpa token (dipakai layar login utk branding)."""
    r = client.get("/api/settings")
    assert r.status_code == 200
    data = r.json()
    # Setelah seed, minimal 'company_name' pasti ada.
    assert "company_name" in data or data == {}


def test_public_cs_list_unauth(client):
    """/api/public/cs-list terbuka tanpa token (dropdown 'Pilih CS' di form daftar)."""
    r = client.get("/api/public/cs-list")
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    # sales1 harus muncul (seed default)
    names = [x["name"] for x in rows]
    assert "Tim Sales" in names
