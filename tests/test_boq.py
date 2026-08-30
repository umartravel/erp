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
def _mk_boq(client, token, name="Skenario Reguler", package_id=None, items=None, target_pax=40, margin=15):
    body = {
        "name": name,
        "package_id": package_id,
        "target_pax": target_pax,
        "target_margin_pct": margin,
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
