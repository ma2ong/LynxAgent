from __future__ import annotations

from typing import Dict

import pandas as pd


_WEIGHTS = {
    "trend": 0.22,
    "momentum": 0.22,
    "rsi": 0.12,
    "risk_control": 0.15,
    "liquidity": 0.10,
    "macd": 0.08,
    "bollinger": 0.06,
    "capital_flow": 0.05,
}


def _last(series: pd.Series, default: float = 0.0) -> float:
    clean = series.dropna()
    if clean.empty:
        return default
    return float(clean.iloc[-1])


def _clip_score(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def enrich_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ret"] = out["close"].pct_change()
    for window in (5, 10, 20, 60, 120):
        out[f"ma{window}"] = out["close"].rolling(window).mean()
        out[f"momentum_{window}"] = out["close"].pct_change(window)

    delta = out["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, pd.NA)
    out["rsi14"] = 100 - (100 / (1 + rs))
    out["volatility20"] = out["ret"].rolling(20).std() * (252 ** 0.5)
    out["volume_ma20"] = out.get("volume", pd.Series(index=out.index, dtype=float)).rolling(20).mean()
    out["amount_ma20"] = out.get("amount", pd.Series(index=out.index, dtype=float)).rolling(20).mean()
    out["rolling_peak"] = out["close"].cummax()
    out["drawdown"] = out["close"] / out["rolling_peak"] - 1

    # MACD
    out["ema12"] = out["close"].ewm(span=12, adjust=False).mean()
    out["ema26"] = out["close"].ewm(span=26, adjust=False).mean()
    out["macd_line"] = out["ema12"] - out["ema26"]
    out["macd_signal"] = out["macd_line"].ewm(span=9, adjust=False).mean()

    # Bollinger Bands (20-day, 2σ)
    bb_mid = out["close"].rolling(20).mean()
    bb_std = out["close"].rolling(20).std()
    out["bb_upper"] = bb_mid + 2 * bb_std
    out["bb_lower"] = bb_mid - 2 * bb_std

    return out


def compute_factor_scores(df: pd.DataFrame) -> Dict[str, float]:
    data = enrich_indicators(df)
    close = _last(data["close"])
    ma20 = _last(data["ma20"], close)
    ma60 = _last(data["ma60"], close) if "ma60" in data.columns else close
    momentum20 = _last(data["momentum_20"])
    momentum60 = _last(data["momentum_60"])
    rsi14 = _last(data["rsi14"], 50.0)
    vol20 = _last(data["volatility20"], 0.35)
    drawdown = abs(_last(data["drawdown"]))
    amount_ma20 = _last(data["amount_ma20"])

    trend_score = _clip_score(50 + (close / ma20 - 1) * 250 + (ma20 / ma60 - 1) * 180)
    momentum_score = _clip_score(50 + momentum20 * 180 + momentum60 * 120)
    rsi_score = _clip_score(100 - abs(rsi14 - 55) * 2.2)
    risk_score = _clip_score(100 - vol20 * 120 - drawdown * 80)
    liquidity_score = _clip_score(40 + amount_ma20 / 10_000_000 * 8) if amount_ma20 else 50.0

    # MACD score: positive histogram (macd_line > signal) → bullish
    macd_line = _last(data["macd_line"])
    macd_signal = _last(data["macd_signal"])
    macd_score = _clip_score(50 + (macd_line - macd_signal) * 2000)

    # Bollinger position: prefers midband (bb_pos=0.5 → score=100); penalizes both extremes (0 or 1 → score=40)
    bb_upper = _last(data["bb_upper"], close * 1.05)
    bb_lower = _last(data["bb_lower"], close * 0.95)
    band_width = bb_upper - bb_lower
    if band_width > 0:
        bb_pos = (close - bb_lower) / band_width  # 0..1
        # Prefer 0.3..0.7 range (neither oversold nor overbought)
        bollinger_score = _clip_score(100 - abs(bb_pos - 0.5) * 120)
    else:
        bollinger_score = 50.0

    # Capital flow proxy: amount * direction of price change
    ret = _last(data["ret"])
    capital_flow_score = _clip_score(50 + (ret * amount_ma20 / 1e8) * 5) if amount_ma20 else 50.0

    return {
        "trend": round(trend_score, 2),
        "momentum": round(momentum_score, 2),
        "rsi": round(rsi_score, 2),
        "risk_control": round(risk_score, 2),
        "liquidity": round(liquidity_score, 2),
        "macd": round(macd_score, 2),
        "bollinger": round(bollinger_score, 2),
        "capital_flow": round(capital_flow_score, 2),
    }


def composite_score(factors: Dict[str, float]) -> float:
    score = sum(factors.get(name, 0.0) * weight for name, weight in _WEIGHTS.items())
    return round(_clip_score(score), 2)


def risk_metrics(df: pd.DataFrame) -> Dict[str, float]:
    data = enrich_indicators(df)
    returns = data["ret"].dropna()
    if returns.empty:
        return {"volatility": 0.0, "max_drawdown": 0.0, "sharpe": 0.0}

    volatility = float(returns.std() * (252 ** 0.5))
    max_drawdown = float(data["drawdown"].min())
    sharpe = float((returns.mean() / returns.std()) * (252 ** 0.5)) if returns.std() else 0.0
    return {
        "volatility": round(volatility, 4),
        "max_drawdown": round(max_drawdown, 4),
        "sharpe": round(sharpe, 4),
    }


def signal_from_score(score: float) -> str:
    if score >= 75:
        return "strong_buy"
    if score >= 62:
        return "buy"
    if score >= 45:
        return "hold"
    return "avoid"
