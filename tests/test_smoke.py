"""
Smoke test: memastikan semua router utama respons (200 utk endpoint sesuai
role, atau 401/403 utk yang harus di-gate). Regression net setelah refactor.
"""
from tests.conftest import bearer


ADMIN_ENDPOINTS_200 = [
    "/api/dashboard/super",
    "/api/tactical-stats",
    "/api/audit-logs",
    "/api/users",
    "/api/users/directory",
    "/api/users/me",
    "/api/jamaah",
    "/api/packages",
    "/api/agents",
    "/api/agents/performance",
    "/api/incidents",
    "/api/ops/home",
    "/api/sales/home",
    "/api/inventory",
    "/api/company-assets",
    "/api/procurement",
    "/api/transactions",
    "/api/expense-projects",
    "/api/expense-approvers",
    "/api/expense-reports",
    "/api/refund-requests",
    "/api/commission-claims",
    "/api/marketing/summary",
    "/api/marketing/filters",
    "/api/reports/pnl",
    "/api/reports/expense-matrix",
    "/api/finance/home",
    "/api/finance/aged-receivable",
    "/api/finance/forecast",
    "/api/finance/reconcile/batches",
    "/api/finance/reconcile/mutations",
    "/api/mgmt/home",
    "/api/mgmt/risk-register",
    "/api/mgmt/company-targets",
    "/api/wa/status",
    "/api/wa/templates",
    "/api/leave-requests",
    "/api/pendaftaran-publik",
    "/api/pendaftaran-agen",
    "/api/followups",
    "/api/sales/performance",
    "/api/sales/targets",
    "/api/sales/targets/me",
]


def test_admin_all_endpoints_200(client, admin_token):
    """Admin token boleh hit semua endpoint utama -> 200."""
    hdr = bearer(admin_token)
    failed = []
    for url in ADMIN_ENDPOINTS_200:
        r = client.get(url, headers=hdr)
        if r.status_code != 200:
            failed.append((url, r.status_code, r.text[:100]))
    assert not failed, f"Endpoint gagal: {failed}"


UNAUTH_ENDPOINTS_401 = [
    "/api/dashboard/super",
    "/api/users",
    "/api/jamaah",
    "/api/incidents",
    "/api/wa/status",
    "/api/audit-logs",
    "/api/procurement",
    "/api/marketing/summary",
    "/api/expense-projects",
]


def test_unauth_all_endpoints_401(client):
    """Tanpa token -> 401 utk semua endpoint auth-guarded."""
    failed = []
    for url in UNAUTH_ENDPOINTS_401:
        r = client.get(url)
        if r.status_code != 401:
            failed.append((url, r.status_code))
    assert not failed, f"Endpoint tidak 401: {failed}"


def test_sales_rbac(client, sales_token):
    """Sales boleh Home Sales & followups, TAPI tidak boleh audit-logs / users."""
    hdr = bearer(sales_token)
    assert client.get("/api/sales/home", headers=hdr).status_code == 200
    assert client.get("/api/followups", headers=hdr).status_code == 200
    assert client.get("/api/jamaah", headers=hdr).status_code == 200
    assert client.get("/api/audit-logs", headers=hdr).status_code == 403
    assert client.get("/api/users", headers=hdr).status_code == 403


def test_ops_rbac(client, ops_token):
    """Ops boleh Home Ops + incidents, TAPI tidak boleh /api/users."""
    hdr = bearer(ops_token)
    assert client.get("/api/ops/home", headers=hdr).status_code == 200
    assert client.get("/api/incidents", headers=hdr).status_code == 200
    assert client.get("/api/users", headers=hdr).status_code == 403


def test_finance_rbac(client, finance_token):
    """Finance boleh Home Finance + transactions, TAPI tidak boleh /api/users."""
    hdr = bearer(finance_token)
    assert client.get("/api/finance/home", headers=hdr).status_code == 200
    assert client.get("/api/transactions", headers=hdr).status_code == 200
    assert client.get("/api/users", headers=hdr).status_code == 403


def test_bulk_ops_not_shadowed(client, admin_token):
    """PUT /api/jamaah/bulk-ops HARUS di-route ke bulk-ops handler (bukan
    di-cast ke {jid: int} yg gagal 422). Regression check untuk routing order
    di routes/jamaah_write.py."""
    hdr = bearer(admin_token)
    r = client.put("/api/jamaah/bulk-ops", json={"updates": []}, headers=hdr)
    # updates kosong -> 400 dari handler, BUKAN 422 dari path param validation.
    assert r.status_code == 400, f"Bulk-ops di-shadow: {r.status_code} {r.text}"


def test_uploads_gated(client):
    """/uploads/{filename} tanpa token -> 401 (bukan 404 dari StaticFiles).
    Regression check: static_pages_router di-include SEBELUM StaticFiles mount."""
    r = client.get("/uploads/nonexistent.jpg")
    assert r.status_code == 401


def test_static_html_pages(client):
    """Halaman HTML publik dilayani sebagai FileResponse."""
    for url in ["/panduan", "/daftar", "/daftar-agen", "/dokumentasi-teknis"]:
        r = client.get(url)
        assert r.status_code == 200, f"{url} -> {r.status_code}"
        assert "<html" in r.text.lower() or "<!doctype" in r.text.lower(), f"{url} bukan HTML"
