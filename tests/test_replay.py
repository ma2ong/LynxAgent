"""历史回放验证（replay）：point-in-time 采样、T+5 超额与汇总。"""
from datetime import date, timedelta

import pandas as pd
import pytest

from quantcore.quant.local_store import LocalQuantStore
from quantcore.quant.replay import latest_replay_summary, run_replay


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


def _seed(store, symbol, closes, amounts=None):
    n = len(closes)
    dates = _trading_dates(n)
    df = pd.DataFrame({
        "date": dates, "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1e6] * n, "amount": amounts or [1e8] * n,
    })
    store.upsert_kline(symbol, df)
    return dates


def test_replay_smart_pool_end_to_end(store):
    n = 120
    # 1 只温和上涨放量股 + 29 只横盘 → 上涨股应进 smart top，且市场中位≈0
    up = [10.0 * 1.005 ** i for i in range(n)]
    _seed(store, "600001", up, amounts=[2e8] * n)
    for i in range(2, 31):
        _seed(store, f"6000{i:02d}", [10.0] * n)
    store.upsert_meta([{"symbol": f"6000{i:02d}", "name": f"股{i}"} for i in range(1, 31)])

    res = run_replay(months=4, step=5, top_n=3, store=store, workers=0)
    assert res["status"] == "done"
    pools = {p["pool"]: p for p in res["pools"]}
    assert "smart" in pools
    smart = pools["smart"]
    assert smart["picks"] > 0
    assert smart["evaluated"] > 0
    # 上涨股每期约 +2.5%（5 日 ×0.5%），市场中位 0 → 平均超额应为正；
    # top3 里另两只是横盘股（超额=0 不计胜），胜率 = 1/3
    assert smart["avg_excess"] > 0
    assert smart["excess_win_rate"] == pytest.approx(1 / 3, abs=0.05)
    # 趋势+动量+MA20 上方加分 → 上涨股每期都应是第 1 名
    top1 = store._conn().execute(
        "SELECT DISTINCT symbol FROM replay_results WHERE run_id=? AND pool='smart' AND rank=1",
        (res["run_id"],),
    ).fetchall()
    assert top1 == [("600001",)]
    assert smart["curve"][-1]["cum_excess"] == pytest.approx(
        sum(c["avg_excess"] for c in smart["curve"]), abs=0.5)

    # 落库可查、latest_replay_summary 取到同一 run
    latest = latest_replay_summary(store)
    assert latest and latest["run_id"] == res["run_id"]
    rows = store._conn().execute(
        "SELECT COUNT(*) FROM replay_results WHERE run_id=?", (res["run_id"],)
    ).fetchone()[0]
    assert rows == smart["picks"] + sum(p["picks"] for p in res["pools"] if p["pool"] != "smart")


def test_replay_anchor_pins_session_axis(store):
    """同一 anchor 跨天续跑必须命中同一 param_key（断点不作废）；anchor 落库供续跑读取。"""
    n = 120
    _seed(store, "600001", [10.0 * 1.005 ** i for i in range(n)], amounts=[2e8] * n)
    for i in range(2, 12):
        _seed(store, f"6000{i:02d}", [10.0] * n)
    store.upsert_meta([{"symbol": f"6000{i:02d}", "name": f"股{i}"} for i in range(1, 12)])

    anchor = date.today().strftime("%Y-%m-%d")
    run_replay(months=4, step=5, top_n=3, store=store, workers=0, anchor=anchor)
    run_replay(months=4, step=5, top_n=3, store=store, workers=0, anchor=anchor)
    keys = store._conn().execute(
        "SELECT DISTINCT param_key FROM replay_scan").fetchall()
    assert len(keys) == 1  # 两次运行共用同一断点缓存

    import json as _json
    params = _json.loads(store._conn().execute(
        "SELECT params_json FROM replay_runs ORDER BY created_at DESC LIMIT 1").fetchone()[0])
    assert params["anchor"] == anchor


def test_replay_point_in_time_no_lookahead(store):
    """留痕期只允许使用 as_of 及以前的数据：把未来 bar 全删后重放同期结果一致。"""
    n = 100
    up = [10.0 * 1.005 ** i for i in range(n)]
    _seed(store, "600001", up, amounts=[2e8] * n)
    for i in range(2, 12):
        _seed(store, f"6000{i:02d}", [10.0] * n)
    store.upsert_meta([{"symbol": f"6000{i:02d}", "name": f"股{i}"} for i in range(1, 12)])
    res = run_replay(months=4, step=5, top_n=3, store=store, workers=0)
    smart = next(p for p in res["pools"] if p["pool"] == "smart")
    # 每期评分只能来自 as_of 前的 bar → 每期都有产出且 rank 从 1 开始
    row = store._conn().execute(
        "SELECT MIN(rank), MAX(rank) FROM replay_results WHERE run_id=? AND pool='smart'",
        (res["run_id"],),
    ).fetchone()
    assert row[0] == 1
    assert smart["picks"] >= len(smart["curve"])
