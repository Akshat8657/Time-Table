"""Public health check (no secrets, no user data)."""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import db

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health(request: Request, deep: bool = False) -> dict[str, str]:
    result = {"status": "ok"}
    if deep:
        try:
            await db.fetchval("select 1")
            result["database"] = "ok"
        except Exception:
            result["database"] = "down"
            result["status"] = "degraded"
        try:
            await request.app.state.auth.settings()
            result["auth"] = "ok"
        except Exception:
            result["auth"] = "down"
            result["status"] = "degraded"
    return result
