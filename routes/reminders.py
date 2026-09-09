"""
Router Reminder Keberangkatan (Phase 9a).

Tujuan: bantu tim Operasional tidak lupa deadline paket yg akan berangkat:
- H-30: waktunya siapkan dokumen (visa/vaksin) + ingatkan jamaah pelunasan.
- H-7 : waktunya finalisasi manifest + closing kursi (proxy: pelunasan 95%+).

2 endpoint:
- GET  /api/reminders/departures        list paket H-30 + H-7 dgn readiness metrik
- POST /api/reminders/departures/check  cek + kirim notif ke role ops (dedupe per-hari)

Konvensi readiness (data-driven, cocok utk isi DB saat ini):
- visa_ready_pct  = SUM(visa_status='Selesai') / total_jamaah_aktif * 100
- paid_full_pct   = SUM(paid_amount>=total_price) / total_jamaah_aktif * 100
- checklist_pct   = SUM(package_checklist_progress IN ('done','na')) / N templates * 100
- vendor_confirmed_pct = SUM(vendor.status='Confirmed') / total_vendor * 100

Ambang notif (bisa diketatkan nanti):
- H-30: visa_ready_pct  < 60  -> notif "Siapkan visa/vaksin"
- H-7 : paid_full_pct   < 90  -> notif "Manifest belum final (kursi belum lunas)"
- H-7 : checklist_pct   < 100 -> notif "Checklist paket belum tuntas"

Dedupe: kind = 'reminder_h{7|30}_{visa|paid|checklist}_pkg{id}_{yyyymmdd}'.
Notif hanya di-insert sekali per (paket, hari) meski endpoint /check dipanggil
berkali-kali oleh banyak user ops -- cek existence via db.query_one sebelum
memanggil notify_role.
"""
import datetime

from fastapi import APIRouter

import db
from deps import Depends, authenticate_token, require_role
from deps.notifications import notify_role

router = APIRouter(tags=["reminders"])


def _compute_readiness(pkg_id: int, pkg_name: str, total_checklist_items: int) -> dict:
    """Hitung 4 metrik readiness utk 1 paket. Semua % dalam integer 0-100."""
    row = db.query_one(
        "SELECT COUNT(*) c, "
        "  SUM(CASE WHEN visa_status = 'Selesai' THEN 1 ELSE 0 END) visa_ok, "
        "  SUM(CASE WHEN COALESCE(paid_amount,0) >= COALESCE(total_price,0) AND COALESCE(total_price,0) > 0 THEN 1 ELSE 0 END) paid_ok "
        "FROM jamaah WHERE package_type = ? AND status NOT IN ('Cancelled', 'Lead - Follow Up')",
        (pkg_name,),
    ) or {"c": 0, "visa_ok": 0, "paid_ok": 0}
    total = row["c"] or 0
    visa_pct = round((row["visa_ok"] or 0) * 100 / total) if total else 0
    paid_pct = round((row["paid_ok"] or 0) * 100 / total) if total else 0

    cl = db.query_one(
        "SELECT COUNT(*) done FROM package_checklist_progress "
        "WHERE package_id = ? AND status IN ('done','na')", (pkg_id,),
    ) or {"done": 0}
    cl_pct = round((cl["done"] or 0) * 100 / total_checklist_items) if total_checklist_items else 0

    vb = db.query_one(
        "SELECT COUNT(*) total, "
        "  SUM(CASE WHEN status = 'Confirmed' THEN 1 ELSE 0 END) confirmed "
        "FROM vendor_bookings WHERE package_id = ? AND status != 'Cancelled'", (pkg_id,),
    ) or {"total": 0, "confirmed": 0}
    v_total = vb["total"] or 0
    vendor_pct = round((vb["confirmed"] or 0) * 100 / v_total) if v_total else 100

    return {
        "jamaah_count": total,
        "visa_ready_pct": visa_pct,
        "paid_full_pct": paid_pct,
        "checklist_pct": cl_pct,
        "vendor_confirmed_pct": vendor_pct,
    }


def _load_buckets():
    """Return {h7: [...], h30: [...]}. Kandidat = departure_date antara hari ini
    dan H+30. H-7 subset: 0..7 hari, H-30 subset: 8..30 hari."""
    n_tpl = (db.query_one("SELECT COUNT(*) c FROM checklist_templates") or {}).get("c") or 1
    rows = db.query_all(
        "SELECT id, name, departure_date, "
        "  CAST(julianday(departure_date) - julianday('now') AS INTEGER) AS days_until "
        "FROM packages "
        "WHERE departure_date IS NOT NULL "
        "  AND date(departure_date) BETWEEN date('now') AND date('now', '+30 days') "
        "ORDER BY departure_date ASC"
    ) or []
    h7, h30 = [], []
    for r in rows:
        readiness = _compute_readiness(r["id"], r["name"], n_tpl)
        item = {**r, **readiness}
        (h7 if (r["days_until"] or 999) <= 7 else h30).append(item)
    return {"h7": h7, "h30": h30}


@router.get("/api/reminders/departures")
async def list_departures(user=Depends(authenticate_token)):
    """List 2 bucket + readiness metrik. Semua role yg akses Home Ops boleh."""
    require_role(user, "admin", "ops", "management", "finance")
    return _load_buckets()


def _already_notified_today(kind: str) -> bool:
    """True kalau kind ini sudah dipakai insert notif di hari yg sama (hari lokal).
    Cek 1 baris cukup -- notify_role broadcast per user, cukup cek 1 baris manapun."""
    row = db.query_one(
        "SELECT 1 x FROM user_notifications "
        "WHERE kind = ? AND date(created_at) = date('now') LIMIT 1",
        (kind,),
    )
    return bool(row)


@router.post("/api/reminders/departures/check")
async def check_and_notify(user=Depends(authenticate_token)):
    """Dipanggil client dari Home Ops saat mount. Sweep bucket H-7 & H-30,
    kirim notif per paket yg bermasalah -- dedupe per (paket, hari)."""
    require_role(user, "admin", "ops", "management")
    buckets = _load_buckets()
    today = datetime.date.today().strftime("%Y%m%d")
    sent = 0

    for p in buckets["h30"]:
        if (p.get("visa_ready_pct") or 0) < 60:
            kind = f"reminder_h30_visa_pkg{p['id']}_{today}"
            if not _already_notified_today(kind):
                notify_role(
                    "ops", kind,
                    f"H-30: siapkan visa/vaksin -- {p['name']}",
                    f"Visa siap baru {p['visa_ready_pct']}% dari {p['jamaah_count']} jamaah. "
                    f"Berangkat {p['departure_date']} (H-{p['days_until']}).",
                    "#page-ops-home",
                )
                sent += 1

    for p in buckets["h7"]:
        if (p.get("paid_full_pct") or 0) < 90:
            kind = f"reminder_h7_paid_pkg{p['id']}_{today}"
            if not _already_notified_today(kind):
                notify_role(
                    "ops", kind,
                    f"H-7: manifest belum final -- {p['name']}",
                    f"Kursi lunas baru {p['paid_full_pct']}% dari {p['jamaah_count']} jamaah. "
                    f"Berangkat {p['departure_date']} (H-{p['days_until']}).",
                    "#page-ops-home",
                )
                sent += 1
        if (p.get("checklist_pct") or 0) < 100:
            kind = f"reminder_h7_checklist_pkg{p['id']}_{today}"
            if not _already_notified_today(kind):
                notify_role(
                    "ops", kind,
                    f"H-7: checklist paket belum tuntas -- {p['name']}",
                    f"Progress checklist {p['checklist_pct']}%. "
                    f"Berangkat {p['departure_date']} (H-{p['days_until']}).",
                    "#page-ops-home",
                )
                sent += 1

    return {"sent": sent, "buckets": buckets}
