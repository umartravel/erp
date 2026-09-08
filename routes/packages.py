"""
Router Master Data Paket (P-A..P-x): CRUD paket + fasilitas tambahan
(package_extras) + assignment staff lapangan + pengelompokan kamar + PDF
Manifest/Roomlist/Absensi.

Semua endpoint dulunya duduk di app.py. Pemindahan tidak mengubah kontrak API.
"""
import os

from fastapi import APIRouter
from fastapi.responses import Response

import db
from auth import authenticate_file_token
from deps import (
    Depends,
    HTTPException,
    PUBLIC_DIR,
    authenticate_token,
    get_setting,
    json_body,
    log_action,
    notify,
    require_role,
)
from jamaah_docs_pdf import auto_group_rooms, build_absensi_pdf, build_manifest_pdf, build_roomlist_pdf

router = APIRouter(tags=["packages"])


@router.get("/api/packages")
async def packages_list(user=Depends(authenticate_token)):
    packages = db.query_all(
        "SELECT p.*, (SELECT COUNT(*) FROM jamaah WHERE package_type = p.name "
        "AND status NOT IN ('Cancelled')) as filled FROM packages p ORDER BY p.departure_date ASC",
        (),
    )
    # Phase 6j: agregasi BOQ per paket -- indicator status BOQ di UI Master Paket
    # supaya user lihat sekilas paket mana yang sudah ada BOQ Approved (siap
    # register), yang masih Draft (butuh submit), atau yang belum ada BOQ.
    boq_by_pkg = {}
    for r in db.query_all(
        "SELECT package_id, status, COUNT(*) AS n FROM package_boq "
        "WHERE package_id IS NOT NULL GROUP BY package_id, status",
        (),
    ):
        agg = boq_by_pkg.setdefault(r["package_id"], {"total": 0, "approved": 0, "draft": 0, "pending": 0, "rejected": 0})
        n = r["n"]
        agg["total"] += n
        st = (r["status"] or "").lower()
        if st == "approved":
            agg["approved"] += n
        elif st == "draft":
            agg["draft"] += n
        elif "pending" in st:
            agg["pending"] += n
        elif st == "rejected":
            agg["rejected"] += n

    for p in packages:
        p["extras"] = db.query_all(
            "SELECT id, category, label as value FROM package_extras WHERE package_id = ? ORDER BY id ASC",
            (p["id"],),
        )
        p["boq_summary"] = boq_by_pkg.get(p["id"], {"total": 0, "approved": 0, "draft": 0, "pending": 0, "rejected": 0})
    return packages


PACKAGE_EXTRA_CATEGORIES = ("country", "citytour", "extra")


def _save_package_extras(pid, extras):
    # Replace-all -- daftar fasilitas tambahan tidak punya id yang ditelusuri lintas
    # sesi edit, jadi cara paling sederhana & aman adalah hapus semua lalu tulis ulang.
    db.execute("DELETE FROM package_extras WHERE package_id = ?", (pid,))
    for ex in extras or []:
        category = ex.get("category") if ex.get("category") in PACKAGE_EXTRA_CATEGORIES else "extra"
        value = (ex.get("value") or "").strip()
        if not value:
            continue
        db.execute(
            "INSERT INTO package_extras (package_id, category, label) VALUES (?, ?, ?)",
            (pid, category, value),
        )


@router.post("/api/packages")
async def packages_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin")
    g = body.get
    route_type = g("route_type") or "Direct"
    last_id, _ = db.execute(
        "INSERT INTO packages (name, price, departure_date, duration, quota, "
        "price_quad, price_triple, price_double, default_commission_fee, "
        "hotel_mekkah, hotel_madinah, route_type, transit_city, transit_airport, "
        "airline_depart, airline_return, airline_transit, return_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            g("name"), g("price"), g("departure_date"), g("duration"),
            int(g("quota")) if g("quota") else 45, g("price_quad"), g("price_triple"), g("price_double"),
            int(g("default_commission_fee")) if g("default_commission_fee") else 0,
            g("hotel_mekkah"), g("hotel_madinah"),
            route_type, g("transit_city") if route_type == "Transit" else None,
            g("transit_airport") if route_type == "Transit" else None,
            g("airline_depart"), g("airline_return"),
            g("airline_transit") if route_type == "Transit" else None,
            g("return_date"),
        ),
    )
    _save_package_extras(last_id, g("extras"))
    log_action(user, "CREATE_PACKAGE", f"Menambah paket baru: {g('name')}")
    notify("data_updated", "package")
    return {"id": last_id, "message": "Paket berhasil ditambahkan."}


@router.put("/api/packages/{pid}")
async def packages_update(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin")
    g = body.get
    pkg = db.query_one("SELECT * FROM packages WHERE id = ?", (pid,))
    if not pkg:
        raise HTTPException(status_code=404, detail="Paket tidak ditemukan")
    route_type = g("route_type") or "Direct"
    # `price` (harga umum lama, sebelum ada rincian per tipe kamar) sengaja TIDAK disentuh --
    # tidak ada field untuk itu di form Edit Paket, jadi kalau ikut ditulis ulang di sini akan
    # selalu jadi NULL (bug yang pernah terjadi & sudah diperbaiki: lihat commit ini).
    db.execute(
        "UPDATE packages SET name = ?, departure_date = ?, duration = ?, quota = ?, "
        "price_quad = ?, price_triple = ?, price_double = ?, default_commission_fee = ?, "
        "hotel_mekkah = ?, hotel_madinah = ?, route_type = ?, transit_city = ?, transit_airport = ?, "
        "airline_depart = ?, airline_return = ?, airline_transit = ?, return_date = ? WHERE id = ?",
        (
            g("name"), g("departure_date"), g("duration"),
            int(g("quota")) if g("quota") else 45, g("price_quad"), g("price_triple"), g("price_double"),
            int(g("default_commission_fee")) if g("default_commission_fee") else 0,
            g("hotel_mekkah"), g("hotel_madinah"),
            route_type, g("transit_city") if route_type == "Transit" else None,
            g("transit_airport") if route_type == "Transit" else None,
            g("airline_depart"), g("airline_return"),
            g("airline_transit") if route_type == "Transit" else None,
            g("return_date"), pid,
        ),
    )
    _save_package_extras(pid, g("extras"))
    log_action(user, "UPDATE_PACKAGE", f"Mengubah data paket: {pkg['name']}")
    notify("data_updated", "package")
    return {"message": "Paket berhasil diperbarui."}


@router.delete("/api/packages/{pid}")
async def packages_delete(pid: int, user=Depends(authenticate_token)):
    require_role(user, "admin")
    pkg = db.query_one("SELECT name FROM packages WHERE id = ?", (pid,))
    db.execute("DELETE FROM packages WHERE id = ?", (pid,))
    if pkg:
        log_action(user, "DELETE_PACKAGE", f"Menghapus paket: {pkg['name']}")
    notify("data_updated", "package")
    return {"message": "Paket dihapus."}


@router.put("/api/packages/{pid}/staff")
async def packages_staff(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    db.execute(
        "UPDATE packages SET tour_leader = ?, mutawwif = ? WHERE id = ?",
        (body.get("tour_leader"), body.get("mutawwif"), pid),
    )
    log_action(user, "UPDATE_PACKAGE_STAFF", f"Menugaskan petugas lapangan paket ID {pid}")
    notify("data_updated", "package")
    return {"message": "Petugas lapangan berhasil ditugaskan."}


# ===========================================================================
# DOKUMEN OPERASIONAL PER PAKET: Manifest, Roomlist, Absensi (PDF)
# ===========================================================================
def _package_jamaah_rows(pid):
    pkg = db.query_one("SELECT * FROM packages WHERE id = ?", (pid,))
    if not pkg:
        raise HTTPException(status_code=404, detail="Paket tidak ditemukan")
    rows = db.query_all(
        "SELECT * FROM jamaah WHERE package_type = ? AND status NOT IN ('Cancelled') ORDER BY name ASC",
        (pkg["name"],),
    )
    return pkg, rows


def _company_and_logo():
    company = {
        "legal_name": get_setting("company_legal_name", "Umar Travel"),
        "address": get_setting("company_address", ""),
        "email": get_setting("company_email", ""),
        "website": get_setting("company_website", ""),
    }
    logo_path = None
    logo_url = get_setting("logo_url")
    if logo_url:
        candidate = os.path.join(PUBLIC_DIR, logo_url.lstrip("/"))
        if os.path.isfile(candidate):
            logo_path = candidate
    return company, logo_path


@router.get("/api/packages/{pid}/room-groups")
async def package_room_groups(pid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    _pkg, rows = _package_jamaah_rows(pid)
    suggested = auto_group_rooms(rows)
    result = []
    for r in rows:
        r = dict(r)
        r["suggested_room_number"] = suggested.get(r["id"])
        result.append(r)
    return result


@router.put("/api/packages/{pid}/room-groups")
async def package_room_groups_save(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    for a in body.get("assignments") or []:
        db.execute(
            "UPDATE jamaah SET room_number = ? WHERE id = ?",
            (a.get("room_number"), a.get("jamaah_id")),
        )
    log_action(user, "UPDATE_ROOM_GROUPS", f"Memperbarui pengelompokan kamar paket ID {pid}")
    notify("data_updated", "jamaah")
    return {"message": "Pengelompokan kamar berhasil disimpan."}


@router.get("/api/packages/{pid}/manifest-pdf")
async def package_manifest_pdf(pid: int, user=Depends(authenticate_file_token)):
    require_role(user, "admin", "ops")
    pkg, rows = _package_jamaah_rows(pid)
    company, logo_path = _company_and_logo()
    pdf_bytes = build_manifest_pdf(pkg, rows, company=company, logo_path=logo_path)
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=Manifest-{pkg['name'].replace(' ', '-')}.pdf"},
    )


@router.get("/api/packages/{pid}/roomlist-pdf")
async def package_roomlist_pdf(pid: int, user=Depends(authenticate_file_token)):
    require_role(user, "admin", "ops")
    pkg, rows = _package_jamaah_rows(pid)
    company, logo_path = _company_and_logo()
    pdf_bytes = build_roomlist_pdf(pkg, rows, company=company, logo_path=logo_path)
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=Roomlist-{pkg['name'].replace(' ', '-')}.pdf"},
    )


@router.get("/api/packages/{pid}/absensi-pdf")
async def package_absensi_pdf(pid: int, user=Depends(authenticate_file_token)):
    require_role(user, "admin", "ops")
    pkg, rows = _package_jamaah_rows(pid)
    company, logo_path = _company_and_logo()
    pdf_bytes = build_absensi_pdf(pkg, rows, company=company, logo_path=logo_path)
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=Absensi-{pkg['name'].replace(' ', '-')}.pdf"},
    )
