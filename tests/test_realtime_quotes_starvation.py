"""实时价刷新不能被慢任务饿死，饿死了也不能端出昨收冒充现价。

2026-09-15 09:30 前后现场：一键智选里科翔股份显示 +20.00%（昨收），实际盘中
+2~3%，每行都挂着「实时行情未覆盖」。行情源本身是通的（同一时刻本机直接拉
腾讯 0.6s 返回）。根因在 _run_data_task 共用的 3 线程池：盘面热力图/涨停分布这类
15~30s 的重活把三条线程全占住，asyncio.wait_for 超时只放弃等待、不释放线程，
5s 的批量取价排队等不到线程就超时，_realtime_quotes 静默吞掉异常返回空，
_enrich_smart_pool_realtime 于是把缓存里的昨收原样端出去。

这里钉住两条：
1. 批量取价走独立的小线程池——数据池全堵住时取价照样几毫秒返回。
2. 批量取价真失败时，退回**内存里已有的**全市场快照（后台每 60s 刷一次，最多
   旧一分钟），而不是昨收；这一步不发起任何网络请求，所以不影响秒读。
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone

import pytest

import app.core.market_data as md


@pytest.fixture
def saturated_data_executor():
    """把 lite_data_executor 的每条线程都用一个 10s 的慢活占满。"""
    release = threading.Event()

    def hog():
        release.wait(10)

    futures = [md.lite_data_executor.submit(hog) for _ in range(md.lite_data_executor._max_workers)]
    time.sleep(0.05)  # 让线程真正拿到活
    yield
    release.set()
    for fut in futures:
        fut.result(timeout=2)


def test_quote_batch_is_not_queued_behind_slow_data_tasks(monkeypatch, saturated_data_executor):
    monkeypatch.setattr(
        md, "_fetch_tencent_realtime_quotes",
        lambda symbols: {s: {"price": 1.0, "change_percent": 2.0, "updated_at": "x"} for s in symbols},
    )
    started = time.monotonic()
    quotes = asyncio.run(md._realtime_quotes(["300903"], allow_snapshot_fallback=False))
    assert quotes["300903"]["change_percent"] == 2.0
    assert time.monotonic() - started < 2.0, "取价排在慢任务后面等线程了"


def test_failed_batch_falls_back_to_todays_in_memory_snapshot(monkeypatch):
    def boom(symbols):
        raise RuntimeError("tencent down")

    monkeypatch.setattr(md, "_fetch_tencent_realtime_quotes", boom)
    today = datetime.now().astimezone().strftime("%Y/%m/%d")
    monkeypatch.setattr(
        md, "lite_realtime_quotes_cache",
        (datetime.now(timezone.utc), {"300903": {"symbol": "300903", "price": 112.05, "change_percent": 3.46, "updated_at": f"{today} 09:34:24"}}),
    )
    # 不允许发起快照拉取：只能用内存里那份。
    monkeypatch.setattr(md, "_load_realtime_quotes_snapshot", lambda *a, **k: pytest.fail("不该发起快照拉取"))
    quotes = asyncio.run(md._realtime_quotes(["300903", "603186"], allow_snapshot_fallback=False))
    assert quotes["300903"]["change_percent"] == 3.46
    assert "603186" not in quotes  # 快照里没有的照样缺席，前端如实标「未覆盖」


def test_stale_in_memory_snapshot_is_not_used(monkeypatch):
    def boom(symbols):
        raise RuntimeError("tencent down")

    monkeypatch.setattr(md, "_fetch_tencent_realtime_quotes", boom)
    monkeypatch.setattr(
        md, "lite_realtime_quotes_cache",
        (datetime.now(timezone.utc), {"300903": {"symbol": "300903", "price": 108.3, "change_percent": 20.0, "updated_at": "2026/09/14 15:00:00"}}),
    )
    monkeypatch.setattr(md, "_load_realtime_quotes_snapshot", lambda *a, **k: {})
    quotes = asyncio.run(md._realtime_quotes(["300903"], allow_snapshot_fallback=False))
    assert quotes == {}
