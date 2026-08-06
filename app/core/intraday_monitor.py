"""Background orchestration for the lightweight intraday signal scanner."""
from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import os
from typing import Any
from zoneinfo import ZoneInfo

from app.core.market_data import _load_industry_map, _load_realtime_quotes_snapshot, _run_data_task
from quantcore.quant.intraday_signals import (
    IntradaySignalEngine,
    build_close_review,
    is_scan_window,
    trading_phase,
)
from quantcore.quant.local_store import get_local_store


logger = logging.getLogger("intraday_monitor")
_TZ = ZoneInfo("Asia/Shanghai")
_task: asyncio.Task | None = None
_lock = asyncio.Lock()
_engine: IntradaySignalEngine | None = None
_restored_date = ""
_closed_review_date = ""
_closed_review_cache: dict[str, Any] = {}
_closed_review_cache_full: dict[str, Any] = {}
_latest: dict[str, Any] = {
    "status": "waiting",
    "as_of": None,
    "phase": "closed",
    "phase_label": "等待交易时段",
    "items": [],
    "recent_events": [],
    "candidate_count": 0,
    "entry_count": 0,
    "watch_count": 0,
    "unbuyable_count": 0,
}
_latest_full: dict[str, Any] = dict(_latest)


def scan_interval_seconds() -> float:
    value = float(os.getenv("INTRADAY_SCAN_INTERVAL", "15"))
    return max(5.0, min(value, 60.0))


def recommendation_limit() -> int:
    value = int(os.getenv("INTRADAY_RECOMMENDATION_LIMIT", "10"))
    return max(5, min(value, 20))


def payload_limit() -> int:
    """回给前端的条数。比 recommendation_limit（默认 10）大，因为页面上有
    入场触发/提前预警/不可追入 三个筛选标签，只回 10 条的话点开标签基本是空的 ——
    而筛选是在这份 items 上做的，不会再向后端要数据。"""
    value = int(os.getenv("INTRADAY_PAYLOAD_LIMIT", "60"))
    return max(recommendation_limit(), min(value, 200))


def _trim_recommendations(payload: dict[str, Any], limit: int | None = None) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or payload_limit()), payload_limit()))
    current = dict(payload)
    # 每个状态各留配额，再按分数排序。
    # 只按总分一刀切会让某一状态吃掉全部名额 —— 2026-08-06 实测 60 个名额里 40 个是
    # entry，提前预警只剩 2 条，用户点开「提前预警」标签几乎是空的。而筛选是在这份
    # items 上做的，后端不给就永远没有。
    by_status: dict[str, list] = {}
    for item in current.get("items") or []:
        by_status.setdefault(str(item.get("status") or ""), []).append(item)
    # 配额是**保底**不是上限：某一档不够数时，空出来的名额由其余最强的条目回填，
    # 否则「只有 entry 有货」的时段会被配额饿死，返回条数远少于上限。
    quota = max(5, safe_limit // 3)
    picked: list = []
    for status in ("entry", "watch", "unbuyable"):
        group = sorted(by_status.get(status) or [], key=lambda it: -float(it.get("score") or 0))
        picked.extend(group[:quota])
    taken = {id(item) for item in picked}
    rest = sorted(
        (item for item in current.get("items") or [] if id(item) not in taken),
        key=lambda it: -float(it.get("score") or 0),
    )
    picked.extend(rest[: max(0, safe_limit - len(picked))])
    items = sorted(picked, key=lambda it: -float(it.get("score") or 0))[:safe_limit]
    current.update({
        "items": items,
        "candidate_count": len(items),
        "entry_count": sum(1 for item in items if item.get("status") == "entry"),
        "watch_count": sum(1 for item in items if item.get("status") == "watch"),
        "unbuyable_count": sum(1 for item in items if item.get("status") == "unbuyable"),
        "recommendation_limit": recommendation_limit(),
        "selection_note": f"\u4ec5\u5c55\u793a\u5168\u5e02\u573a\u91cf\u4ef7\u3001\u7ed3\u6784\u548c\u677f\u5757\u5171\u632f\u7efc\u5408\u6392\u540d\u6700\u9ad8\u7684 {recommendation_limit()} \u53ea\u3002",
    })
    return current


def start() -> None:
    global _task
    if os.getenv("INTRADAY_SIGNAL_ENABLED", "true").lower() in {"0", "false", "no"}:
        logger.info("intraday signal monitor disabled")
        return
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_loop())
    logger.info("intraday signal monitor started")


async def stop() -> None:
    global _task
    if not _task:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None


def _scanner() -> IntradaySignalEngine:
    global _engine
    if _engine is None:
        _engine = IntradaySignalEngine(
            store=get_local_store(),
            industry_map=_load_industry_map(),
        )
    return _engine


def _scan_and_store(snapshot: dict[str, dict[str, Any]], now: datetime) -> dict[str, Any]:
    global _latest_full, _restored_date
    engine = _scanner()
    store = get_local_store()
    today = now.astimezone(_TZ).strftime("%Y-%m-%d")
    if _restored_date != today:
        engine.trade_date = today
        engine.previous_quotes.clear()
        engine.states.clear()
        engine.recent_events.clear()
        engine.restore(store.load_intraday_signal_events(today, 1000))
        _restored_date = today
    result = engine.scan(snapshot, now)
    store.record_intraday_signal_events(result.get("events") or [])
    result = _merge_today_archive(result, store, today, snapshot)
    _latest_full = result
    return _trim_recommendations(result)


def _amount_percentiles(snapshot: dict[str, dict[str, Any]]) -> dict[str, float]:
    """当日成交额的横截面分位（0..1）。"""
    pairs = []
    for symbol, quote in (snapshot or {}).items():
        try:
            amount = float(quote.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if amount > 0:
            pairs.append((amount, str(symbol).zfill(6)))
    pairs.sort()
    return {sym: (i + 1) / len(pairs) for i, (_a, sym) in enumerate(pairs)} if pairs else {}


def _merge_today_archive(
    result: dict[str, Any], store, today: str,
    snapshot: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """盘中也把今日已触发过、但入场区间已过期的信号并进列表。

    信号的 valid_until 是「参考入场价还能不能用」——提前预警 10 分钟、入场触发 20 分钟，
    到点转 invalid。但这个期限原先同时决定了「还要不要显示」，于是任何时刻列表里只剩最近
    十分钟的信号，四个筛选标签（入场触发/提前预警/不可追入）大部分时间是空的，只有收盘后
    的归档视图才有内容。

    2026-08-06 实测：华正新材 09:48 提前预警于 126.33、当时正是机会，09:58 就从列表里
    消失了，而它一路涨到 131 封板。用户看不到自己错过了什么，只能在收盘后复盘里看到。

    并进来的条目标 `signal_mode=intraday_archive`，前端渲染成「盘中曾触发」卡片，
    入场区间照常显示为失效 —— 不谎称还能按原价买，但保留在它自己的状态标签里。
    """
    live = {str(item.get("symbol") or "").zfill(6) for item in (result.get("items") or [])}
    try:
        history = store.load_intraday_signal_events(today, 1000)
    except Exception:  # noqa: BLE001 — 取不到历史不影响当前信号
        return result
    # 归档也要过当日流动性底线。今日早盘的记录是在加这道门槛之前写的，直接并进来会把
    # 「没人交易的小票」重新端回列表 —— 2026-08-06 实测归档里 60% 的条目成交额分位低于 85。
    # 用**当下**的成交额重新判定，而不是信任事件里的旧字段。
    floor = float(os.getenv("LYNX_RADAR_MIN_AMOUNT_PCTL", "0.85"))
    pctl = _amount_percentiles(snapshot or {})
    extra = [
        item for item in _archive_items_from_events(history, 200)
        if str(item.get("symbol") or "").zfill(6) not in live
        and (not pctl or pctl.get(str(item.get("symbol") or "").zfill(6), 0.0) >= floor)
    ]
    if not extra:
        return result
    items = list(result.get("items") or []) + extra
    merged = {**result, "items": items, "candidate_count": len(items)}
    for status in ("entry", "watch", "unbuyable"):
        merged[f"{status}_count"] = sum(1 for i in items if i.get("status") == status)
    return merged


def _archive_items_from_events(
    events: list[dict[str, Any]],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Keep each symbol's strongest actual intraday state for the closing review."""
    priority = {"entry": 0, "watch": 1, "unbuyable": 2}
    selected: dict[str, dict[str, Any]] = {}
    for event in events:
        item = dict(event.get("item") or {})
        symbol = str(event.get("symbol") or item.get("symbol") or "").zfill(6)
        status = str(event.get("status") or item.get("status") or "")
        if not symbol or status not in priority:
            continue
        existing = selected.get(symbol)
        if existing and priority.get(str(existing.get("status")), 9) <= priority[status]:
            continue
        item.update({
            "symbol": symbol,
            "name": str(event.get("name") or item.get("name") or symbol),
            "status": status,
            "triggered_at": str(event.get("triggered_at") or item.get("triggered_at") or ""),
            "signal_price": event.get("signal_price") or item.get("signal_price") or 0,
            "score": event.get("score") or item.get("score") or 0,
            "signal_mode": "intraday_archive",
            "actionable": False,
        })
        item["status_label"] = {
            "entry": "盘中曾触发",
            "watch": "盘中曾预警",
            "unbuyable": "盘中曾不可追",
        }[status]
        selected[symbol] = item

    items = list(selected.values())
    items.sort(key=lambda item: (
        priority.get(str(item.get("status")), 9),
        -float(item.get("score") or 0),
    ))
    safe_limit = max(1, min(int(limit or recommendation_limit()), 200))
    return items[:safe_limit]


def _is_after_close(now: datetime) -> bool:
    current = now.astimezone(_TZ)
    return current.weekday() < 5 and (current.hour, current.minute) > (15, 0)


async def _closed_payload(now: datetime, force: bool = False) -> dict[str, Any]:
    global _closed_review_cache, _closed_review_cache_full, _closed_review_date, _latest, _latest_full
    today = now.strftime("%Y-%m-%d")
    store = get_local_store()
    history = await asyncio.to_thread(store.load_intraday_signal_events, today, 1000)
    archived = _archive_items_from_events(history, 200)

    if (
        not archived
        and _latest_full.get("trade_date") == today
        and _latest_full.get("review_mode") != "close_review"
    ):
        synthetic_events = [
            {
                "symbol": item.get("symbol"),
                "name": item.get("name"),
                "status": item.get("status"),
                "triggered_at": item.get("triggered_at"),
                "signal_price": item.get("signal_price"),
                "score": item.get("score"),
                "item": item,
            }
            for item in (_latest_full.get("items") or [])
            if item.get("signal_mode") in {None, "live", "intraday_archive"}
        ]
        archived = _archive_items_from_events(synthetic_events, 200)

    if archived:
        market = _latest_full.get("market") if _latest_full.get("trade_date") == today else None
        phase_label = "已收盘 · 显示今日盘中记录" if _is_after_close(now) else "休市中 · 显示今日盘中记录"
        _latest_full = {
            **_latest_full,
            "status": "closed",
            "as_of": _latest_full.get("as_of") or now.isoformat(timespec="seconds"),
            "trade_date": today,
            "phase": "closed",
            "phase_label": phase_label,
            "review_mode": "intraday_archive",
            "items": archived,
            "recent_events": history,
            "candidate_count": len(archived),
            "entry_count": sum(1 for item in archived if item.get("status") == "entry"),
            "watch_count": sum(1 for item in archived if item.get("status") == "watch"),
            "unbuyable_count": sum(1 for item in archived if item.get("status") == "unbuyable"),
            "method_note": "展示今日盘中真实记录的状态快照；收盘后原入场区间已经失效，仅用于复盘。",
            "probability_note": "盘中历史信号用于研究和复盘，不代表下一交易日仍可按原价格买入。",
        }
        if market is not None:
            _latest_full["market"] = market
        _latest = _trim_recommendations(_latest_full)
        return _latest

    if _is_after_close(now):
        if force or _closed_review_date != today or not _closed_review_cache_full:
            snapshot = await _run_data_task(
                _load_realtime_quotes_snapshot,
                0 if force else 30,
                timeout=20.0,
            ) or {}
            if not snapshot:
                raise RuntimeError("收盘行情快照为空")
            engine = await asyncio.to_thread(_scanner)
            await asyncio.to_thread(engine.ensure_baselines, now)
            _closed_review_cache_full = await asyncio.to_thread(
                build_close_review,
                snapshot,
                dict(engine.baselines),
                now,
                200,
            )
            _closed_review_cache = _trim_recommendations(_closed_review_cache_full)
            _closed_review_date = today
        _latest_full = {
            **_closed_review_cache_full,
            "recent_events": history,
            "scan_interval_sec": int(scan_interval_seconds()),
        }
        _latest = {
            **_closed_review_cache,
            "recent_events": history,
            "scan_interval_sec": int(scan_interval_seconds()),
        }
        return _latest

    _latest = {
        **_latest,
        "status": "closed",
        "phase": trading_phase(now),
        "phase_label": "非交易时段",
        "recent_events": history,
        "scan_interval_sec": int(scan_interval_seconds()),
    }
    return _latest


async def scan_once(force: bool = False) -> dict[str, Any]:
    """Refresh live signals or build a clearly-labelled closing review."""
    global _latest
    now = datetime.now(_TZ)
    if not is_scan_window(now):
        try:
            return await _closed_payload(now, force)
        except Exception as exc:  # noqa: BLE001
            logger.warning("intraday closing review failed: %s", exc)
            _latest = {
                **_latest,
                "status": "degraded",
                "phase": trading_phase(now),
                "phase_label": "收盘复盘暂时不可用",
                "error": str(exc),
                "as_of": now.isoformat(timespec="seconds"),
                "scan_interval_sec": int(scan_interval_seconds()),
            }
            return _latest

    async with _lock:
        try:
            ttl = 0 if force else 3
            snapshot = await _run_data_task(
                _load_realtime_quotes_snapshot,
                ttl,
                timeout=15.0,
            ) or {}
            if not snapshot:
                raise RuntimeError("实时行情快照为空")
            _latest = await asyncio.to_thread(_scan_and_store, snapshot, now)
            _latest["scan_interval_sec"] = int(scan_interval_seconds())
            return _latest
        except Exception as exc:  # noqa: BLE001
            logger.warning("intraday signal scan failed: %s", exc)
            _latest = {
                **_latest,
                "status": "degraded",
                "error": str(exc),
                "as_of": now.isoformat(timespec="seconds"),
                "scan_interval_sec": int(scan_interval_seconds()),
            }
            return _latest


async def payload(
    limit: int = 50,
    history_limit: int = 100,
    refresh: bool = False,
) -> dict[str, Any]:
    if refresh:
        await scan_once(force=True)
    current = _trim_recommendations(
        dict(_latest),
        max(1, min(limit, recommendation_limit())),
    )
    history = await asyncio.to_thread(
        get_local_store().load_intraday_signal_events,
        None,
        max(1, min(history_limit, 500)),
    )
    visible_symbols = {str(item.get("symbol") or "") for item in current.get("items") or []}
    current["recent_events"] = [
        event for event in history
        if not visible_symbols or str(event.get("symbol") or "") in visible_symbols
    ]
    current["scan_interval_sec"] = int(scan_interval_seconds())
    return current


def _timing_snapshot_is_fresh(current: dict[str, Any], now: datetime) -> bool:
    """Only live snapshots inside the scan window need a strict age check.

    Closing-review/archive snapshots are intentionally kept for the rest of the
    day and are already non-actionable. A live snapshot that stopped updating
    must not keep an entry signal green after its scanner failed.
    """
    today = now.astimezone(_TZ).strftime("%Y-%m-%d")
    if current.get("trade_date") != today:
        return False
    if current.get("review_mode") in {"intraday_archive", "close_review"}:
        return True
    if not is_scan_window(now):
        return True
    try:
        as_of = datetime.fromisoformat(str(current.get("as_of") or ""))
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=_TZ)
        age = (now.astimezone(_TZ) - as_of.astimezone(_TZ)).total_seconds()
    except ValueError:
        return False
    return age <= max(60.0, scan_interval_seconds() * 4)


def _expire_overlay_actionability(signal: dict[str, Any], now: datetime) -> dict[str, Any]:
    current = dict(signal)
    if not current.get("actionable"):
        return current
    try:
        valid_until = datetime.fromisoformat(str(current.get("valid_until") or ""))
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=_TZ)
    except ValueError:
        current["actionable"] = False
        return current
    if now.astimezone(_TZ) > valid_until.astimezone(_TZ):
        current["actionable"] = False
    return current


async def timing_overlay(symbols: list[str]) -> dict[str, Any]:
    """Return the monitor's full current-day signal set for smart-pool reranking.

    The public radar remains capped at ten recommendations.  This internal view
    deliberately keeps the full active set so the smart pool can check all of
    its structural candidates without running a second full-market scan.
    """
    wanted = {
        str(symbol or "").strip().zfill(6)
        for symbol in symbols
        if str(symbol or "").strip()
    }
    now = datetime.now(_TZ)
    current = dict(_latest_full)
    is_current = _timing_snapshot_is_fresh(current, now)
    signal_map: dict[str, dict[str, Any]] = {}
    if is_current:
        for raw in current.get("items") or []:
            symbol = str(raw.get("symbol") or "").strip().zfill(6)
            if symbol in wanted:
                signal_map[symbol] = _expire_overlay_actionability(raw, now)
    return {
        "status": (current.get("status") or "waiting") if is_current else "stale",
        "as_of": current.get("as_of"),
        "trade_date": current.get("trade_date"),
        "phase": current.get("phase") or trading_phase(now),
        "phase_label": current.get("phase_label") or "",
        "review_mode": current.get("review_mode"),
        "is_current": is_current,
        "candidate_count": len(current.get("items") or []) if is_current else 0,
        "signals": signal_map,
    }


async def _loop() -> None:
    await asyncio.sleep(float(os.getenv("INTRADAY_SCAN_WARMUP", "3")))
    while True:
        try:
            if is_scan_window():
                await scan_once()
                delay = scan_interval_seconds()
            else:
                await scan_once()
                delay = 60.0
        except Exception:  # noqa: BLE001
            logger.exception("intraday monitor loop failed")
            delay = 30.0
        await asyncio.sleep(delay)
