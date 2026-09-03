"""
Integration test: modul Simulasi Paket (BOQ).

Skenario penting:
- Sales create -> status Draft. Mgmt create -> status auto Approved.
- Draft locked untuk role lain (owner-only edit).
- Submit tanpa item -> 400 (BOQ kosong tidak layak review).
- Submit valid -> Pending. Mgmt approve -> Approved. Approve non-mgmt -> 403.
- Reject wajib review_note. Reject bukan Pending -> 400.
- Duplicate: apapun status source, hasil selalu Draft baru, items ikut ke-copy.
- Delete guard: sales tidak boleh delete non-Draft-nya sendiri.
- Compute totals: item per_pax dikali target_pax, per_group cukup 1x,
  price_per_pax = cost + margin.
"""
from tests.conftest import bearer


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _mk_boq(client, token, name="Skenario Reguler", package_id=None, items=None,
            target_pax=40, margin=15, extra_triple=0, extra_double=0):
    body = {
        "name": name,
        "package_id": package_id,
        "target_pax": target_pax,
        "target_margin_pct": margin,
        "extra_triple": extra_triple,
        "extra_double": extra_double,
        "items": items or [],
    }
    r = client.post("/api/boq", json=body, headers=bearer(token))
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# create / status
# ---------------------------------------------------------------------------
def test_sales_create_boq_status_draft(client, sales_token):
    """Sales bikin BOQ tanpa package_id -> boleh (BOQ paket baru). Status Draft."""
    b = _mk_boq(client, sales_token, name="TEST BOQ Sales Draft")
    assert b["status"] == "Draft"


def test_management_create_boq_status_approved(client, management_token):
    """Mgmt bikin BOQ -> langsung Approved (skip pending workflow)."""
    b = _mk_boq(client, management_token, name="TEST BOQ Mgmt Auto")
    assert b["status"] == "Approved"


def test_admin_create_boq_status_approved(client, admin_token):
    """Admin sama dengan mgmt -> auto Approved."""
    b = _mk_boq(client, admin_token, name="TEST BOQ Admin Auto")
    assert b["status"] == "Approved"


def test_create_boq_invalid_package_400(client, sales_token):
    r = client.post("/api/boq", json={"name": "X", "package_id": 999999},
                    headers=bearer(sales_token))
    assert r.status_code == 400


def test_create_boq_empty_name_400(client, sales_token):
    r = client.post("/api/boq", json={"name": "   "}, headers=bearer(sales_token))
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# item + workflow: submit / approve / reject
# ---------------------------------------------------------------------------
def test_submit_empty_boq_blocked(client, sales_token):
    """Submit BOQ tanpa item -> 400 (BOQ kosong tidak layak review)."""
    b = _mk_boq(client, sales_token, name="TEST BOQ Kosong")
    r = client.post(f"/api/boq/{b['id']}/submit", headers=bearer(sales_token))
    assert r.status_code == 400
    assert "kosong" in r.json()["error"].lower()


def test_full_lifecycle_sales_submit_mgmt_approve(client, sales_token, management_token):
    """Draft -> Submit -> Pending -> Approved. Full happy path."""
    b = _mk_boq(client, sales_token, name="TEST BOQ Lifecycle", items=[
        {"category": "hotel_mekkah", "item_name": "Hotel Anjum Quad",
         "unit": "per_pax", "quantity": 1, "unit_price": 8_000_000},
        {"category": "tiket", "item_name": "SV-CGK-JED",
         "unit": "per_pax", "quantity": 1, "unit_price": 12_000_000},
    ])
    assert b["status"] == "Draft"

    r = client.post(f"/api/boq/{b['id']}/submit", headers=bearer(sales_token))
    assert r.status_code == 200

    r = client.get(f"/api/boq/{b['id']}", headers=bearer(sales_token))
    assert r.json()["status"] == "Pending Approval"

    r = client.post(f"/api/boq/{b['id']}/approve",
                    json={"review_note": "OK"}, headers=bearer(management_token))
    assert r.status_code == 200

    r = client.get(f"/api/boq/{b['id']}", headers=bearer(sales_token))
    assert r.json()["status"] == "Approved"
    assert r.json()["reviewed_by_name"] is not None


def test_sales_cannot_approve(client, sales_token, management_token):
    """Approve harus mgmt/admin -- sales dilarang."""
    b = _mk_boq(client, sales_token, name="TEST BOQ NoApprove", items=[
        {"category": "tiket", "item_name": "X", "unit": "per_pax",
         "quantity": 1, "unit_price": 100},
    ])
    client.post(f"/api/boq/{b['id']}/submit", headers=bearer(sales_token))
    r = client.post(f"/api/boq/{b['id']}/approve", json={}, headers=bearer(sales_token))
    assert r.status_code == 403


def test_reject_requires_note(client, sales_token, management_token):
    """Reject tanpa review_note -> 400."""
    b = _mk_boq(client, sales_token, name="TEST BOQ Reject", items=[
        {"category": "visa", "item_name": "Visa", "unit": "per_pax",
         "quantity": 1, "unit_price": 1_500_000},
    ])
    client.post(f"/api/boq/{b['id']}/submit", headers=bearer(sales_token))
    r = client.post(f"/api/boq/{b['id']}/reject", json={},
                    headers=bearer(management_token))
    assert r.status_code == 400
    r = client.post(f"/api/boq/{b['id']}/reject", json={"review_note": "Harga hotel terlalu tinggi"},
                    headers=bearer(management_token))
    assert r.status_code == 200


def test_reject_only_pending(client, admin_token, management_token):
    """Reject BOQ yg sudah Approved -> 400 (harus pending)."""
    b = _mk_boq(client, admin_token, name="TEST BOQ NoReject Approved", items=[
        {"category": "tiket", "item_name": "X", "unit": "per_pax",
         "quantity": 1, "unit_price": 100},
    ])
    r = client.post(f"/api/boq/{b['id']}/reject", json={"review_note": "test"},
                    headers=bearer(management_token))
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# edit guard
# ---------------------------------------------------------------------------
def test_owner_cannot_edit_after_submit(client, sales_token):
    """Sales bikin, submit -> tidak bisa edit lagi (harus duplicate untuk revisi)."""
    b = _mk_boq(client, sales_token, name="TEST BOQ Locked", items=[
        {"category": "tiket", "item_name": "X", "unit": "per_pax",
         "quantity": 1, "unit_price": 100},
    ])
    client.post(f"/api/boq/{b['id']}/submit", headers=bearer(sales_token))
    r = client.put(f"/api/boq/{b['id']}", json={"name": "coba edit"},
                   headers=bearer(sales_token))
    assert r.status_code == 400
    assert "Draft" in r.json()["error"]


def test_non_owner_sales_cannot_edit(client, sales_token, admin_token):
    """Sales A tidak boleh edit BOQ sales B (atau BOQ admin)."""
    b = _mk_boq(client, admin_token, name="TEST BOQ Admin Owner")
    r = client.put(f"/api/boq/{b['id']}", json={"name": "coba hijack"},
                   headers=bearer(sales_token))
    assert r.status_code == 403


def test_mgmt_can_edit_anything(client, sales_token, management_token):
    """Mgmt boleh edit BOQ siapapun, apapun status."""
    b = _mk_boq(client, sales_token, name="TEST BOQ SalesOwn", items=[
        {"category": "tiket", "item_name": "X", "unit": "per_pax",
         "quantity": 1, "unit_price": 100},
    ])
    client.post(f"/api/boq/{b['id']}/submit", headers=bearer(sales_token))
    r = client.put(f"/api/boq/{b['id']}", json={"notes": "Direview mgmt"},
                   headers=bearer(management_token))
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# duplicate
# ---------------------------------------------------------------------------
def test_duplicate_clones_items_as_draft(client, admin_token, sales_token):
    """Duplicate BOQ (approved) -> BOQ baru Draft, items ikut ter-copy."""
    src = _mk_boq(client, admin_token, name="TEST BOQ Source", items=[
        {"category": "hotel_mekkah", "item_name": "Anjum", "unit": "per_pax",
         "quantity": 1, "unit_price": 8_000_000},
        {"category": "tiket", "item_name": "Saudia", "unit": "per_pax",
         "quantity": 1, "unit_price": 12_000_000},
    ])
    assert src["status"] == "Approved"
    r = client.post(f"/api/boq/{src['id']}/duplicate", json={"name": "TEST BOQ Clone"},
                    headers=bearer(sales_token))
    assert r.status_code == 200
    new_bid = r.json()["id"]
    detail = client.get(f"/api/boq/{new_bid}", headers=bearer(sales_token)).json()
    assert detail["status"] == "Draft"
    assert detail["name"] == "TEST BOQ Clone"
    assert len(detail["items"]) == 2
    assert detail["created_by_name"] is not None  # sales, bukan admin src


# ---------------------------------------------------------------------------
# totals
# ---------------------------------------------------------------------------
def test_compute_totals_per_pax_scaled(client, admin_token):
    """per_pax subtotal 20jt x target_pax 10 = 200jt group cost. Margin 15%
    dari cost_per_pax 20jt = +3jt -> price_per_pax 23jt."""
    b = _mk_boq(client, admin_token, name="TEST BOQ Total", target_pax=10, margin=15, items=[
        {"category": "tiket", "item_name": "Tiket", "unit": "per_pax",
         "quantity": 1, "unit_price": 20_000_000},
    ])
    detail = client.get(f"/api/boq/{b['id']}", headers=bearer(admin_token)).json()
    t = detail["totals"]
    assert t["total_group_cost"] == 200_000_000
    assert t["cost_per_pax"] == 20_000_000
    assert t["margin_amount"] == 3_000_000
    assert t["price_per_pax"] == 23_000_000


# ---------------------------------------------------------------------------
# Room split (Phase 4a)
# ---------------------------------------------------------------------------
def test_split_default_zero_all_room_same(client, admin_token):
    """Tanpa extra_triple/double -> price_quad = price_triple = price_double."""
    b = _mk_boq(client, admin_token, name="TEST BOQ Split Flat", target_pax=10, margin=15, items=[
        {"category": "tiket", "item_name": "Tiket", "unit": "per_pax",
         "quantity": 1, "unit_price": 20_000_000},
    ])
    t = client.get(f"/api/boq/{b['id']}", headers=bearer(admin_token)).json()["totals"]
    assert t["price_quad"] == 23_000_000
    assert t["price_triple"] == 23_000_000
    assert t["price_double"] == 23_000_000
    assert t["extra_triple"] == 0 and t["extra_double"] == 0


def test_split_extras_applied(client, admin_token):
    """extra_triple 2jt + extra_double 4jt -> price_triple = base+2jt, price_double = base+4jt."""
    b = _mk_boq(client, admin_token, name="TEST BOQ Split Extras",
                target_pax=10, margin=15,
                extra_triple=2_000_000, extra_double=4_000_000, items=[
        {"category": "tiket", "item_name": "Tiket", "unit": "per_pax",
         "quantity": 1, "unit_price": 20_000_000},
    ])
    t = client.get(f"/api/boq/{b['id']}", headers=bearer(admin_token)).json()["totals"]
    assert t["price_quad"] == 23_000_000
    assert t["price_triple"] == 25_000_000
    assert t["price_double"] == 27_000_000
    assert t["extra_triple"] == 2_000_000 and t["extra_double"] == 4_000_000


def test_split_editable_via_put(client, admin_token):
    """extra_triple bisa diubah via PUT header + reflected di totals."""
    # margin=10 (bukan 0) menghindari pre-existing falsy-trap di update code
    # `float(body.get("target_margin_pct") or 15)` -- 0 dianggap missing -> default 15.
    b = _mk_boq(client, admin_token, name="TEST BOQ Split Edit", target_pax=10, margin=10, items=[
        {"category": "tiket", "item_name": "Tiket", "unit": "per_pax",
         "quantity": 1, "unit_price": 10_000_000},
    ])
    # Base price_quad = 10jt + 10% = 11jt
    r = client.put(f"/api/boq/{b['id']}", json={"extra_triple": 1_500_000, "extra_double": 3_000_000},
                   headers=bearer(admin_token))
    assert r.status_code == 200
    t = client.get(f"/api/boq/{b['id']}", headers=bearer(admin_token)).json()["totals"]
    assert t["price_quad"] == 11_000_000
    assert t["price_triple"] == 12_500_000
    assert t["price_double"] == 14_000_000


def test_convert_uses_split_prices(client, admin_token):
    """Convert-to-package pakai price_quad/triple/double dari BOQ (bukan flat)."""
    b = _mk_boq(client, admin_token, name="TEST BOQ Convert Split",
                target_pax=40, margin=15,
                extra_triple=2_000_000, extra_double=4_500_000, items=[
        {"category": "hotel_mekkah", "item_name": "Anjum", "unit": "per_pax",
         "quantity": 1, "unit_price": 8_000_000},
        {"category": "tiket", "item_name": "Saudia", "unit": "per_pax",
         "quantity": 1, "unit_price": 12_000_000},
    ])
    # Base = 20jt + margin 15% = 23jt
    r = client.post(f"/api/boq/{b['id']}/convert-to-package", json={
        "departure_date": "2027-05-10", "duration": 9, "quota": 40,
    }, headers=bearer(admin_token))
    assert r.status_code == 200
    pid = r.json()["package_id"]
    pkg = next(p for p in client.get("/api/packages", headers=bearer(admin_token)).json() if p["id"] == pid)
    assert pkg["price_quad"] == 23_000_000
    assert pkg["price_triple"] == 25_000_000
    assert pkg["price_double"] == 27_500_000


def test_compute_totals_per_group_flat(client, admin_token):
    """per_group subtotal 10jt tidak dikali target_pax -- cukup 1x."""
    b = _mk_boq(client, admin_token, name="TEST BOQ Group", target_pax=10, margin=0, items=[
        {"category": "transport", "item_name": "Bus", "unit": "per_group",
         "quantity": 1, "unit_price": 10_000_000},
    ])
    t = client.get(f"/api/boq/{b['id']}", headers=bearer(admin_token)).json()["totals"]
    assert t["total_group_cost"] == 10_000_000
    assert t["cost_per_pax"] == 1_000_000


# ---------------------------------------------------------------------------
# delete guard
# ---------------------------------------------------------------------------
def test_sales_cannot_delete_after_submit(client, sales_token):
    b = _mk_boq(client, sales_token, name="TEST BOQ NoDel", items=[
        {"category": "tiket", "item_name": "X", "unit": "per_pax",
         "quantity": 1, "unit_price": 100},
    ])
    client.post(f"/api/boq/{b['id']}/submit", headers=bearer(sales_token))
    r = client.delete(f"/api/boq/{b['id']}", headers=bearer(sales_token))
    assert r.status_code == 400


def test_mgmt_can_delete_any_status(client, sales_token, management_token):
    b = _mk_boq(client, sales_token, name="TEST BOQ MgmtDel", items=[
        {"category": "tiket", "item_name": "X", "unit": "per_pax",
         "quantity": 1, "unit_price": 100},
    ])
    client.post(f"/api/boq/{b['id']}/submit", headers=bearer(sales_token))
    r = client.delete(f"/api/boq/{b['id']}", headers=bearer(management_token))
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# item CRUD
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# convert-to-package (Phase 2)
# ---------------------------------------------------------------------------
def test_convert_approved_boq_creates_package(client, admin_token):
    """BOQ Approved -> POST convert -> row packages baru + BOQ.package_id linked."""
    b = _mk_boq(client, admin_token, name="TEST BOQ Convert Happy", target_pax=40, margin=15, items=[
        {"category": "hotel_mekkah", "item_name": "Anjum", "unit": "per_pax",
         "quantity": 1, "unit_price": 8_000_000},
        {"category": "tiket", "item_name": "Saudia", "unit": "per_pax",
         "quantity": 1, "unit_price": 12_000_000},
    ])
    assert b["status"] == "Approved"

    r = client.post(f"/api/boq/{b['id']}/convert-to-package", json={
        "departure_date": "2027-05-10", "duration": 9, "quota": 40,
        "hotel_mekkah": "Anjum Hotel", "route_type": "Direct",
    }, headers=bearer(admin_token))
    assert r.status_code == 200, r.text
    j = r.json()
    pid = j["package_id"]

    # Verify row packages ada + harga match kalkulasi (cost 20jt × 40 pax = 800jt group
    # -> cost/pax 20jt + margin 15% = 23jt).
    pkg = client.get(f"/api/packages", headers=bearer(admin_token)).json()
    row = next((p for p in pkg if p["id"] == pid), None)
    assert row is not None, "Paket hasil convert tidak ketemu di /api/packages"
    assert row["price"] == 23_000_000
    assert row["price_quad"] == 23_000_000
    assert row["departure_date"] == "2027-05-10"
    assert row["duration"] == 9
    assert row["quota"] == 40

    # Verify BOQ.package_id sekarang link ke paket baru + notes ada trace.
    detail = client.get(f"/api/boq/{b['id']}", headers=bearer(admin_token)).json()
    assert detail["package_id"] == pid
    assert "Converted to Package" in (detail["notes"] or "")


def test_convert_requires_mgmt(client, admin_token, sales_token):
    """Sales tidak boleh convert -- keputusan Master Paket = mgmt/admin only."""
    b = _mk_boq(client, admin_token, name="TEST BOQ Convert RBAC", items=[
        {"category": "tiket", "item_name": "X", "unit": "per_pax",
         "quantity": 1, "unit_price": 100},
    ])
    r = client.post(f"/api/boq/{b['id']}/convert-to-package",
                    json={"departure_date": "2027-01-01", "duration": 9},
                    headers=bearer(sales_token))
    assert r.status_code == 403


def test_convert_only_approved(client, sales_token, management_token):
    """BOQ Draft/Pending tidak bisa di-convert -- harus Approved dulu."""
    b = _mk_boq(client, sales_token, name="TEST BOQ Convert NotApproved", items=[
        {"category": "tiket", "item_name": "X", "unit": "per_pax",
         "quantity": 1, "unit_price": 100},
    ])
    # Status Draft -- convert ditolak.
    r = client.post(f"/api/boq/{b['id']}/convert-to-package",
                    json={"departure_date": "2027-01-01", "duration": 9},
                    headers=bearer(management_token))
    assert r.status_code == 400
    assert "Approved" in r.json()["error"]


def test_convert_blocks_if_already_linked(client, admin_token):
    """BOQ sudah punya package_id != NULL -> tidak boleh convert lagi."""
    # Bikin paket dummy dulu supaya bisa link.
    pkg_r = client.post("/api/packages", json={
        "name": "TEST BOQ Convert PreLinked",
        "price": 20_000_000, "price_quad": 20_000_000,
        "departure_date": "2027-01-01", "duration": 9, "quota": 30,
    }, headers=bearer(admin_token))
    pkg_id = pkg_r.json()["id"]

    b = _mk_boq(client, admin_token, name="TEST BOQ Prelinked", package_id=pkg_id, items=[
        {"category": "tiket", "item_name": "X", "unit": "per_pax",
         "quantity": 1, "unit_price": 100},
    ])
    r = client.post(f"/api/boq/{b['id']}/convert-to-package",
                    json={"departure_date": "2027-01-01", "duration": 9},
                    headers=bearer(admin_token))
    assert r.status_code == 400
    assert "terikat" in r.json()["error"].lower()


def test_convert_requires_departure_and_duration(client, admin_token):
    """departure_date + duration wajib -- BOQ tidak simpan info itu."""
    b = _mk_boq(client, admin_token, name="TEST BOQ Convert Missing", items=[
        {"category": "tiket", "item_name": "X", "unit": "per_pax",
         "quantity": 1, "unit_price": 100},
    ])
    r = client.post(f"/api/boq/{b['id']}/convert-to-package", json={},
                    headers=bearer(admin_token))
    assert r.status_code == 400
    r = client.post(f"/api/boq/{b['id']}/convert-to-package",
                    json={"departure_date": "2027-01-01"}, headers=bearer(admin_token))
    assert r.status_code == 400  # duration masih kosong


# ---------------------------------------------------------------------------
# compare (Phase 3a) -- GET /api/boq/compare?ids=1,2,3
# ---------------------------------------------------------------------------
def test_compare_two_boqs_happy(client, admin_token):
    """2 BOQ approved -> response berisi 2 boq lengkap dgn items + totals."""
    a = _mk_boq(client, admin_token, name="TEST BOQ Compare A", target_pax=40, margin=15, items=[
        {"category": "hotel_mekkah", "item_name": "Anjum", "unit": "per_pax",
         "quantity": 1, "unit_price": 8_000_000},
    ])
    b = _mk_boq(client, admin_token, name="TEST BOQ Compare B", target_pax=40, margin=20, items=[
        {"category": "hotel_mekkah", "item_name": "Swissotel", "unit": "per_pax",
         "quantity": 1, "unit_price": 10_000_000},
    ])
    r = client.get(f"/api/boq/compare?ids={a['id']},{b['id']}", headers=bearer(admin_token))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["count"] == 2
    assert len(j["boqs"]) == 2
    # Urutan sesuai input.
    assert j["boqs"][0]["id"] == a["id"]
    assert j["boqs"][1]["id"] == b["id"]
    # Items + totals ikut.
    assert len(j["boqs"][0]["items"]) == 1
    assert j["boqs"][0]["totals"]["price_per_pax"] == 9_200_000  # 8jt + 15%
    assert j["boqs"][1]["totals"]["price_per_pax"] == 12_000_000  # 10jt + 20%


def test_compare_missing_ids_param_400(client, admin_token):
    r = client.get("/api/boq/compare", headers=bearer(admin_token))
    assert r.status_code == 400
    assert "ids" in r.json()["error"].lower()


def test_compare_needs_min_two(client, admin_token):
    a = _mk_boq(client, admin_token, name="TEST BOQ Compare Solo")
    r = client.get(f"/api/boq/compare?ids={a['id']}", headers=bearer(admin_token))
    assert r.status_code == 400
    assert "minimal 2" in r.json()["error"].lower()


def test_compare_caps_at_five(client, admin_token):
    """6 ids -> 400."""
    ids = []
    for i in range(6):
        b = _mk_boq(client, admin_token, name=f"TEST BOQ Compare Cap {i}")
        ids.append(str(b["id"]))
    r = client.get(f"/api/boq/compare?ids={','.join(ids)}", headers=bearer(admin_token))
    assert r.status_code == 400
    assert "maksimal 5" in r.json()["error"].lower()


def test_compare_missing_boq_404(client, admin_token):
    a = _mk_boq(client, admin_token, name="TEST BOQ Compare Exists")
    r = client.get(f"/api/boq/compare?ids={a['id']},999999", headers=bearer(admin_token))
    assert r.status_code == 404


def test_compare_dedups_and_preserves_order(client, admin_token):
    """ids=B,A,B,A -> hasil [B,A] (dedup, urutan input pertama menang)."""
    a = _mk_boq(client, admin_token, name="TEST BOQ Compare Dedup A")
    b = _mk_boq(client, admin_token, name="TEST BOQ Compare Dedup B")
    r = client.get(f"/api/boq/compare?ids={b['id']},{a['id']},{b['id']},{a['id']}",
                   headers=bearer(admin_token))
    assert r.status_code == 200
    j = r.json()
    assert [x["id"] for x in j["boqs"]] == [b["id"], a["id"]]


# ---------------------------------------------------------------------------
# Templates (Phase 3b)
# ---------------------------------------------------------------------------
def _mk_template(client, token, name="TEST Template Umroh 9H", items=None):
    body = {"name": name, "description": "template test", "items": items or []}
    r = client.post("/api/boq/templates", json=body, headers=bearer(token))
    assert r.status_code == 200, r.text
    return r.json()


def test_template_create_requires_mgmt(client, sales_token):
    r = client.post("/api/boq/templates",
                    json={"name": "Sales Buat Template"},
                    headers=bearer(sales_token))
    assert r.status_code == 403


def test_template_create_happy_and_list(client, management_token):
    t = _mk_template(client, management_token, name="TEST Template Reguler", items=[
        {"category": "hotel_mekkah", "item_name": "Anjum", "unit": "per_pax",
         "quantity": 1, "unit_price": 8_000_000},
        {"category": "tiket", "item_name": "Saudia", "unit": "per_pax",
         "quantity": 1, "unit_price": 12_000_000},
    ])
    # Detail berisi 2 items.
    detail = client.get(f"/api/boq/templates/{t['id']}", headers=bearer(management_token)).json()
    assert detail["name"] == "TEST Template Reguler"
    assert len(detail["items"]) == 2
    # List include row tsb.
    rows = client.get("/api/boq/templates", headers=bearer(management_token)).json()
    assert any(r["id"] == t["id"] and r["item_count"] == 2 for r in rows)


def test_template_list_visible_to_sales(client, management_token, sales_token):
    """Sales boleh baca template supaya bisa apply ke Draft-nya."""
    t = _mk_template(client, management_token, name="TEST Template Visible")
    rows = client.get("/api/boq/templates", headers=bearer(sales_token)).json()
    assert any(r["id"] == t["id"] for r in rows)


def test_template_update_replaces_items(client, management_token):
    t = _mk_template(client, management_token, name="TEST Template Update", items=[
        {"category": "tiket", "item_name": "Old", "unit": "per_pax",
         "quantity": 1, "unit_price": 5_000_000},
    ])
    r = client.put(f"/api/boq/templates/{t['id']}", json={
        "name": "TEST Template Updated",
        "items": [
            {"category": "visa", "item_name": "Visa Umroh", "unit": "per_pax",
             "quantity": 1, "unit_price": 1_500_000},
            {"category": "muthawwif", "item_name": "TL", "unit": "per_group",
             "quantity": 1, "unit_price": 3_000_000},
        ],
    }, headers=bearer(management_token))
    assert r.status_code == 200
    detail = client.get(f"/api/boq/templates/{t['id']}", headers=bearer(management_token)).json()
    assert detail["name"] == "TEST Template Updated"
    assert len(detail["items"]) == 2
    assert {i["item_name"] for i in detail["items"]} == {"Visa Umroh", "TL"}


def test_template_delete_cascades(client, management_token):
    t = _mk_template(client, management_token, name="TEST Template Del", items=[
        {"category": "tiket", "item_name": "X", "unit": "per_pax",
         "quantity": 1, "unit_price": 100},
    ])
    r = client.delete(f"/api/boq/templates/{t['id']}", headers=bearer(management_token))
    assert r.status_code == 200
    # Detail sekarang 404.
    r = client.get(f"/api/boq/templates/{t['id']}", headers=bearer(management_token))
    assert r.status_code == 404


def test_template_apply_appends_to_draft(client, management_token, sales_token):
    """Sales bikin Draft (kosong) + apply template mgmt -> items ter-append."""
    t = _mk_template(client, management_token, name="TEST Template Apply", items=[
        {"category": "hotel_mekkah", "item_name": "Anjum", "unit": "per_pax",
         "quantity": 1, "unit_price": 8_000_000},
        {"category": "tiket", "item_name": "Saudia", "unit": "per_pax",
         "quantity": 1, "unit_price": 12_000_000},
    ])
    b = _mk_boq(client, sales_token, name="TEST BOQ Apply Target")
    r = client.post(f"/api/boq/templates/{t['id']}/apply/{b['id']}",
                    headers=bearer(sales_token))
    assert r.status_code == 200
    assert r.json()["applied"] == 2
    detail = client.get(f"/api/boq/{b['id']}", headers=bearer(sales_token)).json()
    assert len(detail["items"]) == 2
    names = {i["item_name"] for i in detail["items"]}
    assert names == {"Anjum", "Saudia"}


def test_template_apply_empty_400(client, management_token, sales_token):
    """Apply template kosong -> 400 (no-op yang confusing)."""
    t = _mk_template(client, management_token, name="TEST Template Empty")  # 0 items
    b = _mk_boq(client, sales_token, name="TEST BOQ Empty Apply")
    r = client.post(f"/api/boq/templates/{t['id']}/apply/{b['id']}",
                    headers=bearer(sales_token))
    assert r.status_code == 400
    assert "kosong" in r.json()["error"].lower()


def test_template_apply_blocks_after_submit(client, management_token, sales_token):
    """Sales submit BOQ -> tidak bisa apply template lagi (BOQ locked)."""
    t = _mk_template(client, management_token, name="TEST Template LockedTarget", items=[
        {"category": "tiket", "item_name": "X", "unit": "per_pax",
         "quantity": 1, "unit_price": 100},
    ])
    b = _mk_boq(client, sales_token, name="TEST BOQ Locked", items=[
        {"category": "visa", "item_name": "Visa", "unit": "per_pax",
         "quantity": 1, "unit_price": 100},
    ])
    client.post(f"/api/boq/{b['id']}/submit", headers=bearer(sales_token))
    r = client.post(f"/api/boq/templates/{t['id']}/apply/{b['id']}",
                    headers=bearer(sales_token))
    assert r.status_code == 400
    assert "Draft" in r.json()["error"]


def test_template_apply_appends_offset_after_existing(client, management_token, admin_token):
    """Apply template tidak menimpa item existing -- sort_order lanjut."""
    b = _mk_boq(client, admin_token, name="TEST BOQ Offset", items=[
        {"category": "hotel_mekkah", "item_name": "Existing1", "unit": "per_pax",
         "quantity": 1, "unit_price": 1_000_000, "sort_order": 0},
    ])
    t = _mk_template(client, management_token, name="TEST Template Offset", items=[
        {"category": "tiket", "item_name": "TplItem", "unit": "per_pax",
         "quantity": 1, "unit_price": 2_000_000, "sort_order": 0},
    ])
    r = client.post(f"/api/boq/templates/{t['id']}/apply/{b['id']}",
                    headers=bearer(admin_token))
    assert r.status_code == 200
    detail = client.get(f"/api/boq/{b['id']}", headers=bearer(admin_token)).json()
    assert len(detail["items"]) == 2
    # Existing item tetap ada; template item ditambah.
    names = [i["item_name"] for i in detail["items"]]
    assert "Existing1" in names and "TplItem" in names


def test_add_item_updates_totals(client, admin_token):
    b = _mk_boq(client, admin_token, name="TEST BOQ ItemAdd", target_pax=1, margin=0)
    r = client.post(f"/api/boq/{b['id']}/items", json={
        "category": "hotel_mekkah", "item_name": "Test Hotel",
        "unit": "per_pax", "quantity": 2, "unit_price": 5_000_000,
    }, headers=bearer(admin_token))
    assert r.status_code == 200
    detail = client.get(f"/api/boq/{b['id']}", headers=bearer(admin_token)).json()
    assert detail["totals"]["item_count"] == 1
    assert detail["items"][0]["subtotal"] == 10_000_000


# ---------------------------------------------------------------------------
# Phase 6a: schema migration 007 (bucket + boq_type)
# Backend/UI belum aware bucket -- test ini fokus pada layer DB & data
# integrity. Backend akan meng-honor bucket setelah Phase 6b.
# ---------------------------------------------------------------------------
def test_migration_007_columns_exist(client):
    """Setelah init_db (yg auto-run migrations), package_boq_items harus punya
    kolom bucket, boq_template_items juga, dan package_boq punya boq_type."""
    import sqlite3
    import os
    con = sqlite3.connect(os.environ["UMAR_DB_FILE"])
    try:
        # package_boq_items
        cols = {r[1]: r for r in con.execute("PRAGMA table_info(package_boq_items)")}
        assert "bucket" in cols, "kolom bucket harus ada di package_boq_items"
        # Default value 'hpp' (kolom 4 di PRAGMA row)
        assert cols["bucket"][4] == "'hpp'", f"default bucket harus 'hpp', got {cols['bucket'][4]}"

        # boq_template_items
        cols = {r[1]: r for r in con.execute("PRAGMA table_info(boq_template_items)")}
        assert "bucket" in cols, "kolom bucket harus ada di boq_template_items"
        assert cols["bucket"][4] == "'hpp'"

        # package_boq
        cols = {r[1]: r for r in con.execute("PRAGMA table_info(package_boq)")}
        assert "boq_type" in cols, "kolom boq_type harus ada di package_boq"
        assert cols["boq_type"][4] == "'umar_reguler'"
    finally:
        con.close()


def test_migration_007_recorded_in_schema_migrations(client):
    """Migration harus tercatat di tabel schema_migrations supaya tidak
    dijalankan ulang di next boot."""
    import sqlite3
    import os
    con = sqlite3.connect(os.environ["UMAR_DB_FILE"])
    try:
        row = con.execute(
            "SELECT version FROM schema_migrations WHERE version=?",
            ("007_boq_bucket_and_type",),
        ).fetchone()
        assert row is not None, "migration 007 harus tercatat"
    finally:
        con.close()


def test_new_boq_items_default_bucket_hpp(client, admin_token):
    """BOQ item baru yg dibuat tanpa specify bucket harus default 'hpp'.
    Ini kontrak backward-compat: caller pra-Phase-6b tidak perlu ubah apapun."""
    import sqlite3
    import os
    b = _mk_boq(client, admin_token, name="TEST BOQ Phase6a-BucketDefault",
                target_pax=1, margin=0)
    r = client.post(f"/api/boq/{b['id']}/items", json={
        "category": "hotel_mekkah", "item_name": "Test Bucket Default",
        "unit": "per_pax", "quantity": 1, "unit_price": 1_000_000,
    }, headers=bearer(admin_token))
    assert r.status_code == 200

    con = sqlite3.connect(os.environ["UMAR_DB_FILE"])
    try:
        row = con.execute(
            "SELECT bucket FROM package_boq_items WHERE boq_id=? "
            "AND item_name='Test Bucket Default'",
            (b["id"],),
        ).fetchone()
        assert row is not None, "item harus tersimpan"
        assert row[0] == "hpp", f"default bucket harus 'hpp', got {row[0]}"
    finally:
        con.close()


def test_new_boq_defaults_boq_type_umar_reguler(client, admin_token):
    """BOQ baru tanpa specify boq_type -> default 'umar_reguler' di DB
    (backward-compat sepenuhnya utk caller lama)."""
    import sqlite3
    import os
    b = _mk_boq(client, admin_token, name="TEST BOQ Phase6a-TypeDefault")

    con = sqlite3.connect(os.environ["UMAR_DB_FILE"])
    try:
        row = con.execute(
            "SELECT boq_type FROM package_boq WHERE id=?", (b["id"],)
        ).fetchone()
        assert row is not None
        assert row[0] == "umar_reguler", f"default boq_type harus 'umar_reguler', got {row[0]}"
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Phase 6b: backend bucket-aware CRUD + prorate + boq_type + compare
# ---------------------------------------------------------------------------
def test_create_boq_with_boq_type_uts(client, admin_token):
    """Body kirim boq_type='uts_partner' -> tersimpan + terlihat di GET detail."""
    r = client.post("/api/boq", json={
        "name": "TEST BOQ Phase6b-UTS",
        "boq_type": "uts_partner",
    }, headers=bearer(admin_token))
    assert r.status_code == 200
    bid = r.json()["id"]
    detail = client.get(f"/api/boq/{bid}", headers=bearer(admin_token)).json()
    assert detail["boq_type"] == "uts_partner"


def test_create_boq_rejects_invalid_boq_type(client, admin_token):
    r = client.post("/api/boq", json={
        "name": "TEST BOQ Phase6b-InvalidType",
        "boq_type": "not_a_real_type",
    }, headers=bearer(admin_token))
    assert r.status_code == 400
    assert "boq_type" in r.json()["error"].lower()


def test_update_boq_changes_boq_type(client, admin_token):
    b = _mk_boq(client, admin_token, name="TEST BOQ Phase6b-UpdateType")
    # Default sudah umar_reguler.
    r = client.put(f"/api/boq/{b['id']}", json={"boq_type": "itikaf"},
                   headers=bearer(admin_token))
    assert r.status_code == 200
    detail = client.get(f"/api/boq/{b['id']}", headers=bearer(admin_token)).json()
    assert detail["boq_type"] == "itikaf"


def test_add_item_with_bucket_fee_agen(client, admin_token):
    """Item baru bisa specify bucket. GET detail return bucket per item."""
    b = _mk_boq(client, admin_token, name="TEST BOQ Phase6b-FeeAgen",
                target_pax=10, margin=0)
    r = client.post(f"/api/boq/{b['id']}/items", json={
        "category": "lain", "item_name": "Fee Agen Utama",
        "unit": "per_pax", "quantity": 1, "unit_price": 500_000,
        "bucket": "fee_agen",
    }, headers=bearer(admin_token))
    assert r.status_code == 200
    detail = client.get(f"/api/boq/{b['id']}", headers=bearer(admin_token)).json()
    assert detail["items"][0]["bucket"] == "fee_agen"
    # Fee agen kontribusi ke price_per_pax, tidak ke cost_per_pax.
    assert detail["totals"]["cost_per_pax"] == 0
    assert detail["totals"]["price_per_pax"] == 500_000
    assert detail["totals"]["buckets"]["fee_agen"]["per_pax"] == 500_000


def test_add_item_rejects_invalid_bucket(client, admin_token):
    b = _mk_boq(client, admin_token, name="TEST BOQ Phase6b-BadBucket",
                target_pax=1, margin=0)
    r = client.post(f"/api/boq/{b['id']}/items", json={
        "category": "hotel_mekkah", "item_name": "X",
        "unit": "per_pax", "quantity": 1, "unit_price": 1_000_000,
        "bucket": "invalid_bucket",
    }, headers=bearer(admin_token))
    assert r.status_code == 400
    assert "bucket" in r.json()["error"].lower()


def test_update_item_changes_bucket(client, admin_token):
    """Existing item bisa dipindah bucket via PUT."""
    b = _mk_boq(client, admin_token, name="TEST BOQ Phase6b-MoveBucket",
                target_pax=1, margin=0)
    r = client.post(f"/api/boq/{b['id']}/items", json={
        "category": "muthawwif", "item_name": "TL Fadli",
        "unit": "per_group", "quantity": 1, "unit_price": 15_000_000,
    }, headers=bearer(admin_token))
    iid = r.json()["id"]
    # Default hpp -> pindah ke prorate_tl.
    r = client.put(f"/api/boq/{b['id']}/items/{iid}", json={"bucket": "prorate_tl"},
                   headers=bearer(admin_token))
    assert r.status_code == 200
    detail = client.get(f"/api/boq/{b['id']}", headers=bearer(admin_token)).json()
    it = next(i for i in detail["items"] if i["id"] == iid)
    assert it["bucket"] == "prorate_tl"


def test_prorate_tl_divides_by_target_pax(client, admin_token):
    """Item bucket=prorate_tl unit=per_group -> dibagi target_pax jadi cost/pax.

    Contoh dari Excel: biaya TL Rp 15jt, target 30 pax, free 2 TL = 1 TL prorate.
    Simulasi: 1 item per_group qty=1 unit_price=15jt, target_pax=30.
    Expected prorate_tl_per_pax = 15_000_000 / 30 = 500_000.
    """
    b = _mk_boq(client, admin_token, name="TEST BOQ Phase6b-Prorate",
                target_pax=30, margin=0)
    r = client.post(f"/api/boq/{b['id']}/items", json={
        "category": "muthawwif", "item_name": "TL Prorate",
        "unit": "per_group", "quantity": 1, "unit_price": 15_000_000,
        "bucket": "prorate_tl",
    }, headers=bearer(admin_token))
    assert r.status_code == 200
    detail = client.get(f"/api/boq/{b['id']}", headers=bearer(admin_token)).json()
    t = detail["totals"]
    assert t["buckets"]["prorate_tl"]["group"] == 15_000_000
    assert t["buckets"]["prorate_tl"]["per_pax"] == 500_000
    # cost_per_pax = hpp + prorate_tl per pax = 0 + 500_000
    assert t["cost_per_pax"] == 500_000
    assert t["price_per_pax"] == 500_000  # no fee/margin


def test_all_buckets_sum_to_price_per_pax(client, admin_token):
    """Skenario Excel lengkap: HPP + prorate + fee_agen + fee_referal + margin.
    Semua bucket dijumlah harus == price_per_pax."""
    b = _mk_boq(client, admin_token, name="TEST BOQ Phase6b-AllBuckets",
                target_pax=30, margin=0)  # margin_pct=0 supaya explicit
    body = [
        # HPP: hotel per_pax 20jt (per pax); tiket per_pax 5jt (per pax)
        ("hotel_mekkah", "Hotel", "per_pax", 1, 20_000_000, "hpp"),
        ("tiket", "Tiket", "per_pax", 1, 5_000_000, "hpp"),
        # prorate_tl: TL per_group 15jt (dibagi 30 = 500rb/pax)
        ("muthawwif", "TL", "per_group", 1, 15_000_000, "prorate_tl"),
        # fee_agen: per_pax 500rb
        ("lain", "Fee Agen", "per_pax", 1, 500_000, "fee_agen"),
        # fee_referal: per_pax 200rb
        ("lain", "Fee Referal", "per_pax", 1, 200_000, "fee_referal"),
        # margin: per_pax 1.5jt
        ("margin", "Margin UMAR", "per_pax", 1, 1_500_000, "margin"),
    ]
    for cat, name, unit, qty, up, bkt in body:
        r = client.post(f"/api/boq/{b['id']}/items", json={
            "category": cat, "item_name": name, "unit": unit,
            "quantity": qty, "unit_price": up, "bucket": bkt,
        }, headers=bearer(admin_token))
        assert r.status_code == 200, r.text
    detail = client.get(f"/api/boq/{b['id']}", headers=bearer(admin_token)).json()
    bkts = detail["totals"]["buckets"]
    # Expected per-pax:
    #   hpp = (20jt + 5jt) = 25jt (semua per_pax)
    #   prorate_tl = 15jt / 30 = 500rb
    #   fee_agen = 500rb
    #   fee_referal = 200rb
    #   margin = 1.5jt (from item, bukan dari %)
    #   total price = 25jt + 500rb + 500rb + 200rb + 1.5jt = 27_700_000
    assert bkts["hpp"]["per_pax"] == 25_000_000
    assert bkts["prorate_tl"]["per_pax"] == 500_000
    assert bkts["fee_agen"]["per_pax"] == 500_000
    assert bkts["fee_referal"]["per_pax"] == 200_000
    assert bkts["margin"]["per_pax"] == 1_500_000
    assert bkts["margin"]["from_items"] is True
    assert detail["totals"]["price_per_pax"] == 27_700_000
    # cost_per_pax = HPP + prorate_tl only (bukan fee/margin)
    assert detail["totals"]["cost_per_pax"] == 25_500_000


def test_legacy_margin_pct_when_no_margin_bucket_item(client, admin_token):
    """BOQ tanpa item bucket=margin, target_margin_pct=15 -> honor legacy
    behavior: margin = hpp_per_pax * 0.15 (pattern pre-Phase 6b).
    """
    b = _mk_boq(client, admin_token, name="TEST BOQ Phase6b-LegacyMargin",
                target_pax=10, margin=15)
    r = client.post(f"/api/boq/{b['id']}/items", json={
        "category": "hotel_mekkah", "item_name": "Hotel",
        "unit": "per_pax", "quantity": 1, "unit_price": 10_000_000,
    }, headers=bearer(admin_token))
    assert r.status_code == 200
    detail = client.get(f"/api/boq/{b['id']}", headers=bearer(admin_token)).json()
    t = detail["totals"]
    # HPP per pax = 10jt, margin dari % = 10jt * 0.15 = 1.5jt
    assert t["cost_per_pax"] == 10_000_000
    assert t["margin_amount"] == 1_500_000
    assert t["price_per_pax"] == 11_500_000
    assert t["buckets"]["margin"]["from_items"] is False


def test_compare_includes_bucket_breakdown(client, admin_token):
    """Compare endpoint return totals.buckets untuk tiap BOQ (Phase 6b)."""
    b1 = _mk_boq(client, admin_token, name="TEST BOQ Phase6b-CompA",
                 target_pax=10, margin=0)
    b2 = _mk_boq(client, admin_token, name="TEST BOQ Phase6b-CompB",
                 target_pax=10, margin=0)
    # BOQ 1: HPP only.
    client.post(f"/api/boq/{b1['id']}/items", json={
        "category": "hotel_mekkah", "item_name": "Hotel", "unit": "per_pax",
        "quantity": 1, "unit_price": 10_000_000, "bucket": "hpp",
    }, headers=bearer(admin_token))
    # BOQ 2: HPP + margin (explicit item).
    client.post(f"/api/boq/{b2['id']}/items", json={
        "category": "hotel_mekkah", "item_name": "Hotel", "unit": "per_pax",
        "quantity": 1, "unit_price": 10_000_000, "bucket": "hpp",
    }, headers=bearer(admin_token))
    client.post(f"/api/boq/{b2['id']}/items", json={
        "category": "margin", "item_name": "Margin", "unit": "per_pax",
        "quantity": 1, "unit_price": 2_000_000, "bucket": "margin",
    }, headers=bearer(admin_token))

    r = client.get(f"/api/boq/compare?ids={b1['id']},{b2['id']}",
                   headers=bearer(admin_token))
    assert r.status_code == 200
    result = r.json()
    assert result["count"] == 2
    for boq in result["boqs"]:
        assert "buckets" in boq["totals"]
        assert set(boq["totals"]["buckets"].keys()) == {
            "hpp", "prorate_tl", "fee_agen", "fee_referal", "margin"
        }
    # BOQ 2 punya margin from_items=True, BOQ 1 tidak.
    b2_data = next(b for b in result["boqs"] if b["id"] == b2["id"])
    assert b2_data["totals"]["buckets"]["margin"]["from_items"] is True


def test_duplicate_boq_preserves_bucket_and_boq_type(client, admin_token):
    """BOQ duplicate -> Draft baru dgn boq_type + semua items' bucket ter-copy."""
    b = _mk_boq(client, admin_token, name="TEST BOQ Phase6b-DupSrc",
                target_pax=1, margin=0)
    # Set boq_type non-default.
    client.put(f"/api/boq/{b['id']}", json={"boq_type": "uts_partner"},
               headers=bearer(admin_token))
    # Tambah 2 items: 1 hpp, 1 fee_agen.
    for cat, name, bkt in [("hotel_mekkah", "Hotel", "hpp"),
                            ("lain", "Fee Agen", "fee_agen")]:
        client.post(f"/api/boq/{b['id']}/items", json={
            "category": cat, "item_name": name, "unit": "per_pax",
            "quantity": 1, "unit_price": 1_000_000, "bucket": bkt,
        }, headers=bearer(admin_token))

    r = client.post(f"/api/boq/{b['id']}/duplicate", json={},
                    headers=bearer(admin_token))
    assert r.status_code == 200
    new_bid = r.json()["id"]
    detail = client.get(f"/api/boq/{new_bid}", headers=bearer(admin_token)).json()
    assert detail["boq_type"] == "uts_partner"
    buckets_new = {i["item_name"]: i["bucket"] for i in detail["items"]}
    assert buckets_new == {"Hotel": "hpp", "Fee Agen": "fee_agen"}


def test_template_apply_copies_bucket(client, admin_token):
    """Template item bucket=fee_agen -> ketika apply ke BOQ, bucket ikut."""
    # Buat template dgn 1 item fee_agen.
    r = client.post("/api/boq/templates", json={
        "name": "TEST TPL Phase6b-BucketApply",
        "items": [{
            "category": "lain", "item_name": "Fee Agen Tpl",
            "unit": "per_pax", "quantity": 1, "unit_price": 300_000,
            "bucket": "fee_agen",
        }],
    }, headers=bearer(admin_token))
    tid = r.json()["id"]

    b = _mk_boq(client, admin_token, name="TEST BOQ Phase6b-TplTarget",
                target_pax=1, margin=0)
    r = client.post(f"/api/boq/templates/{tid}/apply/{b['id']}",
                    headers=bearer(admin_token))
    assert r.status_code == 200
    detail = client.get(f"/api/boq/{b['id']}", headers=bearer(admin_token)).json()
    tpl_item = next(i for i in detail["items"] if i["item_name"] == "Fee Agen Tpl")
    assert tpl_item["bucket"] == "fee_agen"


def test_migration_007_idempotent_backfill(client, admin_token):
    """Simulasi migration 007 dijalankan ulang di DB yg sudah applied.
    Tidak boleh error, tidak boleh mengubah row yg sudah bucket=margin."""
    import sqlite3
    import os
    import importlib.util
    import pathlib

    # Dynamic import migration module (nama file diawali digit -> tak bisa
    # `from migrations import 007_...`)
    mig_path = pathlib.Path(__file__).parent.parent / "migrations" / "007_boq_bucket_and_type.py"
    spec = importlib.util.spec_from_file_location("_mig007", mig_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    con = sqlite3.connect(os.environ["UMAR_DB_FILE"])
    try:
        # Setup: insert item dengan category=margin di boq_template_items.
        tpl_row = con.execute(
            "INSERT INTO boq_templates(name, created_by) VALUES(?, ?) RETURNING id",
            ("TEST TPL Phase6a-Idempotent", 1),
        ).fetchone()
        tpl_id = tpl_row[0]
        con.execute(
            "INSERT INTO boq_template_items"
            "(template_id, category, item_name, unit, quantity, unit_price, bucket) "
            "VALUES(?, 'margin', 'Existing Margin', 'per_pax', 1, 1500000, 'margin')",
            (tpl_id,),
        )
        con.commit()

        # Migration 007 up() runs backfill lagi -- tidak boleh error.
        mod.up(con)
        con.commit()

        row = con.execute(
            "SELECT bucket FROM boq_template_items "
            "WHERE template_id=? AND item_name='Existing Margin'",
            (tpl_id,),
        ).fetchone()
        assert row[0] == "margin", "idempotent backfill tidak boleh mengubah row yg sudah correct"

        # Cleanup.
        con.execute("DELETE FROM boq_template_items WHERE template_id=?", (tpl_id,))
        con.execute("DELETE FROM boq_templates WHERE id=?", (tpl_id,))
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Phase 6d-a: template sell_price + variant + optional + preset endpoint
# ---------------------------------------------------------------------------
def test_migration_008_template_columns_exist(client):
    """Migration 008 wajib tambah sell_price/variant/optional di boq_template_items."""
    import sqlite3
    import os
    con = sqlite3.connect(os.environ["UMAR_DB_FILE"])
    try:
        cols = {r[1]: r for r in con.execute("PRAGMA table_info(boq_template_items)")}
        assert "sell_price" in cols
        assert cols["sell_price"][2] == "INTEGER"
        assert "variant" in cols
        assert cols["variant"][2] == "TEXT"
        assert "optional" in cols
        assert cols["optional"][2] == "INTEGER"
        assert cols["optional"][4] == "0"
    finally:
        con.close()


def test_template_create_accepts_sell_price_variant_optional(client, admin_token):
    """Body items terima 3 kolom baru; GET detail return unchanged."""
    r = client.post("/api/boq/templates", json={
        "name": "TEST TPL Phase6d-Fields",
        "items": [
            {"category": "perlengkapan", "item_name": "Minimalis L",
             "unit": "per_pax", "quantity": 1, "unit_price": 190_000,
             "bucket": "hpp", "sell_price": 350_000, "variant": "Minimalis",
             "optional": False},
            {"category": "lain", "item_name": "Bukhur",
             "unit": "per_pax", "quantity": 1, "unit_price": 100_000,
             "bucket": "hpp", "sell_price": 150_000, "variant": None,
             "optional": True},
        ],
    }, headers=bearer(admin_token))
    assert r.status_code == 200
    tid = r.json()["id"]
    detail = client.get(f"/api/boq/templates/{tid}", headers=bearer(admin_token)).json()
    items = {i["item_name"]: i for i in detail["items"]}
    assert items["Minimalis L"]["sell_price"] == 350_000
    assert items["Minimalis L"]["variant"] == "Minimalis"
    assert items["Minimalis L"]["optional"] == 0
    assert items["Bukhur"]["sell_price"] == 150_000
    assert items["Bukhur"]["variant"] is None
    assert items["Bukhur"]["optional"] == 1


def test_template_create_backward_compat_no_new_fields(client, admin_token):
    """Caller lama tanpa sell_price/variant/optional -> default NULL/NULL/0."""
    r = client.post("/api/boq/templates", json={
        "name": "TEST TPL Phase6d-Legacy",
        "items": [{"category": "hotel_mekkah", "item_name": "Hotel Legacy",
                   "unit": "per_pax", "quantity": 1, "unit_price": 5_000_000}],
    }, headers=bearer(admin_token))
    assert r.status_code == 200
    tid = r.json()["id"]
    detail = client.get(f"/api/boq/templates/{tid}", headers=bearer(admin_token)).json()
    it = detail["items"][0]
    assert it["sell_price"] is None
    assert it["variant"] is None
    assert it["optional"] == 0


def test_preset_list_returns_two_presets(client, admin_token):
    """Endpoint /api/boq/templates/presets/available return 2 preset."""
    r = client.get("/api/boq/templates/presets/available",
                   headers=bearer(admin_token))
    assert r.status_code == 200
    keys = {p["key"] for p in r.json()["presets"]}
    assert keys == {"perlengkapan_standar", "bonus_reguler"}


def test_preset_perlengkapan_standar_creates_6_items(client, admin_token):
    """POST preset -> template baru dgn 6 item perlengkapan (3 variant x 2 gender)."""
    r = client.post("/api/boq/templates/preset", json={
        "preset_key": "perlengkapan_standar",
        "name": "TEST TPL Phase6d-Preset Perlengkapan",
    }, headers=bearer(admin_token))
    assert r.status_code == 200
    assert r.json()["items_created"] == 6
    tid = r.json()["id"]

    detail = client.get(f"/api/boq/templates/{tid}",
                        headers=bearer(admin_token)).json()
    variants = {(i["variant"], "L" if "Laki" in i["item_name"] else "P")
                for i in detail["items"]}
    assert variants == {
        ("Full Set", "L"), ("Full Set", "P"),
        ("Minimalis", "L"), ("Minimalis", "P"),
        ("Koper Only", "L"), ("Koper Only", "P"),
    }
    # Semua required (bukan optional).
    assert all(i["optional"] == 0 for i in detail["items"])
    # Semua punya sell_price > unit_price (HPP < harga jual).
    for i in detail["items"]:
        assert i["sell_price"] is not None
        assert i["sell_price"] > 0
        assert i["unit_price"] > 0


def test_preset_bonus_reguler_creates_2_optional_items(client, admin_token):
    """Preset Bonus Reguler -> 2 item optional (Bukhur + Al Baik)."""
    r = client.post("/api/boq/templates/preset", json={
        "preset_key": "bonus_reguler",
        "name": "TEST TPL Phase6d-Preset Bonus",
    }, headers=bearer(admin_token))
    assert r.status_code == 200
    assert r.json()["items_created"] == 2
    tid = r.json()["id"]

    detail = client.get(f"/api/boq/templates/{tid}",
                        headers=bearer(admin_token)).json()
    names = {i["item_name"] for i in detail["items"]}
    assert "Bukhur Umar Oud" in names
    assert "Al Baik Voucher" in names
    # Semua optional.
    assert all(i["optional"] == 1 for i in detail["items"])


def test_preset_invalid_key_400(client, admin_token):
    r = client.post("/api/boq/templates/preset", json={
        "preset_key": "not_a_preset",
    }, headers=bearer(admin_token))
    assert r.status_code == 400
    assert "preset" in r.json()["error"].lower()


def test_preset_requires_mgmt_role(client, sales_token):
    r = client.post("/api/boq/templates/preset", json={
        "preset_key": "perlengkapan_standar",
    }, headers=bearer(sales_token))
    assert r.status_code == 403


def test_preset_endpoint_route_before_tid_dispatch(client, admin_token):
    """GET .../presets/available JANGAN di-treat sebagai GET .../{tid}
    dgn tid='presets' (yg gagal parse integer 404 atau bahkan 500)."""
    r = client.get("/api/boq/templates/presets/available",
                   headers=bearer(admin_token))
    assert r.status_code == 200, \
        f"Route order salah -- {r.status_code}: {r.text}"
