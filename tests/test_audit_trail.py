"""
Phase 10a: Audit Trail viewer.

Uji:
- GET /api/audit/jamaah/{jid}   return log setelah CREATE + UPDATE
- Filter action jamaah-relevant (UPDATE_JAMAAH etc), skip action lain
- 404 untuk jid tidak ada
- RBAC: semua role kerja (admin/sales/mgmt/finance/ops) boleh baca jamaah;
         package/BOQ endpoint tolak sales/finance
"""
import datetime

from tests.conftest import bearer

import db


_JID_COUNTER = [700]


def _mk_jamaah_via_api(client, admin_token, name, pkg_name, price):
    _JID_COUNTER[0] += 1
    nik = f"9996{_JID_COUNTER[0]:012d}"[:16]
    r = client.post("/api/jamaah", json={
        "nik": nik, "name": name,
        "phone": f"0812{_JID_COUNTER[0]:010d}"[:14],
        "package_type": pkg_name, "total_price": price, "status": "Terdaftar",
    }, headers=bearer(admin_token))
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_audit_jamaah_returns_history(client, admin_token):
    pkg = client.get("/api/packages", headers=bearer(admin_token)).json()[0]
    price = pkg.get("price_quad") or pkg.get("price") or 10_000_000
    jid = _mk_jamaah_via_api(client, admin_token, "Audit E2E Fulan A", pkg["name"], price)

    # Update jamaah utk generate UPDATE_JAMAAH log. Total_price harus sama dgn
    # existing (endpoint tolak perubahan harga tanpa price_change_note).
    r = client.put(f"/api/jamaah/{jid}", json={
        "name": "Audit E2E Fulan A", "phone": "081200077771",
        "status": "DP Masuk", "notes": "test audit",
        "total_price": price,
    }, headers=bearer(admin_token))
    assert r.status_code == 200, r.text

    r = client.get(f"/api/audit/jamaah/{jid}", headers=bearer(admin_token))
    assert r.status_code == 200
    d = r.json()
    assert d["entity"]["id"] == jid
    assert d["count"] >= 1, f"Expected >= 1 log, got: {d['count']}"
    actions = [l["action"] for l in d["logs"]]
    # Setidaknya ada UPDATE_JAMAAH atau CREATE_JAMAAH
    assert any(a in ("UPDATE_JAMAAH", "CREATE_JAMAAH") for a in actions), \
        f"Missing jamaah action in {actions}"


def test_audit_jamaah_404(client, admin_token):
    r = client.get("/api/audit/jamaah/9999999", headers=bearer(admin_token))
    assert r.status_code == 404


def test_audit_jamaah_only_jamaah_actions(client, admin_token):
    """Log dgn action non-jamaah (mis. BOQ_CREATE) tidak boleh muncul di audit jamaah."""
    pkg = client.get("/api/packages", headers=bearer(admin_token)).json()[0]
    price = pkg.get("price_quad") or pkg.get("price") or 10_000_000
    jid = _mk_jamaah_via_api(client, admin_token, "Audit Filter Test", pkg["name"], price)

    # Insert palsu log dgn action BOQ_CREATE yg mention nama jamaah -- ini boleh nyasar
    # hanya kalau filter action tidak ketat. Filter kita: BOQ_CREATE tidak masuk JAMAAH_ACTIONS.
    db.execute(
        "INSERT INTO audit_logs (user_name, action, details, user_id, role) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Test User", "BOQ_CREATE", f"BOQ #99 utk paket -- mention Audit Filter Test", 1, "admin"),
    )
    r = client.get(f"/api/audit/jamaah/{jid}", headers=bearer(admin_token))
    assert r.status_code == 200
    actions = [l["action"] for l in r.json()["logs"]]
    assert "BOQ_CREATE" not in actions, "BOQ_CREATE bocor ke audit jamaah"


def test_audit_jamaah_rbac_allows_all_working_roles(
        client, sales_token, ops_token, finance_token, management_token, admin_token):
    """5 role kerja semua boleh baca audit jamaah (privacy sudah cukup dari
    jamaah-scope RBAC di endpoint lain)."""
    pkg = client.get("/api/packages", headers=bearer(admin_token)).json()[0]
    price = pkg.get("price_quad") or pkg.get("price") or 10_000_000
    jid = _mk_jamaah_via_api(client, admin_token, "Audit RBAC Test", pkg["name"], price)
    for tok in (sales_token, ops_token, finance_token, management_token, admin_token):
        r = client.get(f"/api/audit/jamaah/{jid}", headers=bearer(tok))
        assert r.status_code == 200, f"Role token {tok[:20]} ditolak: {r.status_code}"


def test_audit_package_rbac_denies_sales(client, sales_token, admin_token):
    pkg = client.get("/api/packages", headers=bearer(admin_token)).json()[0]
    r = client.get(f"/api/audit/package/{pkg['id']}", headers=bearer(sales_token))
    assert r.status_code == 403


def test_audit_package_returns_history(client, admin_token):
    pkg = client.get("/api/packages", headers=bearer(admin_token)).json()[0]
    r = client.get(f"/api/audit/package/{pkg['id']}", headers=bearer(admin_token))
    assert r.status_code == 200
    d = r.json()
    assert d["entity"]["type"] == "package"
    assert d["entity"]["id"] == pkg["id"]
    # Boleh 0 log utk paket baru; check structure
    assert isinstance(d["logs"], list)
