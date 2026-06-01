"""第一层排除规则：仅依赖股票名称 + 实时行情（批量获取，零逐只成本）。"""
from __future__ import annotations
from typing import Optional

DEFAULT_MIN_AMOUNT = 50_000_000.0   # 成交额下限：5000 万元
DEFAULT_MIN_PRICE = 2.0             # 仙股下限：2 元

# 排除原因常量（供统计/前端展示）
REASON_LABELS = {
    "st_delist": "ST/退市",
    "suspended": "停牌",
    "penny": "仙股(<2元)",
    "illiquid": "无量(<5000万)",
    "insufficient_bars": "K线不足",
    "fundamental_loss": "基本面利空(业绩亏损/下滑)",
}

# 业绩预告中视为基本面利空的类型（首亏/续亏/预减/略减/预亏）；"减亏"是亏损收窄属改善，不排除
BAD_FORECAST_TYPES = ("首亏", "续亏", "预减", "略减", "预亏")


def exclusion_reason(
    name: str,
    price: float,
    amount: float,
    *,
    exclude_penny: bool = True,
    min_amount: float = DEFAULT_MIN_AMOUNT,
    min_price: float = DEFAULT_MIN_PRICE,
) -> Optional[str]:
    """返回排除原因 key；None 表示保留。第一层（名称+行情）规则。"""
    upper = (name or "").upper()
    if "ST" in upper or "退" in (name or ""):
        return "st_delist"
    if price <= 0 or amount <= 0:
        return "suspended"
    if exclude_penny and price < min_price:
        return "penny"
    if amount < min_amount:
        return "illiquid"
    return None
