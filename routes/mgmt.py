"""
Router Management (executive):
- /api/mgmt/home            (M-A) executive landing (omzet MoM, sales attainment, approvals)
- /api/mgmt/monthly-pdf     (M-C) executive PDF report per bulan
- /api/mgmt/company-targets (M-D) GET+POST revenue/closing target per bulan
- /api/mgmt/risk-register   (M-E) agregasi risk indicator cross-modul
"""
import datetime

from fastapi import APIRouter, Response

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
from mgmt_pdf import build_monthly_report_pdf

router = APIRouter(tags=["mgmt"])

_MGMT_ROLES = ("admin", "management")
_TARGET_READ_ROLES = ("admin", "management", "finance")

MONTH_NAMES_ID = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
                  "Juli", "Agustus", "September", "Oktober", "November", "Desember"]


@router.get("/api/mgmt/home")
async def mgmt_home(user=Depends(authenticate_token)):
    """Executive landing untuk role management. Fokus: omzet MoM/YoY, sales
    performance vs target, top agen, ops readiness, approval inbox mgmt-only."""
    require_role(user, *_MGMT_ROLES)

    now = datetime.datetime.now()
    ym_now = now.strftime("%Y-%m")
    ym_prev = (now.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")

    def month_stat(ym):
        row = db.query_one(
            "SELECT COUNT(*) c, COALESCE(SUM(total_price),0) omzet FROM jamaah "
            "WHERE strftime('%Y-%m', COALESCE(order_date, created_at)) = ? "
            "AND status NOT IN ('Cancelled', 'Lead - Follow Up')",
            (ym,),
        )
        return {"count": row["c"] or 0, "omzet": row["omzet"] or 0}

    m_now = month_stat(ym_now)
    m_prev = month_stat(ym_prev)
    mom_omzet_pct = None
    if m_prev["omzet"] > 0:
        mom_omzet_pct = round(((m_now["omzet"] - m_prev["omzet"]) / m_prev["omzet"]) * 100, 1)

    cash = db.query_one(
        "SELECT COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE -amount END), 0) c FROM transactions"
    )["c"]
    piutang = db.query_one(
        "SELECT COALESCE(SUM(COALESCE(total_price,0) - COALESCE(paid_amount,0)), 0) p, COUNT(*) n FROM jamaah "
        "WHERE payment_status IN ('DP', 'Unpaid') AND status NOT IN ('Cancelled', 'Lead - Follow Up')"
    )

    company_tgt = db.query_one(
        "SELECT revenue_target, closing_target FROM company_targets WHERE month = ?", (ym_now,),
    ) or {"revenue_target": 0, "closing_target": 0}
    rev_target = company_tgt["revenue_target"] or 0
    cls_target = company_tgt["closing_target"] or 0
    revenue_pct = round((m_now["omzet"] / rev_target) * 100) if rev_target else None
    closing_pct = round((m_now["count"] / cls_target) * 100) if cls_target else None

    kpi = {
        "omzet_this_month": m_now["omzet"],
        "closing_this_month": m_now["count"],
        "omzet_prev_month": m_prev["omzet"],
        "mom_omzet_pct": mom_omzet_pct,
        "cash_saldo": cash or 0,
        "piutang_total": piutang["p"] or 0,
        "piutang_count": piutang["n"] or 0,
        "revenue_target": rev_target,
        "closing_target": cls_target,
        "revenue_pct": revenue_pct,
        "closing_pct": closing_pct,
    }

    sales_users = db.query_all("SELECT id, name FROM users WHERE role = 'sales' ORDER BY name")
    sales_perf = []
    for s in sales_users:
        actual = db.query_one(
            "SELECT COUNT(*) c, COALESCE(SUM(total_price),0) omzet FROM jamaah "
            "WHERE sales_id = ? AND strftime('%Y-%m', COALESCE(order_date, created_at)) = ? "
            "AND status NOT IN ('Cancelled', 'Lead - Follow Up')",
            (s["id"], ym_now),
        )
        target = db.query_one(
            "SELECT target_closing, target_omzet FROM sales_targets WHERE user_id = ? AND month = ?",
            (s["id"], ym_now),
        ) or {"target_closing": 0, "target_omzet": 0}
        c_a = actual["c"] or 0
        o_a = actual["omzet"] or 0
        tc = target["target_closing"] or 0
        to = target["target_omzet"] or 0
        sales_perf.append({
            "user_id": s["id"], "name": s["name"],
            "actual_closing": c_a, "actual_omzet": o_a,
            "target_closing": tc, "target_omzet": to,
            "closing_pct": round((c_a / tc) * 100) if tc else None,
            "omzet_pct": round((o_a / to) * 100) if to else None,
        })
    sales_perf.sort(key=lambda x: (x["actual_omzet"] or 0), reverse=True)

    top_agents = db.query_all(
        "SELECT a.id, a.name, COUNT(j.id) closings, COALESCE(SUM(j.total_price),0) omzet "
        "FROM agents a JOIN jamaah j ON j.agent_id = a.id "
        "WHERE strftime('%Y-%m', COALESCE(j.order_date, j.created_at)) = ? "
        "AND j.status NOT IN ('Cancelled', 'Lead - Follow Up') "
        "GROUP BY a.id ORDER BY omzet DESC LIMIT 10", (ym_now,),
    )

    upcoming_pkg = db.query_all(
        "SELECT p.id, p.name, p.departure_date, p.quota, "
        "  (SELECT COUNT(*) FROM jamaah j WHERE j.package_type = p.name AND j.status NOT IN ('Cancelled')) filled, "
        "  CAST(julianday(p.departure_date) - julianday('now') AS INTEGER) days_to_go "
        "FROM packages p WHERE p.departure_date IS NOT NULL "
        "  AND date(p.departure_date) BETWEEN date('now') AND date('now', '+30 days') "
        "ORDER BY p.departure_date ASC LIMIT 8"
    )

    refund_review = db.query_all(
        "SELECT r.id, r.amount, r.reason, r.requested_at, j.name AS jamaah_name "
        "FROM refund_requests r LEFT JOIN jamaah j ON r.jamaah_id = j.id "
        "WHERE r.status = 'Pending' ORDER BY r.requested_at ASC LIMIT 5"
    )
    komisi_review = db.query_all(
        "SELECT c.id, c.amount, c.requested_at, a.name AS agent_name, j.name AS jamaah_name "
        "FROM commission_claims c "
        "LEFT JOIN agents a ON c.agent_id = a.id "
        "LEFT JOIN jamaah j ON c.jamaah_id = j.id "
        "WHERE c.status = 'Pending' ORDER BY c.requested_at ASC LIMIT 5"
    )
    incident_open = db.query_all(
        "SELECT id, package_name, incident_text, severity, created_at FROM incidents "
        "WHERE status IN ('Open', 'InProgress') AND severity IN ('Critical', 'High') "
        "ORDER BY CASE severity WHEN 'Critical' THEN 0 ELSE 1 END, created_at DESC LIMIT 5"
    )

    approvals = []
    for r in refund_review:
        approvals.append({"kind": "refund", "id": r["id"],
                          "label": f"Refund {r['jamaah_name'] or '-'}",
                          "amount": r["amount"], "created": r["requested_at"]})
    for c in komisi_review:
        approvals.append({"kind": "komisi", "id": c["id"],
                          "label": f"Komisi {c['agent_name'] or '-'} - {c['jamaah_name'] or '-'}",
                          "amount": c["amount"], "created": c["requested_at"]})
    for i in incident_open:
        approvals.append({"kind": "incident", "id": i["id"],
                          "label": f"[{i['severity']}] {i['package_name'] or '-'}: {i['incident_text'][:60]}",
                          "amount": None, "created": i["created_at"]})

    attention = []
    n_ref = len(refund_review); n_kom = len(komisi_review); n_inc = len(incident_open)
    if n_inc:
        attention.append({"key": "incident_critical", "severity": "critical",
                          "label": f"{n_inc} insiden Critical/High open", "goto": "incidents"})
    if n_ref:
        attention.append({"key": "refund_review", "severity": "high",
                          "label": f"{n_ref} refund menunggu keputusan", "goto": "finance"})
    if n_kom:
        attention.append({"key": "komisi_review", "severity": "high",
                          "label": f"{n_kom} klaim komisi menunggu keputusan", "goto": "finance"})
    under = [s for s in sales_perf if s["closing_pct"] is not None and s["closing_pct"] < 50]
    if under and (now.day > 15):
        attention.append({"key": "sales_under", "severity": "medium",
                          "label": f"{len(under)} sales < 50% target closing bulan ini", "goto": "mgmt-home"})

    return {
        "kpi": kpi,
        "attention": attention,
        "sales_performance": sales_perf,
        "top_agents": top_agents,
        "upcoming_packages": upcoming_pkg,
        "approvals": approvals,
        "month": ym_now,
    }


@router.get("/api/mgmt/monthly-pdf")
async def mgmt_monthly_pdf(month: str = None, user=Depends(authenticate_token)):
    """Executive Monthly Report PDF. Bulan default = bulan berjalan."""
    require_role(user, *_MGMT_ROLES)
    now = datetime.datetime.now()
    if not month:
        month = now.strftime("%Y-%m")
    if len(month) != 7 or month[4] != "-":
        raise HTTPException(status_code=400, detail="month wajib format YYYY-MM")

    ym = month
    y, m = int(ym[:4]), int(ym[5:])
    prev_dt = (datetime.date(y, m, 1) - datetime.timedelta(days=1))
    ym_prev = prev_dt.strftime("%Y-%m")

    def _month_stat(mm):
        r = db.query_one(
            "SELECT COUNT(*) c, COALESCE(SUM(total_price),0) omzet FROM jamaah "
            "WHERE strftime('%Y-%m', COALESCE(order_date, created_at)) = ? "
            "AND status NOT IN ('Cancelled', 'Lead - Follow Up')", (mm,),
        )
        return {"count": r["c"] or 0, "omzet": r["omzet"] or 0}

    m_now = _month_stat(ym)
    m_prev = _month_stat(ym_prev)
    mom_pct = None
    if m_prev["omzet"] > 0:
        mom_pct = round(((m_now["omzet"] - m_prev["omzet"]) / m_prev["omzet"]) * 100, 1)

    cash = db.query_one(
        "SELECT COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE -amount END), 0) c FROM transactions"
    )["c"] or 0
    piutang = db.query_one(
        "SELECT COALESCE(SUM(COALESCE(total_price,0) - COALESCE(paid_amount,0)), 0) p, COUNT(*) n FROM jamaah "
        "WHERE payment_status IN ('DP', 'Unpaid') AND status NOT IN ('Cancelled', 'Lead - Follow Up')"
    )
    ct = db.query_one("SELECT revenue_target, closing_target FROM company_targets WHERE month = ?", (ym,)) or {}
    rev_t = ct.get("revenue_target") or 0
    cls_t = ct.get("closing_target") or 0

    sales_users = db.query_all("SELECT id, name FROM users WHERE role = 'sales' ORDER BY name")
    sales_perf = []
    for s in sales_users:
        act = db.query_one(
            "SELECT COUNT(*) c, COALESCE(SUM(total_price),0) omzet FROM jamaah "
            "WHERE sales_id = ? AND strftime('%Y-%m', COALESCE(order_date, created_at)) = ? "
            "AND status NOT IN ('Cancelled', 'Lead - Follow Up')", (s["id"], ym),
        )
        tgt = db.query_one(
            "SELECT target_closing, target_omzet FROM sales_targets WHERE user_id = ? AND month = ?",
            (s["id"], ym),
        ) or {}
        c_actual = act["c"] or 0
        o_actual = act["omzet"] or 0
        c_tgt = tgt.get("target_closing") or 0
        o_tgt = tgt.get("target_omzet") or 0
        sales_perf.append({
            "name": s["name"], "actual_closing": c_actual, "actual_omzet": o_actual,
            "closing_pct": round((c_actual / c_tgt) * 100) if c_tgt else None,
            "omzet_pct": round((o_actual / o_tgt) * 100) if o_tgt else None,
        })
    sales_perf.sort(key=lambda x: (x["actual_omzet"] or 0), reverse=True)

    top_agents = db.query_all(
        "SELECT a.name, COUNT(j.id) closings, COALESCE(SUM(j.total_price),0) omzet "
        "FROM agents a JOIN jamaah j ON j.agent_id = a.id "
        "WHERE strftime('%Y-%m', COALESCE(j.order_date, j.created_at)) = ? "
        "AND j.status NOT IN ('Cancelled', 'Lead - Follow Up') "
        "GROUP BY a.id ORDER BY omzet DESC LIMIT 10", (ym,),
    )

    incidents = db.query_all(
        "SELECT package_name, severity, incident_text, status FROM incidents "
        "WHERE strftime('%Y-%m', created_at) = ? OR status IN ('Open', 'InProgress') "
        "ORDER BY CASE severity WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 ELSE 2 END, created_at DESC LIMIT 10",
        (ym,),
    )

    piutang_top = db.query_all(
        "SELECT j.name, j.package_type, (COALESCE(j.total_price,0) - COALESCE(j.paid_amount,0)) sisa, "
        "  j.payment_status FROM jamaah j "
        "WHERE j.payment_status IN ('DP', 'Unpaid') AND j.status NOT IN ('Cancelled', 'Lead - Follow Up') "
        "  AND (COALESCE(j.total_price,0) - COALESCE(j.paid_amount,0)) > 0 "
        "ORDER BY sisa DESC LIMIT 10"
    )

    payload = {
        "month": ym,
        "month_label": f"{MONTH_NAMES_ID[m]} {y}",
        "generated_by": user["name"],
        "generated_at": now.strftime("%d/%m/%Y %H:%M"),
        "kpi": {
            "revenue": m_now["omzet"], "revenue_prev": m_prev["omzet"], "mom_pct": mom_pct,
            "closing": m_now["count"], "closing_prev": m_prev["count"],
            "cash_saldo": cash,
            "piutang_total": piutang["p"] or 0, "piutang_count": piutang["n"] or 0,
            "revenue_target": rev_t, "closing_target": cls_t,
            "revenue_pct": round((m_now["omzet"] / rev_t) * 100) if rev_t else None,
            "closing_pct": round((m_now["count"] / cls_t) * 100) if cls_t else None,
        },
        "sales_perf": sales_perf,
        "top_agents": top_agents,
        "incidents": incidents,
        "piutang_top": piutang_top,
    }

    pdf_bytes = build_monthly_report_pdf(payload)
    log_action(user, "MGMT_MONTHLY_PDF", f"month={ym}")
    return Response(
        pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="Laporan-Eksekutif-{ym}.pdf"'},
    )


@router.get("/api/mgmt/company-targets")
async def company_targets_list(user=Depends(authenticate_token)):
    require_role(user, *_TARGET_READ_ROLES)
    rows = db.query_all(
        "SELECT month, revenue_target, closing_target, set_by, updated_at "
        "FROM company_targets ORDER BY month DESC LIMIT 24"
    )
    return {"targets": rows}


@router.post("/api/mgmt/company-targets")
async def company_targets_upsert(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, *_MGMT_ROLES)
    month = body.get("month")
    revenue_target = int(body.get("revenue_target") or 0)
    closing_target = int(body.get("closing_target") or 0)
    if not month or len(month) != 7 or month[4] != "-":
        raise HTTPException(status_code=400, detail="month wajib format YYYY-MM.")
    db.execute(
        "INSERT INTO company_targets (month, revenue_target, closing_target, set_by) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(month) DO UPDATE SET "
        "  revenue_target = excluded.revenue_target, "
        "  closing_target = excluded.closing_target, "
        "  set_by = excluded.set_by, "
        "  updated_at = CURRENT_TIMESTAMP",
        (month, revenue_target, closing_target, user["name"]),
    )
    log_action(user, "COMPANY_TARGET_SET",
               f"{month} revenue={revenue_target} closing={closing_target}")
    notify("data_updated", "company_target")
    return {"message": "Target company tersimpan."}


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
