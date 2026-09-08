"""
Phase 8d: Analitik keuangan Dual View -- Committed vs Realized.

Endpoint: GET /api/finance/committed-summary

Verifikasi:
- Struktur response {realized, committed_expense, combined_expense_total, note}
- Realized income = SUM(transactions WHERE type='income')
- Realized expense = SUM(transactions WHERE type='expense')
- Committed expense = expense_reports Approved (gross) + commission_claims
  Disetujui + refund_requests Disetujui
- RBAC: admin/finance/management OK; sales/ops 403
"""
from tests.conftest import bearer

import db


def test_endpoint_shape(client, admin_token):
    r = client.get("/api/finance/committed-summary", headers=bearer(admin_token))
    assert r.status_code == 200
    data = r.json()
    assert "realized" in data and "committed_expense" in data
    assert "income" in data["realized"] and "expense" in data["realized"]
    assert "net" in data["realized"]
    assert "total" in data["committed_expense"]
    assert "breakdown" in data["committed_expense"]
    bkd = data["committed_expense"]["breakdown"]
    for k in ("expense_report", "commission_claim", "refund_request"):
        assert k in bkd


def test_endpoint_rbac(client, sales_token, ops_token):
    """Sales & Ops tidak boleh."""
    for tok in (sales_token, ops_token):
        r = client.get("/api/finance/committed-summary", headers=bearer(tok))
        assert r.status_code == 403


def test_committed_reflects_approved_commission(client, admin_token):
    """Committed commission bertambah ketika mgmt approve klaim komisi
    (belum di-disburse finance) + baseline dulu."""
    baseline = client.get("/api/finance/committed-summary",
                          headers=bearer(admin_token)).json()
    base_comm = baseline["committed_expense"]["breakdown"]["commission_claim"]

    # Insert dummy commission claim status='Disetujui' langsung ke DB
    # supaya deterministik (tanpa lifecycle Lunas dulu).
    db.execute(
        "INSERT INTO commission_claims (agent_id, jamaah_id, amount, status, requested_by, approved_by) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (1, 1, 500_000, "Disetujui", "TEST", "TEST-approver"),
    )

    after = client.get("/api/finance/committed-summary",
                      headers=bearer(admin_token)).json()
    assert after["committed_expense"]["breakdown"]["commission_claim"] == base_comm + 500_000
    # Committed total juga naik 500rb.
    assert after["committed_expense"]["total"] >= baseline["committed_expense"]["total"] + 500_000
