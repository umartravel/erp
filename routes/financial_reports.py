"""
Sprint AK-4: Endpoint Neraca + Laba Rugi Multi-Step.

- GET /api/finance/reports/balance-sheet?date=YYYY-MM-DD
  Neraca sd tanggal (default: today).
- GET /api/finance/reports/income-statement?year=Y&month=M
  Laba Rugi bulan tsb.

Fin/admin/mgmt only -- angka finansial sensitif.
"""
import datetime

from fastapi import APIRouter

import financial_reports as fr
from deps import (
    Depends,
    HTTPException,
    authenticate_token,
    require_role,
)

router = APIRouter(tags=["financial-reports"])

_FINANCE_READ = ("admin", "finance", "management")


@router.get("/api/finance/reports/balance-sheet")
async def balance_sheet(date: str | None = None,
                        user=Depends(authenticate_token)):
    require_role(user, *_FINANCE_READ)
    if not date:
        date = datetime.date.today().isoformat()
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400,
                            detail="Format tanggal harus YYYY-MM-DD.")
    return fr.build_balance_sheet(date)


@router.get("/api/finance/reports/income-statement")
async def income_statement(year: int, month: int,
                           user=Depends(authenticate_token)):
    require_role(user, *_FINANCE_READ)
    try:
        return fr.build_income_statement(year, month)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
