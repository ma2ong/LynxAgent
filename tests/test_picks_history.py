"""选股留痕与胜率复盘（picks_history）+ 回测交易成本 的回归测试。"""
from datetime import date, timedelta

import pandas as pd
import pytest

from quantcore.quant.backtest import BUY_COST, run_long_only_backtest
from quantcore.quant.local_store import LocalQuantStore


@pytest.fixture()
def store(tmp_path):
    return LocalQuantStore(str(tmp_path / "test.sqlite"))


def _trading_dates(n: int):
    """生成最近 n 个工作日（近似交易日）。"""
    out = []
    d = date.today() - timedelta(days=n * 2 + 5)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


def _seed_kline(store: LocalQuantStore, symbol: str, closes):
    dates = _trading_dates(len(closes))
    df = pd.DataFrame({
        "date": dates, "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": [1e6] * len(closes), "amount": [1e8] * len(closes),
    })
    store.upsert_kline(symbol, df)
    return dates


def test_record_picks_keeps_first_snapshot_of_day(store):
    n = store.record_picks("smart", [{
        "symbol": "600001", "name": "涨股", "score": 90, "close": 10.0,
        "patterns": [{"name": "趋势强"}],
    }])
    assert n == 1
    # 当日重复写入被忽略，保留首次快照
    store.record_picks("smart", [{"symbol": "600001", "name": "涨股", "score": 95, "close": 999}])
    row = store._conn().execute(
        "SELECT score, close FROM picks_history WHERE pool='smart' AND symbol='600001'"
    ).fetchone()
    assert row == (90.0, 10.0)


def test_record_picks_skips_invalid_symbols(store):
    assert store.record_picks("smart", [{"symbol": "", "score": 1}, {"symbol": "AAPL", "score": 1}]) == 0


def test_evaluate_picks_t_plus_n_returns_and_win_rate(store):
    up = [10.0 * 1.02 ** i for i in range(8)]
    down = [10.0 * 0.98 ** i for i in range(8)]
    dates = _seed_kline(store, "600001", up)
    _seed_kline(store, "600002", down)
    conn = store._conn()
    for sym, closes in (("600001", up), ("600002", down)):
        conn.execute(
            "INSERT OR IGNORE INTO picks_history VALUES (?,?,?,?,?,?,?,?)",
            (dates[1], "pattern", sym, sym, 88.0, closes[1], 1, "金叉"),
        )
    conn.commit()

    stats = store.evaluate_picks(days=60)
    pat = next(p for p in stats["pools"] if p["pool"] == "pattern")
    t3 = pat["horizons"]["t3"]
    assert t3["samples"] == 2
    assert t3["win_rate"] == pytest.approx(0.5)
    up_item = next(i for i in stats["items"] if i["symbol"] == "600001")
    assert up_item["t3"] == pytest.approx((1.02 ** 3 - 1) * 100, abs=0.05)
    down_item = next(i for i in stats["items"] if i["symbol"] == "600002")
    assert down_item["t3"] < 0


def test_evaluate_picks_future_bars_missing_gives_none(store):
    up = [10.0] * 30
    _seed_kline(store, "600003", up)
    store.record_picks("swing", [{"symbol": "600003", "name": "x", "score": 70, "close": 10.0}])
    stats = store.evaluate_picks(days=7)
    item = next(i for i in stats["items"] if i["symbol"] == "600003")
    # 今日留痕，T+N 未来 bar 不存在 → 待更新
    assert item["t1"] is None and item["t5"] is None


def test_backtest_applies_transaction_costs():
    closes = [10.0 * 1.02 ** i for i in range(8)]
    dates = pd.to_datetime(_trading_dates(8))
    df = pd.DataFrame({
        "date": dates, "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": [1e6] * 8, "amount": [1e8] * 8,
    })
    res = run_long_only_backtest(
        "600001", df, lambda d: pd.Series([True] * len(d), index=d.index), "always_in", 100000,
    )
    gross = 1.02 ** 7 - 1
    expected = (1 + 0.02 - BUY_COST) * (1.02 ** 6) - 1
    assert res.trades == 1
    assert res.total_return < gross
    assert res.total_return == pytest.approx(expected, abs=1e-3)
