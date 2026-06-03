"""多路选股的数据源 + 辅助选股线。

职责：
- 提供候选 universe（默认从本地股票池取，可传入小列表用于测试）。
- 提供单只 OHLCV（本地优先，缺失回退直连 akshare），带进程内当日缓存。
- 提供 money_follow / market / contrarian / retail_sentiment 四路"轻量选股线"——
  它们不重写指标，只是从已有 enrich_indicators / 实时行情里取不同角度给候选打标签。

所有函数失败时降级为空/默认值，不抛栈（流水线整体不因单只数据缺失而中断）。
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

import pandas as pd

from ..data import _fetch_from_akshare, default_start_date, load_local_kline, normalize_ohlcv
from ..factors import enrich_indicators

_KLINE_CACHE: Dict[str, "pd.DataFrame"] = {}


def candidate_universe(limit: int = 200) -> List[Dict[str, str]]:
    """候选股票池：优先本地 meta；为空则用 datalake 拉全市场名单。返回 [{symbol,name}]。"""
    try:
        from ..local_store import get_local_store

        metas = get_local_store().load_meta()
        if metas:
            return [{"symbol": str(m["symbol"]).zfill(6), "name": m.get("name") or ""} for m in metas[:limit]]
    except Exception:
        pass
    try:
        from ..datalake import AKShareDataLake

        pool = AKShareDataLake().fetch_a_share_pool(limit)
        return [{"symbol": str(m.get("symbol")).zfill(6), "name": m.get("name") or ""} for m in pool[:limit]]
    except Exception:
        return []


def get_ohlcv(symbol: str, days: int = 540) -> "pd.DataFrame":
    """单只日线：本地优先，缺失回退直连 akshare。当日缓存，失败返回空 DataFrame。"""
    symbol = str(symbol).zfill(6)
    key = f"{symbol}:{days}:{date.today().isoformat()}"
    cached = _KLINE_CACHE.get(key)
    if cached is not None:
        return cached
    df = load_local_kline(symbol, days=days)
    if df is None or df.empty:
        try:
            df = normalize_ohlcv(
                _fetch_from_akshare(symbol, default_start_date(days), date.today().strftime("%Y-%m-%d"))
            )
        except Exception:
            df = pd.DataFrame()
    _KLINE_CACHE[key] = df
    return df


def _latest(enriched: pd.DataFrame, col: str, default: float = 0.0) -> float:
    if col not in enriched.columns:
        return default
    s = enriched[col].dropna()
    return float(s.iloc[-1]) if not s.empty else default


# ---- 四路轻量选股线：每路返回命中的 symbol -> {source, reason}，复用 enrich_indicators ----

def money_follow_pick(symbol: str, df: pd.DataFrame) -> Optional[Dict[str, str]]:
    """资金跟随：OBV/CMF 资金流入 + 放量。"""
    if df is None or len(df) < 60:
        return None
    e = enrich_indicators(df)
    cmf = _latest(e, "cmf20")
    obv_now = _latest(e, "obv")
    obv_prev = float(e["obv"].shift(10).dropna().iloc[-1]) if "obv" in e and len(e["obv"].dropna()) > 10 else obv_now
    vol = _latest(e, "volume")
    vol_ma = _latest(e, "volume_ma20", vol or 1.0)
    if cmf > 0.05 and obv_now >= obv_prev and vol > vol_ma * 1.1:
        return {"source": "money_follow", "reason": f"资金净流入(CMF {cmf:.2f}) + OBV上行 + 放量"}
    return None


def market_pick(symbol: str, df: pd.DataFrame) -> Optional[Dict[str, str]]:
    """随市/趋势：站上 MA20 且 MA20>MA60，中期趋势向上。"""
    if df is None or len(df) < 60:
        return None
    e = enrich_indicators(df)
    close = _latest(e, "close")
    ma20 = _latest(e, "ma20", close)
    ma60 = _latest(e, "ma60", close)
    if close > ma20 > ma60 and _latest(e, "momentum_20") > 0:
        return {"source": "market", "reason": "站上MA20且均线多头排列，趋势向上"}
    return None


def contrarian_pick(symbol: str, df: pd.DataFrame) -> Optional[Dict[str, str]]:
    """逆向：超跌反弹候选——RSI 偏低但已止跌（站上 MA5）。"""
    if df is None or len(df) < 30:
        return None
    e = enrich_indicators(df)
    rsi = _latest(e, "rsi14", 50.0)
    close = _latest(e, "close")
    ma5 = _latest(e, "ma5", close)
    if rsi < 38 and close >= ma5:
        return {"source": "contrarian", "reason": f"超跌(RSI {rsi:.0f})后站上MA5，逆向反弹候选"}
    return None


def retail_sentiment_pick(symbol: str, df: pd.DataFrame) -> Optional[Dict[str, str]]:
    """散户情绪：近 3 日温和回调 2%-4%（强势股回调入场，非追高）。"""
    if df is None or len(df) < 20:
        return None
    e = enrich_indicators(df)
    if len(e) < 4:
        return None
    close = _latest(e, "close")
    close_3ago = float(e["close"].iloc[-4])
    ma20 = _latest(e, "ma20", close)
    chg3 = (close / close_3ago - 1) if close_3ago else 0.0
    if -0.04 <= chg3 <= -0.02 and close > ma20:
        return {"source": "retail_sentiment", "reason": f"强势股回调{chg3*100:.1f}%(MA20上方)，情绪低吸点"}
    return None


SOURCE_PICKERS = {
    "money_follow": money_follow_pick,
    "market": market_pick,
    "contrarian": contrarian_pick,
    "retail_sentiment": retail_sentiment_pick,
}
