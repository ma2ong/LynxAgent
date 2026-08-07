import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import app.core.intraday_monitor as intraday_monitor
from app.core.intraday_monitor import (
    _archive_items_from_events,
    _trim_recommendations,
    recommendation_limit,
)
from quantcore.quant.intraday_signals import IntradaySignalEngine, build_close_review, trading_phase
from quantcore.quant.local_store import LocalQuantStore


TZ = ZoneInfo("Asia/Shanghai")


def _baseline():
    return {
        "000001": {
            "symbol": "000001",
            "name": "测试股份",
            "industry": "电子",
            "prev_close": 10.0,
            "ma5": 10.0,
            "ma20": 9.8,
            "ma60": 9.5,
            "high20": 10.4,
            "high60": 10.8,
            "amount_ma20": 100_000_000,
            "return20": 8.0,
        }
    }


def _quote(price=10.35, pct=3.5, volume_ratio=2.2, amount=25_000_000):
    return {
        "000001": {
            "symbol": "000001",
            "name": "测试股份",
            "price": price,
            "prev_close": 10.0,
            "open": 10.1,
            "high": price + 0.02,
            "low": 10.02,
            "pct_chg": pct,
            "amount": amount,
            "volume_ratio": volume_ratio,
            "quote_source": "test",
        }
    }


def test_trading_phase_covers_full_session():
    assert trading_phase(datetime(2026, 7, 29, 9, 20, tzinfo=TZ)) == "auction"
    assert trading_phase(datetime(2026, 7, 29, 10, 15, tzinfo=TZ)) == "morning"
    assert trading_phase(datetime(2026, 7, 29, 13, 45, tzinfo=TZ)) == "afternoon"
    assert trading_phase(datetime(2026, 7, 29, 14, 58, tzinfo=TZ)) == "closing_auction"
    assert trading_phase(datetime(2026, 7, 29, 12, 0, tzinfo=TZ)) == "closed"


def test_two_scans_upgrade_watch_to_entry_without_duplicate_event():
    engine = IntradaySignalEngine(baselines=_baseline())
    first_at = datetime(2026, 7, 29, 10, 0, tzinfo=TZ)
    first = engine.scan(
        _quote(price=10.40, pct=4.0, volume_ratio=3.2, amount=35_000_000),
        first_at,
    )
    assert first["items"][0]["status"] == "watch"
    assert len(first["events"]) == 1

    second = engine.scan(
        _quote(price=10.405, pct=4.05, volume_ratio=3.2, amount=36_000_000),
        first_at + timedelta(seconds=15),
    )
    assert second["items"][0]["status"] == "entry"
    assert len(second["events"]) == 1
    assert second["items"][0]["signal_price"] == 10.405

    third = engine.scan(
        _quote(price=10.41, pct=4.1, volume_ratio=3.2, amount=37_000_000),
        first_at + timedelta(seconds=30),
    )
    assert third["items"][0]["status"] == "entry"
    assert third["events"] == []


def test_breakout_can_trigger_entry_immediately():
    engine = IntradaySignalEngine(baselines=_baseline())
    result = engine.scan(
        _quote(price=10.55, pct=5.5, volume_ratio=2.8, amount=35_000_000),
        datetime(2026, 7, 29, 9, 38, tzinfo=TZ),
    )
    assert result["items"][0]["status"] == "entry"
    assert result["items"][0]["breakout20"] is True


def test_near_limit_is_never_an_entry_recommendation():
    engine = IntradaySignalEngine(baselines=_baseline())
    result = engine.scan(
        _quote(price=10.92, pct=9.2, volume_ratio=4.0, amount=80_000_000),
        datetime(2026, 7, 29, 10, 5, tzinfo=TZ),
    )
    item = result["items"][0]
    assert item["status"] == "unbuyable"
    assert item["distance_to_limit"] < 1.5


def test_auction_only_emits_prealert_not_entry():
    engine = IntradaySignalEngine(baselines=_baseline())
    result = engine.scan(
        _quote(price=10.55, pct=5.5, volume_ratio=3.0, amount=20_000_000),
        datetime(2026, 7, 29, 9, 24, tzinfo=TZ),
    )
    assert result["items"][0]["status"] == "watch"


def test_intraday_events_round_trip(tmp_path):
    store = LocalQuantStore(str(tmp_path / "quant.sqlite"))
    event = {
        "event_id": "event-1",
        "trade_date": "2026-07-29",
        "symbol": "000001",
        "name": "测试股份",
        "status": "entry",
        "triggered_at": "2026-07-29T10:00:00+08:00",
        "signal_price": 10.37,
        "score": 82.5,
        "item": {"symbol": "000001", "status": "entry", "reasons": ["放量突破"]},
    }
    assert store.record_intraday_signal_events([event]) == 1
    rows = store.load_intraday_signal_events("2026-07-29")
    assert rows[0]["event_id"] == "event-1"
    assert rows[0]["item"]["reasons"] == ["放量突破"]


def test_close_review_shows_candidates_without_faking_trigger_time():
    result = build_close_review(
        _quote(price=10.55, pct=5.5, volume_ratio=4.0, amount=400_000_000),
        _baseline(),
        datetime(2026, 7, 29, 17, 20, tzinfo=TZ),
    )
    assert result["status"] == "closed"
    assert result["review_mode"] == "close_review"
    assert result["candidate_count"] == 1
    assert result["items"][0]["status"] == "entry"
    assert result["items"][0]["status_label"] == "收盘复盘候选"
    assert result["items"][0]["signal_mode"] == "close_review"
    assert result["items"][0]["actionable"] is False
    assert result["items"][0]["triggered_at"] == ""


def test_archive_keeps_real_entry_even_if_later_invalidated():
    base_item = {
        "symbol": "000001",
        "name": "测试股份",
        "score": 88,
        "signal_price": 10.55,
        "triggered_at": "2026-07-29T10:00:00+08:00",
    }
    events = [
        {**base_item, "status": "invalid", "item": {**base_item, "status": "invalid"}},
        {**base_item, "status": "entry", "item": {**base_item, "status": "entry"}},
        {**base_item, "status": "watch", "item": {**base_item, "status": "watch"}},
    ]
    items = _archive_items_from_events(events)
    assert len(items) == 1
    assert items[0]["status"] == "entry"
    assert items[0]["status_label"] == "盘中曾触发"
    assert items[0]["signal_mode"] == "intraday_archive"
    assert items[0]["actionable"] is False


def test_payload_carries_each_status_so_the_filter_tabs_are_not_empty(monkeypatch):
    """回给前端的条数与「展示 10 只」是两件事。

    2026-08-06 改：页面上有 入场触发/提前预警/不可追入 三个筛选标签，筛选是在这份 items
    上做的，后端只回 10 条的话点开标签基本是空的。所以 payload 按状态各留配额（默认
    每档 20、合计 60），展示上限仍是 recommendation_limit，由前端切片。
    """
    monkeypatch.setenv("INTRADAY_RECOMMENDATION_LIMIT", "10")
    monkeypatch.setenv("INTRADAY_PAYLOAD_LIMIT", "60")
    payload = {
        "items": [
            {"symbol": str(i).zfill(6), "status": status, "score": 100 - i}
            for status in ("entry", "watch", "unbuyable")
            for i in range(30)
        ]
    }
    result = _trim_recommendations(payload)
    assert recommendation_limit() == 10
    # 每个状态都拿到配额，没有哪一档被另一档挤空
    for status in ("entry", "watch", "unbuyable"):
        assert result[f"{status}_count"] == 20, status
    assert len(result["items"]) == 60
    # 2026-08-07 改：不再是全局按分数降序。默认视图（前 recommendation_limit 条）只放
    # 还能操作的，已涨停/空间不足的 unbuyable 沉到后面，它们仍在自己的标签里。
    statuses = [item["status"] for item in result["items"]]
    head = statuses[:recommendation_limit()]
    assert "unbuyable" not in head
    assert statuses.index("unbuyable") >= statuses.count("entry") + statuses.count("watch")
    actionable_scores = [
        item["score"] for item in result["items"] if item["status"] in {"entry", "watch"}
    ]
    assert actionable_scores == sorted(actionable_scores, reverse=True)


def test_recent_triggers_keep_reserved_slots_in_the_default_view(monkeypatch):
    """午后的新信号必须挤得进默认视图。

    列表是「当日触发过的票按分数取前 N」，而早盘的分数天然更高（短时涨速在开盘最猛、
    量比的分母是时段进度、板块共振也在开盘最强）。2026-08-06 实测第 10 名 90.9 分，
    全天下午首次触发的票里最高只有 86.2 —— 数学上永远进不来。
    """
    monkeypatch.setenv("INTRADAY_RECOMMENDATION_LIMIT", "10")
    monkeypatch.setenv("INTRADAY_PAYLOAD_LIMIT", "60")
    monkeypatch.setenv("LYNX_RADAR_FRESH_SLOTS", "3")
    monkeypatch.setenv("LYNX_RADAR_FRESH_WINDOW_MIN", "30")
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    stale = (now - timedelta(hours=2)).isoformat(timespec="seconds")
    recent = (now - timedelta(minutes=5)).isoformat(timespec="seconds")
    payload = {
        "items": [
            {"symbol": str(i).zfill(6), "status": "entry", "score": 95 - i * 0.1,
             "triggered_at": stale, "signal_mode": "intraday_archive"}
            for i in range(20)
        ] + [
            {"symbol": f"9000{i}0", "status": "entry", "score": 82 - i,
             "triggered_at": recent, "signal_mode": "intraday_archive"}
            for i in range(5)
        ],
    }
    head = _trim_recommendations(payload)["items"][:recommendation_limit()]
    fresh_in_head = [item for item in head if item["symbol"].startswith("9000")]
    assert len(fresh_in_head) == 3


def test_old_signals_lose_rank_but_keep_their_displayed_score(monkeypatch):
    monkeypatch.setenv("INTRADAY_RECOMMENDATION_LIMIT", "10")
    monkeypatch.setenv("LYNX_RADAR_SCORE_DECAY_PER_HOUR", "2")
    monkeypatch.setenv("LYNX_RADAR_SCORE_DECAY_CAP", "8")
    monkeypatch.setenv("LYNX_RADAR_FRESH_SLOTS", "0")
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    payload = {
        "items": [
            {"symbol": "000001", "status": "entry", "score": 90.0,
             "triggered_at": (now - timedelta(hours=4)).isoformat(timespec="seconds"),
             "signal_mode": "intraday_archive"},
            {"symbol": "000002", "status": "entry", "score": 85.0,
             "triggered_at": (now - timedelta(minutes=2)).isoformat(timespec="seconds"),
             "signal_mode": "intraday_archive"},
        ],
    }
    items = _trim_recommendations(payload)["items"]
    # 90 分的旧信号被 4 小时折价 8 分（封顶）后排在 85 分的新信号之后
    assert [item["symbol"] for item in items] == ["000002", "000001"]
    # 展示的分数一分不动：留痕和复盘要对得上
    assert {item["symbol"]: item["score"] for item in items} == {"000001": 90.0, "000002": 85.0}


def test_trim_respects_an_explicit_smaller_limit(monkeypatch):
    monkeypatch.setenv("INTRADAY_RECOMMENDATION_LIMIT", "10")
    payload = {"items": [{"symbol": str(i).zfill(6), "status": "entry", "score": 100 - i}
                         for i in range(15)]}
    assert len(_trim_recommendations(payload, limit=10)["items"]) == 10


def test_smart_pool_overlay_can_read_full_signal_set_behind_public_top_ten(monkeypatch):
    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")
    full_items = [
        {"symbol": str(index).zfill(6), "status": "entry", "score": 100 - index}
        for index in range(15)
    ]
    monkeypatch.setattr(
        intraday_monitor,
        "_latest_full",
        {
            "status": "live",
            "trade_date": today,
            "as_of": now.isoformat(timespec="seconds"),
            "phase": "morning",
            "items": full_items,
        },
    )

    result = asyncio.run(
        intraday_monitor.timing_overlay([item["symbol"] for item in full_items])
    )

    assert result["is_current"] is True
    assert result["candidate_count"] == 15
    assert len(result["signals"]) == 15


def test_live_timing_snapshot_expires_when_scanner_stops():
    now = datetime(2026, 7, 29, 10, 30, tzinfo=TZ)
    current = {
        "status": "live",
        "trade_date": "2026-07-29",
        "as_of": (now - timedelta(minutes=3)).isoformat(timespec="seconds"),
        "phase": "morning",
    }

    assert intraday_monitor._timing_snapshot_is_fresh(current, now) is False


def test_entry_actionability_expires_at_valid_until():
    now = datetime(2026, 7, 29, 10, 30, tzinfo=TZ)
    signal = {
        "status": "entry",
        "actionable": True,
        "valid_until": (now - timedelta(seconds=1)).isoformat(timespec="seconds"),
    }

    result = intraday_monitor._expire_overlay_actionability(signal, now)

    assert result["status"] == "entry"
    assert result["actionable"] is False


def test_persistent_entry_is_no_longer_actionable_after_original_window():
    engine = IntradaySignalEngine(baselines=_baseline())
    first_at = datetime(2026, 7, 29, 10, 0, tzinfo=TZ)
    engine.scan(_quote(price=10.55, pct=5.5, volume_ratio=3.2, amount=35_000_000), first_at)
    later = engine.scan(
        _quote(price=10.60, pct=6.0, volume_ratio=3.2, amount=80_000_000),
        first_at + timedelta(minutes=21),
    )

    assert later["items"][0]["status"] == "entry"
    assert later["items"][0]["actionable"] is False
