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
    rs = gain / loss.replace(0, float("nan"))
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

    # --- Extra indicators, implemented from standard public definitions ---
    # ATR (Wilder): true range smoothed with RMA(14)
    prev_close = out["close"].shift(1)
    tr = pd.concat([
        out["high"] - out["low"],
        (out["high"] - prev_close).abs(),
        (out["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    out["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()

    # KDJ (9,3,3): stochastic of close within the n-day high/low range
    low9 = out["low"].rolling(9).min()
    high9 = out["high"].rolling(9).max()
    rsv = ((out["close"] - low9) / (high9 - low9).replace(0, float("nan")) * 100).fillna(50.0)
    out["kdj_k"] = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    out["kdj_d"] = out["kdj_k"].ewm(alpha=1 / 3, adjust=False).mean()
    out["kdj_j"] = 3 * out["kdj_k"] - 2 * out["kdj_d"]

    # ADX / DMI (Wilder, 14): directional movement and trend strength
    up_move = out["high"].diff()
    down_move = -out["low"].diff()
    plus_dm = ((up_move > down_move) & (up_move > 0)).astype(float) * up_move.clip(lower=0)
    minus_dm = ((down_move > up_move) & (down_move > 0)).astype(float) * down_move.clip(lower=0)
    atr_di = tr.ewm(alpha=1 / 14, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr_di.replace(0, float("nan"))
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr_di.replace(0, float("nan"))
    out["plus_di"] = plus_di
    out["minus_di"] = minus_di
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    out["adx14"] = dx.ewm(alpha=1 / 14, adjust=False).mean()

    # Chandelier Exit (long stop): highest-high(22) - 3 * ATR
    out["chandelier_long"] = out["high"].rolling(22).max() - 3 * out["atr14"]

    # --- Volume / money-flow indicators (standard public definitions) ---
    vol = out.get("volume", pd.Series(0.0, index=out.index)).fillna(0)
    close_diff = out["close"].diff()
    direction = (close_diff > 0).astype(float) - (close_diff < 0).astype(float)
    out["obv"] = (direction * vol).cumsum()  # On-Balance Volume

    # Money Flow Index (14): volume-weighted RSI on typical price
    typical = (out["high"] + out["low"] + out["close"]) / 3
    raw_mf = typical * vol
    tp_diff = typical.diff()
    pos_mf = raw_mf.where(tp_diff > 0, 0.0).rolling(14).sum()
    neg_mf = raw_mf.where(tp_diff < 0, 0.0).rolling(14).sum()
    mfr = pos_mf / neg_mf.replace(0, float("nan"))
    out["mfi14"] = 100 - (100 / (1 + mfr))

    # Chaikin Money Flow (20)
    hl = (out["high"] - out["low"]).replace(0, float("nan"))
    mf_mult = ((out["close"] - out["low"]) - (out["high"] - out["close"])) / hl
    out["cmf20"] = (mf_mult * vol).rolling(20).sum() / vol.rolling(20).sum().replace(0, float("nan"))

    # Keltner Channel (EMA20 ± 2 * ATR)
    kc_mid = out["close"].ewm(span=20, adjust=False).mean()
    out["kc_mid"] = kc_mid
    out["kc_upper"] = kc_mid + 2 * out["atr14"]
    out["kc_lower"] = kc_mid - 2 * out["atr14"]

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


def indicator_snapshot(df: pd.DataFrame) -> Dict[str, float]:
    """Latest ATR / KDJ / ADX / Chandelier readings (standard public indicators)."""
    data = enrich_indicators(df)
    close = _last(data["close"])
    atr = _last(data["atr14"])
    return {
        "atr": round(atr, 4),
        "atr_pct": round(atr / close * 100, 2) if close else 0.0,
        "kdj_k": round(_last(data["kdj_k"], 50.0), 2),
        "kdj_d": round(_last(data["kdj_d"], 50.0), 2),
        "kdj_j": round(_last(data["kdj_j"], 50.0), 2),
        "adx": round(_last(data["adx14"]), 2),
        "plus_di": round(_last(data["plus_di"]), 2),
        "minus_di": round(_last(data["minus_di"]), 2),
        "chandelier_stop": round(_last(data["chandelier_long"]), 2),
        "mfi": round(_last(data["mfi14"], 50.0), 2),
        "cmf": round(_last(data["cmf20"]), 4),
        "obv_rising": bool(_last(data["obv"]) >= _last(data["obv"].shift(10))),
    }


def latest_adx(df: pd.DataFrame) -> float:
    """Latest ADX(14) value; 0.0 on any failure. Used as a trend-strength filter."""
    try:
        return float(_last(enrich_indicators(df)["adx14"]))
    except Exception:
        return 0.0
