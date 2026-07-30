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


def test_recommendations_are_limited_to_top_ten(monkeypatch):
    monkeypatch.setenv("INTRADAY_RECOMMENDATION_LIMIT", "10")
    payload = {
        "items": [
            {"symbol": str(index).zfill(6), "status": "entry", "score": 100 - index}
            for index in range(15)
        ]
    }
    result = _trim_recommendations(payload)
    assert recommendation_limit() == 10
    assert result["candidate_count"] == 10
    assert len(result["items"]) == 10


def test_smart_pool_overlay_can_read_full_signal_set_behind_public_top_ten(monkeypatch):
    today = datetime.now(TZ).strftime("%Y-%m-%d")
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
