"""相对强度筛选器：指标计算与横截面 RS 评级。"""
import pandas as pd

from quantcore.quant.relative_strength import (
    assign_rs_rating,
    compute_strength_metrics,
    _momentum_raw,
)


def _df(closes, highs=None, lows=None, amounts=None):
    n = len(closes)
    highs = highs if highs is not None else [c * 1.03 for c in closes]
    lows = lows if lows is not None else [c * 0.97 for c in closes]
    amounts = amounts if amounts is not None else [1e8] * n
    return pd.DataFrame({"high": highs, "low": lows, "close": closes, "amount": amounts})


def test_too_short_returns_none():
    """不足 120 根日线无法谈相对强度。"""
    assert compute_strength_metrics(_df([10.0] * 100)) is None


def test_dist_from_low_uptrend():
    """从 10 一路涨到 20：距最低点约 +100%。"""
    closes = [10.0 + i * 10.0 / 200 for i in range(200)]  # 10 → 20 线性
    m = compute_strength_metrics(_df(closes))
    assert m is not None
    assert m["dist_from_low"] > 90  # ≈ +100%


def test_adr_reflects_daily_range():
    """high/low 固定 ±5% → ADR ≈ (1.05/0.95 - 1) ≈ 10.5%。"""
    closes = [10.0 + i * 0.01 for i in range(150)]
    highs = [c * 1.05 for c in closes]
    lows = [c * 0.95 for c in closes]
    m = compute_strength_metrics(_df(closes, highs=highs, lows=lows))
    assert 9.0 < m["adr"] < 12.0


def test_above_ema_in_uptrend():
    """稳定上升趋势中，收盘价站上 EMA8/EMA21 且多头排列。"""
    closes = [10.0 * 1.005 ** i for i in range(150)]
    m = compute_strength_metrics(_df(closes))
    assert m["above_ema8"] and m["above_ema21"] and m["ema_stack"]


def test_below_ema_in_downtrend():
    """下跌趋势中，收盘价跌破 EMA8/EMA21。"""
    closes = [30.0 * 0.99 ** i for i in range(150)]
    m = compute_strength_metrics(_df(closes))
    assert not m["above_ema8"] and not m["above_ema21"]


def test_momentum_ranks_stronger_higher():
    """涨得多的动量原始值更高。"""
    strong = pd.Series([10.0 * 1.01 ** i for i in range(260)])
    weak = pd.Series([10.0 * 1.001 ** i for i in range(260)])
    assert _momentum_raw(strong) > _momentum_raw(weak)


def test_rs_rating_percentile():
    """RS 评级把最强票排到接近 99，最弱票接近 1。"""
    rows = [{"momentum_raw": float(i)} for i in range(100)]
    assign_rs_rating(rows)
    ratings = [r["rs_rating"] for r in rows]
    assert min(ratings) >= 1 and max(ratings) <= 99
    # 动量最高的那行应拿到最高 RS
    top = max(rows, key=lambda r: r["momentum_raw"])
    assert top["rs_rating"] == max(ratings)


def test_rs_rating_empty_safe():
    assign_rs_rating([])  # 不应抛异常
