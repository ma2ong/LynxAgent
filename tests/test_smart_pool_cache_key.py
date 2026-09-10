"""同一份全市场必须只有一把 cache key。

后台保温器按 universe_limit=10000 预热，选股页按本地股票池实际规模（meta_count，
当时 5525）请求，首页按 10000 —— 引擎在 limit >= 池子大小时根本不截断，三者扫的
是同一份全市场，却因为 cache_key 直接带请求原值而各成一把 key。结果是选股页的
cache_only 永远落空、永远端出 warming 空名单，用户天天得手点一键智能推荐
（2026-09-10 定位）。这里钉住归一：够大的请求共用一把 key，真调小的仍然独立。
"""
from __future__ import annotations

import asyncio

import pytest

import app.lite_main as lite_main
import quantcore.quant.local_store as local_store

UNIVERSE_SIZE = 5525


class _FakeStore:
    def symbol_count(self) -> int:
        return UNIVERSE_SIZE

    def latest_real_bar_date(self) -> str:
        return "2026-09-10"


@pytest.fixture
def captured_keys(monkeypatch):
    keys: list[str] = []
    monkeypatch.setattr(local_store, "get_local_store", lambda: _FakeStore())
    monkeypatch.setattr(lite_main, "_cache_get", lambda key, ttl: None)

    def spy(key: str, ttl: int):
        keys.append(key)
        return None

    monkeypatch.setattr(lite_main, "_persistent_cache_get", spy)
    return keys


def _cache_key_for(universe_limit: int, keys: list[str]) -> str:
    # cache_only 冷缓存直接返回 warming 占位，不会真去扫全市场。
    asyncio.run(lite_main._compute_lite_smart_pool("balanced", 20, universe_limit, cache_only=True))
    return keys[-1]


def test_requests_covering_the_whole_universe_share_one_key(captured_keys):
    warmer = _cache_key_for(10000, captured_keys)
    stock_pick_page = _cache_key_for(UNIVERSE_SIZE, captured_keys)
    page_default = _cache_key_for(6000, captured_keys)
    assert warmer == stock_pick_page == page_default
    assert warmer.endswith(f":20:{UNIVERSE_SIZE}")


def test_a_genuinely_smaller_universe_keeps_its_own_key(captured_keys):
    whole_market = _cache_key_for(10000, captured_keys)
    narrowed = _cache_key_for(800, captured_keys)
    assert narrowed != whole_market
    assert narrowed.endswith(":20:800")
