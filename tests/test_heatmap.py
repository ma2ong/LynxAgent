"""行业热力图（latest_daily_stats 兜底 + heatmap 聚合）回归测试。"""
from datetime import date, timedelta

import pandas as pd
import pytest

from quantcore.quant.local_store import LocalQuantStore


@pytest.fixture()
def store(tmp_path):
    return LocalQuantStore(str(tmp_path / "test.sqlite"))


def _kline(days_ago_closes):
    """{天数前: (close, amount)} -> DataFrame"""
    rows = []
    for days_ago, (close, amount) in days_ago_closes.items():
        d = (date.today() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        rows.append({"date": d, "open": close, "high": close, "low": close,
                     "close": close, "volume": 1000, "amount": amount})
    return pd.DataFrame(rows)


def test_latest_daily_stats_pct_and_amount(store):
    store.upsert_kline("600001", _kline({1: (11.0, 5e8), 2: (10.0, 4e8)}))
    stats = store.latest_daily_stats()
    assert stats["600001"]["pct"] == 10.0
    assert stats["600001"]["amount"] == 5e8


def test_latest_daily_stats_skips_placeholder_bars(store):
    # amount=0 的占位 bar 不参与（同 recent_returns 约定）
    store.upsert_kline("600002", _kline({1: (12.0, 0), 2: (11.0, 3e8), 3: (10.0, 3e8)}))
    stats = store.latest_daily_stats()
    assert stats["600002"]["pct"] == 10.0  # 11 vs 10，跳过 amount=0 的 12


def test_latest_daily_stats_needs_two_bars(store):
    store.upsert_kline("600003", _kline({1: (10.0, 1e8)}))
    assert "600003" not in store.latest_daily_stats()


from quantcore.quant.heatmap import build_heatmap_industry, build_heatmap_stocks

SNAP = {
    # 腾讯口径：total_mv 单位亿
    "600001": {"name": "甲", "pct_chg": 5.0, "amount": 8e8, "total_mv": 200.0},
    "600002": {"name": "乙", "pct_chg": -1.0, "amount": 4e8, "total_mv": 100.0},
    # 东财口径：total_mv 单位元（>1e6 判定）
    "600003": {"name": "丙", "pct_chg": 2.0, "amount": 2e8, "total_mv": 5e10},
    # 无市值：用成交额（亿）兜底做面积
    "600004": {"name": "丁", "pct_chg": 0.5, "amount": 3e8, "total_mv": None},
    # 无涨跌幅：跳过
    "600005": {"name": "戊", "pct_chg": None, "amount": 1e8, "total_mv": 50.0},
}
IND = {"600001": "白酒", "600002": "白酒", "600003": "银行"}  # 600004 未映射 -> 其他


def test_build_heatmap_industry_aggregates_and_weights():
    items = build_heatmap_industry(SNAP, IND)
    by_name = {i["name"]: i for i in items}
    assert set(by_name) == {"白酒", "银行", "其他"}
    baijiu = by_name["白酒"]
    assert baijiu["count"] == 2
    assert baijiu["value"] == 300.0  # 200 + 100 亿
    assert baijiu["pct"] == 3.0      # (5*200 + -1*100) / 300 市值加权
    assert by_name["银行"]["value"] == 500.0   # 5e10 元 -> 500 亿
    assert by_name["其他"]["value"] == 3.0     # 3e8 成交额 -> 3 亿兜底
    # 面积降序
    assert [i["name"] for i in items] == ["银行", "白酒", "其他"]


def test_build_heatmap_stocks_filters_by_industry():
    items = build_heatmap_stocks(SNAP, IND, "白酒")
    assert [i["symbol"] for i in items] == ["600001", "600002"]  # 按面积降序
    assert items[0]["pct"] == 5.0 and items[0]["value"] == 200.0
    assert build_heatmap_stocks(SNAP, IND, "其他")[0]["symbol"] == "600004"
    assert build_heatmap_stocks(SNAP, IND, "不存在") == []
