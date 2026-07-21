"""三不卖低位形态：三军会师 / 双管齐下 / 五阳上阵（构造数据逐个验证 + 高位不误报）。"""
import numpy as np
import pandas as pd

from quantcore.quant.integrations import recognize_patterns


def _df(rows):
    n = len(rows)
    dates = pd.date_range("2026-01-01", periods=n, freq="B").strftime("%Y-%m-%d")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df.insert(0, "date", dates)
    df["volume"] = 1e6
    df["amount"] = 1e8
    return df


def _bar(o, c, lo=None, hi=None):
    lo = lo if lo is not None else min(o, c)
    hi = hi if hi is not None else max(o, c)
    return [o, hi, lo, c]


def test_ma_triple_cross_low_detected():
    """先跌到低位 → 5 日内均线金叉多头排列 → 命中三军会师。"""
    rows = [_bar(20 * 0.99 ** i, 20 * 0.99 ** (i + 1)) for i in range(90)]     # 阴跌到低位
    base = rows[-1][3]
    rows += [_bar(base * 1.02 ** i, base * 1.02 ** (i + 1)) for i in range(8)]  # 低位快速拉起金叉
    res = recognize_patterns("600001", _df(rows))
    keys = [p["key"] for p in res.patterns]
    assert "ma_triple_cross_low" in keys
    # 三不卖形态必须带 category 标签，供「图形智选」按类别筛选
    tri = next(p for p in res.patterns if p["key"] == "ma_triple_cross_low")
    assert tri.get("category") == "三不卖"


def test_double_hammer_low_detected():
    """低位连续两根长下影小实体、下影最低点相近 → 双管齐下。"""
    rows = [_bar(20 * 0.99 ** i, 20 * 0.99 ** (i + 1)) for i in range(100)]
    p = rows[-1][3]
    # 两根锤子线：实体极小，下影为实体的数倍，最低点几乎相同
    rows.append(_bar(p, p * 1.002, lo=p * 0.96))
    rows.append(_bar(p * 1.001, p * 1.003, lo=p * 0.9605))
    res = recognize_patterns("600001", _df(rows))
    assert "double_hammer_low" in [x["key"] for x in res.patterns]


def test_five_soldiers_low_detected():
    """低位连续 5 日小阳 → 五阳上阵。"""
    rows = [_bar(20 * 0.99 ** i, 20 * 0.99 ** (i + 1)) for i in range(100)]
    p = rows[-1][3]
    for i in range(5):
        o = p * (1.012 ** i)
        rows.append(_bar(o, o * 1.012))
    res = recognize_patterns("600001", _df(rows))
    assert "five_soldiers_low" in [x["key"] for x in res.patterns]


def test_high_position_not_flagged():
    """同样的 K 线组合出现在高位 → 三个低位形态都不该命中（低位守卫）。"""
    rows = [_bar(10 * 1.01 ** i, 10 * 1.01 ** (i + 1)) for i in range(100)]  # 一路上涨在高位
    p = rows[-1][3]
    for i in range(5):
        o = p * (1.012 ** i)
        rows.append(_bar(o, o * 1.012))
    res = recognize_patterns("600001", _df(rows))
    keys = [x["key"] for x in res.patterns]
    assert "five_soldiers_low" not in keys
    assert "ma_triple_cross_low" not in keys
    assert "double_hammer_low" not in keys
