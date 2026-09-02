"""
Router Simulasi Paket (BOQ = Bill of Quantities).

Modul baru, TERPISAH dari Master Paket. BOQ = breakdown biaya per line item
(hotel Mekkah, Madinah, tiket, visa, dll) plus target margin -> hasilkan
harga per pax. Multiple BOQ Approved boleh per paket (skenario harga).

Endpoint:
  GET    /api/boq                       list (filter package_id, status)
  GET    /api/boq/{bid}                 detail + line items + kalkulasi total
  POST   /api/boq                       create header (+ optional items nested)
  PUT    /api/boq/{bid}                 update header (Draft & Approved editable)
  DELETE /api/boq/{bid}                 hapus BOQ (cascades items)

  POST   /api/boq/{bid}/items           tambah line item
  PUT    /api/boq/{bid}/items/{iid}     update line item
  DELETE /api/boq/{bid}/items/{iid}     hapus line item

  POST   /api/boq/{bid}/submit          Draft -> Pending Approval  (sales/ops)
  POST   /api/boq/{bid}/approve         Pending -> Approved        (mgmt/admin)
  POST   /api/boq/{bid}/reject          Pending -> Rejected        (mgmt/admin)
  POST   /api/boq/{bid}/duplicate       clone BOQ+items ke Draft baru

RBAC:
  - Sales/Ops: create Draft (own), edit/delete own Draft, submit own.
  - Mgmt/Admin: create Approved langsung (bypass pending), edit/delete/approve
    apa saja, terlebih submit sendiri.
  - Finance: view-only (list + detail).
"""
import datetime
import json

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

router = APIRouter(tags=["boq"])


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
_MGMT_ROLES = ("admin", "management")
_AUTHOR_ROLES = ("admin", "management", "sales", "ops")  # boleh bikin BOQ
_UNIT_TYPES = {"per_pax", "per_room_per_night", "per_group", "per_pax_per_day"}

# Phase 6b: bucket = layer di formula harga jual.
#   hpp         -> biaya nyata (hotel, tiket, visa, transport, TL kalau
#                  paket "TL include").
#   prorate_tl  -> biaya TL yang di-share ke pax (paket "TL exclude").
#                  Item pakai unit per_group, sistem auto-bagi target_pax.
#   fee_agen    -> komisi jaringan penjual.
#   fee_referal -> komisi personal referal/sponsor.
#   margin      -> profit UMAR.
# Default 'hpp' -- legacy items tetap valid tanpa perubahan caller.
_BUCKETS = {"hpp", "prorate_tl", "fee_agen", "fee_referal", "margin"}

# Phase 6b: boq_type = kategori paket. Metadata + hint UI validation.
_BOQ_TYPES = {"umar_reguler", "umar_ramadhan", "uts_partner", "itikaf"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _calc_subtotal(quantity: float, unit_price: int) -> int:
    """Cache subtotal biar list endpoint tidak recompute per row."""
    return int(round((quantity or 0) * (unit_price or 0)))


def _boq_or_404(bid: int) -> dict:
    row = db.query_one("SELECT * FROM package_boq WHERE id = ?", (bid,))
    if not row:
        raise HTTPException(status_code=404, detail="BOQ tidak ditemukan.")
    return row


def _assert_editable(boq: dict, user: dict) -> None:
    """Guard: siapa boleh edit BOQ ini?

    - Mgmt/Admin: apa saja.
    - Owner: hanya kalau status Draft.
    Pending Approval / Rejected: locked utk non-mgmt (harus di-approve/reject
    dulu atau duplicate baru).
    """
    role = user.get("role")
    if role in _MGMT_ROLES:
        return
    if boq["created_by"] != user["id"]:
        raise HTTPException(status_code=403, detail="Bukan BOQ Anda.")
    if boq["status"] != "Draft":
        raise HTTPException(
            status_code=400,
            detail="Hanya BOQ Draft yang bisa diubah. Duplicate untuk revisi baru.",
        )


def _compute_totals(
    boq_id: int,
    target_pax: int | None,
    margin_pct: float | None,
    extra_triple: int | None = None,
    extra_double: int | None = None,
) -> dict:
    """Hitung total group cost + harga per pax + split per room type + breakdown
    per bucket (Phase 6b).

    Aturan unit item (berlaku PER bucket, tidak hanya HPP):
      per_pax          -> subtotal * target_pax (per orang, dibayar semua pax)
      per_pax_per_day  -> subtotal * target_pax (user isi quantity = hari)
      per_group        -> subtotal (1x, mis. sewa bus, biaya TL total)
      per_room_per_night -> subtotal (user isi quantity = rooms * nights)

    Bucket semantics (Phase 6b):
      hpp         -> biaya nyata (cost). Termasuk TL kalau paket "TL include".
      prorate_tl  -> biaya TL yang di-share ke pax (paket "TL exclude").
                     Biasanya user isi per_group -> sistem auto-bagi target_pax.
      fee_agen    -> komisi jaringan penjual. Biasanya per_pax.
      fee_referal -> komisi personal referal/sponsor. Biasanya per_pax.
      margin      -> profit UMAR. Bisa per_pax atau per_group.

    Formula harga jual per pax:
      hpp_per_pax         = hpp_group / target_pax
      prorate_tl_per_pax  = prorate_tl_group / target_pax
      fee_agen_per_pax    = fee_agen_group / target_pax
      fee_referal_per_pax = fee_referal_group / target_pax
      margin_per_pax      = margin_group / target_pax
      price_per_pax       = sum semua *_per_pax

    Backward-compat: kalau BOQ TIDAK punya item di bucket margin (semua items
    bucket=hpp default), margin_per_pax dihitung dari hpp_per_pax *
    target_margin_pct/100 -- pattern legacy pre-Phase-6b. BOQ existing yang
    hanya isi hpp + margin_pct=15 tetap menghasilkan price_per_pax identik.

    Room split (Phase 4a) tetap: extra_triple/extra_double ditambahkan ke
    price_per_pax base (yg = quad).

    Return:
      total_group_cost, cost_per_pax, margin_amount, price_per_pax,
      price_quad/triple/double, extra_triple/double, item_count,
      + Phase 6b: buckets = {hpp, prorate_tl, fee_agen, fee_referal, margin}
        masing2 dgn {group, per_pax}, plus margin.from_items (bool: True kalau
        dari item bucket=margin, False kalau derived dari target_margin_pct).
    """
    items = db.query_all(
        "SELECT bucket, category, unit, subtotal FROM package_boq_items WHERE boq_id = ?",
        (boq_id,),
    ) or []
    pax = max(int(target_pax or 1), 1)

    # Per-bucket group cost. Unit multiplier tetap sama semantik.
    by_bucket = {b: 0 for b in _BUCKETS}
    for it in items:
        bkt = it["bucket"] if it["bucket"] in _BUCKETS else "hpp"
        u = it["unit"]
        sub = it["subtotal"] or 0
        if u in ("per_pax", "per_pax_per_day"):
            by_bucket[bkt] += sub * pax
        else:  # per_group, per_room_per_night
            by_bucket[bkt] += sub

    # Per-pax per bucket (integer floor -- konsisten dgn semantik lama).
    hpp_pp = by_bucket["hpp"] // pax
    prorate_tl_pp = by_bucket["prorate_tl"] // pax
    fee_agen_pp = by_bucket["fee_agen"] // pax
    fee_referal_pp = by_bucket["fee_referal"] // pax
    margin_pp_from_items = by_bucket["margin"] // pax

    # Backward-compat: kalau tidak ada item bucket=margin, honor target_margin_pct
    # (dihitung dari HPP saja, bukan total, konsisten dgn legacy behavior).
    margin_from_items = margin_pp_from_items > 0
    if not margin_from_items and (margin_pct or 0) > 0:
        margin_pp = int(hpp_pp * (float(margin_pct) / 100.0))
    else:
        margin_pp = margin_pp_from_items

    # cost_per_pax: HANYA biaya nyata (hpp + prorate_tl), bukan revenue side.
    # Legacy BOQ tanpa prorate/fee -> cost_per_pax = hpp_pp = OLD cost_per_pax.
    cost_per_pax = hpp_pp + prorate_tl_pp

    price_per_pax = (
        hpp_pp + prorate_tl_pp + fee_agen_pp + fee_referal_pp + margin_pp
    )

    # total_group_cost: sum raw items yg sudah include unit multiplier.
    # Backward-compat: OLD semantics = sum of raw subtotals, TIDAK termasuk
    # margin implicit dari target_margin_pct. Tetap dipertahankan.
    total_group = sum(by_bucket.values())

    # Room split (Phase 4a): base price/pax = QUAD.
    if extra_triple is None or extra_double is None:
        row = db.query_one("SELECT extra_triple, extra_double FROM package_boq WHERE id = ?", (boq_id,))
        if row:
            if extra_triple is None:
                extra_triple = row["extra_triple"] or 0
            if extra_double is None:
                extra_double = row["extra_double"] or 0
    et = int(extra_triple or 0)
    ed = int(extra_double or 0)
    price_quad = price_per_pax
    price_triple = price_per_pax + et
    price_double = price_per_pax + ed

    return {
        "total_group_cost": total_group,
        "cost_per_pax": cost_per_pax,
        "margin_amount": margin_pp,
        "price_per_pax": price_per_pax,  # alias price_quad (backward compat)
        "price_quad": price_quad,
        "price_triple": price_triple,
        "price_double": price_double,
        "extra_triple": et,
        "extra_double": ed,
        "item_count": len(items),
        # Phase 6b: bucket breakdown.
        "buckets": {
            "hpp": {"group": by_bucket["hpp"], "per_pax": hpp_pp},
            "prorate_tl": {"group": by_bucket["prorate_tl"], "per_pax": prorate_tl_pp},
            "fee_agen": {"group": by_bucket["fee_agen"], "per_pax": fee_agen_pp},
            "fee_referal": {"group": by_bucket["fee_referal"], "per_pax": fee_referal_pp},
            "margin": {
                "group": margin_pp * pax,
                "per_pax": margin_pp,
                "from_items": margin_from_items,
            },
        },
    }


def _validate_body_create(body: dict) -> None:
    if not (body.get("name") or "").strip():
        raise HTTPException(status_code=400, detail="Nama BOQ wajib diisi.")
    pkg_id = body.get("package_id")
    if pkg_id is not None:
        if not db.query_one("SELECT id FROM packages WHERE id = ?", (pkg_id,)):
            raise HTTPException(status_code=400, detail="Paket tidak ditemukan.")


def _validate_item(body: dict) -> None:
    if not (body.get("item_name") or "").strip():
        raise HTTPException(status_code=400, detail="Nama item wajib diisi.")
    if not (body.get("category") or "").strip():
        raise HTTPException(status_code=400, detail="Kategori wajib diisi.")
    unit = body.get("unit") or "per_pax"
    if unit not in _UNIT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unit tidak valid. Pilihan: {', '.join(sorted(_UNIT_TYPES))}",
        )
    # Phase 6b: bucket validation. Caller boleh omit -> default 'hpp'.
    bucket = body.get("bucket") or "hpp"
    if bucket not in _BUCKETS:
        raise HTTPException(
            status_code=400,
            detail=f"Bucket tidak valid. Pilihan: {', '.join(sorted(_BUCKETS))}",
        )


def _validate_boq_type(t: str | None) -> str:
    """Phase 6b: normalize + validate boq_type. None/'' -> default umar_reguler."""
    v = (t or "").strip() or "umar_reguler"
    if v not in _BOQ_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"boq_type tidak valid. Pilihan: {', '.join(sorted(_BOQ_TYPES))}",
        )
    return v


# ---------------------------------------------------------------------------
# list & detail (semua role authenticated)
# ---------------------------------------------------------------------------
@router.get("/api/boq")
async def boq_list(
    package_id: int | None = None,
    status: str | None = None,
    user=Depends(authenticate_token),
):
    where, params = [], []
    if package_id is not None:
        where.append("b.package_id = ?"); params.append(package_id)
    if status:
        where.append("b.status = ?"); params.append(status)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.query_all(
        f"SELECT b.*, "
        f"       p.name AS package_name, "
        f"       uc.name AS created_by_name, "
        f"       ur.name AS reviewed_by_name, "
        f"       (SELECT COUNT(*) FROM package_boq_items WHERE boq_id = b.id) AS item_count "
        f"FROM package_boq b "
        f"LEFT JOIN packages p ON p.id = b.package_id "
        f"LEFT JOIN users uc ON uc.id = b.created_by "
        f"LEFT JOIN users ur ON ur.id = b.reviewed_by "
        f"{where_sql} "
        f"ORDER BY "
        f"  CASE b.status WHEN 'Pending Approval' THEN 0 "
        f"                WHEN 'Draft' THEN 1 "
        f"                WHEN 'Approved' THEN 2 ELSE 3 END, "
        f"  b.updated_at DESC",
        tuple(params),
    ) or []
    return rows


@router.get("/api/boq/compare")
async def boq_compare(ids: str = "", user=Depends(authenticate_token)):
    """Bandingkan 2-5 BOQ side-by-side.

    Query: `ids=1,2,3` -- integer comma-separated. Minimal 2, maksimal 5.
    Boleh cross-package (misal utk banding vendor across routes), meski
    biasa-nya user pilih BOQ dari paket yang sama.

    Response: list BOQ dgn shape sama seperti detail (header + items + totals),
    urutan sesuai input. Frontend yang render kolom sejajar + highlight beda.

    NOTE: didaftarkan SEBELUM /api/boq/{bid} biar 'compare' tidak dianggap
    integer bid.
    """
    raw = [x.strip() for x in (ids or "").split(",") if x.strip()]
    if not raw:
        raise HTTPException(status_code=400, detail="Parameter ids wajib (comma-separated).")
    try:
        parsed = [int(x) for x in raw]
    except ValueError:
        raise HTTPException(status_code=400, detail="ids harus integer.")
    if len(parsed) < 2:
        raise HTTPException(status_code=400, detail="Minimal 2 BOQ untuk dibandingkan.")
    if len(parsed) > 5:
        raise HTTPException(status_code=400, detail="Maksimal 5 BOQ per compare.")

    # Dedup preserving order.
    seen: set[int] = set()
    ordered = [i for i in parsed if not (i in seen or seen.add(i))]

    boqs: list[dict] = []
    for bid in ordered:
        boq = db.query_one("SELECT * FROM package_boq WHERE id = ?", (bid,))
        if not boq:
            raise HTTPException(status_code=404, detail=f"BOQ #{bid} tidak ditemukan.")
        items = db.query_all(
            "SELECT * FROM package_boq_items WHERE boq_id = ? "
            "ORDER BY category, sort_order, id",
            (bid,),
        ) or []
        totals = _compute_totals(bid, boq["target_pax"], boq["target_margin_pct"])
        pkg = db.query_one("SELECT name FROM packages WHERE id = ?", (boq["package_id"],)) if boq["package_id"] else None
        creator = db.query_one("SELECT name FROM users WHERE id = ?", (boq["created_by"],)) if boq["created_by"] else None
        boqs.append({
            **dict(boq),
            "items": items,
            "totals": totals,
            "package_name": pkg["name"] if pkg else None,
            "created_by_name": creator["name"] if creator else None,
        })

    same_package = len({b["package_id"] for b in boqs}) == 1 and boqs[0]["package_id"] is not None
    return {"boqs": boqs, "count": len(boqs), "same_package": same_package}


# ---------------------------------------------------------------------------
# Phase 3b: BOQ Templates (preset line items).
# Route WAJIB didaftarkan SEBELUM /api/boq/{bid} biar 'templates' tidak
# di-parse jadi bid=templates.
# ---------------------------------------------------------------------------
def _template_or_404(tid: int) -> dict:
    row = db.query_one("SELECT * FROM boq_templates WHERE id = ?", (tid,))
    if not row:
        raise HTTPException(status_code=404, detail="Template BOQ tidak ditemukan.")
    return row


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
            " vendor_name, note, sort_order, bucket) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tid, it.get("category"), it.get("item_name").strip(),
                it.get("unit") or "per_pax",
                float(it.get("quantity") or 1),
                int(it.get("unit_price") or 0),
                it.get("vendor_name"), it.get("note"),
                int(it.get("sort_order") or 0),
                it.get("bucket") or "hpp",
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
async def boq_template_apply(tid: int, bid: int, user=Depends(authenticate_token)):
    """Copy semua template items ke BOQ (append, tidak replace).

    Guard:
    - BOQ target harus editable oleh caller (mgmt/admin apa saja; owner cuma Draft).
    - Template harus punya minimal 1 item (kosong = no-op yang confusing).

    Items ditambahkan ke akhir (sort_order melanjutkan max existing) supaya
    tidak menimpa item yg sudah ada di BOQ.
    """
    boq = _boq_or_404(bid)
    _assert_editable(boq, user)
    t = _template_or_404(tid)

    items = db.query_all(
        "SELECT * FROM boq_template_items WHERE template_id = ? ORDER BY sort_order, id",
        (tid,),
    ) or []
    if not items:
        raise HTTPException(status_code=400, detail="Template kosong -- tambah item template dulu.")

    # Sort_order offset agar item template lanjut setelah item existing.
    max_sort = db.query_one(
        "SELECT COALESCE(MAX(sort_order), -1) AS m FROM package_boq_items WHERE boq_id = ?",
        (bid,),
    )
    offset = (max_sort["m"] if max_sort else -1) + 1

    for i, it in enumerate(items):
        qty = float(it["quantity"] or 1)
        up = int(it["unit_price"] or 0)
        db.execute(
            "INSERT INTO package_boq_items "
            "(boq_id, category, item_name, unit, quantity, unit_price, subtotal, "
            " vendor_name, note, sort_order, bucket) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bid, it["category"], it["item_name"], it["unit"], qty, up,
                _calc_subtotal(qty, up),
                it["vendor_name"], it["note"], offset + i,
                it["bucket"] or "hpp",
            ),
        )

    db.execute("UPDATE package_boq SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (bid,))
    log_action(user, "BOQ_TEMPLATE_APPLY", f"tmpl={tid} boq={bid} items={len(items)}")
    notify("data_updated", "boq")
    return {"applied": len(items), "message": f"{len(items)} item dari template ditambahkan ke BOQ."}


@router.get("/api/boq/{bid}")
async def boq_detail(bid: int, user=Depends(authenticate_token)):
    boq = _boq_or_404(bid)
    items = db.query_all(
        "SELECT * FROM package_boq_items WHERE boq_id = ? ORDER BY sort_order, id",
        (bid,),
    ) or []
    totals = _compute_totals(bid, boq["target_pax"], boq["target_margin_pct"])
    creator = db.query_one("SELECT name FROM users WHERE id = ?", (boq["created_by"],)) if boq["created_by"] else None
    reviewer = db.query_one("SELECT name FROM users WHERE id = ?", (boq["reviewed_by"],)) if boq["reviewed_by"] else None
    return {
        **dict(boq),
        "items": items,
        "totals": totals,
        "created_by_name": creator["name"] if creator else None,
        "reviewed_by_name": reviewer["name"] if reviewer else None,
    }


# ---------------------------------------------------------------------------
# create / update / delete header
# ---------------------------------------------------------------------------
@router.post("/api/boq")
async def boq_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, *_AUTHOR_ROLES)
    _validate_body_create(body)

    # RBAC status: mgmt/admin bikin -> langsung Approved (skip pending workflow).
    # Sales/ops -> selalu Draft (harus submit dulu utk approval).
    status = "Approved" if user.get("role") in _MGMT_ROLES else "Draft"
    reviewed_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") if status == "Approved" else None
    reviewed_by = user["id"] if status == "Approved" else None

    room_split = body.get("room_split")
    if room_split is not None and not isinstance(room_split, str):
        room_split = json.dumps(room_split)  # dict/list -> JSON text

    # Phase 6b: boq_type. Terima kalau ada di body, else default umar_reguler.
    boq_type = _validate_boq_type(body.get("boq_type"))

    bid, _ = db.execute(
        "INSERT INTO package_boq "
        "(package_id, name, status, target_pax, room_split, target_margin_pct, "
        " extra_triple, extra_double, notes, boq_type, created_by, "
        " reviewed_by, reviewed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            body.get("package_id"),
            body.get("name").strip(),
            status,
            int(body.get("target_pax") or 45),
            room_split,
            float(body.get("target_margin_pct") or 15),
            int(body.get("extra_triple") or 0),
            int(body.get("extra_double") or 0),
            body.get("notes"),
            boq_type,
            user["id"],
            reviewed_by,
            reviewed_at,
        ),
    )

    # Optional: line items nested di body create (biar 1 request selesai).
    for it in body.get("items") or []:
        _validate_item(it)
        qty = float(it.get("quantity") or 1)
        up = int(it.get("unit_price") or 0)
        db.execute(
            "INSERT INTO package_boq_items "
            "(boq_id, category, item_name, unit, quantity, unit_price, subtotal, "
            " vendor_name, note, sort_order, bucket) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bid,
                it.get("category"),
                it.get("item_name").strip(),
                it.get("unit") or "per_pax",
                qty, up, _calc_subtotal(qty, up),
                it.get("vendor_name"),
                it.get("note"),
                int(it.get("sort_order") or 0),
                it.get("bucket") or "hpp",
            ),
        )

    log_action(user, "BOQ_CREATE", f"id={bid} status={status} pkg={body.get('package_id')}")
    notify("data_updated", "boq")
    return {"id": bid, "status": status, "message": "BOQ dibuat."}


@router.put("/api/boq/{bid}")
async def boq_update(bid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    boq = _boq_or_404(bid)
    _assert_editable(boq, user)

    name = (body.get("name") or boq["name"]).strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nama BOQ wajib diisi.")

    pkg_id = body.get("package_id", boq["package_id"])
    if pkg_id is not None and pkg_id != boq["package_id"]:
        if not db.query_one("SELECT id FROM packages WHERE id = ?", (pkg_id,)):
            raise HTTPException(status_code=400, detail="Paket tidak ditemukan.")

    room_split = body.get("room_split", boq["room_split"])
    if room_split is not None and not isinstance(room_split, str):
        room_split = json.dumps(room_split)

    # Phase 6b: boq_type. Kalau body tidak kirim field -> pertahankan value existing.
    if "boq_type" in body:
        boq_type = _validate_boq_type(body.get("boq_type"))
    else:
        boq_type = boq["boq_type"] or "umar_reguler"

    db.execute(
        "UPDATE package_boq SET "
        "  package_id = ?, name = ?, target_pax = ?, room_split = ?, "
        "  target_margin_pct = ?, extra_triple = ?, extra_double = ?, notes = ?, "
        "  boq_type = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE id = ?",
        (
            pkg_id,
            name,
            int(body.get("target_pax", boq["target_pax"]) or 45),
            room_split,
            float(body.get("target_margin_pct", boq["target_margin_pct"]) or 15),
            int(body.get("extra_triple", boq["extra_triple"]) or 0),
            int(body.get("extra_double", boq["extra_double"]) or 0),
            body.get("notes", boq["notes"]),
            boq_type,
            bid,
        ),
    )
    log_action(user, "BOQ_UPDATE", f"id={bid}")
    notify("data_updated", "boq")
    return {"message": "BOQ diperbarui."}


@router.delete("/api/boq/{bid}")
async def boq_delete(bid: int, user=Depends(authenticate_token)):
    boq = _boq_or_404(bid)
    # Delete rules: mgmt/admin bisa hapus apa saja; owner bisa hapus Draft-nya.
    role = user.get("role")
    if role not in _MGMT_ROLES:
        if boq["created_by"] != user["id"]:
            raise HTTPException(status_code=403, detail="Bukan BOQ Anda.")
        if boq["status"] != "Draft":
            raise HTTPException(status_code=400, detail="Hanya BOQ Draft yang bisa dihapus.")
    db.execute("DELETE FROM package_boq WHERE id = ?", (bid,))
    log_action(user, "BOQ_DELETE", f"id={bid} status={boq['status']}")
    notify("data_updated", "boq")
    return {"message": "BOQ dihapus."}


# ---------------------------------------------------------------------------
# line items
# ---------------------------------------------------------------------------
@router.post("/api/boq/{bid}/items")
async def boq_item_add(bid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    boq = _boq_or_404(bid)
    _assert_editable(boq, user)
    _validate_item(body)
    qty = float(body.get("quantity") or 1)
    up = int(body.get("unit_price") or 0)
    item_id, _ = db.execute(
        "INSERT INTO package_boq_items "
        "(boq_id, category, item_name, unit, quantity, unit_price, subtotal, "
        " vendor_name, note, sort_order, bucket) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            bid,
            body.get("category"),
            body.get("item_name").strip(),
            body.get("unit") or "per_pax",
            qty, up, _calc_subtotal(qty, up),
            body.get("vendor_name"),
            body.get("note"),
            int(body.get("sort_order") or 0),
            body.get("bucket") or "hpp",
        ),
    )
    db.execute("UPDATE package_boq SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (bid,))
    log_action(user, "BOQ_ITEM_ADD", f"boq={bid} item={item_id} bucket={body.get('bucket') or 'hpp'}")
    notify("data_updated", "boq")
    return {"id": item_id}


@router.put("/api/boq/{bid}/items/{iid}")
async def boq_item_update(bid: int, iid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    boq = _boq_or_404(bid)
    _assert_editable(boq, user)
    it = db.query_one("SELECT * FROM package_boq_items WHERE id = ? AND boq_id = ?", (iid, bid))
    if not it:
        raise HTTPException(status_code=404, detail="Item tidak ditemukan.")
    unit = body.get("unit", it["unit"])
    if unit not in _UNIT_TYPES:
        raise HTTPException(status_code=400, detail="Unit tidak valid.")
    # Phase 6b: bucket bisa di-update. Body tidak kirim -> tetap value existing.
    bucket = body.get("bucket", it["bucket"] or "hpp")
    if bucket not in _BUCKETS:
        raise HTTPException(
            status_code=400,
            detail=f"Bucket tidak valid. Pilihan: {', '.join(sorted(_BUCKETS))}",
        )
    qty = float(body.get("quantity", it["quantity"]) or 0)
    up = int(body.get("unit_price", it["unit_price"]) or 0)
    db.execute(
        "UPDATE package_boq_items SET "
        "  category = ?, item_name = ?, unit = ?, quantity = ?, unit_price = ?, "
        "  subtotal = ?, vendor_name = ?, note = ?, sort_order = ?, bucket = ? "
        "WHERE id = ?",
        (
            body.get("category", it["category"]),
            (body.get("item_name") or it["item_name"]).strip(),
            unit, qty, up, _calc_subtotal(qty, up),
            body.get("vendor_name", it["vendor_name"]),
            body.get("note", it["note"]),
            int(body.get("sort_order", it["sort_order"]) or 0),
            bucket,
            iid,
        ),
    )
    db.execute("UPDATE package_boq SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (bid,))
    log_action(user, "BOQ_ITEM_UPDATE", f"boq={bid} item={iid}")
    notify("data_updated", "boq")
    return {"message": "Item diperbarui."}


@router.delete("/api/boq/{bid}/items/{iid}")
async def boq_item_delete(bid: int, iid: int, user=Depends(authenticate_token)):
    boq = _boq_or_404(bid)
    _assert_editable(boq, user)
    it = db.query_one("SELECT id FROM package_boq_items WHERE id = ? AND boq_id = ?", (iid, bid))
    if not it:
        raise HTTPException(status_code=404, detail="Item tidak ditemukan.")
    db.execute("DELETE FROM package_boq_items WHERE id = ?", (iid,))
    db.execute("UPDATE package_boq SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (bid,))
    log_action(user, "BOQ_ITEM_DELETE", f"boq={bid} item={iid}")
    notify("data_updated", "boq")
    return {"message": "Item dihapus."}


# ---------------------------------------------------------------------------
# workflow: submit / approve / reject
# ---------------------------------------------------------------------------
@router.post("/api/boq/{bid}/submit")
async def boq_submit(bid: int, user=Depends(authenticate_token)):
    boq = _boq_or_404(bid)
    if user.get("role") not in _MGMT_ROLES and boq["created_by"] != user["id"]:
        raise HTTPException(status_code=403, detail="Bukan BOQ Anda.")
    if boq["status"] != "Draft":
        raise HTTPException(status_code=400, detail=f"BOQ status {boq['status']}, tidak bisa di-submit.")
    n = db.query_one("SELECT COUNT(*) AS n FROM package_boq_items WHERE boq_id = ?", (bid,))
    if not n or n["n"] == 0:
        raise HTTPException(status_code=400, detail="BOQ tidak boleh kosong. Tambah minimal 1 item.")
    db.execute(
        "UPDATE package_boq SET status = 'Pending Approval', "
        "  submitted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
        "WHERE id = ?",
        (bid,),
    )
    log_action(user, "BOQ_SUBMIT", f"id={bid}")
    notify("data_updated", "boq")
    return {"message": "BOQ dikirim untuk approval."}


@router.post("/api/boq/{bid}/approve")
async def boq_approve(bid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, *_MGMT_ROLES)
    boq = _boq_or_404(bid)
    if boq["status"] not in ("Pending Approval", "Draft"):
        raise HTTPException(status_code=400, detail=f"BOQ status {boq['status']}, tidak bisa di-approve.")
    db.execute(
        "UPDATE package_boq SET status = 'Approved', "
        "  reviewed_by = ?, reviewed_at = CURRENT_TIMESTAMP, "
        "  review_note = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE id = ?",
        (user["id"], body.get("review_note"), bid),
    )
    log_action(user, "BOQ_APPROVE", f"id={bid}")
    notify("data_updated", "boq")
    return {"message": "BOQ disetujui."}


@router.post("/api/boq/{bid}/reject")
async def boq_reject(bid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, *_MGMT_ROLES)
    boq = _boq_or_404(bid)
    if boq["status"] != "Pending Approval":
        raise HTTPException(status_code=400, detail="Hanya BOQ Pending Approval yang bisa di-reject.")
    note = (body.get("review_note") or "").strip()
    if not note:
        raise HTTPException(status_code=400, detail="Alasan reject wajib diisi.")
    db.execute(
        "UPDATE package_boq SET status = 'Rejected', "
        "  reviewed_by = ?, reviewed_at = CURRENT_TIMESTAMP, "
        "  review_note = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE id = ?",
        (user["id"], note, bid),
    )
    log_action(user, "BOQ_REJECT", f"id={bid}")
    notify("data_updated", "boq")
    return {"message": "BOQ ditolak."}


# ---------------------------------------------------------------------------
# convert BOQ Approved -> row packages baru (Phase 2)
# ---------------------------------------------------------------------------
@router.post("/api/boq/{bid}/convert-to-package")
async def boq_convert_to_package(bid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    """Konversi BOQ Approved (yang belum terikat paket) jadi row `packages` baru.

    Guardrail:
    - Mgmt/Admin only -- bikin row Master Paket = keputusan penting.
    - Status HARUS Approved (Draft/Pending/Rejected ditolak).
    - Kalau BOQ sudah terikat package_id != NULL -> ditolak (skenario multi-BOQ
      per paket dilakukan lewat 'Duplicate' + link manual, bukan convert).
    - `departure_date` + `duration` wajib di body -- BOQ tidak simpan info itu.

    Setelah insert paket, BOQ.package_id di-link ke paket baru + notes ditambah
    catatan trace 'Converted from BOQ #N'. Harga per tipe kamar semua di-set
    sama dgn price_per_pax hasil kalkulasi BOQ (backend). Editor Master Paket
    boleh differentiate quad/triple/double manual setelahnya.
    """
    require_role(user, *_MGMT_ROLES)
    boq = _boq_or_404(bid)
    if boq["status"] != "Approved":
        raise HTTPException(status_code=400, detail="Hanya BOQ Approved yang bisa di-convert.")
    if boq["package_id"] is not None:
        raise HTTPException(status_code=400, detail="BOQ sudah terikat ke paket. Duplicate BOQ dulu untuk skenario baru.")

    dep = (body.get("departure_date") or "").strip()
    if not dep:
        raise HTTPException(status_code=400, detail="departure_date wajib diisi.")
    try:
        duration = int(body.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    if duration <= 0:
        raise HTTPException(status_code=400, detail="duration (hari) wajib > 0.")

    # Sanity: BOQ minimal punya 1 item -- convert BOQ kosong = paket tanpa harga.
    n = db.query_one("SELECT COUNT(*) AS n FROM package_boq_items WHERE boq_id = ?", (bid,))
    if not n or n["n"] == 0:
        raise HTTPException(status_code=400, detail="BOQ kosong tidak bisa di-convert. Tambah item dulu.")

    totals = _compute_totals(bid, boq["target_pax"], boq["target_margin_pct"])
    # Phase 4a: 3 harga per room type dari BOQ (bukan lagi flat).
    price_quad = int(totals.get("price_quad") or 0)
    price_triple = int(totals.get("price_triple") or 0)
    price_double = int(totals.get("price_double") or 0)
    price = price_quad  # header packages.price = base (dipakai display list)
    quota = int(body.get("quota") or boq["target_pax"] or 45)

    # Nama paket: pakai override dari body kalau ada, else pakai nama BOQ.
    pkg_name = (body.get("name") or boq["name"] or "").strip()
    if not pkg_name:
        raise HTTPException(status_code=400, detail="Nama paket kosong.")

    # Insert dgn field hotel/airline dari body (kalau user isi), atau NULL.
    pid, _ = db.execute(
        "INSERT INTO packages (name, price, departure_date, duration, quota, "
        " price_quad, price_triple, price_double, default_commission_fee, "
        " hotel_mekkah, hotel_madinah, route_type, return_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pkg_name, price, dep, duration, quota,
            price_quad, price_triple, price_double,
            int(body.get("default_commission_fee") or 0),
            body.get("hotel_mekkah"), body.get("hotel_madinah"),
            body.get("route_type") or "Direct",
            body.get("return_date"),
        ),
    )

    # Link BOQ ke paket baru + tambah audit note.
    trace = f"[Converted to Package #{pid} on {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} by {user['name']}]"
    combined_notes = (boq["notes"] or "").rstrip()
    combined_notes = (combined_notes + "\n\n" + trace).strip() if combined_notes else trace
    db.execute(
        "UPDATE package_boq SET package_id = ?, notes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (pid, combined_notes, bid),
    )

    log_action(user, "BOQ_CONVERT", f"boq={bid} -> package={pid} price/pax={price}")
    notify("data_updated", "boq")
    notify("data_updated", "package")
    return {
        "package_id": pid,
        "price_per_pax": price,
        "message": f"BOQ berhasil di-convert jadi Paket #{pid}.",
    }


# ---------------------------------------------------------------------------
# duplicate (utility -- clone jadi Draft baru)
# ---------------------------------------------------------------------------
@router.post("/api/boq/{bid}/duplicate")
async def boq_duplicate(bid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, *_AUTHOR_ROLES)
    src = _boq_or_404(bid)
    new_name = (body.get("name") or f"{src['name']} (copy)").strip()
    # Mgmt yg duplicate: hasil TETAP Draft (bukan auto-Approved) supaya ada
    # moment review dulu sebelum lock. Auto-approve hanya berlaku utk create scratch.
    new_bid, _ = db.execute(
        "INSERT INTO package_boq "
        "(package_id, name, status, target_pax, room_split, target_margin_pct, "
        " extra_triple, extra_double, notes, boq_type, created_by) "
        "VALUES (?, ?, 'Draft', ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            src["package_id"], new_name, src["target_pax"], src["room_split"],
            src["target_margin_pct"],
            src["extra_triple"] or 0, src["extra_double"] or 0,
            src["notes"], src["boq_type"] or "umar_reguler", user["id"],
        ),
    )
    src_items = db.query_all(
        "SELECT * FROM package_boq_items WHERE boq_id = ? ORDER BY sort_order, id",
        (bid,),
    ) or []
    for it in src_items:
        db.execute(
            "INSERT INTO package_boq_items "
            "(boq_id, category, item_name, unit, quantity, unit_price, subtotal, "
            " vendor_name, note, sort_order, bucket) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_bid, it["category"], it["item_name"], it["unit"],
                it["quantity"], it["unit_price"], it["subtotal"],
                it["vendor_name"], it["note"], it["sort_order"],
                it["bucket"] or "hpp",
            ),
        )
    log_action(user, "BOQ_DUPLICATE", f"src={bid} new={new_bid}")
    notify("data_updated", "boq")
    return {"id": new_bid, "message": "BOQ berhasil di-duplicate ke Draft baru."}
