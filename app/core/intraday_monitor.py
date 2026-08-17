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


def score_floor() -> float:
    """展示门槛：只有强度到线的机会才进榜，够几条给几条。

    以前是「全市场排名取前 10」，名次不等于质量 —— 弱时段硬凑满 10 条，强时段又把
    89.9 分的挡在门外只因为它排第 11。换成绝对门槛后，行情强就多几条，行情弱就没有。
    """
    value = float(os.getenv("LYNX_RADAR_SCORE_FLOOR", "90"))
    return max(0.0, min(value, 100.0))


def fresh_window_minutes() -> float:
    value = float(os.getenv("LYNX_RADAR_FRESH_WINDOW_MIN", "30"))
    return max(5.0, min(value, 120.0))


def fresh_slots() -> int:
    value = int(os.getenv("LYNX_RADAR_FRESH_SLOTS", "3"))
    return max(0, min(value, 20))


def _decay_per_hour() -> float:
    value = float(os.getenv("LYNX_RADAR_SCORE_DECAY_PER_HOUR", "2.0"))
    return max(0.0, min(value, 20.0))


def _decay_cap() -> float:
    value = float(os.getenv("LYNX_RADAR_SCORE_DECAY_CAP", "8.0"))
    return max(0.0, min(value, 40.0))


def _age_minutes(item: dict[str, Any], now: datetime) -> float:
    try:
        moment = datetime.fromisoformat(str(item.get("triggered_at") or ""))
    except ValueError:
        return 0.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_TZ)
    return max(0.0, (now.astimezone(_TZ) - moment.astimezone(_TZ)).total_seconds() / 60.0)


def _rank_score(item: dict[str, Any], now: datetime) -> float:
    """排序用的分数：早盘攒下的旧信号随时间缓慢让位。展示的 score 一分不动。

    评分本身是「量价 + 结构 + 板块共振」的口径，改它会让留痕和复盘对不上号；时间折价
    只影响谁排在前面。实时信号不打折 —— 它本来就是刚发生的。

    为什么需要：早盘的分数天然更高（短时涨速在开盘最猛、量比的分母是时段进度、板块共振
    也在开盘最强），而列表是「当日触发过的票按分数取前 N」。2026-08-06 实测第 10 名
    90.9 分，全天下午首次触发的票里最高只有 86.2 —— 数学上永远进不来，10:30 之后
    榜单就被上午锁死了。
    """
    score = float(item.get("score") or 0)
    if item.get("signal_mode") != "intraday_archive":
        return score
    return score - min(_decay_cap(), _age_minutes(item, now) / 60.0 * _decay_per_hour())


def _order_for_display(items: list, now: datetime) -> list:
    """还能操作的排前面，不可追的沉到后面。

    * entry / watch 排前面 —— 已涨停或空间不足的 unbuyable 本来就买不进去；它们仍然
      在「不可追入」标签里，只是不占据最上面的位置。
    * 再给「最近 N 分钟内触发」留几个置顶席位，否则午后的新信号排不过早盘的高分存量。
    """
    actionable = sorted(
        (item for item in items if item.get("status") in {"entry", "watch"}),
        key=lambda it: -_rank_score(it, now),
    )
    blocked = sorted(
        (item for item in items if item.get("status") == "unbuyable"),
        key=lambda it: -_rank_score(it, now),
    )
    others = [item for item in items if item.get("status") not in {"entry", "watch", "unbuyable"}]

    window = fresh_window_minutes()
    head: list = []
    seen: set[int] = set()
    for item in actionable:
        if len(head) >= fresh_slots():
            break
        if _age_minutes(item, now) <= window:
            head.append(item)
            seen.add(id(item))
    return head + [item for item in actionable if id(item) not in seen] + blocked + others


def _trim_recommendations(payload: dict[str, Any], limit: int | None = None) -> dict[str, Any]:
    """按信号强度过滤，不再按名次截断 —— 到线的全给，没到线的一条都不给。

    limit 只是接口层的安全上限（防 payload 在极端行情下无限大），正常不会触发。
    """
    floor = score_floor()
    now = datetime.now(_TZ)
    current = dict(payload)
    kept = [
        item for item in current.get("items") or []
        if float(item.get("score") or 0) >= floor
    ]
    items = _order_for_display(kept, now)
    if limit:
        items = items[: max(1, int(limit))]
    current.update({
        "items": items,
        "candidate_count": len(items),
        "entry_count": sum(1 for item in items if item.get("status") == "entry"),
        "watch_count": sum(1 for item in items if item.get("status") == "watch"),
        "unbuyable_count": sum(1 for item in items if item.get("status") == "unbuyable"),
        "score_floor": floor,
        "selection_note": f"\u4ec5\u5c55\u793a\u4fe1\u53f7\u5f3a\u5ea6 {floor:g} \u5206\u4ee5\u4e0a\u7684\u673a\u4f1a\uff0c\u4e0d\u9650\u6570\u91cf\u3002",
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
    safe_limit = max(1, min(int(limit or 200), 200))
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
    limit: int = 200,
    history_limit: int = 100,
    refresh: bool = False,
) -> dict[str, Any]:
    if refresh:
        await scan_once(force=True)
    # limit 只是安全上限：条数由信号强度门槛决定，不再按名次截断。
    current = _trim_recommendations(dict(_latest), max(1, min(limit, 200)))
    # 留痕 = 今天上过榜的每一次状态变化，与「此刻榜上有谁」无关。
    # 原来的写法是「最新 N 条事件 ∩ 当前榜单」，两头都错：一天两三千条状态变化，最新
    # 100 条全是低分票的 watch/invalid 抖动；再与当前榜单取交集，上午触发、现在已经掉
    # 出榜单的票就一条不剩 —— 而那恰恰是用户回头最想看的。改成按展示门槛取当日全量。
    current["recent_events"] = await asyncio.to_thread(
        get_local_store().load_intraday_signal_events,
        None,
        max(1, min(history_limit, 500)),
        score_floor(),
    )
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
