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
