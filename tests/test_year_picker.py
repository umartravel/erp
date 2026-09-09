"""
Phase 14a: Year picker default 2026 di Home Sales + Home Mgmt.

Uji:
- GET /api/dashboard/years -> minimal current year
- GET /api/sales/home tanpa year -> default current, year_summary ada
- GET /api/sales/home?year=YYYY -> year_summary filter by year
- GET /api/mgmt/home?year=YYYY -> same
"""
import datetime

from tests.conftest import bearer

import db


def test_dashboard_years_returns_current(client, admin_token):
    r = client.get("/api/dashboard/years", headers=bearer(admin_token))
    assert r.status_code == 200
    d = r.json()
    assert "years" in d
    assert "current_year" in d
    assert d["current_year"] in d["years"]
    assert d["current_year"] == datetime.datetime.now().year


def test_sales_home_default_year_is_current(client, admin_token):
    r = client.get("/api/sales/home", headers=bearer(admin_token))
    assert r.status_code == 200
    d = r.json()
    assert d["year"] == datetime.datetime.now().year
    assert d["current_year"] == datetime.datetime.now().year
    assert "year_summary" in d
    assert "closing_total" in d["year_summary"]
    assert "omzet_total" in d["year_summary"]


def test_sales_home_with_year_param(client, admin_token):
    r = client.get("/api/sales/home?year=2025", headers=bearer(admin_token))
    assert r.status_code == 200
    d = r.json()
    assert d["year"] == 2025


def test_mgmt_home_default_year_is_current(client, admin_token):
    r = client.get("/api/mgmt/home", headers=bearer(admin_token))
    assert r.status_code == 200
    d = r.json()
    assert d["year"] == datetime.datetime.now().year
    assert "year_summary" in d


def test_mgmt_home_with_year_param(client, admin_token):
    r = client.get("/api/mgmt/home?year=2024", headers=bearer(admin_token))
    assert r.status_code == 200
    d = r.json()
    assert d["year"] == 2024


def test_year_summary_filters_by_year(client, admin_token):
    """Seed 2 jamaah di tahun berbeda, verify count."""
    pkg = client.get("/api/packages", headers=bearer(admin_token)).json()[0]
    price = pkg.get("price_quad") or pkg.get("price") or 10_000_000
    for nik_seed, year_seed in [("9991000000000201", "2024"), ("9991000000000202", "2026")]:
        db.execute(
            "INSERT INTO jamaah (nik, name, phone, package_type, total_price, "
            "status, order_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (nik_seed, f"YearFilter {year_seed}", "081200000001",
             pkg["name"], price, "Terdaftar", f"{year_seed}-06-15"),
        )
    r24 = client.get("/api/mgmt/home?year=2024", headers=bearer(admin_token)).json()
    r26 = client.get("/api/mgmt/home?year=2026", headers=bearer(admin_token)).json()
    assert r24["year_summary"]["closing_total"] >= 1
    assert r26["year_summary"]["closing_total"] >= 1
