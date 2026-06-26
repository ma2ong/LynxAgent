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

    # --- Extra momentum indicators (standard public definitions) ---
    # CCI (20): deviation of typical price from its mean, scaled by mean deviation
    tp_sma = typical.rolling(20).mean()
    mean_dev = (typical - tp_sma).abs().rolling(20).mean()
    out["cci20"] = (typical - tp_sma) / (0.015 * mean_dev.replace(0, float("nan")))

    # Williams %R (14): position of close within the 14-day high/low range (−100..0)
    hh14 = out["high"].rolling(14).max()
    ll14 = out["low"].rolling(14).min()
    out["williams_r"] = (hh14 - out["close"]) / (hh14 - ll14).replace(0, float("nan")) * -100

    # Stochastic RSI (14) with %K smoothing(3)
    rsi_min = out["rsi14"].rolling(14).min()
    rsi_max = out["rsi14"].rolling(14).max()
    stochrsi = (out["rsi14"] - rsi_min) / (rsi_max - rsi_min).replace(0, float("nan")) * 100
    out["stochrsi_k"] = stochrsi.rolling(3).mean()

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


def _aroon_latest(data: pd.DataFrame, period: int = 25) -> tuple[float, float]:
    """Aroon up/down at the latest bar (O(period); avoids slow rolling.apply)."""
    high_tail = data["high"].tail(period + 1).reset_index(drop=True)
    low_tail = data["low"].tail(period + 1).reset_index(drop=True)
    span = len(high_tail) - 1
    if span < 1:
        return 0.0, 0.0
    since_high = span - int(high_tail.values.argmax())
    since_low = span - int(low_tail.values.argmin())
    up = round(100 * (span - since_high) / span, 1)
    down = round(100 * (span - since_low) / span, 1)
    return up, down


def indicator_snapshot(df: pd.DataFrame) -> Dict[str, float]:
    """Latest ATR / KDJ / ADX / Chandelier / money-flow / momentum readings (standard public indicators)."""
    data = enrich_indicators(df)
    close = _last(data["close"])
    atr = _last(data["atr14"])
    aroon_up, aroon_down = _aroon_latest(data)
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
        "cci": round(_last(data["cci20"]), 2),
        "williams_r": round(_last(data["williams_r"], -50.0), 2),
        "stochrsi": round(_last(data["stochrsi_k"], 50.0), 2),
        "aroon_up": aroon_up,
        "aroon_down": aroon_down,
    }


def latest_adx(df: pd.DataFrame) -> float:
    """Latest ADX(14) value; 0.0 on any failure. Used as a trend-strength filter."""
    try:
        return float(_last(enrich_indicators(df)["adx14"]))
    except Exception:
        return 0.0


def swing_short_score(df: pd.DataFrame) -> Dict[str, object]:
    """短线波段 6 维共振评分（1-3 日持仓）。复用 enrich_indicators 的指标，纯本地 K 线可算。

    维度（满分 100）：RSI 超卖 20 / KDJ 金叉 20 / MACD 金叉 15 / 布林下轨 15 /
    放量上涨 15 / 资金代理(CMF) 15。返回 {score, signals, dims}。
    偏好"超卖+金叉+放量"的低吸共振，与追涨形态/动量档形成互补。
    """
    try:
        data = enrich_indicators(df)
    except Exception:
        return {"score": 0.0, "signals": [], "dims": {}}
    if len(data) < 20:
        return {"score": 0.0, "signals": [], "dims": {}}

    close = data["close"]
    score = 0.0
    signals: list = []
    dims: Dict[str, float] = {}

    # 1) RSI 超卖反弹 (20)
    rsi = _last(data["rsi14"], 50.0)
    if rsi < 30:
        s = 20.0; signals.append(f"RSI超卖({rsi:.0f})")
    elif rsi < 40:
        s = 12.0
    elif rsi <= 60:
        s = 6.0
    else:
        s = 0.0
    score += s; dims["rsi"] = round(s, 1)

    # 2) KDJ 金叉 (20)
    k = _last(data["kdj_k"], 50.0); j = _last(data["kdj_j"], 50.0)
    k_prev = _last(data["kdj_k"].shift(1), k); d_prev = _last(data["kdj_d"].shift(1), _last(data["kdj_d"], 50.0))
    golden = k_prev <= d_prev and k > _last(data["kdj_d"], 50.0)
    if golden and j < 50:
        s = 20.0; signals.append(f"KDJ金叉(J={j:.0f})")
    elif j < 20:
        s = 15.0; signals.append(f"KDJ超卖(J={j:.0f})")
    elif golden:
        s = 12.0; signals.append("KDJ金叉")
    else:
        s = 0.0
    score += s; dims["kdj"] = round(s, 1)

    # 3) MACD 金叉 (15)
    macd = _last(data["macd_line"]); macd_sig = _last(data["macd_signal"])
    macd_prev = _last(data["macd_line"].shift(1), macd); sig_prev = _last(data["macd_signal"].shift(1), macd_sig)
    if macd_prev <= sig_prev and macd > macd_sig:
        s = 15.0; signals.append("MACD金叉")
    elif macd > macd_sig:
        s = 8.0
    else:
        s = 0.0
    score += s; dims["macd"] = round(s, 1)

    # 4) 布林下轨支撑 (15)
    bb_u = _last(data["bb_upper"]); bb_l = _last(data["bb_lower"]); c = _last(close)
    width = bb_u - bb_l
    pos = (c - bb_l) / width if width > 0 else 0.5
    if pos < 0.2:
        s = 15.0; signals.append("布林下轨支撑")
    elif pos < 0.4:
        s = 10.0
    elif pos < 0.6:
        s = 6.0
    else:
        s = 0.0
    score += s; dims["bollinger"] = round(s, 1)

    # 5) 放量上涨 (15)
    vol_last = _last(data.get("volume", pd.Series(dtype=float)), 0.0)
    vol_ma = _last(data["volume_ma20"], 0.0)
    ret = _last(data["ret"], 0.0)
    vr = vol_last / vol_ma if vol_ma > 0 else 0.0
    if vr >= 1.5 and ret > 0:
        s = 15.0; signals.append(f"放量上涨(量比{vr:.1f})")
    elif vr >= 1.2 and ret > 0:
        s = 10.0
    elif ret > 0:
        s = 5.0
    else:
        s = 0.0
    score += s; dims["volume"] = round(s, 1)

    # 6) 资金代理 CMF (15)：无逐股实时资金流时，用 Chaikin Money Flow 当代理
    cmf = _last(data["cmf20"], 0.0)
    if cmf > 0.1:
        s = 15.0; signals.append("资金净流入(CMF)")
    elif cmf > 0:
        s = 8.0
    else:
        s = 0.0
    score += s; dims["capital"] = round(s, 1)

    return {"score": round(min(score, 100.0), 1), "signals": signals, "dims": dims, "rsi": round(rsi, 1)}


def latest_atr(df: pd.DataFrame) -> float:
    """Latest ATR(14, Wilder) value; 0.0 on any failure. Used to size trade-plan stops."""
    try:
        prev_close = df["close"].shift(1)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ], axis=1).max(axis=1)
        return float(_last(tr.ewm(alpha=1 / 14, adjust=False).mean()))
    except Exception:
        return 0.0


def trade_plan(
    price: float,
    atr: float = 0.0,
    *,
    stop_mult: float = 2.0,
    profit_mult: float = 3.0,
    fallback_stop_pct: float = 0.05,
) -> Dict[str, object]:
    """把现价 + ATR 组装成可执行交易计划：买点 / 止损 / 止盈 / 盈亏比。

    止损 = 现价 − stop_mult×ATR，止盈 = 现价 + profit_mult×ATR（默认 2×/3× → 盈亏比 1.5）。
    无 ATR 时退回固定百分比（同样保持 1.5 盈亏比），basis 字段标明依据。
    price 无效返回空 dict，调用方据此判断是否展示。
    """
    price = float(price or 0)
    if price <= 0:
        return {}
    atr = float(atr or 0)
    if atr > 0:
        stop = price - stop_mult * atr
        target = price + profit_mult * atr
        basis = "atr"
    else:
        stop = price * (1 - fallback_stop_pct)
        target = price * (1 + fallback_stop_pct * profit_mult / stop_mult)
        basis = "pct"
    stop = max(stop, 0.01)
    downside = price - stop
    upside = target - price
    return {
        "buy_price": round(price, 2),
        "stop_loss": round(stop, 2),
        "take_profit": round(target, 2),
        "stop_loss_pct": round((stop / price - 1) * 100, 2),
        "take_profit_pct": round((target / price - 1) * 100, 2),
        "risk_reward_ratio": round(upside / downside, 2) if downside > 0 else None,
        "atr": round(atr, 4) if atr > 0 else None,
        "basis": basis,
    }


def ml_feature_snapshot(df: pd.DataFrame) -> Dict[str, float]:
    """Lightweight ML-style feature summary for downstream agent reasoning.

    This is not a trained model. It mirrors common financial feature-engineering
    practice: trend persistence, risk-adjusted momentum, volatility rank,
    liquidity quality, and drawdown repair.
    """
    data = enrich_indicators(df)
    if data.empty or len(data) < 60:
        return {
            "feature_score": 50.0,
            "trend_persistence": 50.0,
            "risk_adjusted_momentum": 50.0,
            "volatility_rank": 50.0,
            "liquidity_quality": 50.0,
            "drawdown_repair": 50.0,
        }

    close = data["close"]
    amount = data.get("amount", pd.Series(0.0, index=data.index)).fillna(0.0)
    vol = data["volatility20"].replace([float("inf"), -float("inf")], float("nan"))
    drawdown = data["drawdown"].fillna(0.0)
    momentum_60 = _last(data["momentum_60"])
    vol_last = _last(vol, 0.35)
    vol_rank = float(vol.tail(120).rank(pct=True).iloc[-1] * 100) if len(vol.dropna()) else 50.0

    trend_persistence = float((close.tail(60) > data["ma20"].tail(60)).mean() * 100)
    risk_adjusted_momentum = _clip_score(50 + momentum_60 * 180 - vol_last * 35)
    low_vol_quality = _clip_score(100 - vol_rank)
    liquidity_quality = _clip_score(40 + _last(amount.rolling(20).mean()) / 10_000_000 * 6)
    drawdown_repair = _clip_score(100 + _last(drawdown) * 180)
    feature_score = (
        trend_persistence * 0.30
        + risk_adjusted_momentum * 0.28
        + low_vol_quality * 0.18
        + liquidity_quality * 0.14
        + drawdown_repair * 0.10
    )

    return {
        "feature_score": round(_clip_score(feature_score), 1),
        "trend_persistence": round(_clip_score(trend_persistence), 1),
        "risk_adjusted_momentum": round(risk_adjusted_momentum, 1),
        "volatility_rank": round(_clip_score(vol_rank), 1),
        "liquidity_quality": round(liquidity_quality, 1),
        "drawdown_repair": round(drawdown_repair, 1),
    }
