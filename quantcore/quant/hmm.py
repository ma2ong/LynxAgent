from __future__ import annotations

from typing import Any, Dict, Iterable, List

import pandas as pd

from .data import load_local_kline
from .factors import enrich_indicators
from .local_store import get_local_store


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in ("", "-", None):
            return default
        number = float(value)
        return default if number != number else number
    except (TypeError, ValueError):
        return default


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def mean_reversion_snapshot(df: pd.DataFrame) -> Dict[str, float]:
    data = enrich_indicators(df)
    if data.empty:
        return {
            "score": 50.0,
            "distance_to_lower_pct": 0.0,
            "deviation_pct": 0.0,
            "price": 0.0,
            "bb_lower": 0.0,
            "bb_mid": 0.0,
        }
    latest = data.iloc[-1]
    price = _safe_float(latest.get("close"))
    bb_lower = _safe_float(latest.get("bb_lower"), price)
    bb_mid = _safe_float(latest.get("ma20"), price)
    if price <= 0:
        distance = 0.0
        deviation = 0.0
    else:
        distance = (price / bb_lower - 1) * 100 if bb_lower else 0.0
        deviation = (price / bb_mid - 1) * 100 if bb_mid else 0.0
    score = _clip(70 - distance * 3.2 - max(deviation, 0) * 1.2)
    if price < bb_lower:
        score = _clip(score + min(abs(distance) * 4, 25))
    return {
        "score": round(score, 1),
        "distance_to_lower_pct": round(distance, 2),
        "deviation_pct": round(deviation, 2),
        "price": round(price, 3),
        "bb_lower": round(bb_lower, 3),
        "bb_mid": round(bb_mid, 3),
    }


def multi_asset_hmm(symbol: str, peer_symbols: Iterable[str] | None = None, days: int = 160) -> Dict[str, Any]:
    """Lightweight multi-asset HMM-style regime model.

    A standard HMM observes one return stream. This extension builds a daily
    observation vector from several A-share assets: cross-sectional return,
    volatility, correlation, breadth, and liquidity. Regime probabilities are
    computed from Gaussian-like distances to three stable market states.
    """
    target = str(symbol).strip().zfill(6)
    peers = [target]
    if peer_symbols:
        peers.extend(str(item).strip().zfill(6) for item in peer_symbols if str(item).strip())
    if len(peers) < 8:
        metas = get_local_store().load_meta()[:120]
        peers.extend(str(item.get("symbol") or "").zfill(6) for item in metas if item.get("symbol"))
    peers = list(dict.fromkeys([item for item in peers if item]))[:80]

    frames: List[pd.Series] = []
    amounts: List[pd.Series] = []
    for code in peers:
        df = load_local_kline(code, days=days)
        if df is None or len(df) < 60:
            continue
        s = pd.to_numeric(df["close"], errors="coerce").pct_change()
        s.index = pd.to_datetime(df["date"], errors="coerce")
        frames.append(s.rename(code))
        amt = pd.to_numeric(df.get("amount", pd.Series(0, index=df.index)), errors="coerce").fillna(0)
        amt.index = s.index
        amounts.append(amt.rename(code))

    target_df = load_local_kline(target, days=days)
    if target_df is None or target_df.empty:
        return {"state": "unknown", "probabilities": {}, "dimensions": {}, "mean_reversion": {}}

    mean_rev = mean_reversion_snapshot(target_df)
    if len(frames) < 5:
        return {
            "state": "insufficient-peers",
            "probabilities": {"震荡": 1.0},
            "dimensions": {
                "trend_regime": 50.0,
                "volatility_regime": 50.0,
                "cross_asset_correlation": 50.0,
                "liquidity_regime": 50.0,
                "mean_reversion_potential": mean_rev["score"],
            },
            "mean_reversion": mean_rev,
        }

    rets = pd.concat(frames, axis=1).dropna(how="all").tail(days)
    amt_df = pd.concat(amounts, axis=1).reindex(rets.index).fillna(0.0)
    market_ret = rets.mean(axis=1).fillna(0.0)
    vol = market_ret.rolling(20).std().fillna(market_ret.std() or 0.0)
    breadth = (rets > 0).mean(axis=1).fillna(0.5)
    liquidity = amt_df.sum(axis=1).rolling(20).mean().fillna(amt_df.sum(axis=1))
    corr = rets.rolling(20).corr().groupby(level=0).mean().mean(axis=1).reindex(rets.index).fillna(0.3)

    last_ret = _safe_float(market_ret.iloc[-1])
    last_vol = _safe_float(vol.iloc[-1])
    last_breadth = _safe_float(breadth.iloc[-1], 0.5)
    liq_rank = _safe_float(liquidity.rank(pct=True).iloc[-1], 0.5)
    last_corr = _safe_float(corr.iloc[-1], 0.3)

    states = {
        "进攻趋势": (0.012, 0.012, 0.68, 0.65, 0.45),
        "震荡均衡": (0.000, 0.018, 0.50, 0.50, 0.35),
        "防守退潮": (-0.012, 0.030, 0.32, 0.35, 0.60),
    }
    obs = (last_ret, last_vol, last_breadth, liq_rank, last_corr)
    scales = (0.018, 0.020, 0.25, 0.35, 0.35)
    raw: Dict[str, float] = {}
    for name, center in states.items():
        dist = sum(((obs[i] - center[i]) / scales[i]) ** 2 for i in range(len(obs)))
        raw[name] = 1 / (1 + dist)
    total = sum(raw.values()) or 1.0
    probs = {name: round(value / total, 4) for name, value in raw.items()}
    state = max(probs, key=probs.get)

    dimensions = {
        "trend_regime": round(_clip(50 + last_ret * 1800 + (last_breadth - 0.5) * 70), 1),
        "volatility_regime": round(_clip(100 - last_vol * 2500), 1),
        "cross_asset_correlation": round(_clip(last_corr * 100), 1),
        "liquidity_regime": round(_clip(liq_rank * 100), 1),
        "mean_reversion_potential": mean_rev["score"],
    }
    return {
        "state": state,
        "probabilities": probs,
        "dimensions": dimensions,
        "mean_reversion": mean_rev,
        "peer_count": len(frames),
    }
