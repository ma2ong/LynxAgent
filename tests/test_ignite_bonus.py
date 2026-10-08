# -*- coding: utf-8 -*-
"""「地量点火」形态识别的守卫测试。

2026-10-08 起排序加分已撤（5 周从未触发：这个形态和智选的追涨候选几乎不会同时出现），
下面只钉形态口径本身——它仍作为形态标签显示。以下为原始背景：

这条规则是 2026-09-03 Allen 拍板进排序的，而它的审计证据是**边缘**的：
ig2_best 在 T+3 / T+1 开盘买入口径下，样本 6231 笔 / 1213 个交易日，匹配对照增量
+0.16pp、7 个年份方向一致，但 CI 下沿 −0.05、去右尾后 −0.02 —— 统计上跟 0 分不开，
T+5 口径完全失效。上线的全部理由是攒线上样本做样本外复判。

正因为证据边缘，四个门槛（沉寂天数 / 点火倍量 / 涨幅 / 位置）加板块闸和新鲜度闸，
一个都不能悄悄放宽 —— 松一格，影响面就从每天个位数扩散到整张名单，而它并没有强到
撑得起那个影响面。下面把每一道闸单独钉死。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantcore.quant.integrations import recognize_patterns


def _kline(*, quiet_days: int = 5, ignite_ratio: float = 5.0,
           ignite_pct: float = 4.0, extend: int = 0) -> pd.DataFrame:
    """造一段日线：前段活跃 → 连续 quiet_days 天地量 → 一根点火阳线（→ 再走 extend 天）。"""
    n = 90
    close = np.full(n, 10.0)
    amount = np.full(n, 5.0e8)
    q0 = n - 1 - extend - quiet_days           # 沉寂段起点
    amount[q0:q0 + quiet_days] = 1.0e7         # 地量
    ig = q0 + quiet_days                       # 点火日
    amount[ig] = 1.0e7 * ignite_ratio
    close[ig:] = 10.0 * (1 + ignite_pct / 100)
    for k in range(1, extend + 1):             # 点火后横住，避免干扰位置判断
        amount[ig + k] = 1.0e7 * 1.2
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n).strftime("%Y-%m-%d"),
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": amount / 10.0, "amount": amount,
    })


def _ignite(df: pd.DataFrame):
    hits = [p for p in recognize_patterns("000001", df).patterns
            if p.get("key") == "dryup_ignite" and p.get("active")]
    return hits[0] if hits else None


# ---------- 形态识别的四道闸 ----------

def test_fires_on_ignition_day():
    p = _ignite(_kline())
    assert p is not None
    ev = p["evidence"]
    assert ev["fresh"] is True and ev["days_since"] == 0
    assert ev["quiet_days"] >= 2
    assert ev["amount_ratio"] == pytest.approx(5.0, abs=0.01)


def test_needs_two_quiet_days():
    """只沉寂 1 天不算沉寂 —— 一天缩量到处都是，两天才是「没人要了」。"""
    assert _ignite(_kline(quiet_days=1)) is None


def test_needs_volume_expansion():
    """点火倍量的分母是沉寂期自己。不到 2 倍不算「醒了」。"""
    assert _ignite(_kline(ignite_ratio=1.5)) is None


def test_needs_real_up_bar():
    """涨幅不到 3% 只是缩量后的日常波动，不是点火。"""
    assert _ignite(_kline(ignite_pct=1.0)) is None


def test_denominator_is_the_quiet_period_not_ma20():
    """回归测试：分母若用 20 日均额，这一族票会被整批漏掉。

    欢瑞世纪 2026-08-28 点火那天 amount/ma20 只有 0.95（20 日均额里含着 8 月初的大量），
    而相对沉寂期是 2.24 倍。这里造的样本同样具备「相对沉寂期放大、相对 20 日均额缩小」
    的形状：若哪天有人把分母改回 ma20，这条会立刻红。
    """
    df = _kline(ignite_ratio=5.0)
    amt = df["amount"].to_numpy()
    assert amt[-1] / amt[-21:-1].mean() < 1.0      # 相对 20 日均额是缩量
    assert _ignite(df) is not None                 # 但相对沉寂期是放量，必须命中


def test_window_closes_after_three_days():
    """D+1~D+3 照常标注并逐日衰减，D+4 起摘掉 —— 标一个已失效的形态比不标更糟。"""
    strengths = []
    for d in range(0, 4):
        p = _ignite(_kline(extend=d))
        assert p is not None, f"D+{d} 应仍标注"
        assert p["evidence"]["days_since"] == d
        strengths.append(p["strength"])
    assert strengths == sorted(strengths, reverse=True)   # 逐日衰减
    assert _ignite(_kline(extend=4)) is None


def test_does_not_double_count_with_dryup_bonus():
    """与既有的 factors.dryup_bonus（地量埋伏）互斥，不会同日各加一次。

    那条要求**当日**成交额在近 60 日 ≤10 分位，这条要求当日是沉寂期的 2 倍且涨 ≥3%
    —— 构造上不可能同时成立。这里用点火日的真实数据把互斥性钉死。
    """
    from quantcore.quant.factors import dryup_bonus
    df = _kline()
    assert _ignite(df) is not None
    # 同一根 K 线喂给地量埋伏：人气/板块给到最宽松，仍然不该给分
    assert dryup_bonus(df, industry_heat=100.0, amt_rank=1.0) == 0.0
