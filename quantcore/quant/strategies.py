from __future__ import annotations

import pandas as pd

from .factors import enrich_indicators


def moving_average_volume_signal(df: pd.DataFrame) -> pd.Series:
    data = enrich_indicators(df)
    volume = data.get("volume", pd.Series(index=data.index, dtype=float))
    return (data["ma20"] > data["ma60"]) & (data["close"] > data["ma20"]) & (volume > data["volume_ma20"] * 1.2)


def turtle_breakout_signal(df: pd.DataFrame, window: int = 20) -> pd.Series:
    data = df.copy()
    prior_high = data["high"].rolling(window).max().shift(1)
    return data["close"] > prior_high


def rps_breakout_signal(df: pd.DataFrame) -> pd.Series:
    data = enrich_indicators(df)
    high_60 = data["high"].rolling(60).max().shift(1)
    return (data["close"] > high_60) & (data["momentum_60"] > 0.18)


def high_tight_flag_signal(df: pd.DataFrame) -> pd.Series:
    data = enrich_indicators(df)
    advance = data["close"] / data["close"].rolling(45).min() - 1
    consolidation = (data["high"].rolling(15).max() / data["low"].rolling(15).min() - 1) < 0.18
    volume_calm = data["volume"] < data["volume_ma20"] * 1.15
    return (advance > 0.45) & consolidation & volume_calm & (data["close"] > data["ma20"])


def limit_up_washout_signal(df: pd.DataFrame) -> pd.Series:
    data = enrich_indicators(df)
    pct = data["close"].pct_change()
    recent_limit = pct.rolling(10).max().shift(1) > 0.095
    pullback = data["close"] > data["ma20"]
    volume_recovery = data["volume"] > data["volume_ma20"] * 1.05
    return recent_limit & pullback & volume_recovery & (data["rsi14"] < 70)


def multi_ma_breakout_signal(df: pd.DataFrame) -> pd.Series:
    data = enrich_indicators(df)
    ma_stack = (data["ma5"] > data["ma10"]) & (data["ma10"] > data["ma20"]) & (data["ma20"] > data["ma60"])
    prior_high = data["high"].rolling(30).max().shift(1)
    volume_confirm = data["volume"] > data["volume_ma20"] * 1.3
    return ma_stack & (data["close"] > prior_high) & volume_confirm


def keltner_breakout_signal(df: pd.DataFrame) -> pd.Series:
    data = enrich_indicators(df)
    volume = data.get("volume", pd.Series(index=data.index, dtype=float))
    # 收盘突破 Keltner 上轨（EMA20 + 2*ATR）且站上中轨，配合放量确认
    return (data["close"] > data["kc_upper"]) & (data["close"] > data["kc_mid"]) & (volume > data["volume_ma20"])


STRATEGIES = {
    "ma_volume": moving_average_volume_signal,
    "turtle_breakout": turtle_breakout_signal,
    "rps_breakout": rps_breakout_signal,
    "high_tight_flag": high_tight_flag_signal,
    "limit_up_washout": limit_up_washout_signal,
    "multi_ma_breakout": multi_ma_breakout_signal,
    "keltner_breakout": keltner_breakout_signal,
}


def _combine(funcs, mode: str, threshold: int | None = None):
    """Combine multiple signal functions into one (and / or / majority)."""
    def signal(df: pd.DataFrame) -> pd.Series:
        cols = []
        for i, f in enumerate(funcs):
            s = f(df).reindex(df.index).fillna(False).astype(int)
            s.name = f"s{i}"
            cols.append(s)
        if not cols:
            return pd.Series(False, index=df.index)
        mat = pd.concat(cols, axis=1).fillna(0)
        hits = mat.sum(axis=1)
        if mode == "or":
            return hits >= 1
        if mode == "majority":
            need = threshold or (len(cols) // 2 + 1)
            return hits >= need
        return hits >= len(cols)  # default: and
    return signal


def resolve_strategy(strategy=None, strategies=None, combine: str = "and", threshold: int | None = None):
    """Resolve a single strategy name or a composite into (signal_func, label).

    - single: resolve_strategy("ma_volume")
    - composite: resolve_strategy(strategies=["ma_volume","keltner_breakout"], combine="and")
    """
    names = [n for n in (strategies or []) if n in STRATEGIES]
    if names:
        mode = (combine or "and").lower()
        if mode not in {"and", "or", "majority"}:
            mode = "and"
        funcs = [STRATEGIES[n] for n in names]
        if len(names) == 1:
            return funcs[0], names[0]
        label = f"{mode}({'+'.join(names)})"
        return _combine(funcs, mode, threshold), label
    if strategy and strategy in STRATEGIES:
        return STRATEGIES[strategy], strategy
    supported = ", ".join(sorted(STRATEGIES))
    raise ValueError(f"不支持的策略: {strategy or strategies}. 支持: {supported}")
