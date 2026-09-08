"""
Phase 7a-1: PUT /api/jamaah/{jid} sekarang accept field biodata + dokumen
tambahan (passport_number/issued/expiry/location/issuer_city,
submitted_documents, equipment_package, room_number, bus_group).

Semua field baru optional -- kalau tidak dikirim, nilai lama di DB dijaga
(partial-update safe). Test ini memastikan:
  1. Field baru masuk ke DB bila dikirim
  2. Field lama tetap terpelihara bila body PARTIAL (backward compat)
  3. submitted_documents (list Python) tersimpan sebagai JSON string
"""
import json

from tests.conftest import bearer

import db


def _register_jamaah(client, admin_token, nik, name):
    """Buat jamaah dgn field minimal utk tes Edit."""
    pkg = client.get("/api/packages", headers=bearer(admin_token)).json()[0]
    r = client.post(
        "/api/jamaah",
        json={
            "nik": nik, "name": name, "phone": "0812" + nik[-8:],
            "package_type": pkg["name"],
            "total_price": pkg.get("price_quad") or pkg.get("price") or 25000000,
            "status": "Terdaftar",
        },
        headers=bearer(admin_token),
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _get_row(jid):
    return db.query_one("SELECT * FROM jamaah WHERE id = ?", (jid,))


def test_put_jamaah_accepts_passport_fields(client, admin_token):
    """Kirim seluruh passport field -> tersimpan di DB."""
    jid = _register_jamaah(client, admin_token, "9999999999710001", "TEST Biodata Full")
    r = client.put(
        f"/api/jamaah/{jid}",
        json={
            "nik": "9999999999710001", "name": "TEST Biodata Full",
            "phone": "081210001111", "status": "Terdaftar",
            "package_type": _get_row(jid)["package_type"],
            "total_price": _get_row(jid)["total_price"],
            "passport_number": "A12345678",
            "passport_issued": "2024-01-15",
            "passport_expiry": "2029-01-14",
            "passport_location": "Kanim Jakarta Selatan",
            "passport_issuer_city": "Jakarta Selatan",
            "equipment_package": "Standard",
            "room_number": "301A",
            "bus_group": "Bus 2",
            "submitted_documents": ["ktp", "kk", "passport"],
        },
        headers=bearer(admin_token),
    )
    assert r.status_code == 200, r.text

    row = _get_row(jid)
    assert row["passport_number"] == "A12345678"
    assert row["passport_issued"] == "2024-01-15"
    assert row["passport_expiry"] == "2029-01-14"
    assert row["passport_location"] == "Kanim Jakarta Selatan"
    assert row["passport_issuer_city"] == "Jakarta Selatan"
    assert row["equipment_package"] == "Standard"
    assert row["room_number"] == "301A"
    assert row["bus_group"] == "Bus 2"
    # submitted_documents = JSON string di DB
    assert json.loads(row["submitted_documents"]) == ["ktp", "kk", "passport"]


def test_put_jamaah_preserves_existing_when_body_partial(client, admin_token):
    """Body tanpa passport keys -> nilai lama passport DIJAGA (backward compat)."""
    jid = _register_jamaah(client, admin_token, "9999999999710002", "TEST Biodata Partial")
    # Set passport lewat 1 kali PUT
    client.put(
        f"/api/jamaah/{jid}",
        json={
            "nik": "9999999999710002", "name": "TEST Biodata Partial",
            "phone": "081210002222", "status": "Terdaftar",
            "package_type": _get_row(jid)["package_type"],
            "total_price": _get_row(jid)["total_price"],
            "passport_number": "B98765432",
            "passport_expiry": "2028-06-30",
        },
        headers=bearer(admin_token),
    )
    row = _get_row(jid)
    assert row["passport_number"] == "B98765432"
    assert row["passport_expiry"] == "2028-06-30"

    # Sekarang PUT lagi TANPA kirim passport keys sama sekali -> harus tetap
    r = client.put(
        f"/api/jamaah/{jid}",
        json={
            "nik": "9999999999710002", "name": "TEST Biodata Partial Renamed",
            "phone": "081210002222", "status": "Terdaftar",
            "package_type": _get_row(jid)["package_type"],
            "total_price": _get_row(jid)["total_price"],
        },
        headers=bearer(admin_token),
    )
    assert r.status_code == 200
    row2 = _get_row(jid)
    # Passport TIDAK berubah -- key tidak ada di body kedua.
    assert row2["passport_number"] == "B98765432"
    assert row2["passport_expiry"] == "2028-06-30"
    # Name berubah (yg dikirim).
    assert row2["name"] == "TEST Biodata Partial Renamed"


def test_put_jamaah_submitted_documents_empty_list(client, admin_token):
    """submitted_documents = [] (empty) -> tersimpan sebagai '[]'."""
    jid = _register_jamaah(client, admin_token, "9999999999710003", "TEST Biodata EmptyDocs")
    r = client.put(
        f"/api/jamaah/{jid}",
        json={
            "nik": "9999999999710003", "name": "TEST Biodata EmptyDocs",
            "phone": "081210003333", "status": "Terdaftar",
            "package_type": _get_row(jid)["package_type"],
            "total_price": _get_row(jid)["total_price"],
            "submitted_documents": [],
        },
        headers=bearer(admin_token),
    )
    assert r.status_code == 200
    row = _get_row(jid)
    assert json.loads(row["submitted_documents"]) == []
