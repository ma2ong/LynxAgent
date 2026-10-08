"""用量记账：usage_log 按北京时间日期分桶，供管理员看「今日用量」。

套餐/会员/配额/开通申请已于 2026-10-08 按 Allen 要求整体移除：产品全部免费，
而残留的会员闸门还在真实拦人（回测/研究/流水线 402「会员专属」、微信推送只给会员、
批量深度分析把「不限次数=0」当成上限，每次都报额度用完）。
users 表的 plan/plan_expires_at 列与 upgrade_requests 表保留在库里不再读写，不做迁移。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

CN_TZ = timezone(timedelta(hours=8))

def beijing_today() -> str:
    return datetime.now(CN_TZ).strftime("%Y-%m-%d")


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
from fastapi import Depends  # noqa: E402

from app.lite_auth import get_current_lite_user  # noqa: E402

billing = BillingStore()


def require_quota(action: str, cost: int = 1):
    """依赖工厂：只记一笔用量（管理员看「今日用量」），不再设任何门槛。"""

    async def dep(user: dict = Depends(get_current_lite_user)) -> dict:
        if cost > 0:
            billing.record(user["id"], action, cost)
        return user

    return Depends(dep)
