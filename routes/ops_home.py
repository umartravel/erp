"""
Router Operasional -- Home Dashboard (landing untuk role ops).
- GET /api/ops/home  KPI + attention list + section paket H-30 & paket returned.

Menggabungkan sinyal dari banyak tabel: packages, jamaah, inventory,
company_assets, checklist_templates, vendor_bookings, incidents,
jamaah_inventory (handover), package_debriefs, jamaah_feedback.
"""
from fastapi import APIRouter

import db
from deps import Depends, authenticate_token, require_role

router = APIRouter(tags=["ops-home"])


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
