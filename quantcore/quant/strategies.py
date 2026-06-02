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
