"""
Router Management -- Landing Dashboard (M-A):
- GET /api/mgmt/home  executive landing (omzet MoM, sales attainment, top agen,
                      ops readiness, approval inbox).

Pisah dari file mgmt.py awal supaya tiap sub-fungsi (home/reports/targets/risk)
punya home-nya sendiri dan tidak bareng-bareng bengkak.
"""
import datetime

from fastapi import APIRouter

import db
from deps import Depends, authenticate_token, require_role

router = APIRouter(tags=["mgmt-home"])

_MGMT_ROLES = ("admin", "management")


@router.get("/api/mgmt/home")
async def mgmt_home(year: int | None = None, user=Depends(authenticate_token)):
    """Executive landing untuk role management. Fokus: omzet MoM/YoY, sales
    performance vs target, top agen, ops readiness, approval inbox mgmt-only.

    Phase 14a: query param `year` (default = tahun berjalan) filter metrik agregat
    tahunan (year_summary). Metrik bulan berjalan (this_month, MoM) tetap real-time.
    """
    require_role(user, *_MGMT_ROLES)

    now = datetime.datetime.now()
    ym_now = now.strftime("%Y-%m")
    ym_prev = (now.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")
    current_year = now.year
    year = year if year else current_year
    year_str = str(year)

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

    # Phase 14a: Ringkasan tahun terpilih.
    year_stat = db.query_one(
        "SELECT COUNT(*) c, COALESCE(SUM(total_price),0) omzet FROM jamaah "
        "WHERE status NOT IN ('Cancelled', 'Lead - Follow Up') "
        "AND SUBSTR(COALESCE(order_date, created_at), 1, 4) = ?",
        (year_str,),
    ) or {"c": 0, "omzet": 0}

    return {
        "kpi": kpi,
        "attention": attention,
        "sales_performance": sales_perf,
        "top_agents": top_agents,
        "upcoming_packages": upcoming_pkg,
        "approvals": approvals,
        "month": ym_now,
        # Phase 14a: year picker context.
        "year": year,
        "current_year": current_year,
        "year_summary": {
            "closing_total": year_stat["c"] or 0,
            "omzet_total": year_stat["omzet"] or 0,
        },
    }
