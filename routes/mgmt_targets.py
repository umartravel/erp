"""
Router Management -- Company Targets (M-D):
- GET  /api/mgmt/company-targets  list target revenue/closing per bulan
- POST /api/mgmt/company-targets  upsert target bulanan

READ: admin/management/finance (finance perlu untuk hitung selisih).
WRITE: admin/management saja.
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

router = APIRouter(tags=["mgmt-targets"])

_MGMT_ROLES = ("admin", "management")
_TARGET_READ_ROLES = ("admin", "management", "finance")


@router.get("/api/mgmt/company-targets")
async def company_targets_list(user=Depends(authenticate_token)):
    require_role(user, *_TARGET_READ_ROLES)
    rows = db.query_all(
        "SELECT month, revenue_target, closing_target, set_by, updated_at "
        "FROM company_targets ORDER BY month DESC LIMIT 24"
    )
    return {"targets": rows}


@router.post("/api/mgmt/company-targets")
async def company_targets_upsert(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, *_MGMT_ROLES)
    month = body.get("month")
    revenue_target = int(body.get("revenue_target") or 0)
    closing_target = int(body.get("closing_target") or 0)
    if not month or len(month) != 7 or month[4] != "-":
        raise HTTPException(status_code=400, detail="month wajib format YYYY-MM.")
    db.execute(
        "INSERT INTO company_targets (month, revenue_target, closing_target, set_by) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(month) DO UPDATE SET "
        "  revenue_target = excluded.revenue_target, "
        "  closing_target = excluded.closing_target, "
        "  set_by = excluded.set_by, "
        "  updated_at = CURRENT_TIMESTAMP",
        (month, revenue_target, closing_target, user["name"]),
    )
    log_action(user, "COMPANY_TARGET_SET",
               f"{month} revenue={revenue_target} closing={closing_target}")
    notify("data_updated", "company_target")
    return {"message": "Target company tersimpan."}
