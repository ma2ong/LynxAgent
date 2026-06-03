"""strategy_pick：选股线②（几十个"高胜率策略"满足量化因子 -> 候选）。

不另写指标：直接调度 factor_agent 与 strategies 里已实现的 signal 函数（它们都吃 OHLCV、
基于 enrich_indicators 返回布尔 Series），再补几个常见的"高胜率"组合（突破确认回踩 / 低波动超低RSI /
低涨幅入场 / 强势回调入场 / 均线上方低RSI / 回调反弹2-4%），全部复用 enrich_indicators 的列。

对每只候选：跑所有策略，命中任一即入选，记录命中的策略名作为来源理由。
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

import pandas as pd

from ..factor_agent import _low_vol_momentum, _trend_pullback, _volume_breakout
from ..factors import enrich_indicators
from ..strategies import (
    high_tight_flag_signal,
    keltner_breakout_signal,
    limit_up_washout_signal,
    moving_average_volume_signal,
    multi_ma_breakout_signal,
    rps_breakout_signal,
    turtle_breakout_signal,
)


def _last_true(signal: pd.Series) -> bool:
    """signal 的最新一根是否为 True。"""
    s = signal.dropna()
    return bool(s.iloc[-1]) if not s.empty else False


# ---- 几个"高胜率"补充策略：复用 enrich_indicators 的列，只取最新一根判定 ----

def _breakout_pullback(df: pd.DataFrame) -> pd.Series:
    """突破确认回踩：曾突破60日高点，现回踩 MA20 不破。"""
    e = enrich_indicators(df)
    broke = (e["close"].shift(3) > e["high"].rolling(60).max().shift(4))
    return broke & (e["close"] >= e["ma20"]) & (e["close"] <= e["ma20"] * 1.04)


def _low_vol_ultra_low_rsi(df: pd.DataFrame) -> pd.Series:
    """低波动超低RSI：波动小且 RSI<30 的超跌。"""
    e = enrich_indicators(df)
    return (e["volatility20"] < 0.35) & (e["rsi14"] < 30)


def _low_gain_entry(df: pd.DataFrame) -> pd.Series:
    """低涨幅入场：当日涨幅 0-3%，温和上行不追高。"""
    e = enrich_indicators(df)
    return (e["ret"] > 0) & (e["ret"] <= 0.03) & (e["close"] > e["ma20"])


def _strong_pullback_entry(df: pd.DataFrame) -> pd.Series:
    """强势回调入场：60日动量强但近5日回调，回踩 MA10。"""
    e = enrich_indicators(df)
    pulled = e["close"] < e["close"].shift(5)
    return (e["momentum_60"] > 0.15) & pulled & (e["close"] >= e["ma10"] * 0.98)


def _above_ma_low_rsi(df: pd.DataFrame) -> pd.Series:
    """均线上方低RSI：站上 MA20 但 RSI 仍 <50，未过热。"""
    e = enrich_indicators(df)
    return (e["close"] > e["ma20"]) & (e["ma20"] > e["ma60"]) & (e["rsi14"] < 50)


def _rebound_2_4(df: pd.DataFrame) -> pd.Series:
    """回调反弹2-4%：近3日累计回调 2%-4% 的强势股低吸。"""
    e = enrich_indicators(df)
    chg3 = e["close"] / e["close"].shift(3) - 1
    return (chg3 <= -0.02) & (chg3 >= -0.04) & (e["close"] > e["ma20"])


# 策略目录：名称 -> signal 函数（复用 + 补充，统称"高胜率策略库"）
STRATEGY_LIBRARY: Dict[str, Callable[[pd.DataFrame], pd.Series]] = {
    "趋势回踩": _trend_pullback,
    "低波动动量": _low_vol_momentum,
    "放量突破": _volume_breakout,
    "均线放量": moving_average_volume_signal,
    "海龟突破": turtle_breakout_signal,
    "RPS强势突破": rps_breakout_signal,
    "高而紧旗形": high_tight_flag_signal,
    "涨停洗盘": limit_up_washout_signal,
    "多均线突破": multi_ma_breakout_signal,
    "Keltner突破": keltner_breakout_signal,
    "突破确认回踩": _breakout_pullback,
    "低波动超低RSI": _low_vol_ultra_low_rsi,
    "低涨幅入场": _low_gain_entry,
    "强势回调入场": _strong_pullback_entry,
    "均线上方低RSI": _above_ma_low_rsi,
    "回调反弹2-4%": _rebound_2_4,
}


def run_strategy_pick(symbol: str, df: pd.DataFrame) -> Optional[Dict[str, object]]:
    """对单只跑全策略库；命中任一即入选。返回 {source, matched_strategies, reason} 或 None。"""
    if df is None or len(df) < 60:
        return None
    matched: List[str] = []
    for name, func in STRATEGY_LIBRARY.items():
        try:
            if _last_true(func(df)):
                matched.append(name)
        except Exception:
            continue
    if not matched:
        return None
    return {
        "source": "strategy",
        "matched_strategies": matched,
        "reason": "命中策略: " + " / ".join(matched[:5]),
    }
