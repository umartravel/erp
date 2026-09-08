"""
Router Operasional -- Vendor Bookings per Paket.
- GET    /api/packages/{pid}/vendors  list vendor booking paket
- POST   /api/packages/{pid}/vendors  create booking (Booked default)
- PATCH  /api/vendors/{vid}           update status/amount/dsb
- DELETE /api/vendors/{vid}           admin/mgmt only

VENDOR_TYPES: hotel_mekkah/hotel_madinah/airline_depart/airline_return/
airline_transit/bus_local/muthawif/catering/other.
STATUS lifecycle: Booked -> Deposit -> Paid -> Confirmed (atau Cancelled).
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
from deps.notifications import notify_role  # Phase 8f-3

router = APIRouter(tags=["ops-vendors"])

VENDOR_STATUSES = ("Booked", "Deposit", "Paid", "Confirmed", "Cancelled")
VENDOR_TYPES = (
    "hotel_mekkah", "hotel_madinah", "airline_depart", "airline_return",
    "airline_transit", "bus_local", "muthawif", "catering", "other",
)


@router.get("/api/packages/{pid}/vendors")
async def vendors_list(pid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "ops", "management", "finance")
    if not db.query_one("SELECT id FROM packages WHERE id = ?", (pid,)):
        raise HTTPException(status_code=404, detail="Paket tidak ditemukan.")
    return db.query_all(
        "SELECT * FROM vendor_bookings WHERE package_id = ? "
        "ORDER BY CASE status "
        "  WHEN 'Cancelled' THEN 5 "
        "  WHEN 'Confirmed' THEN 4 "
        "  WHEN 'Paid' THEN 3 "
        "  WHEN 'Deposit' THEN 2 "
        "  WHEN 'Booked' THEN 1 ELSE 0 END ASC, "
        "vendor_type ASC, created_at DESC",
        (pid,),
    ) or []


@router.post("/api/packages/{pid}/vendors")
async def vendors_create(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops", "management")
    if not db.query_one("SELECT id FROM packages WHERE id = ?", (pid,)):
        raise HTTPException(status_code=404, detail="Paket tidak ditemukan.")
    vtype = body.get("vendor_type")
    if vtype not in VENDOR_TYPES:
        raise HTTPException(status_code=400, detail=f"vendor_type harus salah satu: {', '.join(VENDOR_TYPES)}")
    status = body.get("status") or "Booked"
    if status not in VENDOR_STATUSES:
        raise HTTPException(status_code=400, detail=f"status harus salah satu: {', '.join(VENDOR_STATUSES)}")
    db.execute(
        "INSERT INTO vendor_bookings (package_id, vendor_type, vendor_name, status, "
        "deposit_amount, total_amount, due_date, confirmation_code, notes, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pid, vtype, body.get("vendor_name"), status,
            int(body.get("deposit_amount") or 0),
            int(body.get("total_amount") or 0),
            body.get("due_date") or None,
            body.get("confirmation_code"),
            body.get("notes"),
            user["name"],
        ),
    )
    log_action(user, "VENDOR_CREATE", f"pkg={pid} type={vtype} status={status}")
    notify("data_updated", "vendor")
    return {"message": "Vendor booking tersimpan."}


@router.patch("/api/vendors/{vid}")
async def vendors_update(vid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops", "management", "finance")
    row = db.query_one("SELECT * FROM vendor_bookings WHERE id = ?", (vid,))
    if not row:
        raise HTTPException(status_code=404, detail="Vendor booking tidak ditemukan.")
    status = body.get("status") or row["status"]
    if status not in VENDOR_STATUSES:
        raise HTTPException(status_code=400, detail=f"status harus salah satu: {', '.join(VENDOR_STATUSES)}")
    db.execute(
        "UPDATE vendor_bookings SET vendor_name=?, status=?, deposit_amount=?, total_amount=?, "
        "due_date=?, confirmation_code=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (
            body.get("vendor_name") if "vendor_name" in body else row["vendor_name"],
            status,
            int(body.get("deposit_amount") if "deposit_amount" in body else (row["deposit_amount"] or 0)),
            int(body.get("total_amount") if "total_amount" in body else (row["total_amount"] or 0)),
            body.get("due_date") if "due_date" in body else row["due_date"],
            body.get("confirmation_code") if "confirmation_code" in body else row["confirmation_code"],
            body.get("notes") if "notes" in body else row["notes"],
            vid,
        ),
    )
    log_action(user, "VENDOR_UPDATE", f"id={vid} status={status}")
    notify("data_updated", "vendor")
    # Phase 8f-3: notif ke ops + management kalau vendor Cancelled (paling kritis).
    if status == "Cancelled" and row["status"] != "Cancelled":
        pkg = db.query_one("SELECT name FROM packages WHERE id = ?", (row["package_id"],))
        pkg_name = (pkg or {}).get("name") or f"Paket #{row['package_id']}"
        title = f"Vendor DIBATALKAN: {row['vendor_name'] or row['vendor_type']}"
        body_txt = f"Paket {pkg_name}. Segera cari pengganti / rebook."
        notify_role("management", "vendor_cancelled", title, body_txt, "#page-packages")
        notify_role("ops", "vendor_cancelled", title, body_txt, "#page-packages")
    return {"message": "Vendor booking diperbarui."}


@router.delete("/api/vendors/{vid}")
async def vendors_delete(vid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "management")
    if not db.query_one("SELECT id FROM vendor_bookings WHERE id = ?", (vid,)):
        raise HTTPException(status_code=404, detail="Vendor booking tidak ditemukan.")
    db.execute("DELETE FROM vendor_bookings WHERE id = ?", (vid,))
    log_action(user, "VENDOR_DELETE", f"id={vid}")
    notify("data_updated", "vendor")
    return {"message": "Vendor booking dihapus."}
