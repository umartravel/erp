"""
Phase 12a: Lead Scoring / Prioritas Follow-up.

Uji:
- GET /api/sales/lead-scores
- Response shape: leads[], scope, generated_at, total_candidates
- H-7 + DP masuk 50% -> score tinggi
- Belum pernah dikontak + jauh dari berangkat -> score lebih rendah
- MY scope untuk sales -> hanya jamaah miliknya
- Admin dapat aggregate
- RBAC: finance/ops 403
"""
import datetime

from tests.conftest import bearer

import db


_LID = [900]


def _seed_lead(sales_id, name, pkg_name, paid_amount=0, total_price=30_000_000,
               days_no_contact=None, status="Terdaftar"):
    _LID[0] += 1
    nik = f"9994{_LID[0]:012d}"[:16]
    last_contact = None
    if days_no_contact is not None:
        last_contact = (datetime.datetime.now() - datetime.timedelta(days=days_no_contact)
                        ).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "INSERT INTO jamaah (nik, name, phone, package_type, total_price, "
        "paid_amount, status, sales_id, last_contact) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (nik, name, f"0812{_LID[0]:010d}"[:14], pkg_name, total_price,
         paid_amount, status, sales_id, last_contact),
    )
    return db.query_one("SELECT last_insert_rowid() lid")["lid"]


def _sales_id(username):
    r = db.query_one("SELECT id FROM users WHERE username = ?", (username,))
    return r["id"] if r else None


def _mk_test_pkg(client, admin_token, name_suffix, days_until_departure):
    dep = (datetime.date.today() + datetime.timedelta(days=days_until_departure)
           ).strftime("%Y-%m-%d")
    r = client.post("/api/packages", json={
        "name": f"TEST LeadScoreScored {name_suffix}",
        "price": 30_000_000, "duration": 9, "quota": 100,
        "departure_date": dep,
        "price_quad": 30_000_000, "price_triple": 33_000_000, "price_double": 36_000_000,
    }, headers=bearer(admin_token))
    assert r.status_code == 200, r.text
    return db.query_one(
        "SELECT id, name FROM packages WHERE name = ? ORDER BY id DESC LIMIT 1",
        (f"TEST LeadScoreScored {name_suffix}",),
    )


def test_lead_scores_response_shape(client, sales_token, admin_token):
    """Endpoint respond dgn struktur benar."""
    r = client.get("/api/sales/lead-scores", headers=bearer(sales_token))
    assert r.status_code == 200
    d = r.json()
    assert "leads" in d
    assert "scope" in d
    assert "generated_at" in d
    assert "total_candidates" in d
    assert isinstance(d["leads"], list)


def test_h7_lead_gets_high_score(client, admin_token):
    """Jamaah H-7 + DP 50% -> score >= 90."""
    sid = _sales_id("admin")
    pkg = _mk_test_pkg(client, admin_token, "H7", days_until_departure=5)
    _seed_lead(sid, "Prioritas H7 Fulan", pkg["name"],
               paid_amount=15_000_000, total_price=30_000_000,
               days_no_contact=1, status="DP Masuk")

    r = client.get("/api/sales/lead-scores?scope=all", headers=bearer(admin_token))
    d = r.json()
    lead = next((l for l in d["leads"] if l["name"] == "Prioritas H7 Fulan"), None)
    assert lead is not None
    assert lead["score"] >= 90, f"expected >= 90, got {lead['score']}"
    # Faktor breakdown ada
    assert "factors" in lead
    assert lead["factors"]["urgency"] == 30  # H-5 -> +30


def test_never_contacted_far_departure_low_score(client, admin_token):
    """Belum pernah dikontak + berangkat > 60d + unpaid -> score rendah."""
    sid = _sales_id("admin")
    pkg = _mk_test_pkg(client, admin_token, "Far", days_until_departure=120)
    _seed_lead(sid, "Cold Lead Fulan", pkg["name"],
               paid_amount=0, total_price=30_000_000,
               days_no_contact=None, status="Terdaftar")

    # Ambil limit besar supaya cold lead pasti masuk list (test lain buat banyak
    # high-score leads di pool).
    r = client.get("/api/sales/lead-scores?scope=all&limit=100",
                   headers=bearer(admin_token))
    d = r.json()
    lead = next((l for l in d["leads"] if l["name"] == "Cold Lead Fulan"), None)
    assert lead is not None, f"Cold Lead tidak dalam top 100 dari {d['total_candidates']} leads"
    # Baseline 50 + recency (-20) + payment (-5) + urgency (0) = 25
    assert lead["score"] <= 30, f"expected <= 30, got {lead['score']}"


def test_lead_scores_sorted_desc(client, admin_token):
    """List sorted DESC by score."""
    r = client.get("/api/sales/lead-scores?scope=all", headers=bearer(admin_token))
    d = r.json()
    scores = [l["score"] for l in d["leads"]]
    assert scores == sorted(scores, reverse=True)


def test_lead_scores_my_scope_for_sales(client, sales_token, admin_token):
    """Sales scope='my' -> hanya jamaah sales itu sendiri."""
    sid = _sales_id("sales1")
    other = _sales_id("admin")
    pkg = _mk_test_pkg(client, admin_token, "Scope", days_until_departure=15)
    _seed_lead(sid, "MyLead SalesOne", pkg["name"], days_no_contact=5)
    _seed_lead(other, "OtherLead Admin", pkg["name"], days_no_contact=5)

    r = client.get("/api/sales/lead-scores", headers=bearer(sales_token))
    d = r.json()
    names = [l["name"] for l in d["leads"]]
    assert "MyLead SalesOne" in names
    assert "OtherLead Admin" not in names


def test_lead_scores_rbac_denies_finance(client, finance_token):
    r = client.get("/api/sales/lead-scores", headers=bearer(finance_token))
    assert r.status_code == 403
