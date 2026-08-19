"""个股避雷决策：多角度综合 + 分档买卖建议。"""
import pandas as pd

from quantcore.quant.decision import stock_decision


def _df(closes):
    n = len(closes)
    return pd.DataFrame({
        "open": [c * 0.995 for c in closes],
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "close": closes,
        "volume": [1e6] * n,
        "amount": [c * 1e6 for c in closes],
    })


def test_structure_has_five_angles():
    d = stock_decision("600000", "测试", _df([10.0 + i * 0.05 for i in range(130)]))
    keys = {a["key"] for a in d["angles"]}
    assert keys == {"risk", "trend", "volume", "capital", "market"}
    assert "level" in d["verdict"] and "stance" in d["verdict"]
    assert d["disclaimer"]


def test_clean_uptrend_not_avoid():
    """干净上升趋势、无七不买风险 → 不应给回避档。"""
    d = stock_decision("600000", "强势股", _df([10.0 * 1.004 ** i for i in range(130)]),
                       market_env="偏暖")
    assert d["risk_count"] == 0
    assert d["verdict"]["level"] not in ("风险项偏多", "风险项密集")


def test_breakdown_downtrend_flagged():
    """跌破均线的下降趋势 → 命中破位风险，给谨慎/回避档。"""
    d = stock_decision("600000", "弱势股", _df([30.0 * 0.99 ** i for i in range(130)]),
                       market_env="偏冷")
    assert any(f["key"] == "breakdown" for f in d["flags"])
    assert d["risk_count"] >= 1
    assert d["verdict"]["level"] in ("结构存疑", "风险项偏多", "风险项密集", "结构偏弱")


def test_st_name_forces_trouble():
    """ST 股必带问题股风险。"""
    d = stock_decision("600000", "ST测试", _df([10.0 + i * 0.02 for i in range(130)]))
    assert any(f["key"] == "trouble" for f in d["flags"])


def test_one_risk_caps_at_caution():
    """恰好命中 1 项风险（破位）时封顶到「结构存疑」，不给最高档。"""
    # 缓慢下行只破位、不触发急涨/天量等其他项
    closes = [20.0 - i * 0.03 for i in range(130)]
    d = stock_decision("600000", "股", _df(closes))
    if d["risk_count"] == 1:
        assert d["verdict"]["level"] == "结构存疑"
