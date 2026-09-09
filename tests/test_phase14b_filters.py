"""
Phase 14b: Filter Paket + Periode Closing (date range) di 4 halaman.

Uji:
- /api/sales/home?package=X    -> year_summary/this_month ke-scope paket X
- /api/mgmt/home?package=X     -> year_summary/this_month/sales_perf ke-scope paket X
- /api/jamaah?package=X&closing_from=&closing_to=  -> list jamaah filter WHERE
- /api/marketing/summary?closing_from=&closing_to= -> agregat filter tanggal
"""
import datetime

from tests.conftest import bearer

import db


_JID = [900_000]


def _seed_jamaah(pkg_name, order_date, price=15_000_000):
    _JID[0] += 1
    nik = f"9999{_JID[0]:012d}"
    db.execute(
        "INSERT INTO jamaah (nik, name, phone, package_type, total_price, "
        "status, order_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (nik, f"P14b {pkg_name} {order_date}", "081200000009",
         pkg_name, price, "Terdaftar", order_date),
    )


def test_sales_home_filter_by_package(client, admin_token):
    """Seed 2 jamaah beda paket di bulan+tahun ini; filter ?package=X hanya count X."""
    now = datetime.datetime.now()
    ymd = now.strftime("%Y-%m-15")
    pkg_x = "Phase14bPaketX"
    pkg_y = "Phase14bPaketY"
    _seed_jamaah(pkg_x, ymd, 20_000_000)
    _seed_jamaah(pkg_y, ymd, 30_000_000)

    r_all = client.get("/api/sales/home", headers=bearer(admin_token))
    r_x = client.get(f"/api/sales/home?package={pkg_x}", headers=bearer(admin_token))
    assert r_all.status_code == 200 and r_x.status_code == 200
    d_all = r_all.json()
    d_x = r_x.json()

    assert d_x["package"] == pkg_x
    # Filter tsb menyempitkan year_summary + this_month
    assert d_x["year_summary"]["closing_total"] <= d_all["year_summary"]["closing_total"]
    assert d_x["kpi"]["closing_this_month"] <= d_all["kpi"]["closing_this_month"]


def test_mgmt_home_filter_by_package(client, admin_token):
    now = datetime.datetime.now()
    ymd = now.strftime("%Y-%m-16")
    pkg_x = "Phase14bMgmtX"
    _seed_jamaah(pkg_x, ymd, 25_000_000)
    _seed_jamaah("Phase14bMgmtY", ymd, 25_000_000)

    r_all = client.get("/api/mgmt/home", headers=bearer(admin_token))
    r_x = client.get(f"/api/mgmt/home?package={pkg_x}", headers=bearer(admin_token))
    assert r_all.status_code == 200 and r_x.status_code == 200
    d_x = r_x.json()
    assert d_x["package"] == pkg_x
    # year_summary ke-scope; snapshot cash tetap
    d_all = r_all.json()
    assert d_x["year_summary"]["closing_total"] <= d_all["year_summary"]["closing_total"]
    # Cash saldo (snapshot global) tidak berubah oleh filter paket
    assert d_x["kpi"]["cash_saldo"] == d_all["kpi"]["cash_saldo"]


def test_jamaah_list_filter_package_and_daterange(client, admin_token):
    """List jamaah dengan filter package + closing_from/to."""
    pkg = "Phase14bListPaket"
    _seed_jamaah(pkg, "2024-05-10", 10_000_000)
    _seed_jamaah(pkg, "2024-07-20", 10_000_000)
    _seed_jamaah(pkg, "2024-09-05", 10_000_000)

    r = client.get(
        f"/api/jamaah?package={pkg}&closing_from=2024-06-01&closing_to=2024-08-31",
        headers=bearer(admin_token),
    )
    assert r.status_code == 200
    rows = r.json()
    names = [x["name"] for x in rows if x.get("package_type") == pkg]
    assert len(names) == 1
    assert "2024-07-20" in names[0]


def test_marketing_summary_filter_closing_daterange(client, admin_token):
    """Marketing summary hormati closing_from/closing_to."""
    pkg = "Phase14bMktRange"
    _seed_jamaah(pkg, "2024-03-15", 8_000_000)
    _seed_jamaah(pkg, "2024-06-15", 8_000_000)
    _seed_jamaah(pkg, "2024-09-15", 8_000_000)

    r = client.get(
        f"/api/marketing/summary?paket={pkg}&closing_from=2024-05-01&closing_to=2024-07-31",
        headers=bearer(admin_token),
    )
    assert r.status_code == 200
    d = r.json()
    assert d["kpi"]["total_jamaah"] == 1
    assert d["kpi"]["total_omzet"] == 8_000_000
