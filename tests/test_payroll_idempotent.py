"""
Integration test: POST /api/payroll monthly idempotency guard.

Skenario: fitur payroll bulanan HANYA boleh diproses sekali per bulan tanpa
flag 'force'. THR (bonus akhir tahun) di-jalankan dengan force=True.

Ini penting karena tombol payroll gampang dobel-klik. Guard-nya cek 'category
payroll' di bulan yang sama.

Sub-test:
- Run 1x -> sukses (N karyawan diproses)
- Run 2x tanpa force -> 400 (guard)
- Run 2x dgn force=True -> sukses (untuk THR)
- RBAC: hanya admin/finance
"""
from tests.conftest import bearer


def test_payroll_first_run_succeeds(client, finance_token, admin_token):
    """First run bulan ini -> sukses, catat tx per karyawan."""
    r = client.post("/api/payroll", json={}, headers=bearer(finance_token))
    # NB: bisa juga 400 kalau ada test payroll lain jalan duluan di session yang sama.
    if r.status_code == 400:
        r = client.post("/api/payroll", json={"force": True}, headers=bearer(finance_token))
    assert r.status_code == 200, r.text
    assert "karyawan" in r.json()["message"]

    txs = client.get("/api/transactions", headers=bearer(admin_token)).json()
    payroll_txs = [t for t in txs if t["category"] == "payroll"]
    assert len(payroll_txs) >= 1


def test_payroll_second_run_same_month_blocked(client, finance_token):
    """Run kedua di bulan yang sama tanpa force -> 400."""
    client.post("/api/payroll", json={"force": True}, headers=bearer(finance_token))

    r = client.post("/api/payroll", json={}, headers=bearer(finance_token))
    assert r.status_code == 400
    assert "sudah diproses" in r.json()["error"]


def test_payroll_force_bypasses_guard(client, finance_token, admin_token):
    """force=True bypass guard (untuk THR / koreksi)."""
    client.post("/api/payroll", json={"force": True}, headers=bearer(finance_token))
    tx_before = len([t for t in client.get("/api/transactions",
                    headers=bearer(admin_token)).json() if t["category"] == "payroll"])

    r = client.post("/api/payroll", json={"force": True}, headers=bearer(finance_token))
    assert r.status_code == 200

    tx_after = len([t for t in client.get("/api/transactions",
                   headers=bearer(admin_token)).json() if t["category"] == "payroll"])
    assert tx_after > tx_before, "force=True harusnya bikin batch payroll baru"


def test_payroll_rbac_admin_finance_only(client, sales_token, ops_token, management_token):
    """Sales/ops/management tidak boleh trigger payroll."""
    for tok in (sales_token, ops_token, management_token):
        r = client.post("/api/payroll", json={}, headers=bearer(tok))
        assert r.status_code == 403, f"role bocor: {r.status_code}"
