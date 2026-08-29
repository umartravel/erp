"""
Integration test: gate kuota paket saat register jamaah baru.

Skenario:
- Admin bikin paket dgn quota=2.
- 2 jamaah masuk lewat sales/admin -> OK.
- Jamaah ke-3 dari SALES -> ditolak 400 (kuota penuh).
- Jamaah ke-3 dari ADMIN -> LOLOS (admin bisa bypass gate untuk oversell).

Ini adalah gerbang bisnis kritis. Regressi = ops kelabakan handle jamaah
lebih dari kursi.
"""
from tests.conftest import bearer


def _make_test_pkg(client, admin_token, name, quota=2, price=25_000_000):
    """Bikin paket dgn quota terbatas untuk test."""
    hdr = bearer(admin_token)
    r = client.post("/api/packages", json={
        "name": name, "price": price, "price_quad": price,
        "departure_date": "2027-01-15", "duration": 9, "quota": quota,
        "default_commission_fee": 0, "route_type": "Direct",
    }, headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()["id"], price


def _add_jamaah(client, token, nik, name, pkg_name, price):
    return client.post("/api/jamaah", json={
        "nik": nik, "name": name, "phone": "0811" + nik[-8:],
        "package_type": pkg_name, "total_price": price,
        "room_type": "QUAD", "status": "Terdaftar",
    }, headers=bearer(token))


def test_quota_gate_blocks_sales_over_limit(client, admin_token, sales_token):
    """Isi kuota sampai penuh, jamaah ke-3 dari SALES ditolak."""
    pkg_name = "TEST Paket Quota Sales"
    _, price = _make_test_pkg(client, admin_token, pkg_name, quota=2)

    r = _add_jamaah(client, sales_token, "9999999999994001", "TEST Quota J1", pkg_name, price)
    assert r.status_code == 200, r.text
    r = _add_jamaah(client, sales_token, "9999999999994002", "TEST Quota J2", pkg_name, price)
    assert r.status_code == 200, r.text

    r = _add_jamaah(client, sales_token, "9999999999994003", "TEST Quota J3", pkg_name, price)
    assert r.status_code == 400
    assert "kuota" in r.json()["error"].lower()


def test_quota_gate_admin_can_bypass(client, admin_token):
    """Admin lolos gate walau kuota penuh (oversell allowed).

    Dari gatekeeper di routes/jamaah_read.py line 79:
        if row["filled"] >= row["quota"] and user["role"] != "admin":
            raise 400
    Admin bypass untuk kasus force-book yang butuh koreksi manual.
    """
    pkg_name = "TEST Paket Quota AdminBypass"
    _, price = _make_test_pkg(client, admin_token, pkg_name, quota=1)

    r = _add_jamaah(client, admin_token, "9999999999994011", "TEST Quota AJ1", pkg_name, price)
    assert r.status_code == 200

    r = _add_jamaah(client, admin_token, "9999999999994012", "TEST Quota AJ2 Bypass", pkg_name, price)
    assert r.status_code == 200, f"Admin harusnya bypass gate: {r.text}"


def test_quota_cancelled_jamaah_not_counted(client, admin_token, sales_token):
    """Jamaah Cancelled tidak dihitung ke filled. Baru bikin 1 slot lagi kalau ada 1 yang cancelled."""
    pkg_name = "TEST Paket Quota Cancelled"
    _, price = _make_test_pkg(client, admin_token, pkg_name, quota=2)

    r = _add_jamaah(client, admin_token, "9999999999994021", "TEST Quota C1", pkg_name, price)
    jid1 = r.json()["id"]
    _add_jamaah(client, admin_token, "9999999999994022", "TEST Quota C2", pkg_name, price)

    r = _add_jamaah(client, sales_token, "9999999999994023", "TEST Quota C3", pkg_name, price)
    assert r.status_code == 400

    # Cancel jamaah #1 via PUT big update (set status=Cancelled)
    # NB: perlu big update sebab quota query filter j.status NOT IN ('Cancelled').
    r = client.put(f"/api/jamaah/{jid1}", json={
        "nik": "9999999999994021", "name": "TEST Quota C1",
        "phone": "081100094021", "status": "Cancelled",
        "package_type": pkg_name, "total_price": price, "room_type": "QUAD",
    }, headers=bearer(admin_token))
    assert r.status_code == 200, r.text

    r = _add_jamaah(client, sales_token, "9999999999994024", "TEST Quota C4", pkg_name, price)
    assert r.status_code == 200, f"setelah cancel harusnya lolos: {r.text}"
