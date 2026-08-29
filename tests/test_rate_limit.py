"""
Integration test: rate limiter /api/login (brute-force protection).

Skenario:
- MAX_FAILURES kegagalan berturut -> percobaan berikutnya 429.
- Login sukses dgn kredensial benar clear counter.
- Rate limit di-reset SEBELUM test ini via autouse fixture di conftest.

NB: threshold ambil dari rate_limit.MAX_FAILURES supaya kalau nanti
diubah, test ini ikut relevan tanpa perlu diubah angkanya.
"""
import rate_limit


def test_rate_limit_blocks_after_max_failures(client):
    """MAX_FAILURES kegagalan berturut -> percobaan berikutnya 429."""
    for i in range(rate_limit.MAX_FAILURES):
        r = client.post("/api/login", json={"username": "admin", "password": "salah"})
        assert r.status_code == 401, f"attempt {i+1}: {r.status_code} {r.text}"

    # Percobaan ke-(MAX+1) -> 429 (bahkan dgn kredensial benar!)
    r = client.post("/api/login", json={"username": "admin", "password": "password123"})
    assert r.status_code == 429
    assert "Terlalu banyak" in r.json()["error"]


def test_rate_limit_cleared_on_successful_login(client):
    """Login sukses -> counter reset -> failed attempt boleh lagi."""
    for _ in range(3):
        client.post("/api/login", json={"username": "admin", "password": "salah"})

    r = client.post("/api/login", json={"username": "admin", "password": "password123"})
    assert r.status_code == 200

    # Sekarang boleh gagal lagi tanpa langsung ke-block
    for _ in range(3):
        r = client.post("/api/login", json={"username": "admin", "password": "salah"})
        assert r.status_code == 401, "counter tidak ke-reset setelah login sukses"


def test_rate_limit_unknown_user_still_counted(client):
    """Kegagalan '404 user tidak ditemukan' juga dihitung (attacker enumerate
    username tetap dibatasi)."""
    for _ in range(rate_limit.MAX_FAILURES):
        r = client.post("/api/login", json={"username": "ghost-xxx", "password": "x"})
        assert r.status_code == 404

    r = client.post("/api/login", json={"username": "admin", "password": "password123"})
    assert r.status_code == 429
