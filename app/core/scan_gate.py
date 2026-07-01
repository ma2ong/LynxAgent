"""全局重扫描并发闸门。

smart_pool / pattern_pool / swing_pool 都是 CPU + 网络密集的全市场扫描，
每次会占用大量线程（内部还各自开 12~24 个线程池）。这些扫描通过
``asyncio.to_thread`` 跑在 Web 进程里，用的是共享的默认线程池（约 CPU 核数 + 4）。

无限并发的后果：连点两次、或多个扫描同时触发时，线程被过度订阅、互相抢 GIL，
每个扫描都变慢；线程池一旦占满，新的扫描永远排不到线程 → 级联卡死，必须重启才恢复。

对策：用一个全局信号量把这些扫描串行化，同一时刻只跑一个。单次照常出结果，
但永远不会把线程池占满，服务器不再需要靠重启自愈。
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, TypeVar

T = TypeVar("T")

# 同一时刻只允许一个重扫描占用线程池。调成 >1 会重新引入过度订阅风险。
_scan_semaphore = asyncio.Semaphore(1)


def scan_in_progress() -> bool:
    """当前是否已有重扫描在占用闸门（用于给前端提示 / 去重判断）。"""
    return _scan_semaphore.locked()


async def run_scan(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """在全局闸门内、用线程池执行一个同步的重扫描函数。

    若已有扫描在跑，本次会等待其完成后再执行（而不是并发抢线程）。
    """
    async with _scan_semaphore:
        return await asyncio.to_thread(func, *args, **kwargs)
