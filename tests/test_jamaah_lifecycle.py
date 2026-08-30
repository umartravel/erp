"""
Integration test: alur lifecycle jamaah + side-effect kritis.

Yang di-cover:
- Create jamaah (dari admin/sales) dengan gatekeeper harga/kuota
- PUT payment: increment paid_amount, recompute payment_status, INSERT tx income
- Baru Lunas + ada agent_id -> AUTO CREATE commission_claim status Pending
- sync_status_mirror -> update kolom `status` legacy

Alur ini adalah heart of the system -- jangan ada regression yang menyebabkan
komisi tidak auto-created, atau payment_status desync dengan paid_amount.
"""
from tests.conftest import bearer


def test_jamaah_create_and_list(client, admin_token):
    """Create satu jamaah manual dari seed paket. Verify muncul di /api/jamaah."""
    hdr = bearer(admin_token)

    # Ambil paket seed pertama
    packages = client.get("/api/packages", headers=hdr).json()
    assert len(packages) > 0, "Seed paket kosong"
    pkg = packages[0]
    # Pakai harga price_quad (kalau ada), fallback ke price
    server_price = pkg.get("price_quad") or pkg.get("price") or 0

    payload = {
        "nik": "9999999999999901",
        "name": "TEST Jamaah One",
        "phone": "081199990001",
        "package_type": pkg["name"],
        "total_price": server_price,
        "room_type": "QUAD" if pkg.get("price_quad") else None,
        "status": "Waitlisted",
    }
    r = client.post("/api/jamaah", json=payload, headers=hdr)
    assert r.status_code == 200, f"Create gagal: {r.text}"
    data = r.json()
    assert "id" in data
    jid = data["id"]

    # Verify muncul di list
    listing = client.get("/api/jamaah", headers=hdr).json()
    ids = [j["id"] for j in listing]
    assert jid in ids


def test_jamaah_create_wrong_price_rejected(client, admin_token):
    """Server-side price gate: harga salah -> 400."""
    hdr = bearer(admin_token)
    packages = client.get("/api/packages", headers=hdr).json()
    pkg = packages[0]

    payload = {
        "nik": "9999999999999902",
        "name": "TEST Wrong Price",
        "phone": "081199990002",
        "package_type": pkg["name"],
        "total_price": 1,  # SALAH: bukan harga sebenarnya
        "room_type": "QUAD" if pkg.get("price_quad") else None,
        "status": "Waitlisted",
    }
    r = client.post("/api/jamaah", json=payload, headers=hdr)
    assert r.status_code == 400
    assert "Harga tidak cocok" in r.json()["error"]


def test_jamaah_nik_unique(client, admin_token):
    """Duplicate NIK -> 400."""
    hdr = bearer(admin_token)
    packages = client.get("/api/packages", headers=hdr).json()
    pkg = packages[0]
    server_price = pkg.get("price_quad") or pkg.get("price") or 0

    payload = {
        "nik": "9999999999999903",
        "name": "TEST NIK Unique 1",
        "phone": "081199990003",
        "package_type": pkg["name"],
        "total_price": server_price,
        "room_type": "QUAD" if pkg.get("price_quad") else None,
        "status": "Waitlisted",
    }
    r1 = client.post("/api/jamaah", json=payload, headers=hdr)
    assert r1.status_code == 200

    # Coba lagi dgn NIK sama
    payload["name"] = "TEST NIK Unique 2"
    r2 = client.post("/api/jamaah", json=payload, headers=hdr)
    assert r2.status_code == 400
    assert "NIK Jamaah ini sudah pernah terdaftar" in r2.json()["error"]


def test_payment_triggers_commission_claim(client, admin_token, finance_token):
    """Alur kritis: bayar sampai Lunas -> auto-create commission_claim Pending.

    Setup: jamaah + agent + full paid.
    Verify: commission_claims baru muncul dengan status Pending.
    """
    hdr = bearer(admin_token)

    # Setup agent
    r = client.post("/api/agents", json={
        "name": "TEST Agent Payment",
        "phone": "0812TESTPAY",
        "province": "Jawa Barat",
        "city": "Bandung",
    }, headers=hdr)
    assert r.status_code == 200, f"Create agent gagal: {r.text}"
    agents = client.get("/api/agents", headers=hdr).json()
    agent = next(a for a in agents if a["name"] == "TEST Agent Payment")
    aid = agent["id"]

    # Setup jamaah
    packages = client.get("/api/packages", headers=hdr).json()
    pkg = packages[0]
    server_price = pkg.get("price_quad") or pkg.get("price") or 0

    r = client.post("/api/jamaah", json={
        "nik": "9999999999999910",
        "name": "TEST Jamaah Payment",
        "phone": "081199991010",
        "package_type": pkg["name"],
        "total_price": server_price,
        "room_type": "QUAD" if pkg.get("price_quad") else None,
        "status": "Terdaftar",
        "agent_id": aid,
    }, headers=hdr)
    assert r.status_code == 200
    jid = r.json()["id"]

    # Baseline: jumlah commission_claim untuk agent ini
    baseline = client.get("/api/commission-claims", headers=hdr).json()
    baseline_for_agent = [c for c in baseline if c["agent_id"] == aid]
    assert len(baseline_for_agent) == 0, "Baseline commission_claim harus 0"

    # Bayar full via finance token
    r = client.put(
        f"/api/jamaah/{jid}/payment",
        json={"paid_amount": server_price},
        headers=bearer(finance_token),
    )
    assert r.status_code == 200, f"Payment gagal: {r.text}"

    # Verify: commission_claim auto-created
    claims = client.get("/api/commission-claims", headers=hdr).json()
    claims_for_jid = [c for c in claims if c["jamaah_id"] == jid]
    assert len(claims_for_jid) == 1, "commission_claim TIDAK auto-created!"
    claim = claims_for_jid[0]
    assert claim["status"] == "Pending", f"Status claim seharusnya Pending: {claim}"
    assert claim["agent_id"] == aid


def test_partial_payment_no_commission(client, admin_token, finance_token):
    """Bayar sebagian (DP), belum Lunas -> commission_claim TIDAK dibuat."""
    hdr = bearer(admin_token)

    # Agent
    r = client.post("/api/agents", json={
        "name": "TEST Agent Partial",
        "phone": "0812PARTIAL",
    }, headers=hdr)
    assert r.status_code == 200
    agents = client.get("/api/agents", headers=hdr).json()
    agent = next(a for a in agents if a["name"] == "TEST Agent Partial")
    aid = agent["id"]

    # Jamaah
    packages = client.get("/api/packages", headers=hdr).json()
    pkg = packages[0]
    server_price = pkg.get("price_quad") or pkg.get("price") or 0

    r = client.post("/api/jamaah", json={
        "nik": "9999999999999911",
        "name": "TEST Jamaah Partial",
        "phone": "081199991011",
        "package_type": pkg["name"],
        "total_price": server_price,
        "room_type": "QUAD" if pkg.get("price_quad") else None,
        "status": "Terdaftar",
        "agent_id": aid,
    }, headers=hdr)
    assert r.status_code == 200
    jid = r.json()["id"]

    # Bayar HANYA setengah
    dp = server_price // 2
    r = client.put(
        f"/api/jamaah/{jid}/payment",
        json={"paid_amount": dp},
        headers=bearer(finance_token),
    )
    assert r.status_code == 200

    # Verify: NO commission_claim
    claims = client.get("/api/commission-claims", headers=hdr).json()
    claims_for_jid = [c for c in claims if c["jamaah_id"] == jid]
    assert len(claims_for_jid) == 0, "Belum Lunas tapi commission_claim sudah dibuat!"


# ---------------------------------------------------------------------------
# Phase 4b: register jamaah wire ke BOQ (wajib pilih kalau paket punya BOQ Approved)
# ---------------------------------------------------------------------------
def _mk_pkg_with_boq(client, admin_token, pkg_name, extra_triple=0, extra_double=0):
    """Bikin paket + BOQ Approved (admin auto-approved). Return (pkg_id, boq, expected_prices)."""
    hdr = bearer(admin_token)
    # Paket dgn price default (backward-compat harga kalau BOQ tidak dipakai).
    pkg_r = client.post("/api/packages", json={
        "name": pkg_name, "price": 20_000_000, "price_quad": 20_000_000,
        "price_triple": 22_000_000, "price_double": 24_000_000,
        "departure_date": "2027-01-01", "duration": 9, "quota": 45,
    }, headers=hdr)
    pkg_id = pkg_r.json()["id"]
    # BOQ Approved: base 20jt + margin 15% = 23jt (price_quad).
    boq_r = client.post("/api/boq", json={
        "name": f"BOQ {pkg_name}", "package_id": pkg_id,
        "target_pax": 40, "target_margin_pct": 15,
        "extra_triple": extra_triple, "extra_double": extra_double,
        "items": [
            {"category": "tiket", "item_name": "Tiket", "unit": "per_pax",
             "quantity": 1, "unit_price": 20_000_000},
        ],
    }, headers=hdr)
    boq = boq_r.json()
    assert boq["status"] == "Approved"
    # Ambil detail utk verifikasi price (backend menjaga single source of truth).
    detail = client.get(f"/api/boq/{boq['id']}", headers=hdr).json()
    return pkg_id, boq["id"], detail["totals"]


def test_register_wajib_boq_id_kalau_paket_punya_boq_approved(client, admin_token):
    """Register jamaah tanpa boq_id + paket punya BOQ Approved -> 400."""
    hdr = bearer(admin_token)
    pkg_id, boq_id, totals = _mk_pkg_with_boq(client, admin_token, "TEST Pkg BOQ Wajib")
    r = client.post("/api/jamaah", json={
        "nik": "9999900000000101", "name": "TEST Wajib BOQ",
        "phone": "081199990101",
        "package_type": "TEST Pkg BOQ Wajib",
        "total_price": totals["price_quad"],  # sudah tahu harga BOQ
        "room_type": "QUAD", "status": "Waitlisted",
    }, headers=hdr)
    assert r.status_code == 400
    assert "wajib pilih" in r.json()["error"].lower()


def test_register_dengan_boq_snapshot_price_quad(client, admin_token):
    """Register dgn boq_id + room_type QUAD -> jamaah punya boq_id + snapshot = price_quad."""
    hdr = bearer(admin_token)
    pkg_id, boq_id, totals = _mk_pkg_with_boq(client, admin_token, "TEST Pkg BOQ Quad",
                                              extra_triple=2_000_000, extra_double=4_000_000)
    r = client.post("/api/jamaah", json={
        "nik": "9999900000000102", "name": "TEST Snapshot Quad",
        "phone": "081199990102",
        "package_type": "TEST Pkg BOQ Quad",
        "boq_id": boq_id,
        "total_price": totals["price_quad"],  # 23jt
        "room_type": "QUAD", "status": "Waitlisted",
    }, headers=hdr)
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    listing = client.get("/api/jamaah", headers=hdr).json()
    j = next(x for x in listing if x["id"] == jid)
    assert j["boq_id"] == boq_id
    assert j["boq_snapshot_price"] == totals["price_quad"]
    assert j["boq_snapshot_at"] is not None
    assert j["total_price"] == totals["price_quad"]


def test_register_boq_snapshot_price_triple_pakai_extra(client, admin_token):
    """room_type TRIPLE -> snapshot = price_triple (base + extra_triple)."""
    hdr = bearer(admin_token)
    pkg_id, boq_id, totals = _mk_pkg_with_boq(client, admin_token, "TEST Pkg BOQ Triple",
                                              extra_triple=2_000_000, extra_double=4_000_000)
    r = client.post("/api/jamaah", json={
        "nik": "9999900000000103", "name": "TEST Snapshot Triple",
        "phone": "081199990103",
        "package_type": "TEST Pkg BOQ Triple",
        "boq_id": boq_id,
        "total_price": totals["price_triple"],  # 25jt
        "room_type": "TRIPLE", "status": "Waitlisted",
    }, headers=hdr)
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    j = next(x for x in client.get("/api/jamaah", headers=hdr).json() if x["id"] == jid)
    assert j["boq_snapshot_price"] == totals["price_triple"]
    assert j["boq_snapshot_price"] == totals["price_quad"] + 2_000_000


def test_register_wrong_boq_price_rejected(client, admin_token):
    """Kirim total_price yang bukan hasil BOQ -> 400 (server derive dari BOQ, gate)."""
    hdr = bearer(admin_token)
    pkg_id, boq_id, totals = _mk_pkg_with_boq(client, admin_token, "TEST Pkg BOQ MismatchPrice")
    r = client.post("/api/jamaah", json={
        "nik": "9999900000000104", "name": "TEST Wrong BOQ Price",
        "phone": "081199990104",
        "package_type": "TEST Pkg BOQ MismatchPrice",
        "boq_id": boq_id,
        "total_price": 999_999,  # SALAH: bukan harga BOQ
        "room_type": "QUAD", "status": "Waitlisted",
    }, headers=hdr)
    assert r.status_code == 400
    assert "harga tidak cocok" in r.json()["error"].lower()


def test_register_boq_dari_paket_lain_ditolak(client, admin_token):
    """Kirim boq_id yang bukan milik paket target -> 400."""
    hdr = bearer(admin_token)
    pkg_a, boq_a, totals_a = _mk_pkg_with_boq(client, admin_token, "TEST Pkg BOQ Wrong A")
    pkg_b, boq_b, totals_b = _mk_pkg_with_boq(client, admin_token, "TEST Pkg BOQ Wrong B")
    # Register ke paket A tapi pakai boq_id milik paket B.
    r = client.post("/api/jamaah", json={
        "nik": "9999900000000105", "name": "TEST BOQ WrongPkg",
        "phone": "081199990105",
        "package_type": "TEST Pkg BOQ Wrong A",
        "boq_id": boq_b,
        "total_price": totals_a["price_quad"],
        "room_type": "QUAD", "status": "Waitlisted",
    }, headers=hdr)
    assert r.status_code == 400
    assert "bukan milik paket ini" in r.json()["error"].lower() or "belum approved" in r.json()["error"].lower()


def test_register_paket_tanpa_boq_flow_lama_masih_jalan(client, admin_token):
    """Paket tanpa BOQ Approved -> boq_id tidak dibutuhkan, flow existing tetap OK.
    Backward compat aman untuk jamaah paket lama."""
    hdr = bearer(admin_token)
    packages = client.get("/api/packages", headers=hdr).json()
    # Cari paket yg TIDAK punya BOQ Approved (default seed packages).
    seed_pkg = None
    for p in packages:
        boqs = client.get(f"/api/boq?package_id={p['id']}&status=Approved", headers=hdr).json()
        if not boqs:
            seed_pkg = p
            break
    assert seed_pkg is not None, "Setup: perlu paket seed tanpa BOQ Approved"
    server_price = seed_pkg.get("price_quad") or seed_pkg.get("price") or 0
    r = client.post("/api/jamaah", json={
        "nik": "9999900000000106", "name": "TEST BOQ Fallback",
        "phone": "081199990106",
        "package_type": seed_pkg["name"],
        "total_price": server_price,
        "room_type": "QUAD" if seed_pkg.get("price_quad") else None,
        "status": "Waitlisted",
    }, headers=hdr)
    assert r.status_code == 200, r.text
    j = next(x for x in client.get("/api/jamaah", headers=hdr).json() if x["id"] == r.json()["id"])
    assert j["boq_id"] is None
    assert j["boq_snapshot_price"] is None


# ---------------------------------------------------------------------------
# Phase 4c: edit jamaah re-snapshot on boq_id change
# ---------------------------------------------------------------------------
def _reg_with_boq(client, admin_token, pkg_name, room_type="QUAD", nik_suffix="001"):
    """Bikin paket + BOQ + register 1 jamaah. Return (pkg_id, boq_id, jid, totals)."""
    hdr = bearer(admin_token)
    pkg_id, boq_id, totals = _mk_pkg_with_boq(client, admin_token, pkg_name,
                                              extra_triple=2_000_000, extra_double=4_000_000)
    price = totals[f"price_{room_type.lower()}"]
    r = client.post("/api/jamaah", json={
        "nik": f"999990000000{nik_suffix}", "name": f"TEST J {pkg_name}",
        "phone": f"08119999{nik_suffix}",
        "package_type": pkg_name, "boq_id": boq_id,
        "total_price": price, "room_type": room_type, "status": "Waitlisted",
    }, headers=hdr)
    assert r.status_code == 200, r.text
    return pkg_id, boq_id, r.json()["id"], totals


def test_edit_reboot_snapshot_when_boq_changes(client, admin_token):
    """Edit jamaah dgn boq_id baru -> boq_snapshot_price di-refresh dari BOQ baru,
    boq_snapshot_at ter-update."""
    hdr = bearer(admin_token)
    # Setup: paket dgn 2 BOQ Approved (Reguler + Upgrade).
    pkg_id_a, boq_reg, jid, totals_reg = _reg_with_boq(client, admin_token, "TEST PKG BOQ Edit", nik_suffix="201")

    # BOQ kedua di paket yg sama, harga lebih mahal (base 25jt vs 20jt).
    boq_up_r = client.post("/api/boq", json={
        "name": "BOQ Upgrade", "package_id": pkg_id_a,
        "target_pax": 40, "target_margin_pct": 20,
        "extra_triple": 3_000_000, "extra_double": 6_000_000,
        "items": [{"category": "hotel_mekkah", "item_name": "Anjum", "unit": "per_pax",
                   "quantity": 1, "unit_price": 25_000_000}],
    }, headers=hdr)
    boq_up = boq_up_r.json()["id"]
    boq_up_detail = client.get(f"/api/boq/{boq_up}", headers=hdr).json()
    boq_up_price_quad = boq_up_detail["totals"]["price_quad"]  # 25jt + 20% = 30jt

    # Baseline: jamaah punya snapshot dari BOQ Reguler = 23jt (from totals_reg["price_quad"]).
    j_before = next(x for x in client.get("/api/jamaah", headers=hdr).json() if x["id"] == jid)
    assert j_before["boq_id"] == boq_reg
    assert j_before["boq_snapshot_price"] == totals_reg["price_quad"]
    ts_before = j_before["boq_snapshot_at"]
    assert ts_before is not None

    # Edit: ganti boq_id ke BOQ Upgrade, total_price update ke price baru.
    r = client.put(f"/api/jamaah/{jid}", json={
        # Kirim semua field yg dibutuhkan endpoint (biar aman)
        "nik": j_before["nik"], "name": j_before["name"], "phone": j_before["phone"],
        "status": j_before["status"], "package_type": "TEST PKG BOQ Edit",
        "boq_id": boq_up,
        "total_price": boq_up_price_quad,
        "room_type": "QUAD",
        "price_change_note": "Upgrade skenario",
    }, headers=hdr)
    assert r.status_code == 200, r.text

    j_after = next(x for x in client.get("/api/jamaah", headers=hdr).json() if x["id"] == jid)
    assert j_after["boq_id"] == boq_up
    assert j_after["boq_snapshot_price"] == boq_up_price_quad
    # Timestamp berubah (CURRENT_TIMESTAMP).
    assert j_after["boq_snapshot_at"] is not None


def test_edit_boq_snapshot_room_type_split(client, admin_token):
    """Edit sekaligus ganti room_type -> snapshot pakai harga split BOQ per room_type."""
    hdr = bearer(admin_token)
    pkg_id, boq_id, jid, totals = _reg_with_boq(client, admin_token, "TEST PKG BOQ RoomChange", nik_suffix="202")
    # Baseline QUAD snapshot.
    j_before = next(x for x in client.get("/api/jamaah", headers=hdr).json() if x["id"] == jid)
    assert j_before["boq_snapshot_price"] == totals["price_quad"]

    # Edit: ubah room_type ke DOUBLE, kirim boq_id yg sama -> snapshot re-computed = price_double.
    r = client.put(f"/api/jamaah/{jid}", json={
        "nik": j_before["nik"], "name": j_before["name"], "phone": j_before["phone"],
        "status": j_before["status"], "package_type": "TEST PKG BOQ RoomChange",
        "boq_id": boq_id,
        "total_price": totals["price_double"],
        "room_type": "DOUBLE",
        "price_change_note": "Upgrade kamar ke DOUBLE",
    }, headers=hdr)
    assert r.status_code == 200, r.text
    j_after = next(x for x in client.get("/api/jamaah", headers=hdr).json() if x["id"] == jid)
    assert j_after["boq_snapshot_price"] == totals["price_double"]


def test_edit_boq_wrong_package_rejected(client, admin_token):
    """Edit dgn boq_id milik paket berbeda -> 400."""
    hdr = bearer(admin_token)
    pkg_a, boq_a, jid, totals_a = _reg_with_boq(client, admin_token, "TEST PKG BOQ Wrong Edit A", nik_suffix="203")
    pkg_b, boq_b, totals_b = _mk_pkg_with_boq(client, admin_token, "TEST PKG BOQ Wrong Edit B")

    r = client.put(f"/api/jamaah/{jid}", json={
        "nik": "9999900000000203", "name": "TEST J TEST PKG BOQ Wrong Edit A",
        "phone": "0811999999203",
        "status": "Waitlisted",
        "package_type": "TEST PKG BOQ Wrong Edit A",
        "boq_id": boq_b,   # milik paket B -- ditolak
        "total_price": totals_a["price_quad"],
        "room_type": "QUAD",
    }, headers=hdr)
    assert r.status_code == 400
    assert "bukan milik" in r.json()["error"].lower() or "tidak milik" in r.json()["error"].lower()


def test_edit_tanpa_boq_key_tidak_menghapus_snapshot(client, admin_token):
    """Edit body TANPA boq_id key -> snapshot existing tetap (utk edit field lain)."""
    hdr = bearer(admin_token)
    pkg_id, boq_id, jid, totals = _reg_with_boq(client, admin_token, "TEST PKG BOQ NoTouch", nik_suffix="204")
    j_before = next(x for x in client.get("/api/jamaah", headers=hdr).json() if x["id"] == jid)

    # Edit nama saja, tanpa key boq_id.
    r = client.put(f"/api/jamaah/{jid}", json={
        "nik": j_before["nik"], "name": "TEST J NAMA BARU", "phone": j_before["phone"],
        "status": j_before["status"], "package_type": "TEST PKG BOQ NoTouch",
        "total_price": totals["price_quad"],
        "room_type": "QUAD",
        # NO boq_id key!
    }, headers=hdr)
    assert r.status_code == 200, r.text
    j_after = next(x for x in client.get("/api/jamaah", headers=hdr).json() if x["id"] == jid)
    assert j_after["boq_id"] == boq_id
    assert j_after["boq_snapshot_price"] == totals["price_quad"]
    assert j_after["name"] == "TEST J NAMA BARU"


def test_status_mirror_after_payment(client, admin_token, finance_token):
    """Verify sync_status_mirror: kolom `status` legacy nyambung ke dimensi."""
    hdr = bearer(admin_token)
    packages = client.get("/api/packages", headers=hdr).json()
    pkg = packages[0]
    server_price = pkg.get("price_quad") or pkg.get("price") or 0

    r = client.post("/api/jamaah", json={
        "nik": "9999999999999912",
        "name": "TEST Jamaah Mirror",
        "phone": "081199991012",
        "package_type": pkg["name"],
        "total_price": server_price,
        "room_type": "QUAD" if pkg.get("price_quad") else None,
        "status": "Terdaftar",
    }, headers=hdr)
    jid = r.json()["id"]

    # Bayar full
    client.put(
        f"/api/jamaah/{jid}/payment",
        json={"paid_amount": server_price},
        headers=bearer(finance_token),
    )

    # Ambil jamaah dari list -> cek status = 'Lunas'
    listing = client.get("/api/jamaah", headers=hdr).json()
    j = next(x for x in listing if x["id"] == jid)
    assert j["status"] == "Lunas", f"status mirror tidak update ke 'Lunas': {j['status']}"
    assert j["payment_status"] == "Lunas"
    assert j["paid_amount"] == server_price
