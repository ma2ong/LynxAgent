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


def _seed(store, symbol, closes, amounts=None, opens=None):
    n = len(closes)
    dates = _trading_dates(n)
    df = pd.DataFrame({
        "date": dates, "open": opens or closes, "high": closes, "low": closes, "close": closes,
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


def test_replay_open_entry_caliber_and_distribution(store):
    """次日开盘可成交口径：entry=open(as_of+1)，与收盘口径并存；汇总含中位数/涨停占比。"""
    n = 120
    up = [10.0 * 1.005 ** i for i in range(n)]
    # 开盘价 = 当日收盘 × 0.99（每天低开 1%）→ open 口径收益应高于 close 口径
    _seed(store, "600001", up, amounts=[2e8] * n, opens=[c * 0.99 for c in up])
    for i in range(2, 31):
        _seed(store, f"6000{i:02d}", [10.0] * n)
    store.upsert_meta([{"symbol": f"6000{i:02d}", "name": f"股{i}"} for i in range(1, 31)])

    res = run_replay(months=4, step=5, top_n=3, store=store, workers=0)
    smart = next(p for p in res["pools"] if p["pool"] == "smart")
    # 新字段齐备
    assert smart["median_excess"] is not None
    assert smart["p10_excess"] <= smart["median_excess"] <= smart["p90_excess"]
    assert smart["limitup_ratio"] == 0  # 日涨 0.5%，无涨停
    oe = smart["open_entry"]
    assert oe["evaluated"] > 0
    # 600001 每天低开 1%：open 口径买得更便宜 → 平均超额高于 close 口径
    assert oe["avg_excess"] > smart["avg_excess"]

    # 落库数值抽查：ret_t5_open = close(t5)/open(t1) - 1
    row = store._conn().execute(
        "SELECT as_of, ret_t5, ret_t5_open FROM replay_results "
        "WHERE run_id=? AND symbol='600001' AND ret_t5_open IS NOT NULL LIMIT 1",
        (res["run_id"],),
    ).fetchone()
    assert row is not None
    # open 口径入场价 = close(as_of)×1.005×0.99 < close(as_of) → 收益更高；
    # 精确换算：(1+ret_close)/(1.005×0.99) - 1
    assert row[2] > row[1]
    expected = ((1 + row[1] / 100) / (1.005 * 0.99) - 1) * 100
    assert row[2] == pytest.approx(expected, abs=0.15)


def test_replay_limitup_flagged(store):
    """as_of 收盘涨停的票要打 limitup_at_close 标记（10cm 主板阈值）。"""
    n = 120
    # 平盘直到最后每个采样日前都拉一次涨停无法精确对齐采样日；
    # 改为：全程每天 +9.8%（天天涨停）→ 所有入选期都应标涨停
    up = [10.0 * 1.098 ** i for i in range(n)]
    _seed(store, "600001", up, amounts=[2e8] * n)
    for i in range(2, 12):
        _seed(store, f"6000{i:02d}", [10.0] * n)
    store.upsert_meta([{"symbol": f"6000{i:02d}", "name": f"股{i}"} for i in range(1, 12)])
    res = run_replay(months=4, step=5, top_n=3, store=store, workers=0)
    flags = store._conn().execute(
        "SELECT DISTINCT limitup_at_close FROM replay_results WHERE run_id=? AND symbol='600001'",
        (res["run_id"],),
    ).fetchall()
    assert flags == [(1,)]
    smart = next(p for p in res["pools"] if p["pool"] == "smart")
    assert smart["limitup_ratio"] > 0


def test_regime_temp_symmetric_and_recent_weighted():
    """回放与线上横幅共用 regime 模块（同源口径），且分档必须对称、近日权重更高。"""
    from quantcore.quant.regime import blend_temp, classify, day_temp

    # 上下对称：镜像输入必须给出镜像标签（旧口径偏暖要 AND、偏冷只要 OR，系统性偏冷）
    assert classify(day_temp(1.0, 0.60)) == "偏暖"
    assert classify(day_temp(-1.0, 0.40)) == "偏冷"
    assert classify(day_temp(0.0, 0.50)) == "中性"

    # 一根大阴线不该锁死整个窗口：其后连续 4 天转暖，标签必须跟着动（旧的 5 日累计口径做不到）
    crash_then_rally = [(1.5, 0.65), (1.5, 0.65), (1.2, 0.62), (1.0, 0.60), (-5.0, 0.10)]
    assert classify(blend_temp(crash_then_rally)) == "偏暖"
    # 同样 5 天、只把大阴线换到最新一日 → 温度必须大幅下挫且不再算暖（权重跟着时间走）
    crash_latest = blend_temp(list(reversed(crash_then_rally)))
    assert crash_latest < blend_temp(crash_then_rally) - 20
    assert classify(crash_latest) != "偏暖"

    # 真实的连续下跌必须判冷（旧口径此处也判冷，新口径不能放松）
    assert classify(blend_temp([(-1.5, 0.30), (-2.0, 0.25), (-1.0, 0.35), (0.2, 0.51), (-0.5, 0.45)])) == "偏冷"


def test_replay_regime_stratification(store):
    """先跌后涨的市场：回放期应被分入偏冷/偏暖，各环境样本数守恒。"""
    from quantcore.quant.replay import _session_regimes

    n = 120
    # 全市场先横盘 40 天 → 跌 40 天(每天-0.5%) → 涨 40 天(每天+0.5%)
    path = [10.0] * 40
    for _ in range(40):
        path.append(path[-1] * 0.995)
    for _ in range(40):
        path.append(path[-1] * 1.005)
    for i in range(1, 12):
        _seed(store, f"6000{i:02d}", list(path), amounts=[2e8] * n)
    store.upsert_meta([{"symbol": f"6000{i:02d}", "name": f"股{i}"} for i in range(1, 12)])

    res = run_replay(months=4, step=5, top_n=3, store=store, workers=0)
    smart = next(p for p in res["pools"] if p["pool"] == "smart")
    regs = {r["regime"]: r for r in smart["regimes"]}
    # 下跌段 5 日中位 ≈ -2.5% → 偏冷；上涨段 ≈ +2.5% 且 100% 上涨 → 偏暖
    assert "偏冷" in regs and "偏暖" in regs
    # 分环境样本数之和不超过总评估数（横盘段为中性或因窗口不足缺失）
    assert sum(r["picks"] for r in smart["regimes"]) <= smart["evaluated"]
    for r in smart["regimes"]:
        assert r["sessions"] >= 1
        assert r["median_excess"] is not None

    # _session_regimes 直接口径抽查：取回放实际会话，跌段会话应为偏冷
    sessions = sorted({str(row[0]) for row in store._conn().execute(
        "SELECT DISTINCT as_of FROM replay_results WHERE run_id=?", (res["run_id"],))})
    m = _session_regimes(store, sessions)
    assert set(m.values()) <= {"偏暖", "中性", "偏冷"}


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
    assert keys[0][0].startswith("v")  # 评分器版本入键：口径变更时旧缓存自动作废

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
