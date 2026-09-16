"""
Sprint AK-1: Chart of Accounts CRUD endpoints.

- GET  /api/coa                     -- list akun (semua authenticated user).
- GET  /api/coa/tree                -- grouped by account_group.
- POST /api/coa                     -- admin only, create akun.
- PUT  /api/coa/{aid}               -- admin only, update name / is_active.
- DELETE /api/coa/{aid}             -- admin only, soft-delete (is_active=0).
"""
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

router = APIRouter(tags=["chart-of-accounts"])


_VALID_GROUPS = ("ASSET", "LIABILITY", "EQUITY", "REVENUE", "EXPENSE")
_VALID_NORMAL = ("DEBIT", "CREDIT")


@router.get("/api/coa")
async def coa_list(user=Depends(authenticate_token)):
    rows = db.query_all(
        "SELECT id, account_code, account_name, account_group, normal_balance, "
        "  parent_id, is_active "
        "FROM chart_of_accounts "
        "WHERE is_active = 1 "
        "ORDER BY account_code"
    )
    return {"accounts": rows}


@router.get("/api/coa/tree")
async def coa_tree(user=Depends(authenticate_token)):
    rows = db.query_all(
        "SELECT id, account_code, account_name, account_group, normal_balance, "
        "  is_active "
        "FROM chart_of_accounts "
        "ORDER BY account_group, account_code"
    )
    tree = {g: [] for g in _VALID_GROUPS}
    for r in rows:
        tree[r["account_group"]].append(r)
    return {"groups": tree}


@router.post("/api/coa")
async def coa_create(body: dict = Depends(json_body),
                     user=Depends(authenticate_token)):
    require_role(user, "admin")
    code = (body.get("account_code") or "").strip()
    name = (body.get("account_name") or "").strip()
    grp = (body.get("account_group") or "").upper()
    nb = (body.get("normal_balance") or "").upper()
    if not code or not name:
        raise HTTPException(status_code=400, detail="Kode dan nama wajib diisi.")
    if grp not in _VALID_GROUPS:
        raise HTTPException(status_code=400,
                            detail=f"account_group harus salah satu dari {_VALID_GROUPS}.")
    if nb not in _VALID_NORMAL:
        raise HTTPException(status_code=400,
                            detail="normal_balance harus 'DEBIT' atau 'CREDIT'.")
    exist = db.query_one(
        "SELECT id FROM chart_of_accounts WHERE account_code = ?", (code,))
    if exist:
        raise HTTPException(status_code=400,
                            detail=f"Kode akun '{code}' sudah ada.")
    db.execute(
        "INSERT INTO chart_of_accounts "
        "(account_code, account_name, account_group, normal_balance) "
        "VALUES (?, ?, ?, ?)",
        (code, name, grp, nb),
    )
    log_action(user, "COA_CREATE", f"{code} {name} ({grp}/{nb})")
    return {"message": f"Akun {code} - {name} berhasil dibuat."}


@router.put("/api/coa/{aid}")
async def coa_update(aid: int, body: dict = Depends(json_body),
                     user=Depends(authenticate_token)):
    require_role(user, "admin")
    row = db.query_one("SELECT * FROM chart_of_accounts WHERE id = ?", (aid,))
    if not row:
        raise HTTPException(status_code=404, detail="Akun tidak ditemukan.")
    updates = []
    params = []
    if "account_name" in body:
        name = (body.get("account_name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Nama tidak boleh kosong.")
        updates.append("account_name = ?")
        params.append(name)
    if "is_active" in body:
        updates.append("is_active = ?")
        params.append(1 if body.get("is_active") else 0)
    if not updates:
        raise HTTPException(status_code=400, detail="Tidak ada perubahan.")
    params.append(aid)
    db.execute(
        f"UPDATE chart_of_accounts SET {', '.join(updates)} WHERE id = ?",
        tuple(params),
    )
    log_action(user, "COA_UPDATE", f"#{aid} {row['account_code']}")
    return {"message": "Akun diperbarui."}


@router.delete("/api/coa/{aid}")
async def coa_delete(aid: int, user=Depends(authenticate_token)):
    require_role(user, "admin")
    row = db.query_one("SELECT account_code, account_name FROM chart_of_accounts WHERE id = ?", (aid,))
    if not row:
        raise HTTPException(status_code=404, detail="Akun tidak ditemukan.")
    db.execute("UPDATE chart_of_accounts SET is_active = 0 WHERE id = ?", (aid,))
    log_action(user, "COA_DELETE", f"#{aid} {row['account_code']} {row['account_name']}")
    return {"message": "Akun dinonaktifkan (data historis tetap aman)."}
