"""极简管理后台 API：用户列表（含用量）、停启用、规则生命周期。仅 admin。"""
from __future__ import annotations


from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.lite_auth import LiteAuthStore, get_current_lite_user, store as auth_store, utc_now
from app.lite_billing import BillingStore, beijing_today, billing as billing_store


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
                       u.created_at, u.last_login,
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


class SetActiveRequest(BaseModel):
    is_active: bool


@router.get("/users")
async def admin_list_users():
    return {"success": True, "data": admin_store.list_users(), "message": "ok"}


@router.put("/users/{username}/active")
async def admin_set_active(username: str, req: SetActiveRequest):
    admin_store.set_active(username, req.is_active)
    return {"success": True, "data": None, "message": "已更新"}


@router.get("/rule-lifecycle")
async def admin_rule_lifecycle():
    """选股规则生命周期：每条规则处在哪一档、最近一次审计给了什么判定。

    这是研究流程的内部视图，不是给终端用户的：它答的是「这个想法我们试过没有、
    结论是什么」，防的是同一批规则被反复重新提出、重新验证。判定直接读
    experiments/results/ 里的审计结果，档位读 rule_lifecycle.RULE_STAGES。

    读文件可能慢（目录里几十份结果），放线程里，别占事件循环。
    """
    import asyncio

    from quantcore.quant.rule_lifecycle import build_lifecycle

    data = await asyncio.to_thread(build_lifecycle)
    return {"success": True, "data": data, "message": ""}
