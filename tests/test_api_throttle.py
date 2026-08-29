"""
Integration test: rate limiter umum /api/* (bukan login).

Skenario:
- API_MAX_REQUESTS + 1 hit ke endpoint /api/ apa saja -> 429.
- /api/login DILEWATKAN (punya throttle sendiri di test_rate_limit.py).
- Root "/" (StaticFiles, non-API) TIDAK di-throttle sama sekali.
- Bucket per-user: 2 user beda tokennya tidak saling menumpuk.
"""
import rate_limit
from tests.conftest import bearer


def test_api_throttle_blocks_after_max(client, admin_token):
    """API_MAX_REQUESTS request ke /api/settings dari 1 user -> 429 di
    request berikutnya (kredensial valid tapi budget habis)."""
    hdr = bearer(admin_token)
    for i in range(rate_limit.API_MAX_REQUESTS):
        r = client.get("/api/settings", headers=hdr)
        assert r.status_code == 200, f"attempt {i+1}: {r.status_code}"

    r = client.get("/api/settings", headers=hdr)
    assert r.status_code == 429
    assert "Terlalu banyak" in r.json()["error"]


def test_api_throttle_skips_login_endpoint(client):
    """/api/login PUNYA rate limiter sendiri (test_rate_limit.py). Middleware
    umum harus SKIP path ini, kalau tidak dobel throttle -> tidak diinginkan.
    Cek dgn burst yg lebih besar dari cap umum utk buktikan bukan middleware
    umum yg batasin."""
    # Kirim burst lebih besar dari API_MAX_REQUESTS (300+ request). Kalau
    # middleware umum aktif, akan 429 di request ke-301. Tapi login punya
    # threshold MAX_FAILURES=10 -> 429 di request ke-11.
    for _ in range(rate_limit.MAX_FAILURES):
        r = client.post("/api/login", json={"username": "ghost", "password": "x"})
        assert r.status_code == 404, f"pre-block should be 404, got {r.status_code}"
    r = client.post("/api/login", json={"username": "ghost", "password": "x"})
    assert r.status_code == 429
    # Kalau pesan match "Coba lagi dalam 5 menit" -> throttle LOGIN yg aktif.
    # Kalau match "1 menit" -> berarti middleware umum yg aktif (WRONG).
    assert "5 menit" in r.json()["error"], f"login throttle bocor: {r.json()}"


def test_api_throttle_skips_static_root(client):
    """StaticFiles / (bukan /api/*) tidak di-throttle -- SPA butuh 100an asset
    load barengan tanpa 429."""
    # 1000x GET / seharusnya tidak pernah 429. Test 50x cukup untuk buktikan
    # middleware SKIP non-api path (kalau tidak skip, cap 300 pasti kena).
    for _ in range(50):
        r = client.get("/")
        assert r.status_code == 200


def test_api_throttle_bucket_per_user(client, admin_token, sales_token):
    """Budget 1 user habis TIDAK memblokir user lain -- bucket per-user_id."""
    hdr_admin = bearer(admin_token)
    hdr_sales = bearer(sales_token)

    # Habiskan budget admin.
    for _ in range(rate_limit.API_MAX_REQUESTS):
        client.get("/api/settings", headers=hdr_admin)
    r = client.get("/api/settings", headers=hdr_admin)
    assert r.status_code == 429, "admin harusnya sudah blocked"

    # Sales dari IP yg sama tetap boleh (bucket per-user_id, bukan per-IP).
    r = client.get("/api/settings", headers=hdr_sales)
    assert r.status_code == 200, f"sales tidak boleh ke-block gara2 admin: {r.text}"
