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
