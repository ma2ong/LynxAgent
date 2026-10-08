"""业绩预告标签（2026-10-08 Allen 定：只在智选里打标签，不进排序）。

依据见 experiments/README.md 同日段：强预增（预增/扭亏 ≥50%）T+60 匹配增量 +2.68pp、
略增/续盈 +1.77pp，都过七闸；公告后 5~39 个交易日再买仍 +2pp。T+5 无效，所以只标注。
"""
from datetime import date, timedelta

from quantcore.quant.local_store import LocalQuantStore, forecast_tag


def _d(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def test_store_keeps_latest_positive_event_per_symbol(tmp_path):
    store = LocalQuantStore(str(tmp_path / "t.sqlite"))
    store.upsert_forecast_events([
        {"symbol": "600001", "ann_date": _d(30), "period": "20260630", "forecast_type": "预增", "change": 80.0},
        {"symbol": "600001", "ann_date": _d(5), "period": "20260930", "forecast_type": "略增", "change": 20.0},
        {"symbol": "600002", "ann_date": _d(90), "period": "20260331", "forecast_type": "预增", "change": 300.0},
    ])
    recent = store.load_recent_forecasts(since=_d(56))
    assert set(recent) == {"600001"}                     # 超过窗口的不要
    assert recent["600001"]["forecast_type"] == "略增"     # 同一只取最近一次


def test_tag_rules():
    assert forecast_tag({"forecast_type": "预增", "change": 120.0})["label"] == "业绩预增 +120%"
    assert forecast_tag({"forecast_type": "扭亏", "change": 60.0})["label"] == "业绩扭亏"
    assert forecast_tag({"forecast_type": "扭亏", "change": None}) is None   # 审计口径要求 ≥50，缺值不算
    assert forecast_tag({"forecast_type": "略增", "change": 15.0})["label"] == "业绩略增 +15%"
    assert forecast_tag({"forecast_type": "续盈", "change": None})["label"] == "业绩续盈"
    assert forecast_tag({"forecast_type": "预增", "change": 30.0}) is None   # 预增不到 50% 不在过闸口径里
    assert forecast_tag({"forecast_type": "预减", "change": -40.0}) is None


def test_sync_extracts_positive_net_profit_events(tmp_path, monkeypatch):
    import sys
    import types

    import pandas as pd

    from quantcore.quant.sync_service import MarketSyncService

    calls = []

    def fake_yjyg(date):
        calls.append(date)
        return pd.DataFrame({
            "股票代码": ["600001", "600001", "600002", "600003"],
            "预测指标": ["归属于上市公司股东的净利润", "营业收入", "归属于上市公司股东的净利润",
                     "归属于上市公司股东的净利润"],
            "预告类型": ["预增", "预增", "预减", "略增"],
            "业绩变动幅度": [120.0, 30.0, -50.0, 15.0],
            "公告日期": ["2026-08-20", "2026-08-20", "2026-08-21", "2026-08-22"],
        })

    monkeypatch.setitem(sys.modules, "akshare", types.SimpleNamespace(stock_yjyg_em=fake_yjyg))
    svc = MarketSyncService(store=LocalQuantStore(str(tmp_path / "t.sqlite")), max_workers=1)
    rows = svc._fetch_forecast_events()
    assert len(calls) == 2                                  # 只拉最近两个报告期
    assert {(r["symbol"], r["forecast_type"]) for r in rows} == {("600001", "预增"), ("600003", "略增")}


def test_smart_items_get_tag_without_score_change(tmp_path, monkeypatch):
    import app.lite_main as lite_main

    store = LocalQuantStore(str(tmp_path / "t.sqlite"))
    store.upsert_forecast_events([{"symbol": "600001", "ann_date": _d(10), "period": "20260930",
                                   "forecast_type": "预增", "change": 200.0}])
    monkeypatch.setattr("quantcore.quant.local_store.get_local_store", lambda: store)
    monkeypatch.setattr("quantcore.quant.data.load_local_kline", lambda *a, **k: None)
    items = [{"symbol": "600001", "smart_score": 80.0}, {"symbol": "600002", "smart_score": 79.0}]
    lite_main._confluence_enrich_items(items)
    assert items[0]["earnings"]["label"] == "业绩预增 +200%"
    assert items[1]["earnings"] is None
    assert "confluence_bonus" not in items[0]               # 只打标签，不加分
