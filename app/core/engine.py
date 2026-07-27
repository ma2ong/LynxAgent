"""共享的 QuantEngine 实例与建立在它之上的个股名录查询（从 lite_main 抽出）。

`lite_quant_engine` 是全站共用的一份引擎：自选诊断、催化剂榜、个股分析、行情页
都要用它算因子。它原本挂在 lite_main 上，任何模块想用都只能懒导入整个 app 模块。

`get_stock_pool_items` / `resolve_stock` 是引擎股票池上的一层薄查询（代码/名称/
模糊匹配三档），新闻映射、智选池、个股搜索共用。

注意：不要在这里再 new 一个 QuantEngine——`app/routers/quant.py` 另有自己的实例，
那是量化中心的专用引擎，与这份 Lite 共享实例分属两条路径，不要合并。
"""
from __future__ import annotations

from typing import Any

from quantcore.quant import QuantEngine

lite_quant_engine = QuantEngine()


async def get_stock_pool_items(limit: int = 6000) -> list[dict[str, Any]]:
    pool = await lite_quant_engine.stock_pool(limit=limit)
    items = pool.get("items") or []
    return [item for item in items if item.get("symbol")]


async def resolve_stock(query: str, market: str | None = "A股") -> dict[str, Any] | None:
    clean = str(query or "").strip()
    if not clean:
        return None
    clean_lower = clean.lower()
    items = await get_stock_pool_items()

    for item in items:
        if str(item.get("symbol", "")).lower() == clean_lower:
            return {"symbol": item["symbol"], "name": item.get("name") or item["symbol"], "market": item.get("market") or market or "A股"}

    for item in items:
        if str(item.get("name", "")).lower() == clean_lower:
            return {"symbol": item["symbol"], "name": item.get("name") or item["symbol"], "market": item.get("market") or market or "A股"}

    for item in items:
        name = str(item.get("name", "")).lower()
        symbol = str(item.get("symbol", "")).lower()
        if clean_lower in name or clean_lower in symbol:
            return {"symbol": item["symbol"], "name": item.get("name") or item["symbol"], "market": item.get("market") or market or "A股"}

    return None
