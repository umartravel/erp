"""
Router Management -- Risk Register (M-E):
- GET /api/mgmt/risk-register  agregasi risk indicator cross-modul.

Kategori Financial/Operational/Compliance, severity CRITICAL/HIGH/MEDIUM/LOW
mengikuti dampak bisnis (bukan CVSS teknis). Semua ambang batas hard-coded
di file ini -- kalau perlu diubah cari 'julianday' + selisih hari.
"""
import datetime

from fastapi import APIRouter

import db
from deps import Depends, authenticate_token, require_role

router = APIRouter(tags=["mgmt-risk"])

_MGMT_ROLES = ("admin", "management")


@router.get("/api/mgmt/risk-register")
async def mgmt_risk_register(user=Depends(authenticate_token)):
    """Agregasi risk indicator cross-modul. Kategori Financial/Operational/Compliance,
    severity CRITICAL/HIGH/MEDIUM/LOW mengikuti dampak bisnis."""
    require_role(user, *_MGMT_ROLES)
    financial, operational, compliance = [], [], []

    r = db.query_one(
        "SELECT COUNT(*) c, COALESCE(SUM(COALESCE(j.total_price,0) - COALESCE(j.paid_amount,0)), 0) exposure "
        "FROM jamaah j LEFT JOIN packages p ON j.package_type = p.name "
        "WHERE j.payment_status IN ('DP','Unpaid') AND j.status NOT IN ('Cancelled','Lead - Follow Up') "
        "  AND p.departure_date IS NOT NULL "
        "  AND julianday(p.departure_date) - julianday('now') BETWEEN 0 AND 14 "
        "  AND (COALESCE(j.total_price,0) - COALESCE(j.paid_amount,0)) > 0"
    )
    if r["c"]:
        financial.append({"severity": "CRITICAL", "key": "piutang_h14",
                          "title": "Piutang jamaah H-14",
                          "description": f"{r['c']} jamaah dengan sisa Rp {(r['exposure'] or 0):,} belum lunas dalam 14 hari keberangkatan.",
                          "count": r["c"], "exposure_rp": r["exposure"] or 0,
                          "drilldown_page": "aged-receivable"})

    r = db.query_one(
        "SELECT COUNT(*) c, COALESCE(SUM(COALESCE(j.total_price,0) - COALESCE(j.paid_amount,0)), 0) exposure "
        "FROM jamaah j LEFT JOIN packages p ON j.package_type = p.name "
        "WHERE j.payment_status IN ('DP','Unpaid') AND j.status NOT IN ('Cancelled','Lead - Follow Up') "
        "  AND p.departure_date IS NOT NULL "
        "  AND julianday(p.departure_date) - julianday('now') BETWEEN 15 AND 30 "
        "  AND (COALESCE(j.total_price,0) - COALESCE(j.paid_amount,0)) > 0"
    )
    if r["c"]:
        financial.append({"severity": "HIGH", "key": "piutang_h30",
                          "title": "Piutang jamaah H-30",
                          "description": f"{r['c']} jamaah dengan sisa Rp {(r['exposure'] or 0):,} belum lunas dalam 15-30 hari.",
                          "count": r["c"], "exposure_rp": r["exposure"] or 0,
                          "drilldown_page": "aged-receivable"})

    r = db.query_one(
        "SELECT COUNT(*) c, COALESCE(SUM(COALESCE(j.total_price,0) - COALESCE(j.paid_amount,0)), 0) exposure "
        "FROM jamaah j LEFT JOIN packages p ON j.package_type = p.name "
        "WHERE j.payment_status IN ('DP','Unpaid') AND j.status NOT IN ('Cancelled','Lead - Follow Up') "
        "  AND p.departure_date IS NOT NULL "
        "  AND julianday(p.departure_date) - julianday('now') BETWEEN 31 AND 60 "
        "  AND (COALESCE(j.total_price,0) - COALESCE(j.paid_amount,0)) > 0"
    )
    if r["c"]:
        financial.append({"severity": "MEDIUM", "key": "piutang_h60",
                          "title": "Piutang jamaah H-60 (watchlist)",
                          "description": f"{r['c']} jamaah dengan sisa Rp {(r['exposure'] or 0):,} belum lunas dalam 31-60 hari.",
                          "count": r["c"], "exposure_rp": r["exposure"] or 0,
                          "drilldown_page": "aged-receivable"})

    r = db.query_one(
        "SELECT COUNT(*) c, COALESCE(SUM(amount), 0) exposure FROM refund_requests "
        "WHERE status = 'Pending' AND julianday('now') - julianday(requested_at) > 7"
    )
    if r["c"]:
        financial.append({"severity": "HIGH", "key": "refund_stale",
                          "title": "Refund pending > 7 hari",
                          "description": f"{r['c']} pengajuan refund total Rp {(r['exposure'] or 0):,} belum direview management > 7 hari.",
                          "count": r["c"], "exposure_rp": r["exposure"] or 0,
                          "drilldown_page": "finance"})

    r = db.query_one(
        "SELECT COUNT(*) c, COALESCE(SUM(vb.total_amount - vb.deposit_amount), 0) exposure "
        "FROM vendor_bookings vb LEFT JOIN packages p ON vb.package_id = p.id "
        "WHERE vb.status NOT IN ('Paid', 'Cancelled') AND vb.total_amount > vb.deposit_amount "
        "  AND p.departure_date IS NOT NULL "
        "  AND julianday(p.departure_date) - julianday('now') BETWEEN 0 AND 14"
    )
    if r["c"]:
        financial.append({"severity": "HIGH", "key": "vendor_unpaid_h14",
                          "title": "Vendor unpaid H-14",
                          "description": f"{r['c']} vendor booking dengan sisa Rp {(r['exposure'] or 0):,} belum lunas dalam 14 hari.",
                          "count": r["c"], "exposure_rp": r["exposure"] or 0,
                          "drilldown_page": "procurement"})

    checklist_total = db.query_one("SELECT COUNT(*) c FROM checklist_templates")["c"] or 1
    upcoming = db.query_all(
        "SELECT id, name, departure_date FROM packages "
        "WHERE departure_date IS NOT NULL "
        "  AND julianday(departure_date) - julianday('now') BETWEEN 0 AND 7"
    )
    weak_checklist = []
    for p in upcoming:
        done = db.query_one(
            "SELECT COUNT(*) c FROM package_checklist_progress "
            "WHERE package_id = ? AND status IN ('done', 'na')", (p["id"],))["c"] or 0
        pct = round((done / checklist_total) * 100)
        if pct < 70:
            weak_checklist.append({"pkg": p["name"], "pct": pct})
    if weak_checklist:
        operational.append({"severity": "CRITICAL", "key": "checklist_h7",
                            "title": "Ops checklist H-7 lemah",
                            "description": f"{len(weak_checklist)} paket berangkat < 7 hari dengan checklist < 70% completion.",
                            "count": len(weak_checklist), "exposure_rp": 0,
                            "drilldown_page": "ops-home", "detail": weak_checklist})

    r = db.query_one(
        "SELECT COUNT(*) c FROM vendor_bookings vb LEFT JOIN packages p ON vb.package_id = p.id "
        "WHERE vb.status NOT IN ('Confirmed', 'Paid', 'Cancelled') "
        "  AND p.departure_date IS NOT NULL "
        "  AND julianday(p.departure_date) - julianday('now') BETWEEN 0 AND 7"
    )
    if r["c"]:
        operational.append({"severity": "CRITICAL", "key": "vendor_unconfirmed_h7",
                            "title": "Vendor belum Confirmed H-7",
                            "description": f"{r['c']} vendor booking (hotel/maskapai/bus/muthawif) belum status Confirmed dalam 7 hari keberangkatan.",
                            "count": r["c"], "exposure_rp": 0,
                            "drilldown_page": "procurement"})

    r = db.query_one(
        "SELECT COUNT(*) c FROM jamaah j LEFT JOIN packages p ON j.package_type = p.name "
        "WHERE j.status NOT IN ('Cancelled', 'Lead - Follow Up') "
        "  AND COALESCE(j.visa_status, '') NOT IN ('Approved', 'Issued') "
        "  AND p.departure_date IS NOT NULL "
        "  AND julianday(p.departure_date) - julianday('now') BETWEEN 0 AND 14"
    )
    if r["c"]:
        operational.append({"severity": "HIGH", "key": "visa_pending_h14",
                            "title": "Visa jamaah belum approved H-14",
                            "description": f"{r['c']} jamaah dengan visa belum Approved dalam 14 hari keberangkatan.",
                            "count": r["c"], "exposure_rp": 0,
                            "drilldown_page": "jamaah"})

    r = db.query_one(
        "SELECT COUNT(*) c FROM jamaah j LEFT JOIN packages p ON j.package_type = p.name "
        "WHERE j.status NOT IN ('Cancelled', 'Lead - Follow Up') "
        "  AND COALESCE(j.visa_status, '') NOT IN ('Approved', 'Issued') "
        "  AND p.departure_date IS NOT NULL "
        "  AND julianday(p.departure_date) - julianday('now') BETWEEN 15 AND 30"
    )
    if r["c"]:
        operational.append({"severity": "MEDIUM", "key": "visa_pending_h30",
                            "title": "Visa jamaah watchlist H-30",
                            "description": f"{r['c']} jamaah dengan visa belum Approved dalam 15-30 hari.",
                            "count": r["c"], "exposure_rp": 0,
                            "drilldown_page": "jamaah"})

    r = db.query_one(
        "SELECT COUNT(*) c FROM incidents WHERE status = 'Open' AND severity = 'Critical'"
    )
    if r["c"]:
        compliance.append({"severity": "CRITICAL", "key": "incident_critical",
                           "title": "Incident Critical belum resolved",
                           "description": f"{r['c']} incident severity Critical masih Open.",
                           "count": r["c"], "exposure_rp": 0, "drilldown_page": "incidents"})

    r = db.query_one(
        "SELECT COUNT(*) c FROM incidents WHERE status = 'Open' AND severity = 'High' "
        "AND julianday('now') - julianday(created_at) > 3"
    )
    if r["c"]:
        compliance.append({"severity": "HIGH", "key": "incident_high_stale",
                           "title": "Incident High > 3 hari",
                           "description": f"{r['c']} incident severity High belum ditangani > 3 hari.",
                           "count": r["c"], "exposure_rp": 0, "drilldown_page": "incidents"})

    r = db.query_one(
        "SELECT COUNT(*) c FROM packages p "
        "WHERE p.return_date IS NOT NULL AND date(p.return_date) < date('now') "
        "  AND date(p.return_date) > date('now', '-60 days') "
        "  AND NOT EXISTS (SELECT 1 FROM package_debriefs d WHERE d.package_id = p.id)"
    )
    if r["c"]:
        compliance.append({"severity": "MEDIUM", "key": "debrief_missing",
                           "title": "Debrief post-trip belum diisi",
                           "description": f"{r['c']} paket sudah pulang tapi belum ada debrief internal.",
                           "count": r["c"], "exposure_rp": 0, "drilldown_page": "ops-home"})

    r = db.query_one(
        "SELECT COUNT(*) c FROM procurement WHERE status = 'Pending' "
        "AND julianday('now') - julianday(created_at) > 3"
    )
    if r["c"]:
        compliance.append({"severity": "MEDIUM", "key": "procurement_pending",
                           "title": "Kontrak vendor pending > 3 hari",
                           "description": f"{r['c']} kontrak vendor menunggu approval > 3 hari.",
                           "count": r["c"], "exposure_rp": 0, "drilldown_page": "procurement"})

    all_items = financial + operational + compliance
    by_sev = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    total_exposure = 0
    for it in all_items:
        by_sev[it["severity"]] = by_sev.get(it["severity"], 0) + 1
        total_exposure += it.get("exposure_rp", 0) or 0

    return {
        "summary": {
            "total_risks": len(all_items),
            "by_severity": by_sev,
            "total_exposure_rp": total_exposure,
        },
        "categories": [
            {"key": "financial",   "label": "Financial Risk",   "count": len(financial),   "items": financial},
            {"key": "operational", "label": "Operational Risk", "count": len(operational), "items": operational},
            {"key": "compliance",  "label": "Compliance & Quality", "count": len(compliance),  "items": compliance},
        ],
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
