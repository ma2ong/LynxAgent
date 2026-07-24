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
    assert "单一" in g["action"] and "多维" in g["action"]
    assert "无条件了结" not in g["action"]
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
        "600001": {
            "close": 9.0, "ma10": 10.0, "ma20": 11.0, "ma60": 12.0,
            "pct": -7.0, "amount": 8e8, "prev_close": 11.5,
            "prev_ma10": 10.2, "prev_ma20": 10.8, "amount_ratio": 1.8,
            "capital_flow_5d": -45, "return_20d": -20, "close_position": 0.1,
        },   # 中期破位+放量+资金代理流出+相对弱势 → 退出
        "600002": {
            "close": 9.8, "ma10": 10.0, "ma20": 10.0, "ma60": 9.0,
            "pct": -1.0, "amount": 3e8, "prev_close": 10.1,
            "prev_ma10": 10.0, "prev_ma20": 10.0, "amount_ratio": 0.8,
            "capital_flow_5d": 5, "close_position": 0.6,
        },   # 单一破位且缩量、仍在MA60上方 → 持有观察
        "600003": {"close": 12.0, "ma10": 10.0, "ma20": 9.0, "pct": 2.0, "amount": 5e8},    # 均线上方 → 不报
        "600004": {"close": 5.0, "ma10": 4.9, "ma20": 4.8, "pct": 0.5, "amount": 1e8},      # 均线上方但 ST → 问题股
    }
    names = {"600001": "甲", "600002": "乙", "600003": "丙", "600004": "ST丁"}
    res = scan_sell_signals(metrics, names, bad_forecast=set(), limit=50)
    hit = {i["symbol"]: i for i in res["items"]}
    assert set(hit) == {"600001", "600002", "600004"}
    assert res["breakdown_count"] == 2
    assert hit["600001"]["signal"] == "退出/止损" and hit["600001"]["severity"] == 3
    assert hit["600001"]["layer"] == "new_breakdown"
    assert hit["600002"]["signal"] == "持有观察"
    assert "中期结构仍在 MA60" in "；".join(hit["600002"]["protect_factors"])
    assert hit["600004"]["signal"] == "减仓防守"
    assert res["actionable_count"] == 2
    assert res["layer_counts"]["new_breakdown"] == 2
    assert "基本面硬风险" in "；".join(hit["600004"]["risk_factors"])
    # 严重度优先：600001 排在最前
    assert res["items"][0]["symbol"] == "600001"


def test_sell_scan_bad_forecast_flags_trouble():
    metrics = {"600009": {"close": 12.0, "ma10": 10.0, "ma20": 9.0, "pct": 1.0, "amount": 2e8}}
    flags = {"600009": {"bad_forecast": True, "forecast_type": "首亏", "change": "-120%"}}
    res = scan_sell_signals(
        metrics, {"600009": "戊"}, bad_forecast={"600009"},
        fundamental_flags=flags, limit=10,
    )
    item = res["items"][0]
    assert item["signal"] == "减仓防守"
    assert "首亏" in "；".join(item["risk_factors"])


def test_sell_scan_separates_confirmed_and_persistent_weakness():
    metrics = {
        "600010": {
            "close": 8.8, "ma10": 10.0, "ma20": 10.5, "pct": -2.5, "amount": 5e8,
            "prev_close": 9.1, "prev_ma10": 10.1, "prev_ma20": 10.6, "amount_ratio": 1.0,
        },
        "600011": {
            "close": 8.9, "ma10": 10.0, "ma20": 10.5, "pct": 0.2, "amount": 2e8,
            "prev_close": 8.88, "prev_ma10": 10.1, "prev_ma20": 10.6, "amount_ratio": 0.7,
        },
    }
    res = scan_sell_signals(metrics, {"600010": "确认", "600011": "弱势"}, limit=10)
    hit = {item["symbol"]: item for item in res["items"]}

    assert hit["600010"]["layer"] == "confirmed_breakdown"
    assert hit["600010"]["signal"] == "持有观察"
    assert hit["600011"]["layer"] == "persistent_weakness"
    assert hit["600011"]["severity"] == 1
    assert hit["600011"]["signal"] == "持有观察"
    assert res["actionable_count"] == 0


def test_tongfu_realtime_rebound_cancels_previous_breakdown_exit():
    metrics = {
        "002156": {
            "close": 69.82, "ma10": 71.73, "ma20": 71.06, "ma60": 65.16,
            "pct": -6.78, "amount": 143.63e8, "prev_close": 74.90,
            "prev_ma10": 71.20, "prev_ma20": 70.50, "amount_ratio": 1.14,
            "capital_flow_5d": -2.64, "return_20d": -6.46,
            "close_position": 0.07, "lower_shadow": 0.0,
        },
        "000001": {"close": 12, "ma10": 11, "ma20": 10, "pct": 1.8},
        "000002": {"close": 15, "ma10": 14, "ma20": 13, "pct": 1.6},
    }
    realtime = {
        "002156": {
            "price": 73.84,
            "change_percent": 5.76,
        }
    }

    result = scan_sell_signals(
        metrics,
        {"002156": "通富微电", "000001": "样本甲", "000002": "样本乙"},
        realtime_quotes=realtime,
        limit=10,
    )
    item = next(row for row in result["items"] if row["symbol"] == "002156")

    assert item["signal"] == "反包观察"
    assert item["severity"] == 1
    assert item["current_price"] == 73.84
    assert any("已收复 MA10/MA20" in text for text in item["protect_factors"])
    assert item["reason"].endswith("跌破均线未被单独视为卖出依据。")
