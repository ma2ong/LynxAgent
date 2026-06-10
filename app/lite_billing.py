"""配额与套餐：PLANS 常量 + usage_log 记账 + 套餐有效性判定。

套餐为静态两档，定义放代码不建表；usage_log 按北京时间日期分桶。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

CN_TZ = timezone(timedelta(hours=8))

PLANS: dict[str, dict[str, Any]] = {
    "free": {"label": "免费版", "daily_llm": 3, "features": frozenset()},
    "member": {"label": "会员版", "daily_llm": 30, "features": frozenset({"serenity_deep", "lab"})},
}


def beijing_today() -> str:
    return datetime.now(CN_TZ).strftime("%Y-%m-%d")


def effective_plan(user: dict[str, Any]) -> str:
    """返回当前生效套餐 key。会员过期自动视为 free；到期日含当日。"""
    plan = user.get("plan") or "free"
    if plan not in PLANS:
        return "free"
    if plan != "free":
        expires = user.get("plan_expires_at")
        if expires and str(expires)[:10] < beijing_today():
            return "free"
    return plan


class BillingStore:
    def __init__(self, db_path: Optional[str | Path] = None):
        if db_path is None:
            from app.lite_auth import store
            db_path = store.db_path
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    n INTEGER NOT NULL DEFAULT 1,
                    day TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_user_day ON usage_log(user_id, day)")
            conn.commit()

    def used_today(self, user_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(n), 0) AS used FROM usage_log WHERE user_id = ? AND day = ?",
                (user_id, beijing_today()),
            ).fetchone()
            return int(row["used"])

    def record(self, user_id: str, action: str, n: int = 1) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO usage_log (user_id, action, n, day, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, action, n, beijing_today(), datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

    def total_used(self, user_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(n), 0) AS used FROM usage_log WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            return int(row["used"])


# ---- FastAPI 集成 ----
from fastapi import APIRouter, Depends, HTTPException  # noqa: E402

from app.lite_auth import get_current_lite_user  # noqa: E402

billing = BillingStore()


def require_quota(action: str, feature: str | None = None, cost: int = 1):
    """依赖工厂：会员特性门禁 + 当日配额扣减。402 detail 带 code 供前端分流。"""

    async def dep(user: dict = Depends(get_current_lite_user)) -> dict:
        plan_key = effective_plan(user)
        plan = PLANS[plan_key]
        if feature and feature not in plan["features"]:
            raise HTTPException(status_code=402, detail={
                "code": "member_required",
                "message": "该功能为会员专属，升级会员后可用",
            })
        if cost > 0:
            # 已知限制（TOCTOU）：check 与 record 非同一事务，同用户并发请求可能
            # 超额 1-2 次。M1 单机 SQLite 可接受；并发上量后改 BEGIN IMMEDIATE 单事务。
            used = billing.used_today(user["id"])
            if used + cost > plan["daily_llm"]:
                raise HTTPException(status_code=402, detail={
                    "code": "quota_exceeded",
                    "message": f"今日 AI 分析次数已用完（{plan['daily_llm']} 次/天）",
                    "used": used,
                    "limit": plan["daily_llm"],
                })
            billing.record(user["id"], action, cost)
        return user

    return Depends(dep)


router = APIRouter(prefix="/api/billing", tags=["lite-billing"])


@router.get("/me")
async def billing_me(user: dict = Depends(get_current_lite_user)):
    plan_key = effective_plan(user)
    plan = PLANS[plan_key]
    used = billing.used_today(user["id"])
    return {
        "success": True,
        "data": {
            "plan": plan_key,
            "plan_label": plan["label"],
            "plan_expires_at": user.get("plan_expires_at"),
            "daily_limit": plan["daily_llm"],
            "used_today": used,
            "remaining_today": max(0, plan["daily_llm"] - used),
            "features": sorted(plan["features"]),
        },
        "message": "ok",
    }
