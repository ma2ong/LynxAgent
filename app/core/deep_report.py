"""规则版深度分析的装配层：取同业、算因子、交给 deep_rules 定位。

纯计算部分在 quantcore/quant/deep_rules（可单测、无 IO）；这里负责有副作用的取数。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("deep_report")


def _peer_symbols(symbol: str, industry_map: Dict[str, str]) -> tuple[str, list[str]]:
    industry = industry_map.get(symbol, "")
    if not industry:
        return "", []
    peers = [s for s, ind in industry_map.items() if ind == industry and s != symbol]
    return industry, peers


def build_deep_report(symbol: str) -> Dict[str, Any]:
    """同业对位 + 跟踪要点。全部本地计算，不调任何模型，同样输入同样输出。"""
    from app.core.market_data import _load_industry_map
    from quantcore.quant.data import load_local_kline
    from quantcore.quant.deep_rules import PEER_CAP, build_peer_position, build_watch_points
    from quantcore.quant.factors import composite_score, compute_factor_scores, trade_plan

    symbol = str(symbol).strip().zfill(6)

    def factors_of(sym: str):
        data = load_local_kline(sym, days=120)
        if data is None or getattr(data, "empty", True):
            return None, None, None
        f = compute_factor_scores(data)
        return f, composite_score(f), data

    self_f, self_comp, self_data = factors_of(symbol)
    if self_f is None:
        return {"available": False, "message": "本地缺少该股日线数据，无法计算同业对位"}

    industry_map = _load_industry_map()
    industry, peers = _peer_symbols(symbol, industry_map)
    # 超大行业（最多 243 只）全算会拖住请求；截断到 PEER_CAP 只，百分位仍然稳。
    truncated = len(peers) > PEER_CAP
    peers = peers[:PEER_CAP]

    peer_factors: Dict[str, Dict[str, Any]] = {}
    peer_composites: Dict[str, float] = {}
    for p in peers:
        try:
            f, comp, _ = factors_of(p)
        except Exception:  # noqa: BLE001 — 单只取数失败不该让整张卡失败
            continue
        if f is None or comp is None:
            continue
        peer_factors[p] = f
        peer_composites[p] = comp

    position = build_peer_position(
        symbol=symbol, industry=industry,
        self_factors=self_f, self_composite=self_comp,
        peer_factors=peer_factors, peer_composites=peer_composites,
    )
    if truncated:
        position["summary"] += f"（同业超过 {PEER_CAP} 只，已按前 {PEER_CAP} 只取样）"

    last_close = None
    try:
        last_close = float(self_data["close"].iloc[-1])
    except Exception:  # noqa: BLE001
        pass
    try:
        plan = trade_plan(self_data) if self_data is not None else {}
    except Exception:  # noqa: BLE001
        plan = {}

    return {
        "available": True,
        "symbol": symbol,
        "peer_position": position,
        "watch_points": build_watch_points(self_f, plan or {}, last_close),
        "method": "rules",
    }
