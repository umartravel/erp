"""
Sprint AK-1: Journal Lines read endpoints (drill-down per transaksi).

- GET /api/journal/{tx_id}    -- journal_lines untuk transaction_id itu.
- GET /api/journal/account/{aid}?limit=50 -- N mutasi terakhir per akun.
"""
from fastapi import APIRouter

import db
from deps import (
    Depends,
    HTTPException,
    authenticate_token,
    require_role,
)

router = APIRouter(tags=["journal"])

_FINANCE_ROLES = ("admin", "finance", "management")


@router.get("/api/journal/{tx_id}")
async def journal_by_tx(tx_id: int, user=Depends(authenticate_token)):
    require_role(user, *_FINANCE_ROLES)
    tx = db.query_one(
        "SELECT id, transaction_no, status, type, amount, description, created_at, "
        "  reversal_of "
        "FROM transactions WHERE id = ?", (tx_id,))
    if not tx:
        raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan.")

    lines = db.query_all(
        "SELECT jl.id, jl.debit, jl.credit, jl.memo, "
        "  ca.id AS account_id, ca.account_code, ca.account_name, "
        "  ca.account_group, ca.normal_balance "
        "FROM journal_lines jl "
        "JOIN chart_of_accounts ca ON ca.id = jl.account_id "
        "WHERE jl.transaction_id = ? "
        "ORDER BY jl.id", (tx_id,))
    total_debit = sum(int(l["debit"] or 0) for l in lines)
    total_credit = sum(int(l["credit"] or 0) for l in lines)
    return {
        "transaction": tx,
        "lines": lines,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "balanced": total_debit == total_credit,
    }


@router.get("/api/journal/account/{aid}")
async def journal_by_account(aid: int, limit: int = 50,
                             user=Depends(authenticate_token)):
    require_role(user, *_FINANCE_ROLES)
    account = db.query_one(
        "SELECT id, account_code, account_name, account_group, normal_balance "
        "FROM chart_of_accounts WHERE id = ?", (aid,))
    if not account:
        raise HTTPException(status_code=404, detail="Akun tidak ditemukan.")
    if limit < 1 or limit > 500:
        limit = 50
    lines = db.query_all(
        "SELECT jl.id, jl.debit, jl.credit, jl.memo, jl.created_at, "
        "  t.id AS transaction_id, t.transaction_no, t.status, t.description "
        "FROM journal_lines jl "
        "JOIN transactions t ON t.id = jl.transaction_id "
        "WHERE jl.account_id = ? "
        "ORDER BY jl.created_at DESC LIMIT ?", (aid, limit))
    return {"account": account, "lines": lines}
