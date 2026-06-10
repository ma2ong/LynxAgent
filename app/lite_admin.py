"""极简管理后台 API：用户列表（含用量）、改套餐、停启用。仅 admin。"""
from __future__ import annotations

import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.lite_auth import LiteAuthStore, get_current_lite_user, store as auth_store, utc_now
from app.lite_billing import PLANS, BillingStore, beijing_today, billing as billing_store


class AdminStore:
    def __init__(self, auth: LiteAuthStore, billing: BillingStore):
        self.auth = auth
        self.billing = billing

    def list_users(self) -> list[dict[str, Any]]:
        today = beijing_today()
        with self.auth.connect() as conn:
            rows = conn.execute(
                """
                SELECT u.id, u.username, u.email, u.is_admin, u.is_active,
                       u.plan, u.plan_expires_at, u.created_at, u.last_login,
                       COALESCE(t.used, 0) AS used_today,
                       COALESCE(a.used, 0) AS used_total
                FROM users u
                LEFT JOIN (SELECT user_id, SUM(n) AS used FROM usage_log WHERE day = ? GROUP BY user_id) t
                       ON t.user_id = u.id
                LEFT JOIN (SELECT user_id, SUM(n) AS used FROM usage_log GROUP BY user_id) a
                       ON a.user_id = u.id
                ORDER BY u.created_at DESC
                """,
                (today,),
            ).fetchall()
            return [dict(r) for r in rows]

    def set_plan(self, username: str, plan: str, expires_at: Optional[str]) -> None:
        if plan not in PLANS:
            raise HTTPException(status_code=400, detail=f"未知套餐: {plan}")
        if expires_at is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expires_at):
            raise HTTPException(status_code=400, detail="到期日格式须为 YYYY-MM-DD")
        if not self.auth.get_by_username(username):
            raise HTTPException(status_code=404, detail="用户不存在")
        with self.auth.connect() as conn:
            conn.execute(
                "UPDATE users SET plan = ?, plan_expires_at = ?, updated_at = ? WHERE username = ?",
                (plan, expires_at, utc_now(), username),
            )
            conn.commit()

    def set_active(self, username: str, active: bool) -> None:
        row = self.auth.get_by_username(username)
        if not row:
            raise HTTPException(status_code=404, detail="用户不存在")
        if not active and int(row["is_admin"]) == 1:
            # 防自锁：管理员账号不可被停用（含自己），恢复只能动数据库
            raise HTTPException(status_code=400, detail="不能停用管理员账号")
        with self.auth.connect() as conn:
            conn.execute(
                "UPDATE users SET is_active = ?, updated_at = ? WHERE username = ?",
                (1 if active else 0, utc_now(), username),
            )
            conn.commit()


async def require_admin(user: dict = Depends(get_current_lite_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="仅管理员可用")
    return user


admin_store = AdminStore(auth_store, billing_store)
router = APIRouter(prefix="/api/admin", tags=["lite-admin"], dependencies=[Depends(require_admin)])


class SetPlanRequest(BaseModel):
    plan: str
    plan_expires_at: Optional[str] = None  # YYYY-MM-DD，None=长期


class SetActiveRequest(BaseModel):
    is_active: bool


@router.get("/users")
async def admin_list_users():
    return {"success": True, "data": admin_store.list_users(), "message": "ok"}


@router.put("/users/{username}/plan")
async def admin_set_plan(username: str, req: SetPlanRequest):
    admin_store.set_plan(username, req.plan, req.plan_expires_at)
    return {"success": True, "data": None, "message": f"{username} → {PLANS[req.plan]['label']}"}


@router.put("/users/{username}/active")
async def admin_set_active(username: str, req: SetActiveRequest):
    admin_store.set_active(username, req.is_active)
    return {"success": True, "data": None, "message": "已更新"}
