"""
Router Marketing Analytics: 3 endpoint agregasi lintas (jamaah + agents + users +
packages) untuk dashboard marketing. Data 100% dibaca ulang setiap request --
tabel operasional adalah sumber kebenaran, tidak ada tabel marketing terpisah.
"""
from fastapi import APIRouter

import db
from deps import (
    Depends,
    HTTPException,
    authenticate_token,
)

router = APIRouter(tags=["marketing"])

_MARKETING_ROLES = ("admin", "management", "sales")


def _require_marketing_access(user: dict):
    if user.get("role") not in _MARKETING_ROLES:
        raise HTTPException(status_code=403, detail="Halaman ini hanya untuk Admin, Management, dan Sales.")


_MKT_SELECT = """
    SELECT
        j.id, j.name, j.orderer_name, j.external_id,
        j.province, j.city, j.package_type,
        j.total_price, j.paid_amount,
        j.payment_status, j.lead_source, j.created_at, j.order_date,
        j.gender, j.age, j.education, j.job,
        j.agent_id, j.sales_id,
        a.name AS agent_name, a.legacy_code AS agent_code,
        a.province AS agent_province, a.city AS agent_city,
        u.name AS sales_name,
        p.departure_date AS package_departure_date
    FROM jamaah j
    LEFT JOIN agents a ON j.agent_id = a.id
    LEFT JOIN users u ON j.sales_id = u.id
    LEFT JOIN packages p ON j.package_type = p.name
"""


def _age_bucket(age):
    if age is None:
        return None
    try:
        a = int(age)
    except (ValueError, TypeError):
        return None
    if a < 30: return "<30"
    if a < 40: return "30-39"
    if a < 50: return "40-49"
    if a < 60: return "50-59"
    return "60+"


_AGE_BUCKET_ORDER = ["<30", "30-39", "40-49", "50-59", "60+"]


def _txn_month(r):
    """Bulan transaksi (YYYY-MM). Prefer order_date real dari CSV; fallback ke
    package.departure_date; return None kalau dua-duanya kosong."""
    for key in ("order_date", "package_departure_date"):
        s = r.get(key) or ""
        if len(s) >= 7:
            return s[:7]
    return None


def _split_lead_source(src):
    """'ONLINE/FACEBOOK' -> ('ONLINE','FACEBOOK'); 'OFFLINE' -> ('OFFLINE', None)."""
    if not src:
        return (None, None)
    parts = str(src).split("/", 1)
    ch = parts[0].strip().upper() if parts[0] else None
    sub = parts[1].strip().upper() if len(parts) > 1 and parts[1].strip() else None
    return (ch, sub)


def _statpay_bucket(pay_status):
    """Normalisasi jamaah.payment_status ('Lunas'/'DP'/'Unpaid') ke bucket dashboard."""
    return "LUNAS" if (pay_status or "").upper() == "LUNAS" else "BELUM LUNAS"


@router.get("/api/marketing/summary")
async def marketing_summary(
    period: str | None = None,          # YYYY-MM dari packages.departure_date
    paket: str | None = None,           # jamaah.package_type
    admin: str | None = None,           # nama sales (LINA/TITIN/FARAH)
    channel: str | None = None,         # ONLINE/OFFLINE (dari lead_source)
    statpay: str | None = None,         # LUNAS/BELUM LUNAS
    user=Depends(authenticate_token),
):
    _require_marketing_access(user)
    rows = db.query_all(_MKT_SELECT)

    def _match(r):
        if paket and (r.get("package_type") or "") != paket:
            return False
        if admin and (r.get("sales_name") or "") != admin:
            return False
        if channel:
            ch, _sub = _split_lead_source(r.get("lead_source"))
            if (ch or "") != channel:
                return False
        if statpay and _statpay_bucket(r.get("payment_status")) != statpay:
            return False
        if period:
            if _txn_month(r) != period:
                return False
        return True

    filtered = [r for r in rows if _match(r)]

    total_jamaah = len(filtered)
    total_omzet = sum((r.get("total_price") or 0) for r in filtered)
    total_dp = sum((r.get("paid_amount") or 0) for r in filtered)
    total_kurang = sum(
        max(0, (r.get("total_price") or 0) - (r.get("paid_amount") or 0)) for r in filtered
    )
    via_agen = sum(1 for r in filtered if r.get("agent_id"))
    via_direct = total_jamaah - via_agen

    def _bucket(rows_, key_fn, sum_fn=None):
        out = {}
        for r in rows_:
            k = key_fn(r) or "(kosong)"
            if sum_fn:
                out[k] = out.get(k, 0) + sum_fn(r)
            else:
                out[k] = out.get(k, 0) + 1
        return sorted(out.items(), key=lambda kv: -kv[1])

    # 1. Peta jamaah (per kab/kota)
    map_jamaah = {}
    for r in filtered:
        prov = (r.get("province") or "").strip()
        kab = (r.get("city") or "").strip() or prov
        key = f"{kab}||{prov}"
        if key not in map_jamaah:
            map_jamaah[key] = {"kab_kota": kab, "provinsi": prov, "count": 0, "omzet": 0}
        map_jamaah[key]["count"] += 1
        map_jamaah[key]["omzet"] += r.get("total_price") or 0
    map_jamaah_list = sorted(map_jamaah.values(), key=lambda x: -x["count"])

    # 2. Tren bulanan -- pakai jamaah.order_date (tanggal closing asli dari CSV),
    # fallback packages.departure_date kalau order_date kosong.
    tren = {}
    for r in filtered:
        ym = _txn_month(r)
        if ym:
            tren[ym] = tren.get(ym, 0) + 1
    tren_sorted = sorted(tren.items())

    # 3. Leaderboard admin marketing (sales user)
    leaderboard_admin = _bucket(filtered, lambda r: r.get("sales_name"))

    # 4. Distribusi paket (top-10)
    dist_paket = _bucket(filtered, lambda r: r.get("package_type"))[:10]

    # 5. Channel + sub-channel (dari lead_source "CHANNEL/SUB")
    channel_dict, sub_dict = {}, {}
    for r in filtered:
        ch, sub = _split_lead_source(r.get("lead_source"))
        ch = ch or "(kosong)"
        channel_dict[ch] = channel_dict.get(ch, 0) + 1
        if sub:
            sub_dict[sub] = sub_dict.get(sub, 0) + 1
    channel_bucket = sorted(channel_dict.items(), key=lambda kv: -kv[1])
    subchannel_bucket = sorted(sub_dict.items(), key=lambda kv: -kv[1])[:10]

    # 6. Peta AGEN (agregasi via alamat agent, bukan jamaah)
    agen_rows = [r for r in filtered if r.get("agent_id")]
    map_agen = {}
    for r in agen_rows:
        prov = (r.get("agent_province") or "").strip()
        kab = (r.get("agent_city") or "").strip() or prov
        if not kab:
            continue
        key = f"{kab}||{prov}"
        if key not in map_agen:
            map_agen[key] = {"kab_kota": kab, "provinsi": prov, "count": 0, "omzet": 0}
        map_agen[key]["count"] += 1
        map_agen[key]["omzet"] += r.get("total_price") or 0
    map_agen_list = sorted(map_agen.values(), key=lambda x: -x["count"])

    # 7. Leaderboard agen (top-10)
    leaderboard_agen = _bucket(
        agen_rows, lambda r: r.get("agent_name") or "(tanpa nama)"
    )[:10]

    # 8. Distribusi paket per agen (top-5)
    paket_agen = _bucket(agen_rows, lambda r: r.get("package_type"))[:5]

    # 9. Demografi jamaah (gender, age, education, job).
    demo_gender = _bucket(filtered, lambda r: r.get("gender"))
    age_dict = {b: 0 for b in _AGE_BUCKET_ORDER}
    age_null = 0
    for r in filtered:
        b = _age_bucket(r.get("age"))
        if b is None:
            age_null += 1
        else:
            age_dict[b] += 1
    demo_age = [{"name": b, "count": age_dict[b]} for b in _AGE_BUCKET_ORDER]
    if age_null:
        demo_age.append({"name": "(kosong)", "count": age_null})
    demo_education = _bucket(filtered, lambda r: r.get("education"))[:8]
    demo_job = _bucket(filtered, lambda r: r.get("job"))[:8]

    return {
        "kpi": {
            "total_jamaah": total_jamaah,
            "total_omzet": total_omzet,
            "total_dp": total_dp,
            "total_kurang": total_kurang,
            "via_agen": via_agen,
            "via_direct": via_direct,
            "agen_ratio": round((via_agen / total_jamaah * 100), 1) if total_jamaah else 0,
        },
        "map_jamaah": map_jamaah_list,
        "tren": [{"period": k, "count": v} for k, v in tren_sorted],
        "leaderboard_admin": [{"name": k, "count": v} for k, v in leaderboard_admin],
        "dist_paket": [{"name": k, "count": v} for k, v in dist_paket],
        "channel": [{"name": k, "count": v} for k, v in channel_bucket],
        "subchannel": [{"name": k, "count": v} for k, v in subchannel_bucket],
        "map_agen": map_agen_list,
        "leaderboard_agen": [{"name": k, "count": v} for k, v in leaderboard_agen],
        "paket_agen": [{"name": k, "count": v} for k, v in paket_agen],
        "demo_gender": [{"name": k, "count": v} for k, v in demo_gender],
        "demo_age": demo_age,
        "demo_education": [{"name": k, "count": v} for k, v in demo_education],
        "demo_job": [{"name": k, "count": v} for k, v in demo_job],
    }


@router.get("/api/marketing/filters")
async def marketing_filters(user=Depends(authenticate_token)):
    _require_marketing_access(user)
    rows = db.query_all(_MKT_SELECT)
    paket = sorted({(r.get("package_type") or "").strip() for r in rows if r.get("package_type")})
    admin_ = sorted({(r.get("sales_name") or "").strip() for r in rows if r.get("sales_name")})
    channels, periods = set(), set()
    for r in rows:
        ch, _s = _split_lead_source(r.get("lead_source"))
        if ch:
            channels.add(ch)
        ym = _txn_month(r)
        if ym:
            periods.add(ym)
    return {
        "period": sorted(periods),
        "paket": paket,
        "admin": admin_,
        "channel": sorted(channels),
        "statpay": ["LUNAS", "BELUM LUNAS"],
    }


@router.get("/api/marketing/rows")
async def marketing_rows(
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
    user=Depends(authenticate_token),
):
    _require_marketing_access(user)
    limit = max(1, min(200, limit))
    offset = max(0, offset)
    if q:
        like = f"%{q.strip().upper()}%"
        where = (
            "WHERE UPPER(j.name) LIKE ? OR UPPER(COALESCE(j.orderer_name,'')) LIKE ? "
            "OR UPPER(COALESCE(j.external_id,'')) LIKE ? "
            "OR UPPER(COALESCE(j.province,'')) LIKE ? OR UPPER(COALESCE(j.city,'')) LIKE ?"
        )
        params = (like, like, like, like, like)
        total_row = db.query_one(f"SELECT COUNT(*) as c FROM jamaah j {where}", params)
        rows = db.query_all(
            _MKT_SELECT + f" {where} ORDER BY j.id DESC LIMIT ? OFFSET ?",
            params + (limit, offset),
        )
    else:
        total_row = db.query_one("SELECT COUNT(*) as c FROM jamaah")
        rows = db.query_all(
            _MKT_SELECT + " ORDER BY j.id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
    return {"total": total_row["c"] if total_row else 0, "rows": rows}
