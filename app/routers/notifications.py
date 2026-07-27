"""站内通知 + 微信推送绑定路由。

从 lite_main 拆出：这些 handler 只依赖 notification_store / 鉴权 / 会员判断，
不碰 lite_main 的共享状态，所以直接独立成 router，无需懒导入避环。
路径保持不变（无 prefix），前端无感。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.lite_auth import get_current_lite_user
from app.lite_billing import effective_plan
from app.lite_notifications import notification_store

router = APIRouter(tags=["notifications"])


class LiteWechatBindRequest(BaseModel):
    serverchan_key: str | None = None
    pushplus_token: str | None = None
    enabled: bool = True


@router.get("/api/notifications/unread_count")
async def unread_count(user: dict[str, Any] = Depends(get_current_lite_user)):
    return {"success": True, "data": {"count": notification_store.unread_count(user["username"])}, "message": "ok"}


@router.get("/api/notifications")
async def notifications(limit: int = 50, user: dict[str, Any] = Depends(get_current_lite_user)):
    items = notification_store.list(user["username"], limit)
    return {"success": True, "data": {"items": items, "total": len(items)}, "message": "ok"}


@router.post("/api/notifications/{notification_id}/read")
async def read_notification(notification_id: str, user: dict[str, Any] = Depends(get_current_lite_user)):
    notification_store.mark_read(user["username"], notification_id)
    return {"success": True, "data": {"id": notification_id}, "message": "ok"}


@router.post("/api/notifications/read_all")
async def read_all_notifications(user: dict[str, Any] = Depends(get_current_lite_user)):
    notification_store.mark_all_read(user["username"])
    return {"success": True, "data": None, "message": "ok"}


@router.get("/api/notifications/wechat/status")
async def wechat_push_status(user: dict[str, Any] = Depends(get_current_lite_user)):
    return {"success": True, "data": notification_store.wechat_status(user["username"], user), "message": "ok"}


@router.post("/api/notifications/wechat/bind")
async def bind_wechat_push(req: LiteWechatBindRequest, user: dict[str, Any] = Depends(get_current_lite_user)):
    try:
        notification_store.bind_wechat(
            user["username"],
            serverchan_key=req.serverchan_key,
            pushplus_token=req.pushplus_token,
            enabled=req.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"message": str(exc)}) from exc
    return {"success": True, "data": notification_store.wechat_status(user["username"], user), "message": "已绑定微信推送"}


@router.delete("/api/notifications/wechat/bind")
async def unbind_wechat_push(user: dict[str, Any] = Depends(get_current_lite_user)):
    notification_store.unbind_wechat(user["username"])
    return {"success": True, "data": notification_store.wechat_status(user["username"], user), "message": "已解绑微信推送"}


@router.post("/api/notifications/wechat/test")
async def test_wechat_push(user: dict[str, Any] = Depends(get_current_lite_user)):
    if effective_plan(user) != "member":
        raise HTTPException(status_code=402, detail={
            "code": "member_required",
            "message": "微信推送为会员专属功能，升级会员后可用",
        })
    result = notification_store.notify_user(
        user["username"],
        "AStockPick 微信推送测试",
        "这是一条测试通知。收到后说明微信推送绑定已生效。\n\n仅供研究提醒，不构成投资建议。",
        type_="wechat_test",
        payload={"source": "membership"},
        dedupe_key=None,
        send_wechat=True,
    )
    if not result.get("wechat_sent"):
        raise HTTPException(status_code=400, detail={"message": "测试通知未发送成功，请检查 SendKey/Token 是否正确"})
    return {"success": True, "data": result, "message": "测试通知已发送"}
