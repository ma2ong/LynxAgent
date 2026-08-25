"""选股留痕与胜率复盘（picks_history）+ 回测交易成本 的回归测试。"""
from datetime import date, timedelta

import pandas as pd
import pytest

from quantcore.quant.backtest import BUY_COST, run_long_only_backtest
from quantcore.quant import local_store
from quantcore.quant.local_store import LocalQuantStore


@pytest.fixture()
def store(tmp_path):
    return LocalQuantStore(str(tmp_path / "test.sqlite"))


@pytest.fixture()
def pick_clock(monkeypatch):
    """把留痕时点钉在开盘后，并允许测试自己拨表——否则用例在 09:25 前跑会全灭。"""
    from datetime import datetime, timedelta

    state = {"now": datetime.combine(date.today(), datetime.min.time()).replace(hour=10)}
    monkeypatch.setattr(local_store, "_now_cn", lambda: state["now"])

    def advance(**kw):
        state["now"] += timedelta(**kw)

    state["advance"] = advance
    return state


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


def test_record_picks_keeps_first_snapshot_of_day(store, pick_clock):
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


def test_record_picks_replaces_latest_batch_without_changing_history(store, pick_clock):
    """盘中重扫只换 latest_picks，历史留痕锁定当日第一份。

    否则留痕条数取决于当天有多少人刷新页面，复盘样本会被访问量污染。
    """
    store.record_picks("smart", [
        {"symbol": "600001", "name": "旧一", "score": 90, "close": 10.0},
        {"symbol": "600002", "name": "旧二", "score": 80, "close": 9.0},
    ])
    pick_clock["advance"](hours=3)
    store.record_picks("smart", [
        {"symbol": "600003", "name": "新一", "score": 88, "close": 12.0},
    ])

    history = store._conn().execute(
        "SELECT symbol FROM picks_history WHERE pool='smart' ORDER BY symbol"
    ).fetchall()
    latest = store.load_latest_picks("smart")

    assert history == [("600001",), ("600002",)]
    assert [(item["symbol"], item["rank"]) for item in latest] == [("600003", 1)]
    assert latest[0]["batch_at"]


def test_record_picks_skips_history_before_open(store, pick_clock):
    """盘前预热扫描拿的是昨收，绝不能进历史；latest_picks 仍要更新（首页得有货）。"""
    pick_clock["now"] = pick_clock["now"].replace(hour=9, minute=0)
    assert store.record_picks("smart", [
        {"symbol": "600001", "name": "盘前", "score": 90, "close": 10.0},
    ]) == 1
    assert store._conn().execute(
        "SELECT COUNT(*) FROM picks_history WHERE pool='smart'").fetchone()[0] == 0
    assert [i["symbol"] for i in store.load_latest_picks("smart")] == ["600001"]

    # 开盘后第一份才是留痕
    pick_clock["now"] = pick_clock["now"].replace(hour=9, minute=35)
    store.record_picks("smart", [{"symbol": "600002", "name": "盘后", "score": 88, "close": 12.0}])
    assert store._conn().execute(
        "SELECT symbol FROM picks_history WHERE pool='smart'").fetchall() == [("600002",)]


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


def test_evaluate_picks_future_bars_missing_gives_none(store, pick_clock):
    up = [10.0] * 30
    _seed_kline(store, "600003", up)
    store.record_picks("swing", [{"symbol": "600003", "name": "x", "score": 70, "close": 10.0}])
    stats = store.evaluate_picks(days=7)
    item = next(i for i in stats["items"] if i["symbol"] == "600003")
    # 今日留痕，T+N 未来 bar 不存在 → 待更新
    assert item["t1"] is None and item["t5"] is None


def test_evaluate_picks_excess_vs_market_median(store):
    """超额收益 = 个股 T+N 收益 − 同期全市场中位收益。"""
    # 市场由 5 只股票构成：1 只大涨、1 只大跌、3 只横盘 → 中位 = 0
    up = [10.0 * 1.02 ** i for i in range(8)]
    down = [10.0 * 0.98 ** i for i in range(8)]
    flat = [10.0] * 8
    dates = _seed_kline(store, "600001", up)
    _seed_kline(store, "600002", down)
    for sym in ("600003", "600004", "600005"):
        _seed_kline(store, sym, flat)
    conn = store._conn()
    conn.execute(
        "INSERT OR IGNORE INTO picks_history VALUES (?,?,?,?,?,?,?,?)",
        (dates[1], "pattern", "600001", "涨股", 88.0, up[1], 1, "金叉"),
    )
    conn.commit()

    stats = store.evaluate_picks(days=60)
    item = next(i for i in stats["items"] if i["symbol"] == "600001")
    expected_ret = (1.02 ** 3 - 1) * 100
    assert item["t3"] == pytest.approx(expected_ret, abs=0.05)
    # 市场中位 = 横盘股 0% → 超额 ≈ 绝对收益
    assert item["excess_t3"] == pytest.approx(expected_ret, abs=0.05)
    pat = next(p for p in stats["pools"] if p["pool"] == "pattern")
    t3 = pat["horizons"]["t3"]
    assert t3["excess_win_rate"] == pytest.approx(1.0)
    assert t3["avg_excess"] == pytest.approx(expected_ret, abs=0.05)


def test_evaluate_picks_low_coverage_day_not_ready(store):
    """T+N 目标日市场覆盖率 <60% 时该 horizon 记为未就绪（不污染统计）。"""
    flat = [10.0] * 8
    dates = None
    for sym in ("600011", "600012", "600013", "600014", "600015",
                "600016", "600017", "600018", "600019", "600020"):
        dates = _seed_kline(store, sym, flat)
    conn = store._conn()
    # 制造缺口：最后一个交易日仅保留 2/10 只股票的日线（20% < 60%）
    conn.execute(
        "DELETE FROM daily_kline WHERE date = ? AND symbol NOT IN ('600011','600012')",
        (dates[-1],),
    )
    conn.execute(
        "INSERT OR IGNORE INTO picks_history VALUES (?,?,?,?,?,?,?,?)",
        (dates[-2], "smart", "600011", "留痕股", 80.0, 10.0, 1, ""),
    )
    conn.commit()

    stats = store.evaluate_picks(days=60)
    item = next(i for i in stats["items"] if i["symbol"] == "600011")
    # 600011 自身在缺口日有 bar，但市场截面残缺 → t1 必须为 None
    assert item["t1"] is None and item["excess_t1"] is None
    smart = next(p for p in stats["pools"] if p["pool"] == "smart")
    assert smart["horizons"]["t1"]["samples"] == 0


def test_signal_stats_pattern_level_excess(store):
    """signal_stats：按形态名聚合留痕 T+5 超额，供入选理由卡展示信号历史表现。"""
    up = [10.0 * 1.02 ** i for i in range(10)]
    flat = [10.0] * 10
    dates = _seed_kline(store, "600001", up)
    for sym in ("600002", "600003", "600004"):
        _seed_kline(store, sym, flat)
    conn = store._conn()
    conn.execute(
        "INSERT OR IGNORE INTO picks_history VALUES (?,?,?,?,?,?,?,?)",
        (dates[1], "pattern", "600001", "涨股", 88.0, up[1], 1, "金叉,放量突破"),
    )
    conn.commit()

    res = store.signal_stats("pattern", days=60)
    assert res["pool"] == "pattern"
    assert res["live_picks"] == 1
    names = {p["name"]: p for p in res["patterns"]}
    assert set(names) == {"金叉", "放量突破"}
    expected = (1.02 ** 5 - 1) * 100
    assert names["金叉"]["samples"] == 1
    assert names["金叉"]["avg_excess"] == pytest.approx(expected, abs=0.1)
    assert names["金叉"]["excess_win_rate"] == 1.0


def test_evaluate_picks_cache_invalidates_on_new_picks(store, pick_clock):
    """留痕统计缓存：同数据版本命中缓存；新留痕写入后必须失效（否则新票永远不进统计）。"""
    _seed_kline(store, "600001", [10.0 * 1.02 ** i for i in range(8)])
    _seed_kline(store, "600002", [10.0 * 0.98 ** i for i in range(8)])
    store.record_picks("smart", [{"symbol": "600001", "name": "涨股", "score": 90, "close": 10.0}])

    first = store.evaluate_picks(days=30)
    assert first["total_picks"] == 1
    cached = store.evaluate_picks(days=30)
    assert cached["total_picks"] == 1  # 命中缓存，结果一致

    # 新留痕落库 → 缓存 key 变化 → 统计必须包含新票（同池当日只留一份，故拨到次日）
    pick_clock["advance"](days=1)
    store.record_picks("smart", [{"symbol": "600002", "name": "跌股", "score": 80, "close": 10.0}])
    after = store.evaluate_picks(days=30)
    assert after["total_picks"] == 2
    assert {i["symbol"] for i in after["items"]} == {"600001", "600002"}
    assert [item["symbol"] for item in after["latest"]] == ["600002"]


def test_signal_stats_cached_within_day(store, monkeypatch, pick_clock):
    """signal_stats 全量重算约 20s：同日同数据版本必须命中缓存，refresh=True 强制重算。"""
    _seed_kline(store, "600001", [10.0 * 1.02 ** i for i in range(8)])
    store.record_picks("smart", [{"symbol": "600001", "name": "涨股", "score": 90, "close": 10.0}])

    calls = {"n": 0}
    orig = LocalQuantStore.evaluate_picks

    def counting(self, *a, **kw):
        calls["n"] += 1
        return orig(self, *a, **kw)

    monkeypatch.setattr(LocalQuantStore, "evaluate_picks", counting)
    first = store.signal_stats("smart")
    second = store.signal_stats("smart")
    assert calls["n"] == 1  # 第二次命中缓存
    assert second["pool"] == first["pool"] and second["live_picks"] == first["live_picks"]

    store.signal_stats("smart", refresh=True)
    assert calls["n"] == 2  # 强制重算


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


def test_first_seen_records_every_new_entrant_at_its_own_price(store, pick_clock):
    """首推价：盘中每只新上榜的票都要记下自己那一刻的价格，且只记第一次。

    与 picks_history 的「当日只留一份」区分开——13:00 才进榜的票在留痕表里没有行，
    但名单上必须显示它是在什么价位被推的。
    """
    store.record_first_seen("smart", [{"symbol": "600001", "close": 10.0}])
    pick_clock["advance"](hours=2)
    store.record_first_seen("smart", [
        {"symbol": "600001", "close": 12.0},   # 已记过，价格不许被改写
        {"symbol": "600002", "close": 20.0},   # 盘中新进榜，按此刻价格记
    ])

    seen = store.load_first_seen("smart")
    assert seen["600001"]["first_price"] == 10.0
    assert seen["600002"]["first_price"] == 20.0


def test_first_seen_skips_before_open(store, pick_clock):
    """盘前那一轮拿的是昨收，记进去会让「首推后涨跌」凭空多出一整天的涨幅。"""
    pick_clock["now"] = pick_clock["now"].replace(hour=9, minute=0)
    assert store.record_first_seen("smart", [{"symbol": "600001", "close": 10.0}]) == 0
    assert store.load_first_seen("smart") == {}

    pick_clock["now"] = pick_clock["now"].replace(hour=9, minute=40)
    store.record_first_seen("smart", [{"symbol": "600001", "close": 10.6}])
    assert store.load_first_seen("smart")["600001"]["first_price"] == 10.6


def test_first_seen_write_gives_up_instead_of_blocking(store, pick_clock, monkeypatch):
    """写锁被长任务占住时直接放弃这一轮，不能拖住调用方（名单请求的热路径）。"""
    import sqlite3

    def locked(*a, **kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(sqlite3, "connect", locked)
    assert store.record_first_seen("smart", [{"symbol": "600001", "close": 10.0}]) == 0
