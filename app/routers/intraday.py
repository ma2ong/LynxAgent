from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.core import intraday_monitor
from app.lite_auth import get_current_lite_user


router = APIRouter()


@router.get("/api/lite/intraday-signals")
async def intraday_signals(
    limit: int = Query(200, ge=1, le=200),
    history_limit: int = Query(100, ge=1, le=500),
    refresh: bool = False,
    _: dict[str, Any] = Depends(get_current_lite_user),
):
    data = await intraday_monitor.payload(
        limit=limit,
        history_limit=history_limit,
        refresh=refresh,
    )
    return {"success": True, "data": data, "message": "ok"}
