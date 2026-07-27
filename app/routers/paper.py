"""模拟交易（paper trading）路由。

从 lite_main 拆出。该功能由 LYNX_ENABLE_PAPER_TRADING 开关控制（默认关闭，
商用版下线），所有路由前置 require_paper_trading_enabled 守卫。

耦合处理：store/鉴权直接从 app.lite_auth 导入；实时报价 _realtime_quotes 与共享
的 lite_trader_bridge 在 handler/helper 内懒导入，避免与 lite_main 成环。paper 的
SQLite 表由 lite_main 启动时建好，这里只读写。
"""
from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from quantcore.trading import EasyTraderOrder
from app.lite_auth import get_current_lite_user, store

router = APIRouter(tags=["paper"])

PAPER_TRADING_ENABLED = os.getenv("LYNX_ENABLE_PAPER_TRADING", "0").lower() in {"1", "true", "yes"}


class LitePaperOrderRequest(BaseModel):
    code: str
    side: str
    quantity: int
    analysis_id: str | None = None
    execution_mode: str = "paper"


async def require_paper_trading_enabled() -> None:
    if not PAPER_TRADING_ENABLED:
        raise HTTPException(status_code=404, detail="模拟交易已在商用版下线")


def _paper_account_row(username: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM lite_paper_accounts WHERE username = ?", (username,)).fetchone()
        if not row:
            conn.execute(
                """
                INSERT INTO lite_paper_accounts (username, cash, realized_pnl, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (username, 1_000_000.0, 0.0, now, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM lite_paper_accounts WHERE username = ?", (username,)).fetchone()
    return dict(row)


async def _paper_quote_price(code: str) -> float:
    from app.lite_main import _realtime_quotes  # lazy: 避免与 lite_main 成环

    quotes = await _realtime_quotes([code])
    quote = quotes.get(code) or {}
    price = quote.get("price") or quote.get("close") or quote.get("current_price")
    if price is None:
        raise ValueError(f"无法获取 {code} 的实时价格，暂不能模拟成交")
    price_float = float(price)
    if price_float <= 0:
        raise ValueError(f"{code} 的实时价格无效，暂不能模拟成交")
    return round(price_float, 3)


async def _paper_positions(username: str) -> list[dict[str, Any]]:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT code, quantity, avg_cost, updated_at FROM lite_paper_positions WHERE username = ? ORDER BY updated_at DESC",
            (username,),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            last_price = await _paper_quote_price(item["code"])
        except Exception:
            last_price = None
        qty = int(item["quantity"])
        avg_cost = float(item["avg_cost"])
        market_value = round((last_price or avg_cost) * qty, 2)
        item.update(
            {
                "market": "CN",
                "currency": "CNY",
                "available_qty": qty,
                "last_price": last_price,
                "market_value": market_value,
                "unrealized_pnl": None if last_price is None else round((last_price - avg_cost) * qty, 2),
            }
        )
        items.append(item)
    return items


async def _paper_account_summary(username: str) -> dict[str, Any]:
    account = _paper_account_row(username)
    positions = await _paper_positions(username)
    positions_value = round(sum(float(item.get("market_value") or 0.0) for item in positions), 2)
    cash = round(float(account["cash"]), 2)
    equity = round(cash + positions_value, 2)
    exposure_ratio = round(positions_value / equity, 4) if equity > 0 else 0.0
    largest_position = max(
        (
            {
                "code": item.get("code"),
                "market_value": float(item.get("market_value") or 0.0),
                "weight": round(float(item.get("market_value") or 0.0) / equity, 4) if equity > 0 else 0.0,
            }
            for item in positions
        ),
        key=lambda item: item["market_value"],
        default={"code": "", "market_value": 0.0, "weight": 0.0},
    )
    risk_flags: list[str] = []
    if exposure_ratio >= 0.85:
        risk_flags.append("模拟持仓占用超过 85%，不再建议继续提高风险暴露。")
    if largest_position["weight"] >= 0.25:
        risk_flags.append(f"{largest_position['code']} 单票仓位超过 25%，注意集中度风险。")
    if cash / equity < 0.05 if equity > 0 else False:
        risk_flags.append("现金低于总资产 5%，缺少回撤缓冲。")
    return {
        "cash": {"CNY": cash},
        "positions_value": {"CNY": positions_value},
        "equity": {"CNY": equity},
        "realized_pnl": {"CNY": round(float(account["realized_pnl"]), 2)},
        "risk": {
            "mode": "paper_only",
            "exposure_ratio": exposure_ratio,
            "cash_ratio": round(cash / equity, 4) if equity > 0 else 0.0,
            "largest_position": largest_position,
            "max_single_position": 0.25,
            "max_total_exposure": 0.85,
            "flags": risk_flags,
        },
        "updated_at": account["updated_at"],
    }


async def _paper_pretrade_risk_check(username: str, code: str, side: str, amount: float) -> list[str]:
    if side != "buy":
        return []
    summary = await _paper_account_summary(username)
    equity = float((summary.get("equity") or {}).get("CNY") or 0.0)
    cash = float((summary.get("cash") or {}).get("CNY") or 0.0)
    positions = await _paper_positions(username)
    current_value = sum(float(item.get("market_value") or 0.0) for item in positions if item.get("code") == code)
    issues: list[str] = []
    if equity <= 0:
        issues.append("账户权益无效，无法下单。")
        return issues
    if amount > cash:
        issues.append(f"可用资金不足：需要 {amount:.2f}，当前 {cash:.2f}。")
    if (current_value + amount) / equity > 0.25:
        issues.append("单票买入后仓位会超过 25%，已被 paper 风控拦截。")
    if (float((summary.get("positions_value") or {}).get("CNY") or 0.0) + amount) / equity > 0.85:
        issues.append("买入后总仓位会超过 85%，已被 paper 风控拦截。")
    if (cash - amount) / equity < 0.05:
        issues.append("买入后现金低于总资产 5%，缺少回撤缓冲。")
    return issues


@router.get("/api/paper/account")
async def paper_account(
    _: None = Depends(require_paper_trading_enabled),
    user: dict[str, Any] = Depends(get_current_lite_user),
):
    username = user["username"]
    return {
        "success": True,
        "data": {
            "account": await _paper_account_summary(username),
            "positions": await _paper_positions(username),
        },
        "message": "SaaS Lite paper account",
    }


@router.get("/api/paper/positions")
async def paper_positions(
    _: None = Depends(require_paper_trading_enabled),
    user: dict[str, Any] = Depends(get_current_lite_user),
):
    return {"success": True, "data": {"items": await _paper_positions(user["username"])}, "message": "ok"}


@router.get("/api/paper/orders")
async def paper_orders(
    limit: int = 50,
    _: None = Depends(require_paper_trading_enabled),
    user: dict[str, Any] = Depends(get_current_lite_user),
):
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT id, code, side, quantity, price, amount, status, execution_mode,
                   bridge_json, analysis_id, created_at, filled_at
            FROM lite_paper_orders
            WHERE username = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user["username"], max(1, min(int(limit), 200))),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["market"] = "CN"
        item["currency"] = "CNY"
        if item.get("bridge_json"):
            try:
                item["bridge"] = json.loads(item["bridge_json"])
            except json.JSONDecodeError:
                item["bridge"] = None
        item.pop("bridge_json", None)
        items.append(item)
    return {"success": True, "data": {"items": items, "limit": limit}, "message": "ok"}


@router.get("/api/paper/trader/status")
async def paper_trader_status(
    _: None = Depends(require_paper_trading_enabled),
    user: dict[str, Any] = Depends(get_current_lite_user),
):
    from app.lite_main import lite_trader_bridge  # lazy: 复用共享实例
    return {"success": True, "data": asdict(lite_trader_bridge.status()), "message": "ok"}


@router.post("/api/paper/order")
async def paper_order(
    payload: LitePaperOrderRequest,
    _: None = Depends(require_paper_trading_enabled),
    user: dict[str, Any] = Depends(get_current_lite_user),
):
    from app.lite_main import lite_trader_bridge  # lazy: 复用共享实例

    username = user["username"]
    code = payload.code.strip().upper()
    if not re.fullmatch(r"\d{6}", code):
        return {"success": False, "data": None, "message": "SaaS Lite 当前模拟交易先支持 A 股 6 位代码", "code": 400}
    side = payload.side.lower()
    if side not in {"buy", "sell"}:
        return {"success": False, "data": None, "message": "交易方向只能是 buy 或 sell", "code": 400}
    qty = int(payload.quantity)
    if qty <= 0:
        return {"success": False, "data": None, "message": "数量必须大于 0", "code": 400}

    try:
        price = await _paper_quote_price(code)
    except Exception as exc:
        return {"success": False, "data": None, "message": str(exc), "code": 400}
    order = EasyTraderOrder(code=code, side=side, quantity=qty, price=price, market="CN")
    bridge_intent = lite_trader_bridge.build_order_intent(order)
    issues = bridge_intent["risk_checks"]["issues"]
    if issues:
        return {"success": False, "data": {"issues": issues}, "message": "风控检查未通过", "code": 400}

    now = datetime.now(timezone.utc).isoformat()
    order_id = "paper_" + secrets.token_hex(8)
    amount = round(price * qty, 2)
    account = _paper_account_row(username)
    paper_risk_issues = await _paper_pretrade_risk_check(username, code, side, amount)
    if paper_risk_issues:
        return {"success": False, "data": {"issues": paper_risk_issues}, "message": "模拟交易风控未通过", "code": 400}

    with store.connect() as conn:
        pos = conn.execute(
            "SELECT * FROM lite_paper_positions WHERE username = ? AND code = ?",
            (username, code),
        ).fetchone()
        if side == "buy":
            cash = float(account["cash"])
            if cash < amount:
                return {"success": False, "data": None, "message": f"可用资金不足：需要 {amount:.2f}，当前 {cash:.2f}", "code": 400}
            conn.execute(
                "UPDATE lite_paper_accounts SET cash = ?, updated_at = ? WHERE username = ?",
                (round(cash - amount, 2), now, username),
            )
            if pos:
                old_qty = int(pos["quantity"])
                old_cost = float(pos["avg_cost"])
                new_qty = old_qty + qty
                new_avg = round((old_cost * old_qty + price * qty) / new_qty, 4)
                conn.execute(
                    "UPDATE lite_paper_positions SET quantity = ?, avg_cost = ?, updated_at = ? WHERE username = ? AND code = ?",
                    (new_qty, new_avg, now, username, code),
                )
            else:
                conn.execute(
                    "INSERT INTO lite_paper_positions (username, code, quantity, avg_cost, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (username, code, qty, price, now),
                )
        else:
            if not pos or int(pos["quantity"]) < qty:
                return {"success": False, "data": None, "message": "可卖持仓不足", "code": 400}
            old_qty = int(pos["quantity"])
            avg_cost = float(pos["avg_cost"])
            new_qty = old_qty - qty
            pnl = round((price - avg_cost) * qty, 2)
            conn.execute(
                "UPDATE lite_paper_accounts SET cash = cash + ?, realized_pnl = realized_pnl + ?, updated_at = ? WHERE username = ?",
                (amount, pnl, now, username),
            )
            if new_qty == 0:
                conn.execute("DELETE FROM lite_paper_positions WHERE username = ? AND code = ?", (username, code))
            else:
                conn.execute(
                    "UPDATE lite_paper_positions SET quantity = ?, updated_at = ? WHERE username = ? AND code = ?",
                    (new_qty, now, username, code),
                )
        order_doc = {
            "id": order_id,
            "code": code,
            "side": side,
            "quantity": qty,
            "price": price,
            "amount": amount,
            "status": "filled",
            "execution_mode": "paper",
            "bridge": bridge_intent,
            "analysis_id": payload.analysis_id,
            "created_at": now,
            "filled_at": now,
            "market": "CN",
            "currency": "CNY",
        }
        conn.execute(
            """
            INSERT INTO lite_paper_orders (
                id, username, code, side, quantity, price, amount, status,
                execution_mode, bridge_json, analysis_id, created_at, filled_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                username,
                code,
                side,
                qty,
                price,
                amount,
                "filled",
                "paper",
                json.dumps(bridge_intent, ensure_ascii=False),
                payload.analysis_id,
                now,
                now,
            ),
        )
        conn.commit()

    return {"success": True, "data": {"order": order_doc}, "message": "模拟成交成功，实盘交易桥未自动执行"}


@router.post("/api/paper/reset")
async def paper_reset(
    confirm: bool = False,
    _: None = Depends(require_paper_trading_enabled),
    user: dict[str, Any] = Depends(get_current_lite_user),
):
    if not confirm:
        return {"success": False, "data": None, "message": "请设置 confirm=true 以确认重置", "code": 400}
    username = user["username"]
    with store.connect() as conn:
        conn.execute("DELETE FROM lite_paper_accounts WHERE username = ?", (username,))
        conn.execute("DELETE FROM lite_paper_positions WHERE username = ?", (username,))
        conn.execute("DELETE FROM lite_paper_orders WHERE username = ?", (username,))
        conn.commit()
    _paper_account_row(username)
    return {"success": True, "data": {"message": "reset", "cash": 1000000.0, "confirm": confirm}, "message": "ok"}
