"""serenity 事件扫描服务：拉新闻→逐条分析→缓存；非阻塞 computing/poll。"""
from __future__ import annotations

import time
from threading import Lock, Thread
from typing import Dict, List

from .news import market_news_flow
from .serenity import scan_event, deep_report

_CACHE: Dict[str, tuple] = {}
_INFLIGHT: Dict[str, dict] = {}
_LOCK = Lock()
_TTL = 6 * 3600
_KEY = "events"


def _compute_events(max_news: int = 30) -> List[dict]:
    news = market_news_flow(limit=max_news * 3)[:max_news]
    cards: List[dict] = []
    for item in news:
        try:
            card = scan_event(item)
        except Exception:
            card = None
        if card:
            cards.append(card)
    return cards


def _worker(max_news: int):
    try:
        cards = _compute_events(max_news)
        with _LOCK:
            _CACHE[_KEY] = (time.time(), cards)
    finally:
        with _LOCK:
            _INFLIGHT.pop(_KEY, None)


def request_events(force: bool = False, max_news: int = 30) -> dict:
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(_KEY)
        if hit and not force and now - hit[0] < _TTL:
            return {"status": "ready", "events": hit[1], "cached": True,
                    "age_sec": int(now - hit[0]), "count": len(hit[1])}
        if _KEY in _INFLIGHT and not force:
            return {"status": "computing", "elapsed_sec": int(now - _INFLIGHT[_KEY]["started"])}
        if force:
            _CACHE.pop(_KEY, None)
        _INFLIGHT[_KEY] = {"started": now}
    Thread(target=_worker, args=(max_news,), daemon=True).start()
    return {"status": "computing", "elapsed_sec": 0}


def run_events_sync(force: bool = False, max_news: int = 30) -> dict:
    """供调度器调用：同步算完并写缓存。"""
    cards = _compute_events(max_news)
    with _LOCK:
        _CACHE[_KEY] = (time.time(), cards)
    return {"status": "ready", "count": len(cards)}


def deep_for_theme(theme: str, event: str, beneficiaries: List[dict]) -> dict:
    rep = deep_report(theme, event, beneficiaries)
    return rep or {"error": "深度分析失败，请重试"}
