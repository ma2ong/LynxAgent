from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from .factors import enrich_indicators


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value in ("", "-", None):
            return default
        number = float(value)
        return default if number != number else number
    except (TypeError, ValueError):
        return default


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def analyze_wyckoff(df: pd.DataFrame) -> Dict[str, Any]:
    """Clean-room Wyckoff/VSA-style volume-price structure summary.

    Uses public Wyckoff concepts only: effort-vs-result, spring/upthrust,
    selling climax, sign of strength, and low-volume pullback.
    """
    data = enrich_indicators(df)
    if data.empty or len(data) < 40:
        return {
            "phase": "unknown",
            "bias": "neutral",
            "score": 50.0,
            "accumulation_score": 50.0,
            "distribution_score": 50.0,
            "signals": [],
            "reasons": ["history too short for Wyckoff/VSA structure"],
        }

    close = data["close"]
    high = data["high"]
    low = data["low"]
    volume = data.get("volume", pd.Series(0.0, index=data.index)).fillna(0.0)
    ret = close.pct_change().fillna(0.0)
    spread = ((high - low) / close.replace(0, float("nan"))).fillna(0.0)
    vol_ma20 = volume.rolling(20).mean()
    spread_ma20 = spread.rolling(20).mean()

    last_close = _safe_float(close.iloc[-1])
    last_ret = _safe_float(ret.iloc[-1])
    vol_ratio = _safe_float(volume.iloc[-1] / vol_ma20.iloc[-1], 1.0) if _safe_float(vol_ma20.iloc[-1]) else 1.0
    spread_ratio = _safe_float(spread.iloc[-1] / spread_ma20.iloc[-1], 1.0) if _safe_float(spread_ma20.iloc[-1]) else 1.0
    last_range = _safe_float(high.iloc[-1] - low.iloc[-1])
    close_position = _safe_float((close.iloc[-1] - low.iloc[-1]) / last_range, 0.5) if last_range else 0.5
    prior_low_20 = low.rolling(20).min().shift(1)
    prior_high_20 = high.rolling(20).max().shift(1)
    prior_low = _safe_float(prior_low_20.iloc[-1], _safe_float(low.tail(20).min()))
    prior_high = _safe_float(prior_high_20.iloc[-1], _safe_float(high.tail(20).max()))
    ma20 = _safe_float(data["ma20"].iloc[-1], last_close)
    ma60 = _safe_float(data["ma60"].iloc[-1], last_close)

    signals: List[Dict[str, Any]] = []
    reasons: List[str] = []
    accumulation = 50.0
    distribution = 50.0

    spring = low.iloc[-1] < prior_low and last_close > prior_low and close_position >= 0.55
    if spring:
        strength = _clip(65 + min(vol_ratio, 3.0) * 8 + close_position * 12)
        signals.append({"key": "spring", "name": "Spring test", "strength": round(strength, 1)})
        reasons.append("跌破前低后收回，出现 Spring / 假跌破测试")
        accumulation += 18

    upthrust = high.iloc[-1] > prior_high and last_close < prior_high and close_position <= 0.45
    if upthrust:
        strength = _clip(65 + min(vol_ratio, 3.0) * 8 + (1 - close_position) * 12)
        signals.append({"key": "upthrust", "name": "Upthrust", "strength": round(strength, 1)})
        reasons.append("突破前高后回落，出现 Upthrust / 假突破风险")
        distribution += 20

    selling_climax = last_ret < -0.04 and vol_ratio >= 1.8 and close_position >= 0.45
    if selling_climax:
        signals.append({"key": "selling_climax", "name": "Selling climax", "strength": round(_clip(70 + vol_ratio * 6), 1)})
        reasons.append("放量急跌后收在中上部，疑似 Selling Climax")
        accumulation += 12

    sign_of_strength = last_close > prior_high and vol_ratio >= 1.35 and close_position >= 0.65
    if sign_of_strength:
        signals.append({"key": "sign_of_strength", "name": "Sign of strength", "strength": round(_clip(70 + vol_ratio * 7), 1)})
        reasons.append("放量站上阶段高点，出现 Sign of Strength")
        accumulation += 16

    effort_no_result = vol_ratio >= 1.6 and spread_ratio <= 0.85 and abs(last_ret) < 0.012
    if effort_no_result:
        signals.append({"key": "effort_no_result", "name": "Effort without result", "strength": round(_clip(62 + vol_ratio * 8), 1)})
        reasons.append("高量窄幅，努力与结果不匹配，需防吸筹/派发分歧")
        if close_position >= 0.55:
            accumulation += 8
        else:
            distribution += 8

    low_volume_pullback = last_close >= ma20 and ret.tail(5).mean() < 0 and volume.tail(5).mean() < vol_ma20.tail(5).mean()
    if low_volume_pullback:
        signals.append({"key": "low_volume_pullback", "name": "Low-volume pullback", "strength": 64.0})
        reasons.append("回踩仍在 20 日线上方且缩量，偏 LPS / 低量回踩")
        accumulation += 8

    if ma20 > ma60 and last_close > ma20:
        accumulation += 6
    if ma20 < ma60 and last_close < ma20:
        distribution += 6

    accumulation = _clip(accumulation)
    distribution = _clip(distribution)
    net = accumulation - distribution
    score = _clip(50 + net * 0.75)

    if spring or selling_climax:
        phase = "accumulation-test"
    elif sign_of_strength:
        phase = "markup-confirmation"
    elif upthrust:
        phase = "distribution-risk"
    elif low_volume_pullback and ma20 > ma60:
        phase = "reaccumulation-pullback"
    elif distribution > accumulation + 12:
        phase = "distribution"
    elif accumulation > distribution + 12:
        phase = "accumulation"
    else:
        phase = "neutral-range"

    if score >= 62:
        bias = "bullish"
    elif score <= 42:
        bias = "bearish"
    else:
        bias = "neutral"

    if not reasons:
        reasons.append("未出现明确 Wyckoff/VSA 结构，按中性区间处理")

    return {
        "phase": phase,
        "bias": bias,
        "score": round(score, 1),
        "accumulation_score": round(accumulation, 1),
        "distribution_score": round(distribution, 1),
        "vol_ratio": round(vol_ratio, 2),
        "spread_ratio": round(spread_ratio, 2),
        "close_position": round(close_position, 2),
        "signals": signals,
        "reasons": reasons[:6],
    }
