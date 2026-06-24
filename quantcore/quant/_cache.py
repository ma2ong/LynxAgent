"""轻量内存 TTL 缓存：三资金面模块共用。线程安全，重启即失效（快照型只读数据可接受）。"""
from __future__ import annotations

import time
from threading import Lock
from typing import Any, Callable

_STORE: dict[str, tuple[float, Any]] = {}
_LOCK = Lock()


def cached(key: str, ttl: int, fn: Callable[[], Any]) -> Any:
    """命中且未过期则返回缓存；否则调用 fn() 写缓存并返回。ttl 单位秒。"""
    now = time.time()
    with _LOCK:
        hit = _STORE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = fn()  # 不在锁内调用，避免慢 akshare 串行化全部请求
    with _LOCK:
        _STORE[key] = (now, value)
    return value


def peek(key: str, ttl: int | None = None) -> Any:
    """只读取已缓存值，绝不触发计算。未命中（或给了 ttl 且已过期）返回 None。

    用于「重计算只在后台预热任务里做、在线请求只读缓存」的场景。
    """
    with _LOCK:
        hit = _STORE.get(key)
    if not hit:
        return None
    if ttl is not None and time.time() - hit[0] >= ttl:
        return None
    return hit[1]


def invalidate(key: str) -> None:
    with _LOCK:
        _STORE.pop(key, None)
