"""
Router Landing & Utilities:
- Login (POST /api/login): satu-satunya entry unauth; catat last_login_at.
- Dashboard Super (admin/mgmt): agregasi funnel + finance + inventory + agen.
- Tactical Stats: subset agregasi untuk ringkasan operasional.
- Audit Logs: 100 audit entry terbaru.
- Global Search (Cmd+K): jamaah + paket + agen + user (role-scoped untuk sales).

Semua endpoint dulunya duduk di app.py. Pemindahan tidak mengubah kontrak API.
"""
from fastapi import APIRouter, Request

import db
from auth import create_token, verify_password
from deps import (
    Depends,
    HTTPException,
    authenticate_token,
    json_body,
    require_role,
)
from rate_limit import clear_login_failures, is_login_blocked, record_login_failure

router = APIRouter(tags=["dashboard"])


# ===========================================================================
# LOGIN (satu-satunya endpoint tanpa authenticate_token)
# ===========================================================================
@router.post("/api/login")
async def login(request: Request, body: dict = Depends(json_body)):
    # SECURITY: brute-force protection -- 10 gagal per IP dalam 5 menit -> 429.
    # Login sukses langsung clear counter (lihat rate_limit.py).
    client_ip = request.client.host if request.client else "unknown"
    if is_login_blocked(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Terlalu banyak percobaan login. Coba lagi dalam 5 menit.",
        )

    username = body.get("username")
    password = body.get("password")
    user = db.query_one("SELECT * FROM users WHERE username = ?", (username,))
    if not user:
        record_login_failure(client_ip)
        raise HTTPException(status_code=404, detail="User tidak ditemukan")
    if not verify_password(password or "", user["password"]):
        record_login_failure(client_ip)
        raise HTTPException(status_code=401, detail="Password salah")

    clear_login_failures(client_ip)
    # Catat login sukses -- dipakai admin untuk audit user aktif.
    db.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (user["id"],))
    token = create_token(user["id"], user["role"], user["name"])
    return {"token": token, "user": {"id": user["id"], "name": user["name"], "role": user["role"], "photo_url": user["photo_url"]}}


# ===========================================================================
# DASHBOARD & LAPORAN
# ===========================================================================
def _total_piutang():
    """Total tagihan jamaah yang belum lunas (kecuali Cancelled/Lead). Satu source
    of truth -- dipakai dashboard.finance.piutang + tactical.piutang, jangan copas
    query yang sama di banyak tempat (sering out-of-sync antara halaman)."""
    row = db.query_one(
        "SELECT SUM(total_price - paid_amount) as s FROM jamaah "
        "WHERE status NOT IN ('Lead - Follow Up', 'Cancelled') "
        "AND total_price > paid_amount"
    )
    return (row["s"] or 0) if row else 0


@router.get("/api/dashboard/super")
async def dashboard_super(user=Depends(authenticate_token)):
    # SECURITY: dashboard funnel & visa summary all-jamaah. Sales biasa tidak
    # boleh lihat lead/pipeline agen lain. Restrict ke admin/mgmt/ops.
    require_role(user, "admin", "management", "ops")
    funnel = db.query_all("SELECT status, COUNT(*) as count FROM jamaah GROUP BY status")
    visa = db.query_all(
        "SELECT visa_status, COUNT(*) as count FROM jamaah "
        "WHERE status NOT IN ('Lead - Follow Up', 'Cancelled') GROUP BY visa_status"
    )
    low_stock = db.query_all(
        "SELECT item_name, stock FROM inventory WHERE stock <= 20 ORDER BY stock ASC"
    )
    unassigned = db.query_all(
        "SELECT name, departure_date FROM packages "
        "WHERE tour_leader IS NULL OR trim(tour_leader) = '' OR mutawwif IS NULL "
        "OR trim(mutawwif) = '' ORDER BY departure_date DESC"
    )
    inc = db.query_one("SELECT SUM(amount) as s FROM transactions WHERE type = 'income'")
    exp = db.query_one("SELECT SUM(amount) as s FROM transactions WHERE type = 'expense'")
    piutang = _total_piutang()
    recent_exp = db.query_all(
        "SELECT category, amount, description, created_at FROM transactions "
        "WHERE type = 'expense' ORDER BY created_at DESC LIMIT 8"
    )
    procurement = db.query_all(
        "SELECT vendor_name, service_type, total_price, deposit_paid FROM procurement "
        "ORDER BY created_at DESC LIMIT 5"
    )
    top_agents = db.query_all(
        "SELECT a.name, COUNT(j.id) as total FROM agents a JOIN jamaah j ON a.id = j.agent_id "
        "WHERE j.status NOT IN ('Cancelled', 'Lead - Follow Up') GROUP BY a.id "
        "ORDER BY total DESC LIMIT 5"
    )
    bvisa = db.query_one(
        "SELECT COUNT(*) as c FROM jamaah WHERE status IN ('Lunas') AND visa_status = 'Belum Proses' "
        "AND created_at < datetime('now', '-7 days')"
    )
    bpay = db.query_one(
        "SELECT COUNT(*) as c FROM jamaah WHERE status = 'Terdaftar' AND paid_amount = 0 "
        "AND created_at < datetime('now', '-14 days')"
    )
    inc_s = (inc["s"] or 0) if inc else 0
    exp_s = (exp["s"] or 0) if exp else 0
    return {
        "funnel": funnel,
        "visa": visa,
        "lowStock": low_stock,
        "finance": {
            "balance": inc_s - exp_s,
            "piutang": piutang,
            "income": inc_s,
            "expense": exp_s,
        },
        "unassignedPkgs": unassigned,
        "topAgents": top_agents,
        "recentExpenses": recent_exp,
        "procurement": procurement,
        "bottlenecks": {
            "visa": (bvisa["c"] or 0) if bvisa else 0,
            "payment": (bpay["c"] or 0) if bpay else 0,
        },
    }


@router.get("/api/tactical-stats")
async def tactical_stats(user=Depends(authenticate_token)):
    require_role(user, "admin", "management")
    piutang = _total_piutang()
    payroll = db.query_one("SELECT SUM(base_salary) as payroll FROM users")
    readiness = db.query_all(
        "SELECT p.name, p.departure_date, COUNT(j.id) as total_jamaah, "
        "SUM(CASE WHEN j.status = 'Lunas' THEN 1 ELSE 0 END) as lunas_count, "
        "SUM(CASE WHEN j.visa_status = 'Visa Approved' THEN 1 ELSE 0 END) as visa_count "
        "FROM packages p LEFT JOIN jamaah j ON j.package_type = p.name "
        "AND j.status NOT IN ('Lead - Follow Up', 'Cancelled') "
        "GROUP BY p.id ORDER BY p.departure_date DESC LIMIT 4"
    )
    bottleneck = db.query_one(
        "SELECT COUNT(*) as count FROM jamaah WHERE status IN ('Lunas', 'DP Masuk') "
        "AND visa_status = 'Belum Proses' AND created_at < datetime('now', '-7 days')"
    )
    return {
        "piutang": piutang,
        "payroll": (payroll["payroll"] or 0) if payroll else 0,
        "readiness": readiness or [],
        "bottlenecks": bottleneck["count"] if bottleneck else 0,
    }


@router.get("/api/audit-logs")
async def audit_logs(user=Depends(authenticate_token)):
    require_role(user, "admin", "management")
    return db.query_all("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 100", ()) or []


# ===========================================================================
# Phase 14a: Year picker options untuk semua Home dashboard.
# ===========================================================================
@router.get("/api/dashboard/years")
async def dashboard_years(user=Depends(authenticate_token)):
    """Return list tahun yang punya data jamaah (untuk populate dropdown year
    picker di Home Sales/Mgmt). Sort DESC (terbaru dulu).

    Kalau DB kosong / tidak ada order_date, minimal return tahun berjalan
    supaya dropdown tidak empty.
    """
    import datetime as _dt
    rows = db.query_all(
        "SELECT DISTINCT SUBSTR(COALESCE(order_date, created_at), 1, 4) y "
        "FROM jamaah WHERE COALESCE(order_date, created_at) IS NOT NULL "
        "ORDER BY y DESC"
    ) or []
    years = []
    for r in rows:
        y = r["y"]
        try:
            yi = int(y)
            if 2000 <= yi <= 2100:
                years.append(yi)
        except (ValueError, TypeError):
            pass
    current = _dt.datetime.now().year
    if current not in years:
        years.insert(0, current)
    years = sorted(set(years), reverse=True)
    return {"years": years, "current_year": current}


# ===========================================================================
# GLOBAL SEARCH (Cmd+K palette): 5 hasil per kategori, role-scoped untuk sales
# ===========================================================================
@router.get("/api/search")
async def global_search(q: str = "", user=Depends(authenticate_token)):
    """Global search: jamaah + paket + agen + user. Batasi 5 per kategori supaya
    palette tidak kelebihan hasil. Role-gated: sales cuma cari jamaah/paket/agen
    yang dia handle."""
    q = (q or "").strip()
    if len(q) < 2:
        return {"results": []}

    like = f"%{q}%"
    role = user.get("role")
    results = []

    # Jamaah
    jamaah_filter = ""
    jamaah_params = [like, like, like]
    if role == "sales":
        jamaah_filter = " AND sales_id = ?"
        jamaah_params.append(user["id"])
    for j in db.query_all(
        "SELECT id, name, phone, package_type, payment_status, status FROM jamaah "
        f"WHERE (name LIKE ? OR phone LIKE ? OR nik LIKE ?){jamaah_filter} "
        "ORDER BY name ASC LIMIT 5",
        tuple(jamaah_params),
    ):
        results.append({
            "kind": "jamaah",
            "id": j["id"],
            "title": j["name"],
            "subtitle": f"{j['phone'] or '-'} - {j['package_type'] or '-'}",
            "meta": f"{j['payment_status'] or '-'} - {j['status'] or '-'}",
            "goto": "jamaah",
        })

    # Paket
    for p in db.query_all(
        "SELECT id, name, departure_date, quota FROM packages "
        "WHERE name LIKE ? ORDER BY departure_date DESC LIMIT 5",
        (like,),
    ):
        results.append({
            "kind": "paket",
            "id": p["id"],
            "title": p["name"],
            "subtitle": f"Berangkat {p['departure_date'] or '-'}",
            "meta": f"Kuota {p['quota'] or 0}",
            "goto": "packages",
        })

    # Agen
    agent_filter = ""
    agent_params = [like, like]
    if role == "sales":
        agent_filter = " AND handler_cs_id = ?"
        agent_params.append(user["id"])
    for a in db.query_all(
        "SELECT id, name, phone, city FROM agents "
        f"WHERE (name LIKE ? OR phone LIKE ?){agent_filter} "
        "ORDER BY name ASC LIMIT 5",
        tuple(agent_params),
    ):
        results.append({
            "kind": "agen",
            "id": a["id"],
            "title": a["name"],
            "subtitle": f"{a['phone'] or '-'} - {a['city'] or '-'}",
            "meta": "",
            "goto": "agents",
        })

    # User (admin/mgmt only)
    if role in ("admin", "management"):
        for u in db.query_all(
            "SELECT id, name, username, role FROM users "
            "WHERE name LIKE ? OR username LIKE ? "
            "ORDER BY name ASC LIMIT 5",
            (like, like),
        ):
            results.append({
                "kind": "user",
                "id": u["id"],
                "title": u["name"],
                "subtitle": f"@{u['username']}",
                "meta": u["role"],
                "goto": "users",
            })

    return {"results": results, "query": q, "count": len(results)}
