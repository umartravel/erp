"""
Integration test: PUT /api/jamaah/bulk-ops (endpoint mass update Ops).

Yang di-cover:
- Bulk update sukses N jamaah -> field visa_status/room_type/room_number/etc kesave
- Registration order test: /bulk-ops (literal) tidak di-shadow oleh /{jid} (int)
- RBAC: hanya admin/ops (bukan sales/finance)
- Payload salah bentuk (bukan list / kosong) -> 400
- ID tidak ada di DB -> skip silently, tidak error total

Regressi kalau bulk-ops hilang -> pindah ke /{jid} PUT dgn body list -> 422/casting fail.
"""
from tests.conftest import bearer


def _make_jamaah(client, admin_token, nik, name):
    hdr = bearer(admin_token)
    pkg = client.get("/api/packages", headers=hdr).json()[0]
    price = pkg.get("price_quad") or pkg.get("price") or 0
    r = client.post("/api/jamaah", json={
        "nik": nik, "name": name, "phone": "0811" + nik[-8:],
        "package_type": pkg["name"], "total_price": price,
        "room_type": "QUAD" if pkg.get("price_quad") else None,
        "status": "Terdaftar",
    }, headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_bulk_ops_updates_multiple_jamaah(client, admin_token, ops_token):
    """Update visa_status + room_number utk 3 jamaah sekaligus."""
    ids = [
        _make_jamaah(client, admin_token, f"999999999999500{i}", f"TEST BulkOps {i}")
        for i in range(1, 4)
    ]
    r = client.put("/api/jamaah/bulk-ops", json={
        "updates": [
            {"id": ids[0], "visa_status": "Approved", "room_number": "A-101",
             "room_type": "QUAD", "doc_status": None, "passport_expiry": None,
             "passport_location": "kantor", "bus_group": "Bus-1"},
            {"id": ids[1], "visa_status": "Approved", "room_number": "A-102",
             "room_type": "QUAD", "doc_status": None, "passport_expiry": None,
             "passport_location": "kantor", "bus_group": "Bus-1"},
            {"id": ids[2], "visa_status": "Pending", "room_number": "A-103",
             "room_type": "QUAD", "doc_status": None, "passport_expiry": None,
             "passport_location": "rumah", "bus_group": "Bus-2"},
        ]
    }, headers=bearer(ops_token))
    assert r.status_code == 200, r.text
    assert "3 jamaah" in r.json()["message"]

    listing = client.get("/api/jamaah", headers=bearer(admin_token)).json()
    by_id = {j["id"]: j for j in listing}
    assert by_id[ids[0]]["visa_status"] == "Approved"
    assert by_id[ids[0]]["room_number"] == "A-101"
    assert by_id[ids[2]]["visa_status"] == "Pending"
    assert by_id[ids[2]]["bus_group"] == "Bus-2"


def test_bulk_ops_route_not_shadowed_by_int_id(client, ops_token):
    """CRITICAL regression check: /bulk-ops harus match SEBELUM /{jid:int}.
    Kalau /{jid} match dulu, path 'bulk-ops' akan gagal int-cast -> 422."""
    r = client.put("/api/jamaah/bulk-ops", json={"updates": []},
                   headers=bearer(ops_token))
    # 400 (empty list) = benar-benar sampai handler bulk-ops
    # 422 (validation) = FastAPI casting 'bulk-ops' ke int (BAD -- regressi!)
    assert r.status_code == 400, f"Ekspektasi 400 dari handler bulk-ops, dapat {r.status_code} -- kemungkinan ke-shadow oleh /{{jid}}"


def test_bulk_ops_empty_updates_rejected(client, ops_token):
    r = client.put("/api/jamaah/bulk-ops",
                   json={"updates": []}, headers=bearer(ops_token))
    assert r.status_code == 400
    assert "Tidak ada data" in r.json()["error"]


def test_bulk_ops_no_updates_key_rejected(client, ops_token):
    r = client.put("/api/jamaah/bulk-ops", json={}, headers=bearer(ops_token))
    assert r.status_code == 400


def test_bulk_ops_rbac_admin_ops_only(client, sales_token, finance_token):
    """Sales & finance tidak boleh bulk-ops."""
    payload = {"updates": [{"id": 1, "visa_status": "x"}]}
    r = client.put("/api/jamaah/bulk-ops", json=payload,
                   headers=bearer(sales_token))
    assert r.status_code == 403
    r = client.put("/api/jamaah/bulk-ops", json=payload,
                   headers=bearer(finance_token))
    assert r.status_code == 403


def test_bulk_ops_skips_nonexistent_id(client, admin_token, ops_token):
    """ID palsu di list -> di-skip, tapi ID valid tetap ke-update."""
    jid = _make_jamaah(client, admin_token, "9999999999995009", "TEST BulkOps Mixed")
    r = client.put("/api/jamaah/bulk-ops", json={
        "updates": [
            {"id": 999999999, "visa_status": "GhostRow"},
            {"id": jid, "visa_status": "MixedApproved",
             "room_type": None, "room_number": None, "doc_status": None,
             "passport_expiry": None, "passport_location": None, "bus_group": None},
        ]
    }, headers=bearer(ops_token))
    assert r.status_code == 200
    listing = client.get("/api/jamaah", headers=bearer(admin_token)).json()
    j = next(x for x in listing if x["id"] == jid)
    assert j["visa_status"] == "MixedApproved"
