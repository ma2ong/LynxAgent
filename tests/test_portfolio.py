"""模拟组合：整手买入/卖出成本、卖出信号规则、每日快照。"""
from datetime import date, timedelta

import pandas as pd
import pytest

from quantcore.quant.backtest import BUY_COST, SELL_COST
from quantcore.quant.local_store import LocalQuantStore
from quantcore.quant.portfolio import (
    add_position, close_position, list_portfolio, nav_series, sell_signals, settle_daily,
)


@pytest.fixture()
def store(tmp_path):
    return LocalQuantStore(str(tmp_path / "test.sqlite"))


def _trading_dates(n: int):
    out = []
    d = date.today() - timedelta(days=n * 2 + 5)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


def _seed(store, symbol, closes):
    n = len(closes)
    dates = _trading_dates(n)
    df = pd.DataFrame({
        "date": dates, "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1e6] * n, "amount": [1e8] * n,
    })
    store.upsert_kline(symbol, df)
    return dates


def test_add_and_close_position_with_costs(store):
    pos = add_position("u1", "600001", "测试股", price=10.0, budget=10000, store=store)
    # 10000 / (10*(1+0.0003)) = 999.7 → 900 股
    assert pos["shares"] == 900
    assert pos["cost"] == pytest.approx(10.0 * (1 + BUY_COST), abs=1e-4)
    # 重复加同一票被拒
    with pytest.raises(ValueError):
        add_position("u1", "600001", "测试股", price=10.0, store=store)

    res = close_position("u1", pos["id"], price=11.0, store=store)
    expected_pnl = (11.0 * (1 - SELL_COST) - 10.0 * (1 + BUY_COST)) * 900
    assert res["pnl"] == pytest.approx(expected_pnl, abs=0.5)

    snap = list_portfolio("u1", {}, store)
    assert snap["summary"]["open_count"] == 0
    assert snap["summary"]["closed_count"] == 1
    assert snap["summary"]["closed_win_rate"] == 1.0
    assert snap["closed"][0]["pnl"] > 0


def test_sell_signals_rules(store):
    # 下跌趋势：现价远低于 MA20 且亏超 8%
    closes = [20.0 - i * 0.4 for i in range(30)]  # 20 → 8.4
    dates = _seed(store, "600002", closes)
    sigs = sell_signals(store, "600002", cost=15.0, buy_date=dates[5], latest_price=8.4)
    keys = {s["key"] for s in sigs}
    assert "stop_loss" in keys
    assert "below_ma20" in keys
    assert "timeout" in keys  # 持有超 10 交易日且未盈利

    # 上涨趋势：无信号
    closes_up = [10.0 * 1.01 ** i for i in range(30)]
    dates2 = _seed(store, "600003", closes_up)
    sigs2 = sell_signals(store, "600003", cost=10.0, buy_date=dates2[-3], latest_price=closes_up[-1])
    assert sigs2 == []


def test_settle_daily_and_nav(store):
    dates = _seed(store, "600004", [10.0] * 30)
    # 市场基准需要 >=100 只
    for i in range(120):
        _seed(store, f"7{i:05d}", [10.0] * 30)
    add_position("u2", "600004", "股", price=10.0, budget=10000, store=store)
    n = settle_daily(dates[-1], {"600004": 10.5}, store=store)
    assert n == 1
    series = nav_series("u2", store=store)
    assert len(series) == 1
    assert series[0]["pnl_pct"] > 0  # 10.5 vs 成本 10.003
    assert series[0]["bench_cum_pct"] == pytest.approx(0.0, abs=0.01)
