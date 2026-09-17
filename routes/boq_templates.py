"""
Router BOQ Templates: preset line-item BOQ (perlengkapan, bonus, dll) yang
bisa di-apply ke BOQ existing.

Dipisah dari `routes/boq.py` (yang sudah 1000+ LOC) krn cohesive & mostly
standalone -- cuma butuh helper BOQ (`_boq_or_404`, `_assert_editable`,
`_calc_subtotal`, `_validate_item`) yang di-import lokal dari `routes.boq`
saat dipakai (pola yang sama dgn jamaah_read.py yang butuh `_compute_totals`).

Kontrak URL TIDAK berubah -- FastAPI mount kedua router di root path yang
sama, jadi frontend & test tidak perlu update.

Endpoint (8):
  GET    /api/boq/templates                       list semua template
  POST   /api/boq/templates/preset                buat template dari preset key
  GET    /api/boq/templates/presets/available     list preset keys yg tersedia
  GET    /api/boq/templates/{tid}                 detail template + items
  POST   /api/boq/templates                       create template baru
  PUT    /api/boq/templates/{tid}                 update header + replace items
  DELETE /api/boq/templates/{tid}                 hapus template
  POST   /api/boq/templates/{tid}/apply/{bid}     copy template items -> BOQ

RBAC:
  - GET list/detail/presets: semua role authenticated (sales perlu utk
    apply-template).
  - POST/PUT/DELETE template: mgmt/admin only.
  - apply/{bid}: sesuai guard BOQ target (mgmt/admin apa saja; owner cuma Draft).
"""
from fastapi import APIRouter

import db
from deps import (
    Depends,
    HTTPException,
    authenticate_token,
    json_body,
    log_action,
    notify,
    require_role,
)

router = APIRouter(tags=["boq-templates"])


# ---------------------------------------------------------------------------
# constants (dipertahankan sinkron dgn routes/boq.py; kalau di sana berubah,
# di sini juga harus ikut).
# ---------------------------------------------------------------------------
_MGMT_ROLES = ("admin", "management")


# ---------------------------------------------------------------------------
# Phase 6d-a: Preset seeds untuk template.
# Endpoint ini shortcut untuk admin bikin template siap-pakai dari data Excel
# HPP Perlengkapan (numbers dari Sheet1 + HPP Perlengkapan). Admin bisa
# edit/duplicate setelah preset di-import.
# ---------------------------------------------------------------------------
_BOQ_PRESETS = {
    "perlengkapan_standar": {
        "name": "Perlengkapan Standar UMAR",
        "description": (
            "Preset dari HPP Perlengkapan (Excel). Terdiri dari 3 variant "
            "(Full Set / Minimalis / Koper Only) x 2 gender."
        ),
        "items": [
            # (category, item_name, unit, qty, hpp, sell, vendor, note, sort, bucket, variant, optional)
            ("perlengkapan", "Perlengkapan Full Set Laki-laki",  "per_pax", 1, 750000, 850000, None, "Koper+ransel+paspor+tumbler+ihram+kemeja+aksesoris", 1, "hpp", "Full Set",   0),
            ("perlengkapan", "Perlengkapan Full Set Perempuan",  "per_pax", 1, 700000, 850000, None, "Koper+daypack+paspor+mukena+kerudung+outer+aksesoris", 2, "hpp", "Full Set",   0),
            ("perlengkapan", "Perlengkapan Minimalis Laki-laki", "per_pax", 1, 190000, 350000, None, "Koko+aksesoris",   3, "hpp", "Minimalis",  0),
            ("perlengkapan", "Perlengkapan Minimalis Perempuan", "per_pax", 1, 240000, 350000, None, "Kerudung+outer+aksesoris", 4, "hpp", "Minimalis",  0),
            ("perlengkapan", "Perlengkapan Koper Only Laki-laki","per_pax", 1, 550000, 650000, None, "Koper 24'' policarbon + kain ihram + aksesoris", 5, "hpp", "Koper Only", 0),
            ("perlengkapan", "Perlengkapan Koper Only Perempuan","per_pax", 1, 550000, 650000, None, "Koper 24'' policarbon + mukena + aksesoris",      6, "hpp", "Koper Only", 0),
        ],
    },
    "bonus_reguler": {
        "name": "Bonus Reguler UMAR",
        "description": (
            "Preset bonus opsional per paket (Bukhur + Al Baik). Item optional -- "
            "user pilih di apply modal apakah paket ini dapat bonus atau tidak."
        ),
        "items": [
            ("lain",     "Bukhur Umar Oud", "per_pax", 1, 100000, 150000, None, "Parfum khas UMAR",          1, "hpp", None, 1),
            ("konsumsi", "Al Baik Voucher", "per_pax", 1, 125000, 175000, None, "Voucher makan Al Baik Jeddah/Mekkah", 2, "hpp", None, 1),
        ],
    },
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _template_or_404(tid: int) -> dict:
    row = db.query_one("SELECT * FROM boq_templates WHERE id = ?", (tid,))
    if not row:
        raise HTTPException(status_code=404, detail="Template BOQ tidak ditemukan.")
    return row


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------
@router.get("/api/boq/templates")
async def boq_template_list(user=Depends(authenticate_token)):
    """List semua template. Semua role authenticated boleh baca (sales perlu
    utk apply-template ke Draft-nya)."""
    return db.query_all(
        "SELECT t.*, "
        "       u.name AS created_by_name, "
        "       (SELECT COUNT(*) FROM boq_template_items WHERE template_id = t.id) AS item_count "
        "FROM boq_templates t "
        "LEFT JOIN users u ON u.id = t.created_by "
        "ORDER BY t.name"
    ) or []


@router.post("/api/boq/templates/preset")
async def boq_template_preset(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    """Create template dari preset key yang sudah didefinisikan.

    Body: {"preset_key": "perlengkapan_standar" | "bonus_reguler", "name": "override name"}

    Preset items di-seed dgn HPP + sell_price + variant + optional dari
    data Excel HPP Perlengkapan. Admin bisa edit/duplicate template setelah
    di-create -- preset cuma starting point.

    Mgmt/admin only.
    """
    require_role(user, *_MGMT_ROLES)
    key = (body.get("preset_key") or "").strip()
    if key not in _BOQ_PRESETS:
        raise HTTPException(
            status_code=400,
            detail=f"Preset tidak dikenal. Pilihan: {', '.join(sorted(_BOQ_PRESETS.keys()))}",
        )
    preset = _BOQ_PRESETS[key]
    name = (body.get("name") or preset["name"]).strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nama template wajib diisi.")

    tid, _ = db.execute(
        "INSERT INTO boq_templates (name, description, created_by) VALUES (?, ?, ?)",
        (name, preset["description"], user["id"]),
    )
    for (cat, iname, unit, qty, hpp, sell, vendor, note, sort_o, bucket, variant, opt) in preset["items"]:
        db.execute(
            "INSERT INTO boq_template_items "
            "(template_id, category, item_name, unit, quantity, unit_price, "
            " vendor_name, note, sort_order, bucket, sell_price, variant, optional) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tid, cat, iname, unit, float(qty), int(hpp), vendor, note,
             int(sort_o), bucket, int(sell) if sell is not None else None,
             variant, int(opt)),
        )
    log_action(user, "BOQ_TEMPLATE_PRESET", f"key={key} tid={tid} items={len(preset['items'])}")
    notify("data_updated", "boq_template")
    return {"id": tid, "preset_key": key, "items_created": len(preset["items"]),
            "message": f"Template preset '{name}' dibuat dgn {len(preset['items'])} item."}


@router.get("/api/boq/templates/presets/available")
async def boq_template_presets_list(user=Depends(authenticate_token)):
    """List preset keys + metadata (nama, deskripsi, jumlah items). Dipakai UI
    dropdown 'Import dari Preset'."""
    return {
        "presets": [
            {
                "key": k,
                "name": v["name"],
                "description": v["description"],
                "item_count": len(v["items"]),
            }
            for k, v in _BOQ_PRESETS.items()
        ]
    }


@router.get("/api/boq/templates/{tid}")
async def boq_template_detail(tid: int, user=Depends(authenticate_token)):
    t = _template_or_404(tid)
    items = db.query_all(
        "SELECT * FROM boq_template_items WHERE template_id = ? ORDER BY sort_order, id",
        (tid,),
    ) or []
    creator = db.query_one("SELECT name FROM users WHERE id = ?", (t["created_by"],)) if t["created_by"] else None
    return {**dict(t), "items": items, "created_by_name": creator["name"] if creator else None}


@router.post("/api/boq/templates")
async def boq_template_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    """Mgmt/admin only -- template = governance harga preset."""
    require_role(user, *_MGMT_ROLES)
    # Local import: helper validasi item tetap di routes/boq.py utk dipakai
    # jg oleh endpoint BOQ CRUD -- hindari duplikasi.
    from routes.boq import _validate_item
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nama template wajib diisi.")

    tid, _ = db.execute(
        "INSERT INTO boq_templates (name, description, created_by) VALUES (?, ?, ?)",
        (name, body.get("description"), user["id"]),
    )
    for it in body.get("items") or []:
        _validate_item(it)
        db.execute(
            "INSERT INTO boq_template_items "
            "(template_id, category, item_name, unit, quantity, unit_price, "
            " vendor_name, note, sort_order, bucket, sell_price, variant, optional) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tid, it.get("category"), it.get("item_name").strip(),
                it.get("unit") or "per_pax",
                float(it.get("quantity") or 1),
                int(it.get("unit_price") or 0),
                it.get("vendor_name"), it.get("note"),
                int(it.get("sort_order") or 0),
                it.get("bucket") or "hpp",
                # Phase 6d-a: sell_price/variant/optional.
                int(it["sell_price"]) if it.get("sell_price") not in (None, "") else None,
                (it.get("variant") or None),
                1 if it.get("optional") else 0,
            ),
        )
    log_action(user, "BOQ_TEMPLATE_CREATE", f"id={tid} name={name}")
    notify("data_updated", "boq_template")
    return {"id": tid, "message": "Template dibuat."}


@router.put("/api/boq/templates/{tid}")
async def boq_template_update(tid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    """Update header + (kalau `items` di body) replace items secara utuh.
    Mgmt/admin only."""
    require_role(user, *_MGMT_ROLES)
    from routes.boq import _validate_item
    t = _template_or_404(tid)
    name = (body.get("name") or t["name"]).strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nama template wajib diisi.")

    db.execute(
        "UPDATE boq_templates SET name = ?, description = ?, "
        " updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (name, body.get("description", t["description"]), tid),
    )
    # Replace items secara utuh kalau body membawa key 'items'
    # (biar UI simpel: kirim seluruh state items terbaru).
    if "items" in body:
        db.execute("DELETE FROM boq_template_items WHERE template_id = ?", (tid,))
        for it in body.get("items") or []:
            _validate_item(it)
            db.execute(
                "INSERT INTO boq_template_items "
                "(template_id, category, item_name, unit, quantity, unit_price, "
                " vendor_name, note, sort_order) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tid, it.get("category"), it.get("item_name").strip(),
                    it.get("unit") or "per_pax",
                    float(it.get("quantity") or 1),
                    int(it.get("unit_price") or 0),
                    it.get("vendor_name"), it.get("note"),
                    int(it.get("sort_order") or 0),
                ),
            )
    log_action(user, "BOQ_TEMPLATE_UPDATE", f"id={tid}")
    notify("data_updated", "boq_template")
    return {"message": "Template diperbarui."}


@router.delete("/api/boq/templates/{tid}")
async def boq_template_delete(tid: int, user=Depends(authenticate_token)):
    require_role(user, *_MGMT_ROLES)
    _template_or_404(tid)
    db.execute("DELETE FROM boq_templates WHERE id = ?", (tid,))
    log_action(user, "BOQ_TEMPLATE_DELETE", f"id={tid}")
    notify("data_updated", "boq_template")
    return {"message": "Template dihapus."}


@router.post("/api/boq/templates/{tid}/apply/{bid}")
async def boq_template_apply(tid: int, bid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    """Copy template items ke BOQ (append, tidak replace).

    Phase 6d-b: apply sekarang bucket-aware + auto-margin + optional-filter.

    Body optional:
      {"include_ids": [1, 3, 5]}
      Kalau ada, HANYA template items dgn id ini yg ter-copy (utk filter
      items optional). Kalau tidak ada, semua items ter-copy (required +
      optional). Items required (optional=0) SELALU ter-copy walau include_ids
      given -- filter cuma affect items optional=1.

    Auto-margin (Phase 6d-a schema):
      - Kalau template item sell_price NULL atau <= unit_price:
        Create 1 BOQ item dgn bucket dari template (default hpp).
      - Kalau sell_price > unit_price:
        Create 2 BOQ items:
          (a) bucket=hpp,    unit_price=template.unit_price          (HPP cost)
          (b) bucket=margin, unit_price=sell_price - unit_price     (margin selisih)
        Ini merepresentasikan konsep Excel "harga jual per item".

    Guard:
    - BOQ target harus editable (mgmt/admin apa saja; owner cuma Draft).
    - Template harus punya minimal 1 item.
    """
    # Local import: helper BOQ instance-level tetap di routes/boq.py.
    from routes.boq import _boq_or_404, _assert_editable, _calc_subtotal
    boq = _boq_or_404(bid)
    _assert_editable(boq, user)
    t = _template_or_404(tid)

    items = db.query_all(
        "SELECT * FROM boq_template_items WHERE template_id = ? ORDER BY sort_order, id",
        (tid,),
    ) or []
    if not items:
        raise HTTPException(status_code=400, detail="Template kosong -- tambah item template dulu.")

    # Filter items optional lewat include_ids (opsional dari body).
    include_ids = body.get("include_ids")
    if include_ids is not None:
        try:
            include_set = {int(x) for x in include_ids}
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="include_ids harus list of integer.")
        # Optional items: harus di include_set. Required items: selalu ikut.
        items = [it for it in items if not it["optional"] or it["id"] in include_set]
        if not items:
            raise HTTPException(status_code=400, detail="Tidak ada item terpilih -- pilih minimal 1 item optional atau include item required.")

    # Sort_order offset agar item template lanjut setelah item existing.
    max_sort = db.query_one(
        "SELECT COALESCE(MAX(sort_order), -1) AS m FROM package_boq_items WHERE boq_id = ?",
        (bid,),
    )
    offset = (max_sort["m"] if max_sort else -1) + 1

    boq_items_created = 0
    sort_cursor = offset
    for it in items:
        qty = float(it["quantity"] or 1)
        hpp_price = int(it["unit_price"] or 0)
        sell_price = int(it["sell_price"]) if it["sell_price"] is not None else None
        base_bucket = it["bucket"] or "hpp"

        # Case A: no sell_price OR sell_price tidak lebih tinggi dari HPP.
        # Cukup 1 item dgn bucket dari template.
        if sell_price is None or sell_price <= hpp_price:
            db.execute(
                "INSERT INTO package_boq_items "
                "(boq_id, category, item_name, unit, quantity, unit_price, subtotal, "
                " vendor_name, note, sort_order, bucket) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    bid, it["category"], it["item_name"], it["unit"], qty, hpp_price,
                    _calc_subtotal(qty, hpp_price),
                    it["vendor_name"], it["note"], sort_cursor, base_bucket,
                ),
            )
            sort_cursor += 1
            boq_items_created += 1
            continue

        # Case B: sell_price > HPP. Create 2 items: HPP + Margin selisih.
        # HPP item pakai bucket dari template (biasa 'hpp').
        db.execute(
            "INSERT INTO package_boq_items "
            "(boq_id, category, item_name, unit, quantity, unit_price, subtotal, "
            " vendor_name, note, sort_order, bucket) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bid, it["category"], it["item_name"], it["unit"], qty, hpp_price,
                _calc_subtotal(qty, hpp_price),
                it["vendor_name"], it["note"], sort_cursor, base_bucket,
            ),
        )
        sort_cursor += 1
        boq_items_created += 1

        # Margin item -- item_name dgn suffix '(Margin)', bucket=margin.
        # Category tetap sama supaya report per kategori tetap konsisten.
        margin_amt = sell_price - hpp_price
        db.execute(
            "INSERT INTO package_boq_items "
            "(boq_id, category, item_name, unit, quantity, unit_price, subtotal, "
            " vendor_name, note, sort_order, bucket) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bid, it["category"], f"{it['item_name']} (Margin)",
                it["unit"], qty, margin_amt,
                _calc_subtotal(qty, margin_amt),
                it["vendor_name"],
                f"Auto dari template: sell {sell_price} - HPP {hpp_price}",
                sort_cursor, "margin",
            ),
        )
        sort_cursor += 1
        boq_items_created += 1

    db.execute("UPDATE package_boq SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (bid,))
    log_action(
        user, "BOQ_TEMPLATE_APPLY",
        f"tmpl={tid} boq={bid} tpl_items={len(items)} boq_items={boq_items_created}",
    )
    notify("data_updated", "boq")
    return {
        "applied": boq_items_created,
        "template_items_used": len(items),
        "message": f"{boq_items_created} item ditambahkan ke BOQ (dari {len(items)} template items).",
    }
