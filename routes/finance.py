"""
Router Finance:
- /api/finance/home         (F-A) landing dashboard finance
- /api/finance/aged-receivable (F-D) drill-down piutang jamaah per bucket age
- /api/finance/forecast     (F-B) cash flow proyeksi 60 hari

Rekonsiliasi bank (F-E) ada di routes/reconcile.py terpisah.
"""
import datetime

from fastapi import APIRouter

import db
from deps import Depends, authenticate_token, require_role
from deps.notifications import notify_role  # Phase 9c

router = APIRouter(tags=["finance"])

_ROLES = ("admin", "finance", "management")


@router.get("/api/finance/home")
async def finance_home(user=Depends(authenticate_token)):
    """Landing dashboard untuk role Finance. Fokus: cash-on-hand + piutang jamaah +
    utang vendor + antrian approval yang menunggu Finance action."""
    require_role(user, *_ROLES)

    cash = db.query_one(
        "SELECT COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE -amount END), 0) c FROM transactions"
    )["c"]
    piutang = db.query_one(
        "SELECT COALESCE(SUM(COALESCE(total_price,0) - COALESCE(paid_amount,0)), 0) p, "
        "       COUNT(*) n FROM jamaah "
        "WHERE payment_status IN ('DP', 'Unpaid') AND status NOT IN ('Cancelled', 'Lead - Follow Up')"
    )
    utang_vendor = db.query_one(
        "SELECT COALESCE(SUM(CASE WHEN status='Booked' THEN COALESCE(total_amount,0) "
        "                       WHEN status='Deposit' THEN COALESCE(total_amount,0) - COALESCE(deposit_amount,0) "
        "                       ELSE 0 END), 0) u, COUNT(*) n "
        "FROM vendor_bookings WHERE status IN ('Booked', 'Deposit')"
    )
    expense_pending = db.query_one(
        "SELECT COUNT(*) c FROM expense_reports WHERE status IN ('Submitted', 'Approved')"
    )["c"]
    refund_pending = db.query_one(
        "SELECT COUNT(*) c FROM refund_requests WHERE status = 'Disetujui'"
    )["c"]
    komisi_pending = db.query_one(
        "SELECT COUNT(*) c FROM commission_claims WHERE status = 'Disetujui'"
    )["c"]

    kpi = {
        "cash_saldo": cash or 0,
        "piutang_total": piutang["p"] or 0,
        "piutang_count": piutang["n"] or 0,
        "utang_vendor_total": utang_vendor["u"] or 0,
        "utang_vendor_count": utang_vendor["n"] or 0,
        "expense_pending_count": expense_pending or 0,
        "refund_pending_count": refund_pending or 0,
        "komisi_pending_count": komisi_pending or 0,
    }

    attention = []
    if refund_pending:
        attention.append({"key": "refund_pending", "severity": "high",
                          "label": f"{refund_pending} refund menunggu dicairkan", "goto": "finance"})
    if komisi_pending:
        attention.append({"key": "komisi_pending", "severity": "high",
                          "label": f"{komisi_pending} klaim komisi agen menunggu dicairkan", "goto": "finance"})
    if expense_pending:
        attention.append({"key": "expense_pending", "severity": "medium",
                          "label": f"{expense_pending} expense report menunggu proses", "goto": "expense"})

    vendor_due_soon = db.query_all(
        "SELECT v.id, v.package_id, v.vendor_type, v.vendor_name, v.status, v.total_amount, v.deposit_amount, "
        "       (COALESCE(v.total_amount,0) - CASE WHEN v.status='Deposit' THEN COALESCE(v.deposit_amount,0) ELSE 0 END) AS sisa, "
        "       v.due_date, p.name AS package_name, "
        "       CAST(julianday(v.due_date) - julianday('now') AS INTEGER) AS days_to_due "
        "FROM vendor_bookings v LEFT JOIN packages p ON v.package_id = p.id "
        "WHERE v.status IN ('Booked', 'Deposit') "
        "  AND v.due_date IS NOT NULL AND v.due_date != '' "
        "  AND date(v.due_date) BETWEEN date('now', '-7 days') AND date('now', '+14 days') "
        "ORDER BY v.due_date ASC LIMIT 8"
    )
    if vendor_due_soon:
        overdue = [v for v in vendor_due_soon if (v.get("days_to_due") or 0) < 0]
        if overdue:
            attention.append({"key": "vendor_overdue", "severity": "critical",
                              "label": f"{len(overdue)} pembayaran vendor sudah lewat jatuh tempo", "goto": "finance"})
        else:
            attention.append({"key": "vendor_due_soon", "severity": "medium",
                              "label": f"{len(vendor_due_soon)} vendor payment jatuh tempo ≤14 hari", "goto": "finance"})

    piutang_jamaah = db.query_all(
        "SELECT j.id, j.name, j.phone, j.package_type, j.total_price, j.paid_amount, "
        "       (COALESCE(j.total_price,0) - COALESCE(j.paid_amount,0)) AS sisa, "
        "       j.payment_status, p.departure_date, "
        "       CAST(julianday(p.departure_date) - julianday('now') AS INTEGER) AS days_to_depart, "
        "       COALESCE(u.name, '-') AS sales_name "
        "FROM jamaah j LEFT JOIN packages p ON j.package_type = p.name "
        "LEFT JOIN users u ON j.sales_id = u.id "
        "WHERE j.payment_status IN ('DP', 'Unpaid') AND j.status NOT IN ('Cancelled', 'Lead - Follow Up') "
        "  AND (COALESCE(j.total_price,0) - COALESCE(j.paid_amount,0)) > 0 "
        "ORDER BY sisa DESC LIMIT 10"
    )
    piutang_urgent = [j for j in piutang_jamaah if (j.get("days_to_depart") or 999) <= 30]
    if piutang_urgent:
        attention.append({"key": "piutang_urgent", "severity": "critical",
                          "label": f"{len(piutang_urgent)} jamaah belum lunas, berangkat ≤30 hari", "goto": "finance"})

    aged_buckets = db.query_all(
        "SELECT "
        "  CASE "
        "    WHEN julianday('now') - julianday(COALESCE(order_date, created_at)) <= 7 THEN '0-7 hari' "
        "    WHEN julianday('now') - julianday(COALESCE(order_date, created_at)) <= 30 THEN '8-30 hari' "
        "    WHEN julianday('now') - julianday(COALESCE(order_date, created_at)) <= 60 THEN '31-60 hari' "
        "    ELSE '60+ hari' END AS bucket, "
        "  COUNT(*) n, "
        "  COALESCE(SUM(COALESCE(total_price,0) - COALESCE(paid_amount,0)), 0) sisa "
        "FROM jamaah "
        "WHERE payment_status IN ('DP', 'Unpaid') AND status NOT IN ('Cancelled', 'Lead - Follow Up') "
        "  AND (COALESCE(total_price,0) - COALESCE(paid_amount,0)) > 0 "
        "GROUP BY bucket ORDER BY MIN(julianday('now') - julianday(COALESCE(order_date, created_at)))"
    )

    expense_pending_list = db.query_all(
        "SELECT er.id, er.ref, er.user_name, er.status, er.created_at, er.note, "
        "  (SELECT COALESCE(SUM(CAST(unit_price_net * qty * (1 + tax_percent/100.0) AS INTEGER)),0) "
        "   FROM expense_lines WHERE report_id = er.id) AS total_amount "
        "FROM expense_reports er WHERE er.status IN ('Submitted', 'Approved') "
        "ORDER BY er.created_at ASC LIMIT 8"
    )
    refund_pending_list = db.query_all(
        "SELECT r.id, r.amount, r.reason, r.requested_at, j.name AS jamaah_name "
        "FROM refund_requests r LEFT JOIN jamaah j ON r.jamaah_id = j.id "
        "WHERE r.status = 'Disetujui' ORDER BY r.approved_at ASC LIMIT 8"
    )
    komisi_pending_list = db.query_all(
        "SELECT c.id, c.amount, c.approved_at, a.name AS agent_name, j.name AS jamaah_name "
        "FROM commission_claims c "
        "LEFT JOIN agents a ON c.agent_id = a.id "
        "LEFT JOIN jamaah j ON c.jamaah_id = j.id "
        "WHERE c.status = 'Disetujui' ORDER BY c.approved_at ASC LIMIT 8"
    )

    return {
        "kpi": kpi,
        "attention": attention,
        "vendor_due_soon": vendor_due_soon,
        "piutang_jamaah": piutang_jamaah,
        "aged_buckets": aged_buckets,
        "expense_pending": expense_pending_list,
        "refund_pending": refund_pending_list,
        "komisi_pending": komisi_pending_list,
    }


@router.get("/api/finance/aged-receivable")
async def finance_aged_receivable(user=Depends(authenticate_token)):
    """Detail piutang jamaah dengan bucket aging + drill-down full list."""
    require_role(user, *_ROLES)

    buckets = db.query_all(
        "SELECT "
        "  CASE "
        "    WHEN julianday('now') - julianday(COALESCE(order_date, created_at)) <= 7 THEN '0-7 hari' "
        "    WHEN julianday('now') - julianday(COALESCE(order_date, created_at)) <= 30 THEN '8-30 hari' "
        "    WHEN julianday('now') - julianday(COALESCE(order_date, created_at)) <= 60 THEN '31-60 hari' "
        "    ELSE '60+ hari' END AS bucket, "
        "  COUNT(*) n, "
        "  COALESCE(SUM(COALESCE(total_price,0) - COALESCE(paid_amount,0)), 0) sisa "
        "FROM jamaah "
        "WHERE payment_status IN ('DP', 'Unpaid') AND status NOT IN ('Cancelled', 'Lead - Follow Up') "
        "  AND (COALESCE(total_price,0) - COALESCE(paid_amount,0)) > 0 "
        "GROUP BY bucket "
        "ORDER BY MIN(julianday('now') - julianday(COALESCE(order_date, created_at)))"
    )

    rows = db.query_all(
        "SELECT j.id, j.name, j.phone, j.package_type, j.payment_status, "
        "       COALESCE(j.total_price,0) total_price, COALESCE(j.paid_amount,0) paid_amount, "
        "       (COALESCE(j.total_price,0) - COALESCE(j.paid_amount,0)) sisa, "
        "       COALESCE(j.order_date, j.created_at) order_date, "
        "       CAST(julianday('now') - julianday(COALESCE(j.order_date, j.created_at)) AS INTEGER) age_days, "
        "       p.departure_date, "
        "       CAST(julianday(p.departure_date) - julianday('now') AS INTEGER) days_to_depart, "
        "       COALESCE(u.name, '-') sales_name "
        "FROM jamaah j LEFT JOIN packages p ON j.package_type = p.name "
        "LEFT JOIN users u ON j.sales_id = u.id "
        "WHERE j.payment_status IN ('DP', 'Unpaid') AND j.status NOT IN ('Cancelled', 'Lead - Follow Up') "
        "  AND (COALESCE(j.total_price,0) - COALESCE(j.paid_amount,0)) > 0 "
        "ORDER BY age_days DESC, sisa DESC"
    )
    total = sum(r["sisa"] or 0 for r in rows)
    return {"buckets": buckets, "rows": rows, "total": total, "count": len(rows)}


@router.get("/api/finance/forecast")
async def finance_forecast(user=Depends(authenticate_token)):
    """Proyeksi arus kas 60 hari ke depan.

    Cash-in: piutang jamaah (DP/Unpaid) - asumsi lunas H-7 sebelum berangkat.
    Cash-out:
    - Vendor payment jatuh tempo (sisa dari total_amount - deposit_amount)
    - Payroll di akhir setiap bulan (SUM base_salary semua non-admin)
    - Refund + komisi approved (segera cair, asumsi H+3)

    Running balance dihitung kumulatif dari saldo kas saat ini.
    """
    require_role(user, *_ROLES)

    HORIZON = 60
    cash_current = db.query_one(
        "SELECT COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE -amount END), 0) c "
        "FROM transactions"
    )["c"] or 0

    events = []

    for v in db.query_all(
        "SELECT v.id, v.vendor_name, v.vendor_type, v.status, v.due_date, "
        "       (COALESCE(v.total_amount,0) - CASE WHEN v.status='Deposit' THEN COALESCE(v.deposit_amount,0) ELSE 0 END) AS sisa, "
        "       p.name AS package_name "
        "FROM vendor_bookings v LEFT JOIN packages p ON v.package_id = p.id "
        "WHERE v.status IN ('Booked', 'Deposit') "
        "  AND v.due_date IS NOT NULL AND v.due_date != '' "
        "  AND date(v.due_date) BETWEEN date('now') AND date('now', ?)",
        (f"+{HORIZON} days",),
    ):
        if v["sisa"] and v["sisa"] > 0:
            events.append({
                "date": v["due_date"], "kind": "cash_out", "category": "vendor",
                "label": f"Vendor: {v['vendor_name'] or v['vendor_type']} - {v['package_name'] or ''}",
                "amount": v["sisa"], "source_id": v["id"],
            })

    plus3 = (datetime.datetime.now() + datetime.timedelta(days=3)).strftime("%Y-%m-%d")
    for r in db.query_all(
        "SELECT r.id, r.amount, j.name AS jamaah_name "
        "FROM refund_requests r LEFT JOIN jamaah j ON r.jamaah_id = j.id "
        "WHERE r.status = 'Disetujui'"
    ):
        events.append({
            "date": plus3, "kind": "cash_out", "category": "refund",
            "label": f"Refund: {r['jamaah_name'] or '-'}",
            "amount": r["amount"] or 0, "source_id": r["id"],
        })
    for c in db.query_all(
        "SELECT c.id, c.amount, a.name AS agent_name "
        "FROM commission_claims c LEFT JOIN agents a ON c.agent_id = a.id "
        "WHERE c.status = 'Disetujui'"
    ):
        events.append({
            "date": plus3, "kind": "cash_out", "category": "komisi",
            "label": f"Komisi: {c['agent_name'] or '-'}",
            "amount": c["amount"] or 0, "source_id": c["id"],
        })

    payroll_estimate = db.query_one(
        "SELECT COALESCE(SUM(base_salary), 0) c FROM users WHERE role != 'admin'"
    )["c"] or 0
    if payroll_estimate > 0:
        today = datetime.datetime.now().date()
        end = today + datetime.timedelta(days=HORIZON)
        m = today.replace(day=1)
        while m <= end:
            next_m_start = (m.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
            last = next_m_start - datetime.timedelta(days=1)
            if today <= last <= end:
                events.append({
                    "date": last.strftime("%Y-%m-%d"), "kind": "cash_out", "category": "payroll",
                    "label": "Payroll bulanan (estimasi)",
                    "amount": payroll_estimate, "source_id": None,
                })
            m = next_m_start

    for j in db.query_all(
        "SELECT j.id, j.name, "
        "       (COALESCE(j.total_price,0) - COALESCE(j.paid_amount,0)) AS sisa, "
        "       p.departure_date, "
        "       date(p.departure_date, '-7 days') AS expected_paid_date "
        "FROM jamaah j LEFT JOIN packages p ON j.package_type = p.name "
        "WHERE j.payment_status IN ('DP', 'Unpaid') AND j.status NOT IN ('Cancelled', 'Lead - Follow Up') "
        "  AND p.departure_date IS NOT NULL "
        "  AND date(p.departure_date, '-7 days') BETWEEN date('now') AND date('now', ?) "
        "  AND (COALESCE(j.total_price,0) - COALESCE(j.paid_amount,0)) > 0",
        (f"+{HORIZON} days",),
    ):
        events.append({
            "date": j["expected_paid_date"], "kind": "cash_in", "category": "piutang",
            "label": f"Piutang: {j['name']} (dep {j['departure_date']})",
            "amount": j["sisa"], "source_id": j["id"],
        })

    events.sort(key=lambda e: e["date"])
    running = cash_current
    min_balance = cash_current
    min_balance_date = datetime.datetime.now().strftime("%Y-%m-%d")
    total_in_30d = 0
    total_out_30d = 0
    day_30 = (datetime.datetime.now() + datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    for e in events:
        if e["kind"] == "cash_in":
            running += e["amount"] or 0
            if e["date"] <= day_30:
                total_in_30d += e["amount"] or 0
        else:
            running -= e["amount"] or 0
            if e["date"] <= day_30:
                total_out_30d += e["amount"] or 0
        e["running_balance"] = running
        if running < min_balance:
            min_balance = running
            min_balance_date = e["date"]

    return {
        "cash_current": cash_current,
        "horizon_days": HORIZON,
        "events": events,
        "summary": {
            "total_in_30d": total_in_30d,
            "total_out_30d": total_out_30d,
            "net_30d": total_in_30d - total_out_30d,
            "min_balance": min_balance,
            "min_balance_date": min_balance_date,
            "final_balance": running,
        },
    }


# ===========================================================================
# Phase 9c: Cashflow negatif alert -- cek forecast, kalau min_balance <= 0
# atau net_30d < 0 -> notif role finance + management. Dedupe per-hari.
# ===========================================================================
@router.post("/api/finance/cashflow-alert/check")
async def cashflow_alert_check(user=Depends(authenticate_token)):
    """Cek proyeksi cashflow H-30. Kirim notif ke role finance + management
    kalau ada risiko (min_balance <= 0, atau net_30d < 0).

    Dipanggil client dari Home Finance saat mount. Aman dipanggil berkali2 --
    dedupe kind unique per-hari.

    Response:
    - alerted: bool -- ada notif yg baru dikirim atau tidak
    - reason: str (kalau tidak alerted)
    - kind: str kind dedupe yang dipakai (untuk troubleshoot)
    - summary: {net_30d, min_balance, min_balance_date}
    """
    require_role(user, *_ROLES)

    # Reuse forecast logic minimal -- panggil ulang query utk snapshot ringkas.
    # (Duplikasi query kecil demi kesederhanaan; kalau perlu, refactor jadi
    # helper `_compute_forecast_summary()` di iterasi berikut.)
    from datetime import datetime as _dt, timedelta as _td
    HORIZON = 60
    cash_current = (db.query_one(
        "SELECT COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE -amount END), 0) c "
        "FROM transactions") or {}).get("c") or 0

    events = []
    for v in db.query_all(
        "SELECT (COALESCE(v.total_amount,0) - CASE WHEN v.status='Deposit' THEN COALESCE(v.deposit_amount,0) ELSE 0 END) sisa, v.due_date "
        "FROM vendor_bookings v WHERE v.status IN ('Booked','Deposit') "
        "AND v.due_date IS NOT NULL AND v.due_date != '' "
        "AND date(v.due_date) BETWEEN date('now') AND date('now', ?)",
        (f"+{HORIZON} days",),
    ) or []:
        if (v["sisa"] or 0) > 0:
            events.append({"date": v["due_date"], "amount": v["sisa"], "kind": "cash_out"})

    plus3 = (_dt.now() + _td(days=3)).strftime("%Y-%m-%d")
    for r in db.query_all(
        "SELECT amount FROM refund_requests WHERE status='Disetujui'"
    ) or []:
        events.append({"date": plus3, "amount": r["amount"] or 0, "kind": "cash_out"})
    for c in db.query_all(
        "SELECT amount FROM commission_claims WHERE status='Disetujui'"
    ) or []:
        events.append({"date": plus3, "amount": c["amount"] or 0, "kind": "cash_out"})

    payroll_estimate = (db.query_one(
        "SELECT COALESCE(SUM(base_salary), 0) c FROM users WHERE role != 'admin'"
    ) or {}).get("c") or 0
    if payroll_estimate > 0:
        today_d = _dt.now().date()
        end = today_d + _td(days=HORIZON)
        m = today_d.replace(day=1)
        while m <= end:
            next_m_start = (m.replace(day=28) + _td(days=4)).replace(day=1)
            last = next_m_start - _td(days=1)
            if today_d <= last <= end:
                events.append({"date": last.strftime("%Y-%m-%d"),
                               "amount": payroll_estimate, "kind": "cash_out"})
            m = next_m_start

    for j in db.query_all(
        "SELECT (COALESCE(j.total_price,0) - COALESCE(j.paid_amount,0)) sisa, "
        "date(p.departure_date, '-7 days') exp_paid "
        "FROM jamaah j LEFT JOIN packages p ON j.package_type = p.name "
        "WHERE j.payment_status IN ('DP','Unpaid') AND j.status NOT IN ('Cancelled','Lead - Follow Up') "
        "AND p.departure_date IS NOT NULL "
        "AND date(p.departure_date,'-7 days') BETWEEN date('now') AND date('now', ?) "
        "AND (COALESCE(j.total_price,0) - COALESCE(j.paid_amount,0)) > 0",
        (f"+{HORIZON} days",),
    ) or []:
        events.append({"date": j["exp_paid"], "amount": j["sisa"], "kind": "cash_in"})

    events.sort(key=lambda e: e["date"])
    running = cash_current
    min_balance = cash_current
    min_balance_date = _dt.now().strftime("%Y-%m-%d")
    total_in_30d = 0
    total_out_30d = 0
    day_30 = (_dt.now() + _td(days=30)).strftime("%Y-%m-%d")
    for e in events:
        if e["kind"] == "cash_in":
            running += e["amount"] or 0
            if e["date"] <= day_30:
                total_in_30d += e["amount"] or 0
        else:
            running -= e["amount"] or 0
            if e["date"] <= day_30:
                total_out_30d += e["amount"] or 0
        if running < min_balance:
            min_balance = running
            min_balance_date = e["date"]

    net_30d = total_in_30d - total_out_30d
    summary = {"net_30d": net_30d, "min_balance": min_balance,
               "min_balance_date": min_balance_date}

    risky = (min_balance <= 0) or (net_30d < 0)
    if not risky:
        return {"alerted": False, "reason": "cashflow_ok", "summary": summary}

    today_key = _dt.now().strftime("%Y%m%d")
    kind = f"cashflow_negatif_{today_key}"
    dup = db.query_one(
        "SELECT 1 x FROM user_notifications WHERE kind = ? "
        "AND date(created_at) = date('now') LIMIT 1", (kind,),
    )
    if dup:
        return {"alerted": False, "reason": "already_notified_today",
                "kind": kind, "summary": summary}

    # Pilih label warning vs critical
    if min_balance <= 0:
        title = "Cashflow KRITIS: saldo bisa habis"
        body = (f"Proyeksi saldo minimum Rp {min_balance:,} pada "
                f"{min_balance_date}. Net H-30 Rp {net_30d:,}. "
                f"Segera cek pembayaran vendor & piutang.").replace(",", ".")
    else:
        title = "Cashflow negatif H-30"
        body = (f"Net proyeksi H-30 Rp {net_30d:,} (out melebihi in). "
                f"Saldo minimum Rp {min_balance:,} pada {min_balance_date}.").replace(",", ".")

    notify_role("finance", kind, title, body, "#page-finance-home")
    notify_role("management", kind, title, body, "#page-finance-home")

    return {"alerted": True, "kind": kind, "summary": summary}


# Phase 8d: Analitik keuangan Dual View -- Committed vs Realized.
# Realized = uang sudah bergerak (dari `transactions`, sama seperti sekarang).
# Committed = sudah disetujui management tapi finance belum cairkan
# (expense_reports.status='Approved' + commission_claims.status='Disetujui'
#  + refund_requests.status='Disetujui'). Bedanya: analitik "berapa yg akan
# segera keluar" jadi bisa dilihat mgmt, bukan cuma "yg sudah keluar".
@router.get("/api/finance/committed-summary")
async def finance_committed_summary(user=Depends(authenticate_token)):
    require_role(user, *_ROLES)

    # Realized (existing behavior): jumlah tx per type.
    realized_in = db.query_one(
        "SELECT COALESCE(SUM(amount), 0) t FROM transactions WHERE type = 'income'"
    )["t"] or 0
    realized_out_total = db.query_one(
        "SELECT COALESCE(SUM(amount), 0) t FROM transactions WHERE type = 'expense'"
    )["t"] or 0

    # Committed expense (sudah disetujui, belum cair):
    # 1. Expense reports Approved (belum Paid). Total = sum(qty * unit_net * (1+tax/100))
    exp_approved_rows = db.query_all(
        "SELECT er.id, "
        "COALESCE(SUM(el.qty * el.unit_price_net * (1.0 + el.tax_percent/100.0)), 0) as gross "
        "FROM expense_reports er LEFT JOIN expense_lines el ON el.report_id = er.id "
        "WHERE er.status = 'Approved' GROUP BY er.id",
        (),
    )
    committed_expense_reports = int(sum(r["gross"] or 0 for r in exp_approved_rows))

    # 2. Commission claims Disetujui (belum Dicairkan).
    committed_commission = db.query_one(
        "SELECT COALESCE(SUM(amount), 0) t FROM commission_claims WHERE status = 'Disetujui'"
    )["t"] or 0

    # 3. Refund requests Disetujui (belum Dicairkan).
    committed_refund = db.query_one(
        "SELECT COALESCE(SUM(amount), 0) t FROM refund_requests WHERE status = 'Disetujui'"
    )["t"] or 0

    committed_expense_total = (
        committed_expense_reports + committed_commission + committed_refund
    )

    return {
        "realized": {
            "income": realized_in,
            "expense": realized_out_total,
            "net": realized_in - realized_out_total,
        },
        "committed_expense": {
            "total": committed_expense_total,
            "breakdown": {
                "expense_report": committed_expense_reports,
                "commission_claim": committed_commission,
                "refund_request": committed_refund,
            },
        },
        # Total realistis "posisi budget" = realized + committed.
        "combined_expense_total": realized_out_total + committed_expense_total,
        "note": "Committed = sudah disetujui management, belum dicairkan finance",
    }
