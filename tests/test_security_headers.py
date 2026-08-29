"""
Integration test: security response headers middleware.

Cek header defensif hadir di:
- root HTML "/" (SPA landing)
- endpoint JSON API
- endpoint error (401 / 404) -- middleware harus tetap kena walau handler raise
- endpoint autentikasi (401 tanpa token)

Regressi = clickjacking / MIME sniff / drive-by loading kembali terbuka.
"""


def _assert_common_headers(resp):
    """Header yang WAJIB ada di setiap response, apapun status code."""
    csp = resp.headers.get("Content-Security-Policy")
    assert csp is not None, "CSP header hilang"
    # Sanity spot check: default-src harus 'self', frame-ancestors 'none'.
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    # Domain CDN yg dipakai index.html harus di allowlist -- kalau salah satu
    # hilang, seluruh SPA jadi blank di browser (silent break).
    for cdn in ("cdn.tailwindcss.com", "cdn.jsdelivr.net", "cdn.socket.io"):
        assert cdn in csp, f"CDN {cdn} hilang dari CSP -- UI production akan blank"

    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    perm = resp.headers.get("Permissions-Policy")
    assert perm is not None and "camera=()" in perm and "geolocation=()" in perm


def test_security_headers_on_html_root(client):
    """Root SPA (/) -- StaticFiles response harus tetap kena middleware."""
    r = client.get("/")
    assert r.status_code == 200
    _assert_common_headers(r)


def test_security_headers_on_json_api(client, admin_token):
    """Endpoint JSON API (authenticated) -- middleware jalan di semua router."""
    r = client.get("/api/settings", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    _assert_common_headers(r)


def test_security_headers_on_401_response(client):
    """Exception handler (401 unauth) harus TETAP kena middleware --
    kalau middleware register terlambat, error response bisa lolos tanpa header."""
    r = client.get("/api/jamaah")  # no token -> 401
    assert r.status_code == 401
    _assert_common_headers(r)


def test_security_headers_on_404_response(client):
    """Endpoint tidak ada -> 404. Middleware masih jalan."""
    r = client.get("/api/route-yang-tidak-ada-sama-sekali")
    assert r.status_code == 404
    _assert_common_headers(r)


def test_hsts_off_by_default(client):
    """HSTS default OFF supaya localhost HTTP tidak ke-lock ke HTTPS oleh browser."""
    r = client.get("/")
    assert "Strict-Transport-Security" not in r.headers
