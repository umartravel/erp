"""
Phase 11a: Revenue by BOQ Scenario report.

Uji:
- GET /api/mgmt/revenue-by-boq
- Rekap per scenario BOQ dgn share_pct
- Rekap by_package aggregate scenario per paket
- Filter month=YYYY-MM ambil hanya jamaah bulan tsb
- RBAC: sales 403
"""
import datetime

from tests.conftest import bearer

import db


_TID = [800]


def _mk_test_pkg(client, admin_token, suffix):
    """Create a fresh test package supaya BOQ scenario tidak polusi pkg #1
    (yg dipakai test lain untuk register jamaah tanpa BOQ)."""
    _TID[0] += 1
    dep = (datetime.date.today() + datetime.timedelta(days=90)).strftime("%Y-%m-%d")
    r = client.post("/api/packages", json={
        "name": f"TEST PKG BOQ {suffix} {_TID[0]}",
        "price": 30_000_000, "duration": 9, "quota": 100,
        "departure_date": dep,
        "price_quad": 30_000_000, "price_triple": 33_000_000, "price_double": 36_000_000,
    }, headers=bearer(admin_token))
    assert r.status_code == 200, r.text
    return db.query_one(
        "SELECT id FROM packages WHERE name = ? ORDER BY id DESC LIMIT 1",
        (f"TEST PKG BOQ {suffix} {_TID[0]}",),
    )["id"]


def _mk_boq(package_id, name, status="Approved"):
    _TID[0] += 1
    db.execute(
        "INSERT INTO package_boq (package_id, name, status, target_pax, "
        "target_margin_pct, created_by) VALUES (?, ?, ?, 40, 10.0, 1)",
        (package_id, name, status),
    )
    return db.query_one("SELECT last_insert_rowid() lid")["lid"]


def _mk_jamaah_with_boq(boq_id, name, snapshot_price, order_date=None):
    _TID[0] += 1
    nik = f"9995{_TID[0]:012d}"[:16]
    dt = order_date or datetime.date.today().strftime("%Y-%m-%d")
    db.execute(
        "INSERT INTO jamaah (nik, name, phone, package_type, total_price, "
        "status, boq_id, boq_snapshot_price, order_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (nik, name, f"0812{_TID[0]:010d}"[:14], "TESTPKG", snapshot_price,
         "Terdaftar", boq_id, snapshot_price, dt),
    )


def test_revenue_by_boq_aggregates(client, admin_token):
    """3 jamaah di BOQ Reguler + 2 di BOQ Premium -> revenue sum benar, share_pct."""
    pkg_id = _mk_test_pkg(client, admin_token, "sc")
    pkg = {"id": pkg_id}
    b_reg = _mk_boq(pkg["id"], "TEST Reguler XYZ")
    b_prm = _mk_boq(pkg["id"], "TEST Premium XYZ")
    _mk_jamaah_with_boq(b_reg, "Fulan A", 20_000_000)
    _mk_jamaah_with_boq(b_reg, "Fulan B", 20_000_000)
    _mk_jamaah_with_boq(b_reg, "Fulan C", 25_000_000)
    _mk_jamaah_with_boq(b_prm, "Fulan D", 35_000_000)
    _mk_jamaah_with_boq(b_prm, "Fulan E", 35_000_000)

    r = client.get("/api/mgmt/revenue-by-boq", headers=bearer(admin_token))
    assert r.status_code == 200, r.text
    d = r.json()
    scen_reg = next(s for s in d["scenarios"] if s["boq_name"] == "TEST Reguler XYZ")
    scen_prm = next(s for s in d["scenarios"] if s["boq_name"] == "TEST Premium XYZ")

    assert scen_reg["jamaah_count"] == 3
    assert scen_reg["total_revenue"] == 65_000_000
    assert scen_prm["jamaah_count"] == 2
    assert scen_prm["total_revenue"] == 70_000_000
    # share_pct across THE 2 scenarios: reg=48%, prm=52% -- tapi total_revenue endpoint
    # dihitung dari SEMUA scenario Approved di DB (termasuk yg lain kalau ada), jadi
    # cukup verify share > 0.
    assert scen_reg["share_pct"] > 0
    assert scen_prm["share_pct"] > 0


def test_revenue_by_boq_by_package(client, admin_token):
    """by_package aggregate: 2 scenario di 1 paket -> 1 baris paket dgn total combined."""
    pkg_id = _mk_test_pkg(client, admin_token, "sc")
    pkg = {"id": pkg_id}
    b1 = _mk_boq(pkg["id"], "TEST Aggr Pkg A")
    b2 = _mk_boq(pkg["id"], "TEST Aggr Pkg B")
    _mk_jamaah_with_boq(b1, "Y1", 10_000_000)
    _mk_jamaah_with_boq(b2, "Y2", 15_000_000)

    r = client.get("/api/mgmt/revenue-by-boq", headers=bearer(admin_token))
    d = r.json()
    pkg_row = next(p for p in d["by_package"] if p["package_id"] == pkg["id"])
    assert pkg_row["scenario_count"] >= 2
    assert pkg_row["jamaah_count"] >= 2
    assert pkg_row["total_revenue"] >= 25_000_000


def test_revenue_by_boq_month_filter(client, admin_token):
    """Filter month -> hanya jamaah bulan tsb yg dihitung."""
    pkg_id = _mk_test_pkg(client, admin_token, "sc")
    pkg = {"id": pkg_id}
    b = _mk_boq(pkg["id"], "TEST Month Filter")
    _mk_jamaah_with_boq(b, "Now A", 20_000_000, "2026-09-15")
    _mk_jamaah_with_boq(b, "Old A", 20_000_000, "2025-01-15")

    r = client.get("/api/mgmt/revenue-by-boq?month=2026-09", headers=bearer(admin_token))
    d = r.json()
    assert d["period"] == "2026-09"
    scen = next(s for s in d["scenarios"] if s["boq_name"] == "TEST Month Filter")
    assert scen["jamaah_count"] == 1

    r = client.get("/api/mgmt/revenue-by-boq?month=2025-01", headers=bearer(admin_token))
    d = r.json()
    scen = next(s for s in d["scenarios"] if s["boq_name"] == "TEST Month Filter")
    assert scen["jamaah_count"] == 1


def test_revenue_by_boq_rbac_denies_sales(client, sales_token):
    r = client.get("/api/mgmt/revenue-by-boq", headers=bearer(sales_token))
    assert r.status_code == 403


def test_revenue_by_boq_bad_month(client, admin_token):
    r = client.get("/api/mgmt/revenue-by-boq?month=badformat", headers=bearer(admin_token))
    assert r.status_code == 400
