"""
Router Operasional -- Feedback Jamaah + Debrief Post-Trip.
- GET  /api/packages/{pid}/feedback   list feedback + summary avg rating
- POST /api/feedback                  upsert per (jamaah, paket)
- GET  /api/packages/{pid}/debriefs   list debrief per kategori
- POST /api/packages/{pid}/debriefs   upsert satu kategori

DEBRIEF_CATEGORIES: hotel_mekkah/hotel_madinah/airline/bus/muthawif/overall.
Rating 1-5, semua nullable. Feedback jamaah unik per (jamaah_id, package_id).
"""
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

router = APIRouter(tags=["ops-feedback"])

DEBRIEF_CATEGORIES = ("hotel_mekkah", "hotel_madinah", "airline", "bus", "muthawif", "overall")


@router.get("/api/packages/{pid}/feedback")
async def package_feedback_list(pid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "ops", "management", "sales")
    pkg = db.query_one("SELECT id, name, departure_date FROM packages WHERE id = ?", (pid,))
    if not pkg:
        raise HTTPException(status_code=404, detail="Paket tidak ditemukan.")
    rows = db.query_all(
        "SELECT j.id, j.name, j.phone, "
        "  f.id AS feedback_id, f.rating, f.testimonial, f.complaint, f.would_recommend, "
        "  f.source, f.created_by, f.created_at "
        "FROM jamaah j "
        "LEFT JOIN jamaah_feedback f ON f.jamaah_id = j.id AND f.package_id = ? "
        "WHERE j.package_type = ? AND j.status NOT IN ('Cancelled') "
        "ORDER BY j.name ASC",
        (pid, pkg["name"]),
    )
    filled = [r for r in rows if r["feedback_id"]]
    ratings = [r["rating"] for r in filled if r["rating"] is not None]
    recommend = sum(1 for r in filled if r["would_recommend"])
    return {
        "package": pkg,
        "jamaah": rows,
        "summary": {
            "total": len(rows),
            "filled": len(filled),
            "avg_rating": round(sum(ratings)/len(ratings), 2) if ratings else None,
            "recommend_pct": round((recommend / len(filled)) * 100) if filled else 0,
        },
    }


@router.post("/api/feedback")
async def feedback_upsert(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops", "management", "sales")
    jid = body.get("jamaah_id")
    pid = body.get("package_id")
    if not jid or not pid:
        raise HTTPException(status_code=400, detail="jamaah_id & package_id wajib.")
    rating = body.get("rating")
    if rating is not None:
        rating = int(rating)
        if rating < 1 or rating > 5:
            raise HTTPException(status_code=400, detail="rating harus 1-5.")
    db.execute(
        "INSERT INTO jamaah_feedback (jamaah_id, package_id, rating, testimonial, complaint, "
        "would_recommend, source, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(jamaah_id, package_id) DO UPDATE SET "
        "  rating = excluded.rating, "
        "  testimonial = excluded.testimonial, "
        "  complaint = excluded.complaint, "
        "  would_recommend = excluded.would_recommend, "
        "  source = excluded.source, "
        "  created_by = excluded.created_by",
        (
            jid, pid, rating,
            body.get("testimonial"), body.get("complaint"),
            1 if body.get("would_recommend") else 0,
            body.get("source") or "manual",
            user["name"],
        ),
    )
    log_action(user, "JAMAAH_FEEDBACK", f"pkg={pid} jamaah={jid} rating={rating}")
    notify("data_updated", "feedback")
    return {"message": "Feedback tersimpan."}


@router.get("/api/packages/{pid}/debriefs")
async def package_debrief_list(pid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "ops", "management")
    pkg = db.query_one("SELECT id, name, departure_date FROM packages WHERE id = ?", (pid,))
    if not pkg:
        raise HTTPException(status_code=404, detail="Paket tidak ditemukan.")
    existing = db.query_all(
        "SELECT category, rating, notes, created_by, created_at, updated_at "
        "FROM package_debriefs WHERE package_id = ?",
        (pid,),
    )
    existing_map = {r["category"]: r for r in existing}
    items = []
    for cat in DEBRIEF_CATEGORIES:
        r = existing_map.get(cat)
        items.append({
            "category": cat,
            "rating": r["rating"] if r else None,
            "notes": r["notes"] if r else "",
            "created_by": r["created_by"] if r else None,
            "updated_at": r["updated_at"] if r else None,
        })
    ratings = [i["rating"] for i in items if i["rating"] is not None]
    filled = len(ratings)
    return {
        "package": pkg,
        "items": items,
        "summary": {
            "filled": filled,
            "total": len(DEBRIEF_CATEGORIES),
            "pct": round((filled / len(DEBRIEF_CATEGORIES)) * 100),
            "avg_rating": round(sum(ratings)/len(ratings), 2) if ratings else None,
        },
    }


@router.post("/api/packages/{pid}/debriefs")
async def package_debrief_upsert(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops", "management")
    if not db.query_one("SELECT id FROM packages WHERE id = ?", (pid,)):
        raise HTTPException(status_code=404, detail="Paket tidak ditemukan.")
    cat = body.get("category")
    if cat not in DEBRIEF_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"category harus salah satu: {', '.join(DEBRIEF_CATEGORIES)}")
    rating = body.get("rating")
    if rating is not None:
        rating = int(rating)
        if rating < 1 or rating > 5:
            raise HTTPException(status_code=400, detail="rating harus 1-5.")
    db.execute(
        "INSERT INTO package_debriefs (package_id, category, rating, notes, created_by) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(package_id, category) DO UPDATE SET "
        "  rating = excluded.rating, "
        "  notes = excluded.notes, "
        "  created_by = excluded.created_by, "
        "  updated_at = CURRENT_TIMESTAMP",
        (pid, cat, rating, body.get("notes"), user["name"]),
    )
    log_action(user, "DEBRIEF_UPDATE", f"pkg={pid} cat={cat} rating={rating}")
    notify("data_updated", "debrief")
    return {"message": "Debrief tersimpan."}
