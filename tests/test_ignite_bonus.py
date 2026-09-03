# -*- coding: utf-8 -*-
"""「地量点火」形态与排序加分的守卫测试。

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

import app.lite_main as lite_main
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


# ---------- 排序加分的两道闸 ----------

def _enrich(monkeypatch, *, sector_mom, extend=0):
    df = _kline(extend=extend)
    monkeypatch.setattr("quantcore.quant.data.load_local_kline",
                        lambda symbol, days=540: df)
    monkeypatch.setattr("quantcore.quant.relative_strength.compute_strength_metrics",
                        lambda data: None)
    monkeypatch.setattr("quantcore.quant.risk_check.check_risks",
                        lambda *a, **k: {"risk_count": 0, "advice": "", "flags": []})
    monkeypatch.setattr("quantcore.quant.industry.industry_map",
                        lambda: {"000001": "测试板块"})
    item = {"symbol": "000001", "name": "测试", "smart_score": 80.0}
    lite_main._confluence_enrich_items([item], sector_mom)
    return item


def test_bonus_requires_hot_sector(monkeypatch):
    """板块是必要条件不是装饰：去掉它，匹配增量从 +0.16 掉到 +0.03。"""
    hot = _enrich(monkeypatch, sector_mom={"测试板块": 0.85})
    assert hot["ignite"]["gated"] is True
    assert hot["ignite"]["bonus"] == pytest.approx(lite_main.SMART_POOL_IGNITE_BONUS)
    assert hot["confluence_bonus"] >= lite_main.SMART_POOL_IGNITE_BONUS

    cold = _enrich(monkeypatch, sector_mom={"测试板块": 0.40})
    assert cold["ignite"] is not None          # 形态照常显示
    assert cold["ignite"]["gated"] is False    # 但不加分
    assert cold["ignite"]["bonus"] == 0.0


def test_missing_sector_data_gives_no_bonus(monkeypatch):
    """板块缓存没就绪时按未过闸处理 —— 宁可少给分，不在数据缺失时白送。"""
    item = _enrich(monkeypatch, sector_mom={})
    assert item["ignite"]["gated"] is False
    assert item["ignite"]["bonus"] == 0.0


def test_bonus_only_on_ignition_day(monkeypatch):
    """加分只给点火当日：审计口径是「点火日入选、T+1 开盘买」，
    D+1 之后的收益不在那 6231 笔样本里，给分等于凭空外推。"""
    item = _enrich(monkeypatch, sector_mom={"测试板块": 0.90}, extend=1)
    assert item["ignite"]["days_since"] == 1
    assert item["ignite"]["fresh"] is False
    assert item["ignite"]["gated"] is False
    assert item["ignite"]["bonus"] == 0.0


def test_gated_pick_is_marked_for_later_review(monkeypatch):
    """留痕只存形态名字。过闸的改名带「·过闸」，否则三四周后复判分不开
    「真加了分的」和「只标注的」—— 而上线的全部理由就是等那次复判。"""
    item = _enrich(monkeypatch, sector_mom={"测试板块": 0.85})
    names = [p.get("name") for p in item["patterns"]]
    assert "地量点火·过闸" in names


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
