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


def _compute_totals(boq_id: int, target_pax: int | None, margin_pct: float | None) -> dict:
    """Hitung total group cost + harga per pax dari items.

    Aturan unit:
      per_pax          -> subtotal utk 1 pax; total_group = subtotal * target_pax
      per_group        -> subtotal = total group (1x, mis. sewa bus)
      per_room_per_night -> total group (user isi quantity = rooms * nights)
      per_pax_per_day  -> subtotal * target_pax (asumsi quantity = hari)

    Untuk MVP sederhana: subtotal disimpan apa adanya di items, total_group =
    SUM(subtotal). User boleh interpretasi sendiri (misal: item konsumsi
    per_pax_per_day, user isi quantity = 9 hari, unit_price = 50rb -> subtotal
    450rb utk 1 pax; total_group = 450rb * target_pax).
    """
    items = db.query_all(
        "SELECT category, unit, subtotal FROM package_boq_items WHERE boq_id = ?",
        (boq_id,),
    ) or []
    pax = max(int(target_pax or 1), 1)
    total_group = 0
    for it in items:
        u = it["unit"]
        sub = it["subtotal"] or 0
        if u in ("per_pax", "per_pax_per_day"):
            total_group += sub * pax
        else:
            total_group += sub  # per_group, per_room_per_night
    cost_per_pax = total_group // pax if pax else 0
    margin_amt = int(cost_per_pax * (float(margin_pct or 0) / 100.0))
    price_per_pax = cost_per_pax + margin_amt
    return {
        "total_group_cost": total_group,
        "cost_per_pax": cost_per_pax,
        "margin_amount": margin_amt,
        "price_per_pax": price_per_pax,
        "item_count": len(items),
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

    bid, _ = db.execute(
        "INSERT INTO package_boq "
        "(package_id, name, status, target_pax, room_split, target_margin_pct, "
        " notes, created_by, reviewed_by, reviewed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            body.get("package_id"),
            body.get("name").strip(),
            status,
            int(body.get("target_pax") or 45),
            room_split,
            float(body.get("target_margin_pct") or 15),
            body.get("notes"),
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
            " vendor_name, note, sort_order) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bid,
                it.get("category"),
                it.get("item_name").strip(),
                it.get("unit") or "per_pax",
                qty, up, _calc_subtotal(qty, up),
                it.get("vendor_name"),
                it.get("note"),
                int(it.get("sort_order") or 0),
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

    db.execute(
        "UPDATE package_boq SET "
        "  package_id = ?, name = ?, target_pax = ?, room_split = ?, "
        "  target_margin_pct = ?, notes = ?, "
        "  updated_at = CURRENT_TIMESTAMP "
        "WHERE id = ?",
        (
            pkg_id,
            name,
            int(body.get("target_pax", boq["target_pax"]) or 45),
            room_split,
            float(body.get("target_margin_pct", boq["target_margin_pct"]) or 15),
            body.get("notes", boq["notes"]),
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
        " vendor_name, note, sort_order) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            bid,
            body.get("category"),
            body.get("item_name").strip(),
            body.get("unit") or "per_pax",
            qty, up, _calc_subtotal(qty, up),
            body.get("vendor_name"),
            body.get("note"),
            int(body.get("sort_order") or 0),
        ),
    )
    db.execute("UPDATE package_boq SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (bid,))
    log_action(user, "BOQ_ITEM_ADD", f"boq={bid} item={item_id}")
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
    qty = float(body.get("quantity", it["quantity"]) or 0)
    up = int(body.get("unit_price", it["unit_price"]) or 0)
    db.execute(
        "UPDATE package_boq_items SET "
        "  category = ?, item_name = ?, unit = ?, quantity = ?, unit_price = ?, "
        "  subtotal = ?, vendor_name = ?, note = ?, sort_order = ? "
        "WHERE id = ?",
        (
            body.get("category", it["category"]),
            (body.get("item_name") or it["item_name"]).strip(),
            unit, qty, up, _calc_subtotal(qty, up),
            body.get("vendor_name", it["vendor_name"]),
            body.get("note", it["note"]),
            int(body.get("sort_order", it["sort_order"]) or 0),
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
    price = int(totals.get("price_per_pax") or 0)
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
            price, price, price,   # quad/triple/double sementara sama; editor boleh differentiate
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
        " notes, created_by) "
        "VALUES (?, ?, 'Draft', ?, ?, ?, ?, ?)",
        (
            src["package_id"], new_name, src["target_pax"], src["room_split"],
            src["target_margin_pct"], src["notes"], user["id"],
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
            " vendor_name, note, sort_order) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_bid, it["category"], it["item_name"], it["unit"],
                it["quantity"], it["unit_price"], it["subtotal"],
                it["vendor_name"], it["note"], it["sort_order"],
            ),
        )
    log_action(user, "BOQ_DUPLICATE", f"src={bid} new={new_bid}")
    notify("data_updated", "boq")
    return {"id": new_bid, "message": "BOQ berhasil di-duplicate ke Draft baru."}
