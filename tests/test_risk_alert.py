"""风险预警：市场级仪表分档 + 全市场卖出信号扫描。"""
from quantcore.quant.risk_alert import market_risk_gauge, scan_sell_signals


def _daily(seq):
    """seq: [(median_pct, breadth_up), ...] 最新在前 → recent_daily_breadth 结构"""
    return [{"median_pct": m, "breadth_up": b} for m, b in seq]


def test_gauge_calm_market_is_safe():
    g = market_risk_gauge(_daily([(1.2, 0.62), (0.8, 0.58), (0.5, 0.55)]),
                          temp=64.0, limitdown_share=0.002, breakdown_share=0.25)
    assert g["level"] == "安全"
    assert g["score"] < 35


def test_gauge_crash_market_is_dangerous():
    """连续普跌 + 高破位广度 + 跌停潮 → 至少危险，且给出停止买入的动作。"""
    g = market_risk_gauge(_daily([(-3.0, 0.15), (-2.5, 0.20), (-1.5, 0.28), (-2.0, 0.22), (-1.0, 0.35)]),
                          temp=28.0, limitdown_share=0.06, breakdown_share=0.72)
    assert g["level"] in ("危险", "极危")
    assert g["score"] >= 55
    assert "停止" in g["action"] and "买入" in g["action"]
    # 破位广度必须作为一项独立信号出现
    assert any(s["key"] == "breakdown" for s in g["signals"])


def test_gauge_breakdown_breadth_moves_score():
    """单日反弹但 70% 破位：温度中性也要因结构性下跌抬高风险。"""
    base = _daily([(0.3, 0.52), (0.2, 0.51), (0.1, 0.50)])
    low = market_risk_gauge(base, temp=50.0, breakdown_share=0.30)
    high = market_risk_gauge(base, temp=50.0, breakdown_share=0.70)
    assert high["score"] > low["score"] + 15


def test_gauge_handles_missing_optional_signals():
    g = market_risk_gauge(_daily([(0.1, 0.5)]), temp=50.0)
    assert 0 <= g["score"] <= 100
    assert g["level"] in ("安全", "警惕", "危险", "极危")


def test_sell_scan_flags_breakdown_and_trouble():
    metrics = {
        "600001": {"close": 9.0, "ma10": 10.0, "ma20": 11.0, "pct": -5.0, "amount": 8e8},   # 破位+大跌 → 卖出
        "600002": {"close": 9.8, "ma10": 10.0, "ma20": 10.0, "pct": -1.0, "amount": 3e8},   # 破位小跌 → 减仓
        "600003": {"close": 12.0, "ma10": 10.0, "ma20": 9.0, "pct": 2.0, "amount": 5e8},    # 均线上方 → 不报
        "600004": {"close": 5.0, "ma10": 4.9, "ma20": 4.8, "pct": 0.5, "amount": 1e8},      # 均线上方但 ST → 问题股
    }
    names = {"600001": "甲", "600002": "乙", "600003": "丙", "600004": "ST丁"}
    res = scan_sell_signals(metrics, names, bad_forecast=set(), limit=50)
    hit = {i["symbol"]: i for i in res["items"]}
    assert set(hit) == {"600001", "600002", "600004"}
    assert res["breakdown_count"] == 2
    assert hit["600001"]["signal"] == "卖出" and hit["600001"]["severity"] == 2
    assert hit["600002"]["signal"] == "减仓/回避"
    assert "问题股" in hit["600004"]["reason"]
    # 严重度优先：600001 排在最前
    assert res["items"][0]["symbol"] == "600001"


def test_sell_scan_bad_forecast_flags_trouble():
    metrics = {"600009": {"close": 12.0, "ma10": 10.0, "ma20": 9.0, "pct": 1.0, "amount": 2e8}}
    res = scan_sell_signals(metrics, {"600009": "戊"}, bad_forecast={"600009"}, limit=10)
    assert res["items"][0]["reason"].startswith("问题股")
