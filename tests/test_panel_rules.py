"""五方判读规则版：确定性、口径方向、缺数据的处理。

这些测试守的是「规则是否按各派的偏好在打分」，不是「分数好不好看」——
有没有预测力由 experiments/panel_eval.py 在长样本上裁决，不在这里假设。
"""
from quantcore.quant.panel_rules import score_panel

_STRONG_FUND = {"roe": 20, "net_margin": 25, "revenue_yoy": 40, "net_profit_yoy": 60}
_WEAK_FUND = {"roe": 0, "net_margin": 0, "revenue_yoy": -20, "net_profit_yoy": -30}
_MID_FACTORS = {
    "trend": 50, "momentum": 50, "rsi": 50, "risk_control": 50,
    "liquidity": 50, "macd": 50, "bollinger": 50, "capital_flow": 50,
}


def _by_style(result):
    return {v["style"]: v for v in result["verdicts"]}


def test_same_input_gives_same_output():
    """规则版的全部意义在于可复现：同样输入必须逐字段一致。"""
    a = score_panel(_MID_FACTORS, _STRONG_FUND, composite=60, chg60=10)
    b = score_panel(_MID_FACTORS, _STRONG_FUND, composite=60, chg60=10)
    assert a == b
    assert a["method"] == "rules"


def test_five_verdicts_always_present():
    r = score_panel({}, {}, composite=None, chg60=None)
    assert len(r["verdicts"]) == 5
    assert {v["style"] for v in r["verdicts"]} == {"value", "trend", "hot_money", "contrarian", "quant"}


def test_value_persona_follows_fundamentals():
    strong = _by_style(score_panel(_MID_FACTORS, _STRONG_FUND, 50, 0))["value"]
    weak = _by_style(score_panel(_MID_FACTORS, _WEAK_FUND, 50, 0))["value"]
    assert strong["score"] > weak["score"]
    assert strong["stance"] == "看多" and weak["stance"] == "看空"


def test_trend_persona_follows_trend_factors():
    up = {**_MID_FACTORS, "trend": 90, "macd": 90, "momentum": 90}
    down = {**_MID_FACTORS, "trend": 10, "macd": 10, "momentum": 10}
    assert _by_style(score_panel(up, {}, 50, 0))["trend"]["score"] > \
           _by_style(score_panel(down, {}, 50, 0))["trend"]["score"]


def test_contrarian_and_hot_money_disagree_on_a_runaway_stock():
    """同一只暴涨的票，游资派该喜欢、逆向派该嫌贵 —— 这正是「分歧度」要捕捉的东西。"""
    hot = {**_MID_FACTORS, "rsi": 88, "capital_flow": 85, "liquidity": 90}
    r = _by_style(score_panel(hot, {}, 70, chg60=55))
    assert r["hot_money"]["score"] > r["contrarian"]["score"]


def test_missing_data_counts_as_neutral_not_bearish():
    """缺财务不等于基本面差。按 0 计会把一只没数据的票打成看空，那是数据缺失不是判断。"""
    r = score_panel(_MID_FACTORS, {}, composite=50, chg60=0)
    value = _by_style(r)["value"]
    assert value["score"] == 50.0
    assert value["stance"] == "中性"
    assert value["available"] is False
    assert "数据缺失按中性计" in r["summary"]


def test_divergence_is_the_spread_between_personas():
    r = score_panel({**_MID_FACTORS, "trend": 95, "macd": 95, "momentum": 95, "rsi": 95},
                    _WEAK_FUND, composite=50, chg60=50)
    scores = [v["score"] for v in r["verdicts"]]
    assert r["divergence"] == round(max(scores) - min(scores), 1)
    assert r["consensus"] == round(sum(scores) / len(scores), 1)


def test_no_llm_import_needed():
    """规则版不该再依赖 llm 模块 —— 断掉这条依赖是这次改动的重点之一。"""
    import inspect

    from quantcore.quant import investor_panel as ip

    assert "llm" not in inspect.getsource(ip.investor_panel)
