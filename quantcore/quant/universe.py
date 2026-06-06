"""可投资域判定：单一真相来源（ST/退市名称屏蔽 + 价格/流动性下限）。"""
from __future__ import annotations

from typing import Optional

from .local_store import get_local_store

PRICE_FLOOR = 2.0        # 元
MIN_AMOUNT = 2e7         # 20日均成交额下限（元）


def is_blocked_name(name: Optional[str]) -> bool:
    """名称含 ST/*ST 或 退（退市/退市整理期）→ 不可投资。"""
    if not name:
        return False
    return "ST" in name.upper() or "退" in name


def latest_price_amount(symbol: str) -> tuple[float, float]:
    """返回 (最新收盘价, 20日均成交额)；无数据返回 (0,0)。"""
    df = get_local_store().load_kline(symbol)
    if df is None or df.empty:
        return 0.0, 0.0
    close = float(df["close"].iloc[-1])
    amt20 = float(df["amount"].tail(20).mean())
    return close, amt20


def is_investable(symbol: str, name: Optional[str] = None,
                  price_floor: float = PRICE_FLOOR, min_amount: float = MIN_AMOUNT) -> bool:
    """综合判定：名称未被屏蔽 且 价格≥下限 且 流动性≥下限。"""
    if is_blocked_name(name):
        return False
    close, amt20 = latest_price_amount(symbol)
    if close < price_floor:
        return False
    if amt20 < min_amount:
        return False
    return True
