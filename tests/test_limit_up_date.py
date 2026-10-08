"""涨停热点的日期跟快照自报的行情日走：开盘前/节假日不能把上一交易日标成「今天 实时」。"""
import asyncio
from datetime import datetime, timedelta

from app.routers import insights


def _run(monkeypatch, snap_day):
    seen = {}

    def fake_compute(target, quotes):
        seen["target"], seen["quotes"] = target, quotes
        return {"date": target, "realtime": bool(quotes)}

    snap = {f"{i:06d}": {"change_percent": 1.0, "updated_at": f"{snap_day.replace('-', '/')} 15:00:03"}
            for i in range(600)}
    monkeypatch.setattr(insights, "_load_realtime_quotes_snapshot", lambda *_a, **_k: snap)
    monkeypatch.setattr("quantcore.quant.limit_up.compute_limit_up_distribution", fake_compute)
    insights.lite_insights_cache.clear()
    asyncio.run(insights.lite_limit_up_distribution())
    return seen


def test_stale_snapshot_falls_back_to_that_day(monkeypatch):
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    seen = _run(monkeypatch, yesterday)
    assert seen["target"] == yesterday
    assert seen["quotes"] == {}          # 用那天的完整日线，不再当实时


def test_same_day_snapshot_stays_realtime(monkeypatch):
    today = datetime.now().strftime("%Y-%m-%d")
    seen = _run(monkeypatch, today)
    assert seen["target"] == today and seen["quotes"]


def test_snapshot_failure_falls_back_and_is_not_cached(monkeypatch):
    """实时快照超时/失败：退回最近完整交易日，且不把这份结果缓存 10 分钟
    （2026-10-08 10:30 机器满载，快照超时后页面显示「今日 0 涨停」并一直挂着）。"""
    seen = {}

    def fake_compute(target, quotes):
        seen.setdefault("targets", []).append(target)
        return {"date": target}

    def boom(*_a, **_k):
        raise TimeoutError("snapshot")

    class Store:
        def latest_real_bar_date(self):
            return "2026-09-30"

    monkeypatch.setattr(insights, "_load_realtime_quotes_snapshot", boom)
    monkeypatch.setattr("quantcore.quant.limit_up.compute_limit_up_distribution", fake_compute)
    monkeypatch.setattr("quantcore.quant.local_store.get_local_store", lambda: Store())
    insights.lite_insights_cache.clear()
    asyncio.run(insights.lite_limit_up_distribution())
    assert seen["targets"] == ["2026-09-30"]
    assert not insights.lite_insights_cache
