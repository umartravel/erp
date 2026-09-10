"""
Phase F2 (Finance): Kategori pemasukan/pengeluaran + summary yearly/monthly.

Endpoint:
- GET   /api/finance/categories           tree parent+child (role finance)
- POST  /api/finance/categories           admin buat kategori baru
- PUT   /api/finance/categories/{cid}     admin edit
- DELETE /api/finance/categories/{cid}    admin soft-delete (is_active=0)
- GET   /api/finance/summary/year         summary tahunan + monthly breakdown +
                                          per-kategori aggregate
- GET   /api/finance/summary/month        summary bulanan + kategori detail +
                                          transaksi list
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
    require_role,
)

router = APIRouter(tags=["finance-categories"])

_FINANCE_ROLES = ("admin", "finance", "management")


def _clamp_year(year, current):
    """Security: batasi year 2000..2100 (konsisten dgn Phase 14a-2 fix)."""
    if not year:
        return current
    if year < 2000 or year > 2100:
        return current
    return year


def _resolve_category_tree():
    """Return list of parent kategori dgn nested 'subcategories'."""
    rows = db.query_all(
        "SELECT id, parent_id, name, group_type, is_active, sort_order "
        "FROM expense_categories WHERE is_active = 1 "
        "ORDER BY group_type, sort_order"
    )
    by_id = {r["id"]: {**r, "subcategories": []} for r in rows}
    tree = []
    for r in rows:
        if r["parent_id"] is None:
            tree.append(by_id[r["id"]])
        else:
            parent = by_id.get(r["parent_id"])
            if parent:
                parent["subcategories"].append(by_id[r["id"]])
    return tree


@router.get("/api/finance/categories")
async def categories_list(user=Depends(authenticate_token)):
    require_role(user, *_FINANCE_ROLES)
    tree = _resolve_category_tree()
    income = [t for t in tree if t["group_type"] == "income"]
    expense = [t for t in tree if t["group_type"] == "expense"]
    return {"income": income, "expense": expense}


@router.post("/api/finance/categories")
async def category_create(body: dict = Depends(json_body),
                          user=Depends(authenticate_token)):
    require_role(user, "admin")
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name wajib diisi.")
    group_type = body.get("group_type") or "expense"
    if group_type not in ("income", "expense"):
        raise HTTPException(status_code=400,
                            detail="group_type harus 'income' atau 'expense'.")
    parent_id = body.get("parent_id") or None
    if parent_id:
        parent = db.query_one(
            "SELECT group_type FROM expense_categories WHERE id = ?", (parent_id,))
        if not parent:
            raise HTTPException(status_code=404, detail="Parent kategori tidak ditemukan.")
        if parent["group_type"] != group_type:
            raise HTTPException(
                status_code=400,
                detail=f"Parent group_type ({parent['group_type']}) beda dgn child ({group_type}).")
    max_order = db.query_one(
        "SELECT COALESCE(MAX(sort_order), 0) mo FROM expense_categories "
        "WHERE parent_id IS ? OR parent_id = ?", (parent_id, parent_id))["mo"]
    db.execute(
        "INSERT INTO expense_categories (parent_id, name, group_type, sort_order) "
        "VALUES (?, ?, ?, ?)",
        (parent_id, name, group_type, (max_order or 0) + 10),
    )
    cid = db.query_one("SELECT last_insert_rowid() lid")["lid"]
    log_action(user, "FINANCE_CATEGORY_CREATE", f"#{cid} {group_type}/{name}")
    return {"id": cid, "message": "Kategori berhasil dibuat."}


@router.put("/api/finance/categories/{cid}")
async def category_update(cid: int, body: dict = Depends(json_body),
                          user=Depends(authenticate_token)):
    require_role(user, "admin")
    row = db.query_one("SELECT * FROM expense_categories WHERE id = ?", (cid,))
    if not row:
        raise HTTPException(status_code=404, detail="Kategori tidak ditemukan.")
    name = (body.get("name") or row["name"]).strip()
    is_active = int(body.get("is_active", row["is_active"]))
    sort_order = int(body.get("sort_order", row["sort_order"]))
    db.execute(
        "UPDATE expense_categories SET name = ?, is_active = ?, sort_order = ? "
        "WHERE id = ?", (name, is_active, sort_order, cid),
    )
    log_action(user, "FINANCE_CATEGORY_UPDATE", f"#{cid} -> {name}")
    return {"message": "Kategori berhasil diperbarui."}


@router.delete("/api/finance/categories/{cid}")
async def category_delete(cid: int, user=Depends(authenticate_token)):
    require_role(user, "admin")
    row = db.query_one("SELECT id, name FROM expense_categories WHERE id = ?", (cid,))
    if not row:
        raise HTTPException(status_code=404, detail="Kategori tidak ditemukan.")
    db.execute("UPDATE expense_categories SET is_active = 0 WHERE id = ?", (cid,))
    log_action(user, "FINANCE_CATEGORY_DELETE", f"#{cid} {row['name']}")
    return {"message": "Kategori dinonaktifkan (data historis tetap aman)."}


def _by_category_breakdown(period_sql: str, params: tuple):
    """Aggregate transactions per parent+subcategory. period_sql like
    "strftime('%Y', created_at) = ?" atau "strftime('%Y-%m', ...) = ?"."""
    cat_rows = db.query_all(
        f"SELECT p.id parent_id, p.name parent_name, p.group_type, "
        f"  c.id sub_id, c.name sub_name, "
        f"  COALESCE(SUM(t.amount), 0) total "
        f"FROM expense_categories p "
        f"LEFT JOIN expense_categories c ON c.parent_id = p.id "
        f"LEFT JOIN transactions t ON t.category_id = c.id "
        f"  AND {period_sql} "
        f"WHERE p.parent_id IS NULL "
        f"GROUP BY p.id, c.id "
        f"ORDER BY p.group_type, p.sort_order, c.sort_order", params,
    )
    by_cat_map = {}
    for r in cat_rows:
        pid = r["parent_id"]
        if pid not in by_cat_map:
            by_cat_map[pid] = {
                "parent_id": pid, "name": r["parent_name"],
                "group": r["group_type"], "total": 0, "subcategories": [],
            }
        if r["sub_id"] is not None:
            sub_total = r["total"] or 0
            by_cat_map[pid]["subcategories"].append(
                {"id": r["sub_id"], "name": r["sub_name"], "total": sub_total}
            )
            by_cat_map[pid]["total"] += sub_total
    return list(by_cat_map.values())


@router.get("/api/finance/summary/year")
async def summary_year(year: int | None = None,
                       user=Depends(authenticate_token)):
    require_role(user, *_FINANCE_ROLES)
    now = datetime.datetime.now()
    year = _clamp_year(year, now.year)
    ys = str(year)

    totals = db.query_all(
        "SELECT type, COALESCE(SUM(amount), 0) total FROM transactions "
        "WHERE strftime('%Y', created_at) = ? GROUP BY type", (ys,),
    )
    total_income = 0
    total_expense = 0
    for t in totals:
        if t["type"] == "income":
            total_income = t["total"] or 0
        elif t["type"] == "expense":
            total_expense = t["total"] or 0

    monthly_rows = db.query_all(
        "SELECT strftime('%m', created_at) m, type, "
        "COALESCE(SUM(amount), 0) total FROM transactions "
        "WHERE strftime('%Y', created_at) = ? "
        "GROUP BY m, type ORDER BY m", (ys,),
    )
    monthly = [{"month": i, "income": 0, "expense": 0, "net": 0}
               for i in range(1, 13)]
    for r in monthly_rows:
        idx = int(r["m"]) - 1
        if r["type"] == "income":
            monthly[idx]["income"] = r["total"] or 0
        elif r["type"] == "expense":
            monthly[idx]["expense"] = r["total"] or 0
    for m in monthly:
        m["net"] = (m["income"] or 0) - (m["expense"] or 0)

    by_category = _by_category_breakdown(
        "strftime('%Y', t.created_at) = ?", (ys,))

    return {
        "year": year,
        "current_year": now.year,
        "total_income": total_income,
        "total_expense": total_expense,
        "net_saldo": total_income - total_expense,
        "monthly": monthly,
        "by_category": by_category,
    }


@router.get("/api/finance/summary/month")
async def summary_month(year: int | None = None, month: int | None = None,
                        user=Depends(authenticate_token)):
    require_role(user, *_FINANCE_ROLES)
    now = datetime.datetime.now()
    year = _clamp_year(year, now.year)
    month = month if month else now.month
    if month < 1 or month > 12:
        month = now.month
    ym = f"{year:04d}-{month:02d}"

    totals = db.query_all(
        "SELECT type, COALESCE(SUM(amount), 0) total FROM transactions "
        "WHERE strftime('%Y-%m', created_at) = ? GROUP BY type", (ym,),
    )
    total_income = 0
    total_expense = 0
    for t in totals:
        if t["type"] == "income":
            total_income = t["total"] or 0
        elif t["type"] == "expense":
            total_expense = t["total"] or 0

    by_category = _by_category_breakdown(
        "strftime('%Y-%m', t.created_at) = ?", (ym,))

    transactions = db.query_all(
        "SELECT t.id, t.type, t.amount, t.description, t.created_at, "
        "  c.name AS category_name, p.name AS parent_name "
        "FROM transactions t "
        "LEFT JOIN expense_categories c ON c.id = t.category_id "
        "LEFT JOIN expense_categories p ON p.id = c.parent_id "
        "WHERE strftime('%Y-%m', t.created_at) = ? "
        "ORDER BY t.created_at DESC LIMIT 50", (ym,),
    )

    return {
        "period": ym,
        "year": year,
        "month": month,
        "total_income": total_income,
        "total_expense": total_expense,
        "net": total_income - total_expense,
        "by_category": by_category,
        "transactions": transactions,
    }
