"""大盘环境的实时口径：快照时间戳解析 + 盘中数据必须盖过库里的半截日线 bar。"""
from quantcore.quant.engine import _snapshot_breadth, _snapshot_stamp


def _snap(n: int, pct: float, updated_at: str = "2026/07/22 14:03:11") -> dict:
    return {str(i).zfill(6): {"change_percent": pct, "updated_at": updated_at} for i in range(n)}


def test_snapshot_stamp_uses_quote_time_not_local_clock():
    """节假日快照停在上一交易日，必须按快照自报时间判断，否则会把旧收盘当成今天盘中。"""
    assert _snapshot_stamp(_snap(10, 1.0, "2026/07/21 15:00:03")) == ("2026-07-21", "15:00")
    assert _snapshot_stamp({}) == ("", "")
    assert _snapshot_stamp({"000001": {"change_percent": 1.0, "updated_at": ""}}) == ("", "")


def test_snapshot_stamp_takes_latest_time_of_dominant_date():
    snap = _snap(5, 1.0, "2026/07/22 09:31:00")
    snap.update({"900001": {"change_percent": 1.0, "updated_at": "2026/07/22 14:57:20"}})
    assert _snapshot_stamp(snap) == ("2026-07-22", "14:57")


def test_snapshot_breadth_needs_full_market_sample():
    """样本不足（快照只回来几百只）宁可不用，也不能拿残缺广度冒充全市场。"""
    assert _snapshot_breadth(_snap(100, 1.0)) is None
    out = _snapshot_breadth(_snap(600, 1.0))
    assert out is not None and out["breadth_up"] == 1.0 and out["count"] == 600


def test_realtime_replaces_same_day_partial_bar(monkeypatch):
    """盘中增量同步会写入当天的半截 bar；实时快照同日必须替换它，否则整个交易日都不更新。"""
    from quantcore.quant import engine

    engine._MARKET_CTX_CACHE.clear()
    stale = [
        {"date": "2026-07-22", "median_pct": 0.10, "breadth_up": 0.5000, "count": 5000},
        {"date": "2026-07-21", "median_pct": 0.51, "breadth_up": 0.5514, "count": 5194},
        {"date": "2026-07-20", "median_pct": -3.15, "breadth_up": 0.2176, "count": 5281},
    ]

    class _Store:
        def recent_daily_breadth(self, days=5):
            return [dict(d) for d in stale]

        def latest_real_bar_date(self):
            return "2026-07-22"

        def recent_returns(self, window=5):
            return {}

    monkeypatch.setattr(engine, "get_local_store", lambda: _Store())
    monkeypatch.setattr(engine, "_fetch_tencent_quotes", lambda syms: {})
    # 指数走网络，测试里屏蔽掉，只验证个股口径的拼接
    monkeypatch.setattr("quantcore.quant.macro_bar.fetch_index_quotes", lambda: [])
    monkeypatch.setattr("quantcore.quant.macro_bar.fetch_index_history",
                        lambda as_of="", window=5: [])

    ctx = engine.market_context(_snap(5200, -1.27))
    assert ctx["intraday"] is True
    assert ctx["as_of"] == "2026-07-22"
    # 同一天只能有一条，且用的是实时值而不是库里那根 +0.10 的半截 bar
    dates = [d["date"] for d in ctx["daily"]]
    assert dates == ["2026-07-22", "2026-07-21", "2026-07-20"]
    assert ctx["latest_day"]["median_pct"] == -1.27


def test_stale_snapshot_does_not_fabricate_a_day(monkeypatch):
    """快照比库里旧（休市/断源）时必须退回日线口径，不能把旧收盘再算一天。"""
    from quantcore.quant import engine

    engine._MARKET_CTX_CACHE.clear()

    class _Store:
        def recent_daily_breadth(self, days=5):
            return [{"date": "2026-07-22", "median_pct": -1.27, "breadth_up": 0.2742, "count": 5192}]

        def latest_real_bar_date(self):
            return "2026-07-22"

        def recent_returns(self, window=5):
            return {}

    monkeypatch.setattr(engine, "get_local_store", lambda: _Store())
    monkeypatch.setattr("quantcore.quant.macro_bar.fetch_index_quotes", lambda: [])
    monkeypatch.setattr("quantcore.quant.macro_bar.fetch_index_history",
                        lambda as_of="", window=5: [])

    ctx = engine.market_context(_snap(5200, 5.0, "2026/07/21 15:00:03"))
    assert ctx["intraday"] is False
    assert [d["date"] for d in ctx["daily"]] == ["2026-07-22"]
    assert ctx["latest_day"]["median_pct"] == -1.27
