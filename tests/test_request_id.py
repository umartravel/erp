"""
Integration test: X-Request-Id middleware.

Skenario:
- Request tanpa X-Request-Id -> response header berisi UUID auto-generate.
- Request DENGAN X-Request-Id -> response echo balik nilai yg sama (untuk
  trace cross-service via reverse proxy).
- Request ID hadir di response 200 dan 401/404 (exception path tetap kena).
"""


def _rid(resp):
    return resp.headers.get("X-Request-Id")


def test_request_id_auto_generated(client):
    """Tanpa header masuk -> middleware generate UUID pendek."""
    r = client.get("/")
    assert r.status_code == 200
    rid = _rid(r)
    assert rid, "X-Request-Id header tidak di-set"
    # UUID pendek 12 hex chars.
    assert len(rid) == 12 and all(c in "0123456789abcdef" for c in rid), \
        f"format request_id tidak seperti UUID hex pendek: {rid!r}"


def test_request_id_echoed_from_upstream(client):
    """Kalau upstream (LB/nginx) kirim X-Request-Id, harus echo balik utk
    trace cross-service."""
    my_id = "trace-abc-123"
    r = client.get("/", headers={"X-Request-Id": my_id})
    assert _rid(r) == my_id


def test_request_id_on_error_response(client):
    """Exception path (401 unauth) juga harus punya request_id -- ini penting
    supaya user bisa lapor 'error di request id X'."""
    r = client.get("/api/jamaah")  # no token -> 401
    assert r.status_code == 401
    assert _rid(r), "request_id hilang di response error"


def test_request_id_unique_per_request(client):
    """2 request beda -> 2 request_id beda (bukan cached)."""
    a = _rid(client.get("/"))
    b = _rid(client.get("/"))
    assert a and b and a != b, f"request_id sama antar-request: {a} vs {b}"
