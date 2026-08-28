"""
HTTP helper: body parser + RBAC guard.
"""
from fastapi import HTTPException, Request


async def json_body(request: Request) -> dict:
    """Body parser longgar (meniru req.body Express) -- dipakai sebagai Depends()."""
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def require_role(user, *roles):
    if user.get("role") not in roles:
        raise HTTPException(status_code=403, detail="Akses Ditolak")


__all__ = ["json_body", "require_role"]
