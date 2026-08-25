"""规则审计尺子的守卫测试。

这把尺子存在的全部理由，是把「规则挑的票涨得多」和「这类票本来就涨得多」分开。
如果匹配对照那一步坏了而没人发现，它给出的结论会比没有尺子更危险——因为它看起来
很严谨。所以这里只测一件最要紧的事：**对照能不能把 beta 剥掉**。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments"))

from rule_audit import audit_one, holm, matched_increment, verdict  # noqa: E402


def _panel(rows):
    """构造最小面板。rows = (date, symbol, b_prior, fwd_excess)。"""
    df = pd.DataFrame(rows, columns=["date", "symbol", "b_prior", "fwd_excess"])
    df["b_amount"] = 0
    df["year"] = df["date"].str.slice(0, 4)
    return df


def test_matched_control_strips_beta_from_a_rule_with_no_edge():
    """规则只是挑中了「本来就涨得多的那一档」，对照增量必须约等于 0。

    这是全套东西的核心：深跌票本来就比全市场反弹得多，拿全市场中位当基准会把这份
    beta 记成规则的 alpha。造一个「档内所有票表现相同」的世界，真实增量为 0。
    """
    rows = []
    for day in ("2026-01-05", "2026-01-06", "2026-01-07"):
        # b_prior=1 这一档整体 +5pp（beta），b_prior=0 这一档 0pp
        for i in range(10):
            rows.append((day, f"6000{i:02d}", 1, 5.0))
        for i in range(10, 20):
            rows.append((day, f"6000{i:02d}", 0, 0.0))
    panel = _panel(rows)
    # 规则 = 只买高档的前 5 只：档内没有任何选股能力
    mask = panel["b_prior"].eq(1) & panel["symbol"].str.slice(4).astype(int).lt(5)

    inc_ret, inc_win, _ = matched_increment(panel, mask)
    assert inc_ret, "应当有匹配到对照的信号"
    assert sum(inc_ret) / len(inc_ret) == pytest.approx(0.0, abs=1e-9)
    assert sum(inc_win) / len(inc_win) == pytest.approx(0.0, abs=1e-9)


def test_matched_control_keeps_a_real_within_bucket_edge():
    """规则在同档内确实挑得更好时，增量必须留下来（别把 alpha 一起剥掉）。"""
    rows = []
    for day in ("2026-01-05", "2026-01-06", "2026-01-07"):
        for i in range(5):           # 信号票：档内 +8
            rows.append((day, f"6000{i:02d}", 1, 8.0))
        for i in range(5, 15):       # 同档对照：+2
            rows.append((day, f"6000{i:02d}", 1, 2.0))
    panel = _panel(rows)
    mask = panel["symbol"].str.slice(4).astype(int).lt(5)

    inc_ret, _, _ = matched_increment(panel, mask)
    assert sum(inc_ret) / len(inc_ret) == pytest.approx(6.0)


def test_tail_gate_rejects_a_rule_carried_by_its_best_few():
    """平均为正但全靠少数大涨股撑着的规则，必须倒在「去右尾」这一关。"""
    rows = []
    for d in range(40):
        day = f"2026-02-{d + 1:02d}"
        for i in range(20):
            # 19 只小亏，1 只暴涨 -> 平均为正、去掉最好的 5% 就转负
            ex = 60.0 if i == 0 else -1.0
            rows.append((day, f"6000{i:02d}", 0, ex))
    panel = _panel(rows)
    mask = pd.Series(True, index=panel.index)

    r = verdict(audit_one(panel, mask, "tail_driven", 5))
    assert r["avg_excess"] > 0
    assert r["avg_excess_ex_tail"] < 0
    assert "去右尾仍为正" in r["failed_gates"]


def test_holm_tightens_the_bar_as_more_rules_are_submitted():
    """一次提交的规则越多，阈值越严——防的是「横竖多切几刀总能切出显著」。"""
    one = [{"p_inc": 0.03}]
    holm(one)
    assert one[0]["pass_holm"] is True

    many = [{"p_inc": 0.03}] + [{"p_inc": 0.4 + i / 100} for i in range(9)]
    holm(many)
    # 同一个 p=0.03，混在 10 条里就过不了 0.05/10 的阈值
    assert many[0]["p_holm_threshold"] == pytest.approx(0.005)
    assert many[0]["pass_holm"] is False
