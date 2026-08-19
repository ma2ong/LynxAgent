"""配额与套餐：PLANS 常量 + usage_log 记账 + 套餐有效性判定。

套餐为静态两档，定义放代码不建表；usage_log 按北京时间日期分桶。
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

CN_TZ = timezone(timedelta(hours=8))

# daily_llm = 0 表示不限次数。2026-08-19 起产品不设付费档，每日额度原本是用来区分
# 免费/会员的，现在没有会员可区分了；实测两个月里用户触发的 AI 调用只有 15 次，
# 限制拦不住任何成本，只会让第一次来的人点两下就没了。
NO_DAILY_LIMIT = 0

PLANS: dict[str, dict[str, Any]] = {
    "free": {"label": "免费版", "daily_llm": NO_DAILY_LIMIT, "features": frozenset()},
    "member": {"label": "会员版", "daily_llm": NO_DAILY_LIMIT, "features": frozenset({"serenity_deep", "lab"})},
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

from pydantic import BaseModel  # noqa: E402

from app.lite_auth import get_current_lite_user, store as auth_store, utc_now  # noqa: E402

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
        if cost > 0 and plan["daily_llm"] > NO_DAILY_LIMIT:
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
        if cost > 0:
            billing.record(user["id"], action, cost)
        return user

    return Depends(dep)


router = APIRouter(prefix="/api/billing", tags=["lite-billing"])


@router.get("/me")
async def billing_me(user: dict = Depends(get_current_lite_user)):
    plan_key = effective_plan(user)
    plan = PLANS[plan_key]
    used = billing.used_today(user["id"])
    unlimited = plan["daily_llm"] <= NO_DAILY_LIMIT
    # ai_enabled：这个用户自己有没有配可用的 LLM 密钥（BYOK）。站点本身不配密钥，
    # 所以这里只看用户。前端据此显示「未配置」而不是让人点下去撞报错。
    try:
        from app.core.user_llm_keys import get_store
        ai_enabled = get_store().resolve(user["id"]) is not None
    except Exception:  # noqa: BLE001 — 探测失败一律按不可用处理
        ai_enabled = False
    return {
        "success": True,
        "data": {
            "plan": plan_key,
            "plan_label": plan["label"],
            "plan_expires_at": user.get("plan_expires_at"),
            "daily_limit": plan["daily_llm"],
            "unlimited": unlimited,
            "used_today": used,
            "remaining_today": None if unlimited else max(0, plan["daily_llm"] - used),
            "features": sorted(plan["features"]),
            "ai_enabled": ai_enabled,
        },
        "message": "ok",
    }


@router.get("/upgrade-info")
async def upgrade_info():
    """开通信息。收款方式为支付宝，值全部来自环境变量，不硬编码。

    2026-08-06 从微信改成支付宝：微信支付的网站产品签约要求域名已备案，而本站跑在
    Cloudflare Tunnel / Oracle 上，本来就不解析到境内服务器。支付仍是人工确认 ——
    这一版只把口头对账换成有留痕的申请单，接口化留到以后决定服务商时再说。
    """
    alipay_id = os.getenv("LYNX_MEMBERSHIP_ALIPAY", "").strip()
    qr_url = os.getenv("LYNX_MEMBERSHIP_QR_URL", "").strip()
    price_text = os.getenv("LYNX_MEMBERSHIP_PRICE_TEXT", "内测会员：人工确认后开通").strip()
    return {
        "success": True,
        "data": {
            "price_text": price_text,
            "alipay_id": alipay_id,
            "qr_url": qr_url,
            "configured": bool(alipay_id or qr_url),
            "instructions": "支付宝扫码付款后，在下方填写订单号提交开通申请；管理员核对后开通，无需添加好友。",
        },
        "message": "ok",
    }


class UpgradeRequestBody(BaseModel):
    plan: str = "member"
    order_no: str = ""
    note: str = ""


@router.post("/upgrade-request")
async def submit_upgrade_request(body: UpgradeRequestBody, user: dict = Depends(get_current_lite_user)):
    """用户付款后自助提交开通申请。同一用户只保留一条待处理申请，重复提交即更新。"""
    if body.plan not in PLANS:
        raise HTTPException(status_code=400, detail=f"未知套餐: {body.plan}")
    username = str(user.get("username") or "")
    now = utc_now()
    with auth_store.connect() as conn:
        row = conn.execute(
            "SELECT id FROM upgrade_requests WHERE username=? AND status='pending'", (username,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE upgrade_requests SET plan=?, order_no=?, note=?, created_at=? WHERE id=?",
                (body.plan, body.order_no.strip()[:64], body.note.strip()[:200], now, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO upgrade_requests(username, plan, order_no, note, status, created_at)"
                " VALUES(?,?,?,?,'pending',?)",
                (username, body.plan, body.order_no.strip()[:64], body.note.strip()[:200], now),
            )
        conn.commit()
    return {"success": True, "data": None, "message": "已提交，管理员核对后开通"}


@router.get("/upgrade-request")
async def my_upgrade_request(user: dict = Depends(get_current_lite_user)):
    """自己最近一条申请的状态，让用户能自查进度而不是来回追问。"""
    with auth_store.connect() as conn:
        row = conn.execute(
            "SELECT plan, order_no, status, created_at, handled_at FROM upgrade_requests"
            " WHERE username=? ORDER BY id DESC LIMIT 1",
            (str(user.get("username") or ""),),
        ).fetchone()
    return {"success": True, "data": dict(row) if row else None, "message": "ok"}
