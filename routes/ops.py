"""
Router Operasional (O-A .. O-x): landing dashboard ops, insiden, feedback jamaah,
debrief post-trip, vendor bookings per paket, checklist pra-keberangkatan.

Semua endpoint di sini dulunya duduk di app.py. Pemindahan HANYA memindahkan
lokasi kode -- kontrak API + role gating + payload response identik.
"""
import datetime

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

router = APIRouter(tags=["ops"])


# ===========================================================================
# INSIDEN
# ===========================================================================
@router.get("/api/incidents")
async def incidents_list(
    status: str | None = None,
    severity: str | None = None,
    package: str | None = None,
    user=Depends(authenticate_token),
):
    where = []
    params = []
    if status:
        where.append("i.status = ?"); params.append(status)
    if severity:
        where.append("i.severity = ?"); params.append(severity)
    if package:
        where.append("i.package_name = ?"); params.append(package)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    return db.query_all(
        f"SELECT i.*, j.name AS jamaah_name FROM incidents i "
        f"LEFT JOIN jamaah j ON j.id = i.jamaah_id "
        f"{where_sql} ORDER BY "
        "  CASE i.status WHEN 'Open' THEN 0 WHEN 'InProgress' THEN 1 ELSE 2 END, "
        "  CASE i.severity WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END, "
        "  i.created_at DESC",
        tuple(params),
    ) or []


@router.post("/api/incidents")
async def incidents_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    db.execute(
        "INSERT INTO incidents (package_name, reported_by, incident_text, severity, jamaah_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            body.get("package_name") or "Umum",
            user["name"],
            body.get("incident_text"),
            body.get("severity") or "Medium",
            body.get("jamaah_id") or None,
        ),
    )
    log_action(user, "INCIDENT_CREATE", f"pkg={body.get('package_name')} sev={body.get('severity')}")
    notify("data_updated", "incident")
    return {"message": "Laporan insiden berhasil dikirim ke Pusat."}


@router.patch("/api/incidents/{iid}")
async def incidents_update(iid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops", "management")
    row = db.query_one("SELECT * FROM incidents WHERE id = ?", (iid,))
    if not row:
        raise HTTPException(status_code=404, detail="Insiden tidak ditemukan.")
    status = body.get("status") or row["status"]
    severity = body.get("severity") or row["severity"] or "Medium"
    assigned_to = body.get("assigned_to") if "assigned_to" in body else row["assigned_to"]
    resolution_note = body.get("resolution_note") if "resolution_note" in body else row["resolution_note"]
    resolved_at = row["resolved_at"]
    if status == "Resolved" and not resolved_at:
        resolved_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    elif status != "Resolved":
        resolved_at = None
    db.execute(
        "UPDATE incidents SET status=?, severity=?, assigned_to=?, resolution_note=?, resolved_at=? WHERE id=?",
        (status, severity, assigned_to, resolution_note, resolved_at, iid),
    )
    log_action(user, "INCIDENT_UPDATE", f"id={iid} status={status} sev={severity}")
    notify("data_updated", "incident")
    return {"message": "Insiden diperbarui."}


# ===========================================================================
# HOME OPS -- landing dashboard untuk role ops
# ===========================================================================
@router.get("/api/ops/home")
async def ops_home(user=Depends(authenticate_token)):
    require_role(user, "admin", "ops", "management")
    active_pkg = db.query_one(
        "SELECT COUNT(*) c FROM packages WHERE departure_date IS NOT NULL AND date(departure_date) >= date('now')"
    )["c"]
    active_jamaah = db.query_one(
        "SELECT COUNT(*) c FROM jamaah WHERE status NOT IN ('Cancelled', 'Lead - Follow Up')"
    )["c"]
    low_stock_count = db.query_one(
        "SELECT COUNT(*) c FROM inventory WHERE COALESCE(stock, 0) <= COALESCE(min_stock_threshold, 20)"
    )["c"]
    passport_expiring = db.query_all(
        "SELECT j.id, j.name, j.phone, j.passport_number, j.passport_expiry, j.package_type "
        "FROM jamaah j "
        "WHERE j.passport_expiry IS NOT NULL AND j.passport_expiry != '' "
        "  AND date(j.passport_expiry) BETWEEN date('now', '-12 months') AND date('now', '+6 months') "
        "  AND j.status NOT IN ('Cancelled') "
        "ORDER BY j.passport_expiry ASC LIMIT 20"
    )
    asset_checkout = db.query_one(
        "SELECT COUNT(*) c FROM company_assets WHERE assigned_to IS NOT NULL AND assigned_to != ''"
    )
    asset_checkout_count = (asset_checkout or {}).get("c", 0)

    total_checklist_items = db.query_one("SELECT COUNT(*) c FROM checklist_templates")["c"] or 1
    upcoming_packages = db.query_all(
        "SELECT p.id, p.name, p.departure_date, p.duration, p.quota, p.hotel_mekkah, p.airline_depart, "
        "  (SELECT COUNT(*) FROM jamaah j WHERE j.package_type = p.name AND j.status NOT IN ('Cancelled')) AS filled, "
        "  CAST(julianday(p.departure_date) - julianday('now') AS INTEGER) AS days_to_go, "
        "  (SELECT COUNT(*) FROM package_checklist_progress pc WHERE pc.package_id = p.id AND pc.status IN ('done','na')) AS checklist_done, "
        "  ? AS checklist_total, "
        "  (SELECT COUNT(*) FROM vendor_bookings v WHERE v.package_id = p.id AND v.status != 'Cancelled') AS vendor_total, "
        "  (SELECT COUNT(*) FROM vendor_bookings v WHERE v.package_id = p.id AND v.status = 'Confirmed') AS vendor_confirmed "
        "FROM packages p "
        "WHERE p.departure_date IS NOT NULL AND date(p.departure_date) BETWEEN date('now') AND date('now', '+30 days') "
        "ORDER BY p.departure_date ASC LIMIT 5",
        (total_checklist_items,),
    )
    for p in upcoming_packages:
        p["checklist_pct"] = round((p["checklist_done"] / p["checklist_total"]) * 100) if p["checklist_total"] else 0
        p["vendor_pending"] = (p["vendor_total"] or 0) - (p["vendor_confirmed"] or 0)

    incidents_open = db.query_all(
        "SELECT i.id, i.package_name, i.incident_text, i.severity, i.assigned_to, i.reported_by, i.created_at "
        "FROM incidents i WHERE i.status IN ('Open', 'InProgress') "
        "ORDER BY CASE i.severity WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 ELSE 2 END, i.created_at DESC LIMIT 10"
    )

    low_stock_items = db.query_all(
        "SELECT item_name AS name, stock, min_stock_threshold FROM inventory "
        "WHERE COALESCE(stock, 0) <= COALESCE(min_stock_threshold, 20) "
        "ORDER BY (COALESCE(stock,0) - COALESCE(min_stock_threshold,20)) ASC LIMIT 10"
    )

    handover_pending = db.query_all(
        "SELECT j.id, j.name, j.phone, j.package_type, j.total_price, j.paid_amount "
        "FROM jamaah j "
        "LEFT JOIN packages p ON p.name = j.package_type "
        "WHERE j.paid_amount >= (j.total_price * 0.75) "
        "  AND j.status NOT IN ('Cancelled', 'On Trip') "
        "  AND p.departure_date IS NOT NULL "
        "  AND date(p.departure_date) BETWEEN date('now') AND date('now', '+30 days') "
        "  AND NOT EXISTS (SELECT 1 FROM jamaah_inventory ji WHERE ji.jamaah_id = j.id) "
        "ORDER BY p.departure_date ASC LIMIT 15"
    )

    # Paket yang sudah kembali (return_date atau departure+duration < today, dalam 60 hari terakhir)
    # -- ini masuk section "Paket Selesai" untuk debrief post-trip
    returned_packages = db.query_all(
        "SELECT p.id, p.name, p.departure_date, p.return_date, p.duration, p.hotel_mekkah, "
        "  (SELECT COUNT(*) FROM jamaah j WHERE j.package_type = p.name AND j.status NOT IN ('Cancelled')) AS filled, "
        "  COALESCE(p.return_date, date(p.departure_date, '+' || COALESCE(p.duration,9) || ' days')) AS effective_return, "
        "  (SELECT COUNT(*) FROM package_debriefs d WHERE d.package_id = p.id) AS debrief_filled, "
        "  (SELECT COUNT(*) FROM jamaah_feedback f WHERE f.package_id = p.id) AS feedback_count "
        "FROM packages p "
        "WHERE p.departure_date IS NOT NULL "
        "  AND date(COALESCE(p.return_date, date(p.departure_date, '+' || COALESCE(p.duration,9) || ' days'))) BETWEEN date('now', '-60 days') AND date('now') "
        "ORDER BY effective_return DESC LIMIT 5"
    )
    debrief_pending_count = sum(1 for p in returned_packages if (p["debrief_filled"] or 0) == 0)

    paket_not_ready = sum(
        1 for p in upcoming_packages
        if (p.get("days_to_go") or 999) <= 7 and (p.get("checklist_pct") or 0) < 70
    )
    vendor_at_risk = sum(
        1 for p in upcoming_packages
        if (p.get("days_to_go") or 999) <= 14 and (p.get("vendor_pending") or 0) > 0
    )
    attention = {
        "upcoming_H7": sum(1 for p in upcoming_packages if (p.get("days_to_go") or 999) <= 7),
        "upcoming_H14": sum(1 for p in upcoming_packages if (p.get("days_to_go") or 999) <= 14),
        "paket_not_ready": paket_not_ready,
        "vendor_at_risk": vendor_at_risk,
        "incidents_open": len(incidents_open),
        "low_stock": low_stock_count,
        "passport_expiring": len(passport_expiring),
        "handover_pending": len(handover_pending),
        "debrief_pending": debrief_pending_count,
    }
    attention["total"] = (
        paket_not_ready + vendor_at_risk + attention["incidents_open"] + attention["low_stock"]
        + attention["passport_expiring"] + attention["handover_pending"] + debrief_pending_count
    )

    return {
        "me": {"id": user["id"], "name": user["name"], "role": user["role"]},
        "kpi": {
            "active_packages": active_pkg,
            "active_jamaah": active_jamaah,
            "low_stock_count": low_stock_count,
            "asset_checkout_count": asset_checkout_count,
            "passport_expiring_count": len(passport_expiring),
        },
        "attention": attention,
        "upcoming_packages": upcoming_packages,
        "returned_packages": returned_packages,
        "incidents_open": incidents_open,
        "low_stock_items": low_stock_items,
        "passport_expiring": passport_expiring,
        "handover_pending": handover_pending,
    }


# ===========================================================================
# FEEDBACK JAMAAH + DEBRIEF POST-TRIP PER PAKET
# ===========================================================================
DEBRIEF_CATEGORIES = ("hotel_mekkah", "hotel_madinah", "airline", "bus", "muthawif", "overall")


@router.get("/api/packages/{pid}/feedback")
async def package_feedback_list(pid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "ops", "management", "sales")
    pkg = db.query_one("SELECT id, name, departure_date FROM packages WHERE id = ?", (pid,))
    if not pkg:
        raise HTTPException(status_code=404, detail="Paket tidak ditemukan.")
    rows = db.query_all(
        "SELECT j.id, j.name, j.phone, "
        "  f.id AS feedback_id, f.rating, f.testimonial, f.complaint, f.would_recommend, "
        "  f.source, f.created_by, f.created_at "
        "FROM jamaah j "
        "LEFT JOIN jamaah_feedback f ON f.jamaah_id = j.id AND f.package_id = ? "
        "WHERE j.package_type = ? AND j.status NOT IN ('Cancelled') "
        "ORDER BY j.name ASC",
        (pid, pkg["name"]),
    )
    filled = [r for r in rows if r["feedback_id"]]
    ratings = [r["rating"] for r in filled if r["rating"] is not None]
    recommend = sum(1 for r in filled if r["would_recommend"])
    return {
        "package": pkg,
        "jamaah": rows,
        "summary": {
            "total": len(rows),
            "filled": len(filled),
            "avg_rating": round(sum(ratings)/len(ratings), 2) if ratings else None,
            "recommend_pct": round((recommend / len(filled)) * 100) if filled else 0,
        },
    }


@router.post("/api/feedback")
async def feedback_upsert(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops", "management", "sales")
    jid = body.get("jamaah_id")
    pid = body.get("package_id")
    if not jid or not pid:
        raise HTTPException(status_code=400, detail="jamaah_id & package_id wajib.")
    rating = body.get("rating")
    if rating is not None:
        rating = int(rating)
        if rating < 1 or rating > 5:
            raise HTTPException(status_code=400, detail="rating harus 1-5.")
    db.execute(
        "INSERT INTO jamaah_feedback (jamaah_id, package_id, rating, testimonial, complaint, "
        "would_recommend, source, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(jamaah_id, package_id) DO UPDATE SET "
        "  rating = excluded.rating, "
        "  testimonial = excluded.testimonial, "
        "  complaint = excluded.complaint, "
        "  would_recommend = excluded.would_recommend, "
        "  source = excluded.source, "
        "  created_by = excluded.created_by",
        (
            jid, pid, rating,
            body.get("testimonial"), body.get("complaint"),
            1 if body.get("would_recommend") else 0,
            body.get("source") or "manual",
            user["name"],
        ),
    )
    log_action(user, "JAMAAH_FEEDBACK", f"pkg={pid} jamaah={jid} rating={rating}")
    notify("data_updated", "feedback")
    return {"message": "Feedback tersimpan."}


@router.get("/api/packages/{pid}/debriefs")
async def package_debrief_list(pid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "ops", "management")
    pkg = db.query_one("SELECT id, name, departure_date FROM packages WHERE id = ?", (pid,))
    if not pkg:
        raise HTTPException(status_code=404, detail="Paket tidak ditemukan.")
    existing = db.query_all(
        "SELECT category, rating, notes, created_by, created_at, updated_at "
        "FROM package_debriefs WHERE package_id = ?",
        (pid,),
    )
    existing_map = {r["category"]: r for r in existing}
    items = []
    for cat in DEBRIEF_CATEGORIES:
        r = existing_map.get(cat)
        items.append({
            "category": cat,
            "rating": r["rating"] if r else None,
            "notes": r["notes"] if r else "",
            "created_by": r["created_by"] if r else None,
            "updated_at": r["updated_at"] if r else None,
        })
    ratings = [i["rating"] for i in items if i["rating"] is not None]
    filled = len(ratings)
    return {
        "package": pkg,
        "items": items,
        "summary": {
            "filled": filled,
            "total": len(DEBRIEF_CATEGORIES),
            "pct": round((filled / len(DEBRIEF_CATEGORIES)) * 100),
            "avg_rating": round(sum(ratings)/len(ratings), 2) if ratings else None,
        },
    }


@router.post("/api/packages/{pid}/debriefs")
async def package_debrief_upsert(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops", "management")
    if not db.query_one("SELECT id FROM packages WHERE id = ?", (pid,)):
        raise HTTPException(status_code=404, detail="Paket tidak ditemukan.")
    cat = body.get("category")
    if cat not in DEBRIEF_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"category harus salah satu: {', '.join(DEBRIEF_CATEGORIES)}")
    rating = body.get("rating")
    if rating is not None:
        rating = int(rating)
        if rating < 1 or rating > 5:
            raise HTTPException(status_code=400, detail="rating harus 1-5.")
    db.execute(
        "INSERT INTO package_debriefs (package_id, category, rating, notes, created_by) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(package_id, category) DO UPDATE SET "
        "  rating = excluded.rating, "
        "  notes = excluded.notes, "
        "  created_by = excluded.created_by, "
        "  updated_at = CURRENT_TIMESTAMP",
        (pid, cat, rating, body.get("notes"), user["name"]),
    )
    log_action(user, "DEBRIEF_UPDATE", f"pkg={pid} cat={cat} rating={rating}")
    notify("data_updated", "debrief")
    return {"message": "Debrief tersimpan."}


# ===========================================================================
# VENDOR BOOKINGS PER PAKET
# ===========================================================================
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


# ===========================================================================
# CHECKLIST PRA-KEBERANGKATAN PER PAKET
# ===========================================================================
@router.get("/api/packages/{pid}/checklist")
async def package_checklist_get(pid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "ops", "management")
    pkg = db.query_one(
        "SELECT id, name, departure_date, CAST(julianday(departure_date) - julianday('now') AS INTEGER) AS days_to_go "
        "FROM packages WHERE id = ?",
        (pid,),
    )
    if not pkg:
        raise HTTPException(status_code=404, detail="Paket tidak ditemukan.")
    items = db.query_all(
        "SELECT t.item_key, t.label, t.category, t.default_offset_days, t.sort_order, "
        "  COALESCE(p.status, 'pending') AS status, "
        "  p.completed_by, p.completed_at, p.note "
        "FROM checklist_templates t "
        "LEFT JOIN package_checklist_progress p ON p.item_key = t.item_key AND p.package_id = ? "
        "ORDER BY t.sort_order ASC",
        (pid,),
    )
    done = sum(1 for i in items if i["status"] in ("done", "na"))
    total = len(items)
    return {
        "package": pkg,
        "items": items,
        "summary": {"done": done, "total": total, "pct": round((done/total)*100) if total else 0},
    }


@router.post("/api/packages/{pid}/checklist")
async def package_checklist_upsert(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops", "management")
    item_key = body.get("item_key")
    status = body.get("status") or "pending"
    if not item_key:
        raise HTTPException(status_code=400, detail="item_key wajib diisi.")
    if status not in ("pending", "done", "na"):
        raise HTTPException(status_code=400, detail="status harus pending/done/na.")
    if not db.query_one("SELECT id FROM checklist_templates WHERE item_key = ?", (item_key,)):
        raise HTTPException(status_code=400, detail="item_key tidak dikenal.")
    if not db.query_one("SELECT id FROM packages WHERE id = ?", (pid,)):
        raise HTTPException(status_code=404, detail="Paket tidak ditemukan.")
    completed_by = user["name"] if status in ("done", "na") else None
    completed_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") if status in ("done", "na") else None
    note = body.get("note") if "note" in body else None
    db.execute(
        "INSERT INTO package_checklist_progress (package_id, item_key, status, completed_by, completed_at, note) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(package_id, item_key) DO UPDATE SET "
        "  status = excluded.status, "
        "  completed_by = excluded.completed_by, "
        "  completed_at = excluded.completed_at, "
        "  note = COALESCE(excluded.note, package_checklist_progress.note)",
        (pid, item_key, status, completed_by, completed_at, note),
    )
    log_action(user, "CHECKLIST_UPDATE", f"pkg={pid} item={item_key} status={status}")
    notify("data_updated", "package")
    return {"message": "Checklist diperbarui."}
