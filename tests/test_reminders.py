"""
Phase 9a: Reminder Keberangkatan H-30 / H-7.

Uji end-to-end:
- GET /api/reminders/departures return bucket h7 + h30 sesuai departure_date
- Metrik readiness: visa_ready_pct, paid_full_pct, checklist_pct
- POST /check kirim notif ke role ops
- Dedupe per-hari: call 2x -> notif hanya 1x per (paket, hari, kind)
- RBAC: sales tidak boleh akses
"""
import datetime

from tests.conftest import bearer

import db


def _mk_pkg(client, admin_token, name, departure_date, quota=30):
    r = client.post("/api/packages", json={
        "name": name, "price": 30_000_000, "duration": 9,
        "quota": quota, "departure_date": departure_date,
        "price_quad": 30_000_000, "price_triple": 33_000_000, "price_double": 36_000_000,
    }, headers=bearer(admin_token))
    assert r.status_code == 200, r.text
    return r.json().get("id") or db.query_one(
        "SELECT id FROM packages WHERE name = ? ORDER BY id DESC LIMIT 1", (name,))["id"]


_JID_COUNTER = [0]


def _mk_jamaah(client, admin_token, name, pkg_name, paid_amount, total_price,
               visa_status="Belum Proses"):
    _JID_COUNTER[0] += 1
    nik = f"9998{_JID_COUNTER[0]:012d}"[:16]
    r = client.post("/api/jamaah", json={
        "nik": nik,
        "name": name, "phone": f"0812000{_JID_COUNTER[0]:07d}"[:14],
        "package_type": pkg_name,
        "total_price": total_price, "status": "Terdaftar",
    }, headers=bearer(admin_token))
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    # Set visa + paid_amount via direct DB (bypass endpoint utk isolasi test).
    db.execute("UPDATE jamaah SET visa_status = ?, paid_amount = ? WHERE id = ?",
               (visa_status, paid_amount, jid))
    return jid


def test_list_departures_buckets(client, admin_token):
    """Paket H-4 masuk bucket h7; paket H-20 masuk bucket h30."""
    d_h7 = (datetime.date.today() + datetime.timedelta(days=4)).strftime("%Y-%m-%d")
    d_h30 = (datetime.date.today() + datetime.timedelta(days=20)).strftime("%Y-%m-%d")
    _mk_pkg(client, admin_token, "TEST Bucket H7", d_h7)
    _mk_pkg(client, admin_token, "TEST Bucket H30", d_h30)

    r = client.get("/api/reminders/departures", headers=bearer(admin_token))
    assert r.status_code == 200
    data = r.json()
    h7_names = [p["name"] for p in data["h7"]]
    h30_names = [p["name"] for p in data["h30"]]
    assert "TEST Bucket H7" in h7_names, f"Expected in h7: {h7_names}"
    assert "TEST Bucket H30" in h30_names, f"Expected in h30: {h30_names}"


def test_readiness_metrics_visa_and_paid(client, admin_token):
    """4 jamaah: 3 visa Selesai + 2 lunas -> visa 75%, paid 50%."""
    dep = (datetime.date.today() + datetime.timedelta(days=15)).strftime("%Y-%m-%d")
    pkg = "TEST Readiness Metrics"
    _mk_pkg(client, admin_token, pkg, dep)
    _mk_jamaah(client, admin_token, "Fulan A", pkg, 30_000_000, 30_000_000, "Selesai")
    _mk_jamaah(client, admin_token, "Fulan B", pkg, 30_000_000, 30_000_000, "Selesai")
    _mk_jamaah(client, admin_token, "Fulan C", pkg, 10_000_000, 30_000_000, "Selesai")
    _mk_jamaah(client, admin_token, "Fulan D", pkg, 5_000_000, 30_000_000, "Belum Proses")

    r = client.get("/api/reminders/departures", headers=bearer(admin_token))
    assert r.status_code == 200
    combined = r.json()["h30"] + r.json()["h7"]
    row = next(p for p in combined if p["name"] == pkg)
    assert row["jamaah_count"] == 4
    assert row["visa_ready_pct"] == 75
    assert row["paid_full_pct"] == 50


def test_check_notifies_ops_and_dedupes(
        client, admin_token, ops_token, management_token):
    """H-30 dgn visa 0% -> notif ke role ops. Call 2x -> tetap 1 notif per kind."""
    dep = (datetime.date.today() + datetime.timedelta(days=20)).strftime("%Y-%m-%d")
    pkg = "TEST Reminder Dedupe"
    pid = _mk_pkg(client, admin_token, pkg, dep)
    _mk_jamaah(client, admin_token, "Zaenab X", pkg, 30_000_000, 30_000_000, "Belum Proses")

    before = client.get("/api/notifications/count",
                        headers=bearer(ops_token)).json()["unread"]

    r1 = client.post("/api/reminders/departures/check",
                     headers=bearer(admin_token))
    assert r1.status_code == 200
    sent1 = r1.json()["sent"]
    assert sent1 >= 1, f"Expected notif utk H-30 visa rendah, sent={sent1}"

    # Call kedua -> dedupe, tidak ada notif baru utk paket yg sama hari ini
    r2 = client.post("/api/reminders/departures/check",
                     headers=bearer(admin_token))
    assert r2.status_code == 200

    after = client.get("/api/notifications/count",
                       headers=bearer(ops_token)).json()["unread"]
    # Ops harus dpt >= 1 notif (dari r1). r2 tidak menambah.
    delta = after - before
    assert delta >= 1

    # Verify kind unique: hanya 1 baris utk kind reminder_h30_visa_pkg{pid}_today
    today = datetime.date.today().strftime("%Y%m%d")
    expected_kind = f"reminder_h30_visa_pkg{pid}_{today}"
    dup_count = db.query_one(
        "SELECT COUNT(*) c FROM user_notifications WHERE kind = ? AND date(created_at) = date('now')",
        (expected_kind,),
    )["c"]
    # notify_role broadcast per user -- boleh > 1 baris (1 per user ops),
    # tapi tidak boleh double di run kedua. Jumlah harus sama dgn jumlah user role ops.
    n_ops = db.query_one("SELECT COUNT(*) c FROM users WHERE role = 'ops'")["c"]
    assert dup_count == n_ops, f"Dedupe gagal: {dup_count} rows vs {n_ops} ops users"


def test_check_rbac_denies_sales(client, sales_token):
    r = client.post("/api/reminders/departures/check",
                    headers=bearer(sales_token))
    assert r.status_code == 403
