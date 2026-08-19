"""规则版深度分析：同业对位的百分位口径与样本不足时的处理。

守的是「算法是否按定义在算」，不是「结论好不好看」。
"""
from quantcore.quant.deep_rules import build_peer_position, build_watch_points

_F = {"trend": 60, "momentum": 55, "capital_flow": 45, "risk_control": 70}


def _peers(n: int, comp_start: float = 10.0):
    """造 n 只同业，综合分从 comp_start 递增，其余因子同步。"""
    factors, comps = {}, {}
    for i in range(n):
        sym = f"{900000 + i}"
        v = comp_start + i
        factors[sym] = {"trend": v, "momentum": v, "capital_flow": v, "risk_control": v}
        comps[sym] = v
    return factors, comps


def test_percentile_counts_peers_below():
    """百分位 = 同业里低于本股的比例。本股压过 6/10 就该是 60%。"""
    pf, pc = _peers(10, comp_start=1)  # 同业综合分 1..10
    r = build_peer_position("600000", "测试行业", _F, 6.5, pf, pc)
    comp = next(m for m in r["metrics"] if m["key"] == "composite")
    assert comp["percentile"] == 60.0
    # 偶数样本取中间两个的均值：1..10 的中位是 (5+6)/2 = 5.5
    assert comp["industry_median"] == 5.5


def test_small_sample_gives_no_percentile():
    """同业不足 5 只时不给百分位——4 个样本算出来的分位没有意义。"""
    pf, pc = _peers(3)
    r = build_peer_position("600000", "冷门行业", _F, 50, pf, pc)
    assert all(m["percentile"] is None for m in r["metrics"])
    assert all(m["verdict"] == "样本不足" for m in r["metrics"])
    assert "样本不足" in r["summary"]


def test_leading_and_lagging_are_reported_together():
    """一只票可以同时有长板和短板，结论要两边都说，不能只挑好的讲。"""
    pf, pc = _peers(20, comp_start=1)
    # 风控远高于同业、资金流远低于同业
    mine = {"trend": 10, "momentum": 10, "capital_flow": 0, "risk_control": 100}
    r = build_peer_position("600000", "测试行业", mine, 10, pf, pc)
    assert "风控" in r["leading"]
    assert "资金流" in r["lagging"]
    assert "风控" in r["summary"] and "资金流" in r["summary"]


def test_same_input_same_output():
    pf, pc = _peers(12)
    a = build_peer_position("600000", "行业", _F, 50, pf, pc)
    b = build_peer_position("600000", "行业", _F, 50, pf, pc)
    assert a == b
    assert a["method"] == "rules"


def test_watch_points_are_checkable_states_not_advice():
    """跟踪要点必须是可被行情直接判定真假的状态，不能是操作指令。"""
    pts = build_watch_points({"rsi": 82, "trend": 30, "capital_flow": 25}, {"stop_loss": 10.0}, 11.0)
    text = " ".join(p["state"] for p in pts)
    assert "偏高区间" in text
    for banned in ("建议", "应该", "买入", "卖出", "止损离场", "减仓"):
        assert banned not in text
