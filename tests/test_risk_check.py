"""七不买避雷：每条规则的命中/不命中边界（板块感知）。"""
import pandas as pd

from quantcore.quant.risk_check import check_risks


def _df(closes, volumes=None):
    n = len(closes)
    return pd.DataFrame({
        "close": closes,
        "volume": volumes if volumes is not None else [1e6] * n,
    })


def _flat(n, price=10.0):
    return [price] * n


def test_surge_flagged_mainboard():
    """主板近10日涨 50%+ 且无调整 → 急涨。"""
    closes = _flat(60) + [10.0 * 1.05 ** i for i in range(11)]  # 10日复利+5%/日 ≈ +63%
    res = check_risks("600001", "测试股", _df(closes))
    assert any(f["key"] == "surge" for f in res["flags"])
    assert res["risk_count"] >= 1


def test_surge_threshold_is_board_aware():
    """同样 +63%：主板（阈值50%）命中，创业板（阈值100%）不命中。"""
    closes = _flat(60) + [10.0 * 1.05 ** i for i in range(11)]
    assert any(f["key"] == "surge" for f in check_risks("600001", "股", _df(closes))["flags"])
    assert not any(f["key"] == "surge" for f in check_risks("300001", "股", _df(closes))["flags"])


def test_surge_with_pullback_not_flagged():
    """涨得多但中途回撤 >8% → 不算「无调整急涨」。"""
    up = [10.0 * 1.05 ** i for i in range(6)]
    dip = [up[-1] * 0.9]  # 回撤10%
    more = [dip[-1] * 1.06 ** i for i in range(5)]
    closes = _flat(60) + up + dip + more
    res = check_risks("600001", "股", _df(closes))
    assert not any(f["key"] == "surge" for f in res["flags"])


def test_flat_is_info_not_risk():
    """长期横盘是提示不是雷（大基底盘整可能是机会）。"""
    res = check_risks("600001", "股", _df(_flat(80)))
    flat = [f for f in res["flags"] if f["key"] == "flat"]
    assert flat and flat[0]["level"] == "info"
    assert res["risk_count"] == 0
    assert "未命中" in res["advice"]


def test_volume_spike_at_high_flagged():
    """高位放天量：当日量 4 倍于前5日均量且处于60日高位。"""
    closes = _flat(50) + [10.0 * 1.02 ** i for i in range(11)]  # 缓涨到高位
    vols = [1e6] * 60 + [5e6]  # 最后一日 5 倍量
    res = check_risks("600001", "股", _df(closes, vols))
    assert any(f["key"] == "volume_spike" for f in res["flags"])


def test_volume_spike_at_low_not_flagged():
    """低位放量不是雷（可能是启动），不命中天量。"""
    closes = [10.0 * 0.99 ** i for i in range(60)] + [6.0]  # 一路阴跌在低位
    vols = [1e6] * 60 + [5e6]
    res = check_risks("600001", "股", _df(closes, vols))
    assert not any(f["key"] == "volume_spike" for f in res["flags"])


def test_stall_flagged():
    """高位放量滞涨：近5日量增1.5倍但涨幅<2%，且处于高位。"""
    closes = _flat(40) + [10.0 * 1.02 ** i for i in range(15)] + [13.46] * 6  # 涨到高位后横住
    vols = [1e6] * 55 + [2e6] * 6  # 近5日量翻倍
    res = check_risks("600001", "股", _df(closes, vols))
    assert any(f["key"] == "stall" for f in res["flags"])


def test_breakdown_flagged():
    """收盘同时跌破 MA10/MA20 → 破位。"""
    closes = _flat(40, 12.0) + [12.0 * 0.985 ** i for i in range(15)]  # 连跌15日
    res = check_risks("600001", "股", _df(closes))
    assert any(f["key"] == "breakdown" for f in res["flags"])


def test_trouble_stock_st_and_forecast():
    assert any(f["key"] == "trouble" for f in
               check_risks("600001", "*ST某某", _df(_flat(70)))["flags"])
    assert any(f["key"] == "trouble" for f in
               check_risks("600001", "正常股", _df(_flat(70)), bad_forecast=True)["flags"])


def test_advice_escalates_with_risk_count():
    """两项以上风险 → 回避；一项 → 谨慎。"""
    # 急涨 + 天量 同时命中
    closes = _flat(50) + [10.0 * 1.05 ** i for i in range(11)]
    vols = [1e6] * 60 + [5e6]
    res = check_risks("600001", "股", _df(closes, vols))
    assert res["risk_count"] >= 2
    assert res["advice"].startswith("回避")
    assert "不构成投资建议" in res["advice"]


def test_short_history_degrades_gracefully():
    """数据不足时只跑可跑的规则，不抛异常。"""
    res = check_risks("600001", "股", _df(_flat(5)))
    assert isinstance(res["flags"], list)
