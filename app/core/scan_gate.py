"""全局重扫描闸门 —— 把 CPU 密集的全市场扫描放到独立子进程里跑。

smart_pool / pattern_pool / swing_pool / strength_pool 都是 CPU 密集的全市场
扫描（3700~5000 只逐只算因子）。以前它们通过 ``asyncio.to_thread`` 跑在 Web
进程的线程里——但 CPU 密集的 Python 代码会一直占着 GIL，把异步事件循环整个饿死：
连秒回的 ``/api/health`` 都在看门狗 5 秒超时内响应不了，看门狗误判后端已死、
120 秒后强杀正在跑扫描的后端 → 公网 ~50 秒宕机 + 那次扫描半途夭折。

对策：扫描改到 ``ProcessPoolExecutor`` 的独立子进程里跑（独立 GIL）。子进程闷头
算，Web 进程的事件循环始终空闲、``/api/health`` 永远秒回，看门狗不再误杀。
子进程常驻复用（摊薄 spawn+import 成本），跑满 ``max_tasks_per_child`` 次后自动
回收，避免 pandas 长期运行的内存漂移。

外层再套一个信号量把扫描串行化：同一时刻只跑一个，既给前端 ``scan_in_progress``
去重判断，也和单 worker 进程池一致（永不过度订阅）。
"""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor
from typing import Any

# 允许在子进程里跑的重扫描方法名（QuantEngine 的方法）。白名单防止任意方法被远程指定。
_ALLOWED_SCANS = frozenset({"smart_pool", "pattern_pool", "swing_pool", "strength_pool"})

# 同一时刻只允许一个重扫描占用进程池。调成 >1 会重新引入过度订阅风险。
_scan_semaphore = asyncio.Semaphore(1)

_process_pool: ProcessPoolExecutor | None = None
_pool_lock = threading.Lock()

# 子进程内复用的引擎实例（首次调用惰性构造；worker 常驻则跨次复用）。
_worker_engine: Any = None


def scan_in_progress() -> bool:
    """当前是否已有重扫描在占用闸门（用于给前端提示 / 去重判断）。"""
    return _scan_semaphore.locked()


def _worker_run_scan(method: str, kwargs: dict[str, Any]) -> Any:
    """在子进程里执行一个全市场扫描。必须是模块级函数才能被进程池 pickle。"""
    if method not in _ALLOWED_SCANS:
        raise ValueError(f"unknown scan method: {method}")
    global _worker_engine
    if _worker_engine is None:
        from quantcore.quant import QuantEngine

        _worker_engine = QuantEngine()
    return getattr(_worker_engine, method)(**kwargs)


def _get_process_pool() -> ProcessPoolExecutor:
    """惰性创建单 worker 进程池（在导入期创建会在 Windows 上递归 spawn）。"""
    global _process_pool
    if _process_pool is None:
        with _pool_lock:
            if _process_pool is None:
                _process_pool = ProcessPoolExecutor(max_workers=1, max_tasks_per_child=50)
    return _process_pool


def _reset_process_pool() -> None:
    global _process_pool
    with _pool_lock:
        if _process_pool is not None:
            _process_pool.shutdown(wait=False, cancel_futures=True)
        _process_pool = None


async def run_scan(method: str, /, **kwargs: Any) -> Any:
    """在全局闸门内、用独立子进程执行一个全市场扫描。

    ``method`` 为 QuantEngine 的扫描方法名（见 ``_ALLOWED_SCANS``），``kwargs``
    必须是可 pickle 的基本类型。若已有扫描在跑，本次会等待其完成后再执行。
    """
    async with _scan_semaphore:
        loop = asyncio.get_running_loop()
        try:
            pool = _get_process_pool()
            return await loop.run_in_executor(pool, _worker_run_scan, method, kwargs)
        except BrokenExecutor:
            # worker 进程意外死亡（OOM/崩溃）：重建进程池后重试一次。
            _reset_process_pool()
            pool = _get_process_pool()
            return await loop.run_in_executor(pool, _worker_run_scan, method, kwargs)
