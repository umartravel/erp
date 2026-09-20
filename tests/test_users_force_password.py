"""
KRITIS #2 (audit 2026-09-20): must_change_password lifecycle + min 8 char.

Cover:
1. Migration 020 sudah backfill semua user existing dgn flag=1.
2. Login response echo must_change_password.
3. /api/users/me include flag.
4. Self change password (PUT /me/password) clears flag ke 0.
5. Admin reset password set flag ke 1 lagi.
6. POST /api/users create user baru default flag=1.
7. Password baru < 8 char rejected.
8. Password baru = password lama rejected (prevent bypass).
"""
import db
from tests.conftest import bearer


def test_flag_backfilled_all_users_after_migration(client, admin_token):
    """Semua seed user (admin/finance1/sales1/ops1/manager1) harus flag=1."""
    rows = db.query_all(
        "SELECT username, must_change_password FROM users WHERE username IN "
        "('admin','finance1','sales1','ops1','manager1')"
    )
    assert len(rows) == 5, f"Seed users tidak lengkap: {rows}"
    for r in rows:
        assert r["must_change_password"] == 1, (
            f"User {r['username']} harus must_change=1 setelah migration 020")


def test_login_response_includes_flag(client):
    """POST /api/login response.user harus include must_change_password."""
    r = client.post("/api/login", json={"username": "admin", "password": "password123"})
    assert r.status_code == 200
    user = r.json()["user"]
    assert "must_change_password" in user
    assert user["must_change_password"] == 1  # backfilled utk seed users


def test_users_me_includes_flag(client, sales_token):
    """GET /api/users/me include must_change_password."""
    r = client.get("/api/users/me", headers=bearer(sales_token))
    assert r.status_code == 200
    assert "must_change_password" in r.json()


def test_self_change_clears_flag(client):
    """PUT /me/password sukses -> flag jadi 0."""
    # Login sebagai ops1 (fresh, non-shared token supaya tidak polusi fixture).
    tok = client.post("/api/login",
                      json={"username": "ops1", "password": "password123"}).json()["token"]
    # Rotate password: 'password123' -> 'strongPw2026!'
    r = client.put("/api/users/me/password", headers=bearer(tok),
                   json={"old_password": "password123", "new_password": "strongPw2026!"})
    assert r.status_code == 200, r.text
    # Verify flag jadi 0 di DB.
    row = db.query_one("SELECT must_change_password FROM users WHERE username='ops1'")
    assert row["must_change_password"] == 0
    # Restore ke password123 supaya test lain tidak break -- pakai password baru
    # sebagai old_password.
    r2 = client.put("/api/users/me/password", headers=bearer(tok),
                    json={"old_password": "strongPw2026!", "new_password": "password123"})
    assert r2.status_code == 200
    # Reset flag secara manual (utk test independent -- production migration akan
    # persist flag=0 setelah self-change, tapi test suite butuh state konsisten).
    db.execute("UPDATE users SET must_change_password = 1 WHERE username='ops1'")


def test_self_change_reject_short_password(client, sales_token):
    """Password baru < 8 char -> 400."""
    r = client.put("/api/users/me/password", headers=bearer(sales_token),
                   json={"old_password": "password123", "new_password": "short7c"})
    assert r.status_code == 400
    assert "8 karakter" in r.json()["error"]


def test_self_change_reject_same_as_old(client):
    """Password baru = password lama -> 400 (prevent bypass rotate)."""
    tok = client.post("/api/login",
                      json={"username": "finance1", "password": "password123"}).json()["token"]
    r = client.put("/api/users/me/password", headers=bearer(tok),
                   json={"old_password": "password123", "new_password": "password123"})
    assert r.status_code == 400
    assert "tidak boleh sama" in r.json()["error"]


def test_admin_reset_sets_flag(client, admin_token):
    """PUT /api/users/{uid}/reset-password -> target user flag=1."""
    # Manually clear dulu untuk target (sales1) supaya assert masuk akal.
    db.execute("UPDATE users SET must_change_password = 0 WHERE username='sales1'")
    sales_row = db.query_one("SELECT id FROM users WHERE username='sales1'")
    r = client.put(f"/api/users/{sales_row['id']}/reset-password",
                   headers=bearer(admin_token),
                   json={"new_password": "tempPw2026!"})
    assert r.status_code == 200, r.text
    row = db.query_one("SELECT must_change_password FROM users WHERE username='sales1'")
    assert row["must_change_password"] == 1
    # Restore sales1 password ke 'password123' + flag 1 supaya fixture sales_token
    # (session-scope) tidak break di test lain.
    from auth import hash_password
    db.execute(
        "UPDATE users SET password = ?, must_change_password = 1 WHERE username='sales1'",
        (hash_password("password123"),),
    )


def test_admin_reset_reject_short_password(client, admin_token):
    """Admin reset dgn password baru < 8 char -> 400."""
    sales_row = db.query_one("SELECT id FROM users WHERE username='sales1'")
    r = client.put(f"/api/users/{sales_row['id']}/reset-password",
                   headers=bearer(admin_token),
                   json={"new_password": "1234567"})
    assert r.status_code == 400
    assert "8 karakter" in r.json()["error"]


def test_create_user_defaults_flag_true(client, admin_token):
    """POST /api/users -> user baru default must_change=1."""
    r = client.post("/api/users", headers=bearer(admin_token),
                    json={"username": "kritis2_new", "password": "tempPw2026!",
                          "name": "Test KRITIS2", "role": "sales", "base_salary": 0})
    assert r.status_code == 200, r.text
    row = db.query_one("SELECT must_change_password FROM users WHERE username='kritis2_new'")
    assert row is not None
    assert row["must_change_password"] == 1
    # Cleanup: hapus user test.
    db.execute("DELETE FROM users WHERE username='kritis2_new'")


def test_create_user_reject_short_password(client, admin_token):
    """POST /api/users dgn password < 8 char -> 400."""
    r = client.post("/api/users", headers=bearer(admin_token),
                    json={"username": "shouldfail", "password": "abc",
                          "name": "Fail Case", "role": "sales", "base_salary": 0})
    assert r.status_code == 400
    assert "8 karakter" in r.json()["error"]
