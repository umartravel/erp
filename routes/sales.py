"""
Router Sales (S-A .. S-x): landing dashboard sales role-aware (MY-scoped),
target closing bulanan, leaderboard performa, list follow-up.

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
from deps.notifications import notify_user  # Phase 9b

router = APIRouter(tags=["sales"])


# ===========================================================================
# FOLLOW-UP LEAD (list sederhana untuk halaman Follow-up Lead)
# ===========================================================================
@router.get("/api/followups")
async def followups(user=Depends(authenticate_token)):
    """Daftar lead yang dijadwalkan follow-up (sales: miliknya; admin: semua)."""
    q = (
        "SELECT j.id, j.name, j.phone, j.status, j.package_type, j.next_follow_up, "
        "j.last_contact, j.total_price, j.paid_amount "
        "FROM jamaah j WHERE j.next_follow_up IS NOT NULL AND j.next_follow_up != '' "
        "AND j.status NOT IN ('Cancelled', 'On Trip')"
    )
    params = []
    if user["role"] == "sales":
        q += " AND j.sales_id = ?"
        params.append(user["id"])
    q += " ORDER BY j.next_follow_up ASC"
    return db.query_all(q, tuple(params))


# ===========================================================================
# PERFORMANCE / LEADERBOARD SALES
# ===========================================================================
def _sales_stats(sales_id):
    base = " FROM jamaah WHERE sales_id = ?"
    p = (sales_id,)
    total = db.query_one("SELECT COUNT(*) c" + base, p)["c"]
    converted = db.query_one(
        "SELECT COUNT(*) c" + base + " AND status IN ('Lunas', 'Visa Approved', 'On Trip')", p
    )["c"]
    active = db.query_one(
        "SELECT COUNT(*) c" + base + " AND status NOT IN ('Cancelled', 'Lunas', 'Visa Approved', 'On Trip')", p
    )["c"]
    paid = db.query_one("SELECT COALESCE(SUM(paid_amount), 0) s" + base, p)["s"]
    this_month = db.query_one(
        "SELECT COUNT(*) c" + base + " AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')", p
    )["c"]
    due = db.query_one(
        "SELECT COUNT(*) c" + base + " AND next_follow_up IS NOT NULL AND next_follow_up != '' "
        "AND date(next_follow_up) <= date('now') AND status NOT IN ('Cancelled', 'On Trip')", p
    )["c"]
    agents_recruited = db.query_one(
        "SELECT COUNT(*) c FROM agents WHERE handler_cs_id = ?", (sales_id,)
    )["c"]
    return {
        "total_leads": total,
        "converted": converted,
        "active": active,
        "total_paid": paid or 0,
        "this_month": this_month,
        "due_followups": due,
        "conversion_rate": round(converted * 100.0 / total, 1) if total else 0,
        "agents_recruited": agents_recruited,
    }


@router.get("/api/sales/performance")
async def sales_performance(user=Depends(authenticate_token)):
    if user["role"] == "sales":
        return {"self": {**_sales_stats(user["id"]), "name": user["name"]}}
    if user["role"] == "admin":
        # Leaderboard semua sales
        sales_users = db.query_all("SELECT id, name FROM users WHERE role = 'sales'", ())
        board = [{**_sales_stats(s["id"]), "name": s["name"], "id": s["id"]} for s in sales_users]
        board.sort(key=lambda x: x["converted"], reverse=True)
        return {"leaderboard": board}
    raise HTTPException(status_code=403, detail="Akses Ditolak")


# ===========================================================================
# HOME SALES (dashboard role-aware untuk LINA/TITIN/FARAH -- data MY-scoped)
# ===========================================================================
def _load_target_row(user_id, ym):
    """Ambil target closing + omzet untuk user+bulan. Return None untuk aggregate view."""
    if not user_id:
        return None
    row = db.query_one(
        "SELECT target_closing, target_omzet FROM sales_targets WHERE user_id = ? AND month = ?",
        (user_id, ym),
    )
    return {
        "target_closing": row["target_closing"] if row else 0,
        "target_omzet": row["target_omzet"] if row else 0,
    }


def _compute_attention(followup_due, payment_stale, stale_contact, new_leads):
    """Hitung count aksi urgen hari ini untuk hero widget + tab-title badge."""
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    followup_today = sum(
        1 for r in followup_due
        if r.get("next_follow_up") and str(r["next_follow_up"])[:10] <= today
    )
    return {
        "followup_today": followup_today,
        "payment_stale": len(payment_stale),
        "stale_contact": len(stale_contact),
        "new_leads": new_leads,
        "total": followup_today + len(payment_stale) + len(stale_contact) + new_leads,
    }


@router.get("/api/sales/home")
async def sales_home(user=Depends(authenticate_token)):
    """Data dashboard sales -- MY-scoped: cuma jamaah/lead yang di-handle user login.
    Admin & management dapat scope 'admin' (agregasi semua sales) untuk preview
    tanpa perlu login sebagai sales tertentu."""
    role = user.get("role")
    if role not in ("sales", "admin", "management"):
        raise HTTPException(status_code=403, detail="Halaman Home Sales hanya untuk role sales.")

    if role == "sales":
        sales_filter = "j.sales_id = ?"
        params = (user["id"],)
        scope = "self"
    else:
        # Admin/mgmt: preview aggregate (semua sales), pass sales_id=0 utk noop
        sales_filter = "j.sales_id IS NOT NULL"
        params = ()
        scope = "all_sales"

    # 1. My Pipeline: jamaah aktif (bukan Cancelled)
    pipeline = db.query_one(
        f"SELECT COUNT(*) c FROM jamaah j WHERE {sales_filter} AND j.status != 'Cancelled'",
        params,
    )["c"]

    # 2. My Closing Bulan Ini: pakai order_date supaya akurat (created_at semua =
    # tanggal migrasi historis). Falls back ke created_at kalau order_date NULL.
    ym = datetime.datetime.now().strftime("%Y-%m")
    this_month = db.query_one(
        f"SELECT COUNT(*) c, COALESCE(SUM(j.total_price),0) omzet FROM jamaah j "
        f"WHERE {sales_filter} AND j.status != 'Cancelled' "
        f"AND SUBSTR(COALESCE(j.order_date, j.created_at), 1, 7) = ?",
        params + (ym,),
    )

    # 3. My Piutang
    piutang = db.query_one(
        f"SELECT COALESCE(SUM(j.total_price - j.paid_amount), 0) s FROM jamaah j "
        f"WHERE {sales_filter} AND j.status NOT IN ('Cancelled', 'Lead - Follow Up') "
        f"AND j.total_price > j.paid_amount",
        params,
    )["s"]

    # 4. My Lead Baru (dari pendaftaran publik yang belum di-review admin)
    new_leads = db.query_one(
        "SELECT COUNT(*) c FROM pendaftaran_publik WHERE status = 'Pending' "
        "AND date(created_at) >= date('now', '-7 days')"
    )["c"]

    # 5. Follow-up jatuh tempo (today/overdue) -- MY jamaah
    followup_due = db.query_all(
        f"SELECT j.id, j.name, j.phone, j.status, j.next_follow_up, "
        f"j.last_contact, j.package_type "
        f"FROM jamaah j WHERE {sales_filter} "
        f"AND j.next_follow_up IS NOT NULL AND j.next_follow_up != '' "
        f"AND date(j.next_follow_up) <= date('now') "
        f"AND j.status NOT IN ('Cancelled', 'On Trip', 'Lunas') "
        f"ORDER BY j.next_follow_up ASC LIMIT 30",
        params,
    )

    # 6. Payment Reminder: jamaah DP tapi belum lunas > 14 hari sejak order
    payment_stale = db.query_all(
        f"SELECT j.id, j.name, j.phone, j.total_price, j.paid_amount, "
        f"(j.total_price - j.paid_amount) sisa, j.order_date, j.package_type "
        f"FROM jamaah j WHERE {sales_filter} "
        f"AND j.status NOT IN ('Cancelled', 'Lunas') "
        f"AND j.paid_amount > 0 AND j.total_price > j.paid_amount "
        f"AND j.order_date IS NOT NULL "
        f"AND date(j.order_date) <= date('now', '-14 days') "
        f"ORDER BY j.order_date ASC LIMIT 20",
        params,
    )

    # 7. Leaderboard sales bulan ini (mini) -- semua sales, biar sales tahu posisinya
    leaderboard = db.query_all(
        "SELECT u.id, u.name, COUNT(j.id) closing, COALESCE(SUM(j.total_price),0) omzet "
        "FROM users u LEFT JOIN jamaah j ON j.sales_id = u.id "
        "AND j.status != 'Cancelled' "
        "AND SUBSTR(COALESCE(j.order_date, j.created_at), 1, 7) = ? "
        "WHERE u.role = 'sales' GROUP BY u.id ORDER BY closing DESC",
        (ym,),
    )

    # 8. Paket Terjadwal: 5 paket berangkat ke depan -- quick reference untuk sales
    # supaya bisa langsung push closing tanpa buka Master Paket.
    upcoming_packages = db.query_all(
        "SELECT p.id, p.name, p.departure_date, p.duration, p.quota, "
        "  COALESCE(p.price_quad, p.price) AS price, "
        "  p.hotel_mekkah, p.hotel_madinah, p.airline_depart, "
        "  (SELECT COUNT(*) FROM jamaah j2 WHERE j2.package_type = p.name "
        "   AND j2.status NOT IN ('Cancelled')) AS filled "
        "FROM packages p "
        "WHERE p.departure_date IS NOT NULL AND date(p.departure_date) >= date('now') "
        "ORDER BY p.departure_date ASC LIMIT 5"
    )

    # 9b. Agen yang saya handle (Skenario B): total + top-5 aktif berdasarkan
    # jumlah jamaah yang mereka bawa lifetime (bukan cuma bulan ini) supaya sales
    # tahu partner mana yang paling produktif untuk di-nurture.
    if role == "sales":
        total_my_agents = db.query_one(
            "SELECT COUNT(*) c FROM agents WHERE handler_cs_id = ?", (user["id"],)
        )["c"]
        top_my_agents = db.query_all(
            "SELECT a.id, a.name, a.province, a.city, "
            "  COUNT(CASE WHEN j.status != 'Cancelled' THEN j.id END) total_jamaah, "
            "  MAX(j.order_date) last_order "
            "FROM agents a LEFT JOIN jamaah j ON j.agent_id = a.id "
            "WHERE a.handler_cs_id = ? "
            "GROUP BY a.id ORDER BY total_jamaah DESC, a.name ASC LIMIT 5",
            (user["id"],),
        )
    else:
        # Admin/mgmt preview: aggregate semua sales
        total_my_agents = db.query_one(
            "SELECT COUNT(*) c FROM agents WHERE handler_cs_id IS NOT NULL"
        )["c"]
        top_my_agents = db.query_all(
            "SELECT a.id, a.name, a.province, a.city, "
            "  COUNT(CASE WHEN j.status != 'Cancelled' THEN j.id END) total_jamaah, "
            "  MAX(j.order_date) last_order "
            "FROM agents a LEFT JOIN jamaah j ON j.agent_id = a.id "
            "WHERE a.handler_cs_id IS NOT NULL "
            "GROUP BY a.id ORDER BY total_jamaah DESC, a.name ASC LIMIT 5"
        )

    # 9. Jamaah Perlu Dihubungi (stale contact): MY-scoped jamaah dengan
    # last_contact NULL atau > 3 hari, hanya status active (belum lunas/cancel).
    stale_contact = db.query_all(
        f"SELECT j.id, j.name, j.phone, j.status, j.total_price, j.paid_amount, "
        f"(j.total_price - j.paid_amount) sisa, j.last_contact, j.package_type, "
        f"j.order_date "
        f"FROM jamaah j WHERE {sales_filter} "
        f"AND j.status IN ('Terdaftar', 'DP Masuk', 'Lead - Follow Up') "
        f"AND (j.last_contact IS NULL OR datetime(j.last_contact) < datetime('now', '-3 days')) "
        f"ORDER BY COALESCE(j.last_contact, '1970-01-01') ASC LIMIT 10",
        params,
    )

    return {
        "scope": scope,
        "me": {"id": user["id"], "name": user["name"]},
        "kpi": {
            "pipeline": pipeline,
            "closing_this_month": this_month["c"] if this_month else 0,
            "omzet_this_month": (this_month["omzet"] or 0) if this_month else 0,
            "piutang": piutang or 0,
            "new_leads_7d": new_leads,
        },
        "followup_due": followup_due,
        "payment_stale": payment_stale,
        "leaderboard": leaderboard,
        "upcoming_packages": upcoming_packages,
        "stale_contact": stale_contact,
        "my_agents": {
            "total": total_my_agents,
            "top": top_my_agents,
        },
        "target_this_month": _load_target_row(user["id"] if role == "sales" else None, ym),
        "attention": _compute_attention(followup_due, payment_stale, stale_contact, new_leads),
        "period": ym,
    }


# ===========================================================================
# Phase 9b: SLA Follow-up Lead -- notif ke sales owner ketika ada jamaah
# 'Dihubungi' > 3 hari tanpa update, dipanggil dari Home Sales boot.
# Dedupe per-hari via kind unique.
# ===========================================================================
@router.post("/api/sales/sla-followup/check")
async def check_sla_followup(user=Depends(authenticate_token)):
    """Cek jamaah stale (last_contact NULL/>3d) di scope MY. Kirim notif ke user
    sendiri kalau > 0. Dedupe per-hari -- aman dipanggil setiap Home Sales open."""
    require_role(user, "sales", "admin", "management")
    # Hanya sales yg dapat notif ke diri sendiri; admin/mgmt cukup preview count.
    if user.get("role") != "sales":
        # Untuk role bukan-sales: return count aggregate saja tanpa notif.
        row = db.query_one(
            "SELECT COUNT(*) c FROM jamaah j "
            "WHERE j.sales_id IS NOT NULL "
            "AND j.status IN ('Terdaftar', 'DP Masuk', 'Lead - Follow Up') "
            "AND (j.last_contact IS NULL OR datetime(j.last_contact) < datetime('now', '-3 days'))"
        )
        return {"stale_count": (row or {}).get("c") or 0, "notified": False}

    row = db.query_one(
        "SELECT COUNT(*) c FROM jamaah j WHERE j.sales_id = ? "
        "AND j.status IN ('Terdaftar', 'DP Masuk', 'Lead - Follow Up') "
        "AND (j.last_contact IS NULL OR datetime(j.last_contact) < datetime('now', '-3 days'))",
        (user["id"],),
    )
    count = (row or {}).get("c") or 0
    if count <= 0:
        return {"stale_count": 0, "notified": False}

    today = datetime.datetime.now().strftime("%Y%m%d")
    kind = f"sla_followup_alert_uid{user['id']}_{today}"
    # Dedupe: jangan kirim notif kedua kali di hari yg sama.
    dup = db.query_one(
        "SELECT 1 x FROM user_notifications "
        "WHERE user_id = ? AND kind = ? AND date(created_at) = date('now') LIMIT 1",
        (user["id"], kind),
    )
    if dup:
        return {"stale_count": count, "notified": False, "reason": "already_notified_today"}

    notify_user(
        user["id"], kind,
        f"SLA Follow-up: {count} jamaah perlu dikontak",
        f"Ada {count} lead/jamaah aktif yang belum di-follow up > 3 hari. "
        f"Buka Home Sales -> panel 'Jamaah Perlu Dihubungi'.",
        "#page-sales-home",
    )
    return {"stale_count": count, "notified": True}


# ===========================================================================
# Phase 12a: Lead Scoring / Prioritas Follow-up.
# Score jamaah aktif 0-100 dari 4 faktor: baseline, recency last_contact,
# payment progress, urgency (H-N keberangkatan). Return sorted DESC.
# ===========================================================================
def _score_lead(row) -> tuple[int, dict, str]:
    """Return (score 0-100, factors dict, top_reason string).

    Faktor:
    - baseline:  50 (starting point)
    - recency:   -20 (>7d) .. +20 (<=1d dari last_contact)
    - payment:    -5 (0%) .. +25 (50-99% lunas)
    - urgency:    0 (>60d) .. +30 (H-7 dari keberangkatan)
    """
    now = datetime.datetime.now().date()
    factors = {"baseline": 50, "recency": 0, "payment": 0, "urgency": 0}
    reasons = []

    # 1. Recency: last_contact
    if row.get("last_contact"):
        try:
            lc = datetime.datetime.strptime(
                row["last_contact"][:10], "%Y-%m-%d").date()
            days_no = (now - lc).days
            if days_no <= 1:
                factors["recency"] = 20
                reasons.append("baru dikontak")
            elif days_no <= 3:
                factors["recency"] = 10
            elif days_no <= 7:
                factors["recency"] = 0
            else:
                factors["recency"] = -15
                reasons.append(f"stale {days_no}d")
        except (ValueError, TypeError):
            factors["recency"] = 0
    else:
        factors["recency"] = -20
        reasons.append("belum pernah dikontak")

    # 2. Payment progress
    total = row.get("total_price") or 0
    paid = row.get("paid_amount") or 0
    ratio = (paid / total) if total > 0 else 0
    if ratio >= 1.0:
        factors["payment"] = 5
    elif ratio >= 0.5:
        factors["payment"] = 25
        reasons.append(f"lunas {int(ratio*100)}%")
    elif ratio > 0:
        factors["payment"] = 15
        reasons.append("DP masuk")
    else:
        factors["payment"] = -5

    # 3. Urgency: departure_date
    if row.get("departure_date"):
        try:
            dd = datetime.datetime.strptime(row["departure_date"][:10], "%Y-%m-%d").date()
            days_until = (dd - now).days
            if 0 <= days_until <= 7:
                factors["urgency"] = 30
                reasons.append(f"H-{days_until} berangkat")
            elif days_until <= 30:
                factors["urgency"] = 20
                reasons.append(f"H-{days_until}")
            elif days_until <= 60:
                factors["urgency"] = 10
            elif days_until > 60:
                factors["urgency"] = 0
        except (ValueError, TypeError):
            factors["urgency"] = 0

    raw = sum(factors.values())
    score = max(0, min(100, raw))
    top_reason = " · ".join(reasons[:2]) if reasons else "-"
    return score, factors, top_reason


@router.get("/api/sales/lead-scores")
async def sales_lead_scores(scope: str = "my", limit: int = 20,
                            user=Depends(authenticate_token)):
    """List jamaah aktif dgn score prioritas, sorted DESC.

    Query params:
    - scope: 'my' (default) -> MY-scoped utk sales; admin/mgmt selalu bisa 'all'
    - limit: default 20

    Kandidat = status NOT IN Cancelled/Lunas (Lunas sudah closing, prioritas
    lain). Bisa disesuaikan nanti.
    """
    role = user.get("role")
    if role not in ("sales", "admin", "management"):
        raise HTTPException(status_code=403,
                            detail="Endpoint ini hanya utk sales/admin/mgmt.")

    if role == "sales" and scope != "all":
        where_scope = "j.sales_id = ?"
        params = (user["id"],)
    else:
        where_scope = "j.sales_id IS NOT NULL"
        params = ()

    rows = db.query_all(
        f"SELECT j.id, j.name, j.phone, j.package_type, j.status, "
        f"       j.total_price, j.paid_amount, j.last_contact, "
        f"       p.departure_date "
        f"FROM jamaah j LEFT JOIN packages p ON p.name = j.package_type "
        f"WHERE {where_scope} "
        f"  AND j.status NOT IN ('Cancelled', 'Lunas') "
        f"ORDER BY j.last_contact IS NULL DESC, j.last_contact ASC "
        f"LIMIT 200",  # pool utk scoring; potong lagi ke limit setelah sort
        params,
    ) or []

    scored = []
    for r in rows:
        d = dict(r)
        score, factors, top_reason = _score_lead(d)
        d["score"] = score
        d["factors"] = factors
        d["top_reason"] = top_reason
        scored.append(d)

    scored.sort(key=lambda x: x["score"], reverse=True)
    return {
        "scope": "self" if role == "sales" else "all",
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "leads": scored[: max(1, min(limit, 100))],
        "total_candidates": len(scored),
    }


# ===========================================================================
# TARGET SALES (admin/management set, sales lihat sendiri)
# ===========================================================================
@router.get("/api/sales/targets/me")
async def sales_target_me(month: str | None = None, user=Depends(authenticate_token)):
    require_role(user, "sales", "admin", "management")
    ym = month or datetime.datetime.now().strftime("%Y-%m")
    return _load_target_row(user["id"], ym) or {"target_closing": 0, "target_omzet": 0}


@router.get("/api/sales/targets")
async def sales_targets_list(month: str | None = None, user=Depends(authenticate_token)):
    require_role(user, "admin", "management")
    ym = month or datetime.datetime.now().strftime("%Y-%m")
    rows = db.query_all(
        "SELECT u.id AS user_id, u.name, "
        "  COALESCE(t.target_closing, 0) AS target_closing, "
        "  COALESCE(t.target_omzet, 0) AS target_omzet, "
        "  t.updated_at, t.set_by, "
        "  COUNT(CASE WHEN j.status != 'Cancelled' "
        "    AND SUBSTR(COALESCE(j.order_date, j.created_at), 1, 7) = ? THEN j.id END) AS actual_closing, "
        "  COALESCE(SUM(CASE WHEN j.status != 'Cancelled' "
        "    AND SUBSTR(COALESCE(j.order_date, j.created_at), 1, 7) = ? THEN j.total_price END), 0) AS actual_omzet "
        "FROM users u "
        "LEFT JOIN sales_targets t ON t.user_id = u.id AND t.month = ? "
        "LEFT JOIN jamaah j ON j.sales_id = u.id "
        "WHERE u.role = 'sales' GROUP BY u.id ORDER BY u.name",
        (ym, ym, ym),
    )
    return {"month": ym, "rows": rows}


@router.post("/api/sales/targets")
async def sales_target_upsert(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "management")
    user_id = body.get("user_id")
    month = body.get("month")
    target_closing = int(body.get("target_closing") or 0)
    target_omzet = int(body.get("target_omzet") or 0)
    if not user_id or not month:
        raise HTTPException(status_code=400, detail="user_id & month wajib diisi.")
    urow = db.query_one("SELECT role FROM users WHERE id = ?", (user_id,))
    if not urow or urow["role"] != "sales":
        raise HTTPException(status_code=400, detail="user_id bukan sales.")
    db.execute(
        "INSERT INTO sales_targets (user_id, month, target_closing, target_omzet, set_by) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, month) DO UPDATE SET "
        "  target_closing = excluded.target_closing, "
        "  target_omzet = excluded.target_omzet, "
        "  set_by = excluded.set_by, "
        "  updated_at = CURRENT_TIMESTAMP",
        (user_id, month, target_closing, target_omzet, user["name"]),
    )
    log_action(user, "SALES_TARGET_SET", f"user={user_id} {month} closing={target_closing} omzet={target_omzet}")
    notify("data_updated", "sales_target")
    return {"message": "Target sales tersimpan."}
