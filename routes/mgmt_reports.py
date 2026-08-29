"""
Router Management -- Executive PDF Report (M-C):
- GET /api/mgmt/monthly-pdf  Laporan Eksekutif Bulanan PDF (dipanggil dari
                             tombol "Download Laporan" di dashboard mgmt).

NB nama file 'mgmt_reports.py' (bukan 'mgmt_pdf.py') supaya tidak bentrok
dengan module PDF builder existing di root project (mgmt_pdf.py).
"""
import datetime

from fastapi import APIRouter, Response

import db
from deps import Depends, HTTPException, authenticate_token, log_action, require_role
from mgmt_pdf import build_monthly_report_pdf

router = APIRouter(tags=["mgmt-reports"])

_MGMT_ROLES = ("admin", "management")

MONTH_NAMES_ID = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
                  "Juli", "Agustus", "September", "Oktober", "November", "Desember"]


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
