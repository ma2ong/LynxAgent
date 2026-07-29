"""交易时段后台保温各重板块缓存 —— 让端点秒读、首页进入即见全貌。

问题：实时快照/市场环境/风险扫描/涨停热点/一键智选 这些重板块，原本都在用户
点进去时才现算全市场（breakdown ~10s、涨停热点 ~25s、一键智选 ~100s），冷缓存
必超时，首页更没法一次性并发加载全部。

对策：一个后台 asyncio 循环，交易时段周期性 proactively 把每个板块算好塞进它们
各自既有的缓存（smart-pool 走已隔离的进程池，不饿死事件循环）。端点保持「只读
缓存、冷则秒回 warming」，于是永不超时；首页 mount 时的并发拉取全部命中热缓存
→ 一次看到全貌，且数据至多约一个刷新周期旧。

全部 best-effort：单个板块算失败只记日志、不影响其他板块，也不影响 Web 主流程。
可用 BOARD_REFRESH_ENABLED=false 关闭（预览/测试）。
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger("board_refresh")

_task: asyncio.Task | None = None
_TZ = ZoneInfo("Asia/Shanghai")


def start() -> None:
    """在 FastAPI startup 里调用：拉起后台保温循环（幂等）。"""
    global _task
    if os.getenv("BOARD_REFRESH_ENABLED", "true").lower() in ("0", "false", "no"):
        logger.info("board refresh disabled via BOARD_REFRESH_ENABLED")
        return
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_loop())
    logger.info("board refresh loop started")


def _in_active_window() -> bool:
    """交易时段（含集合竞价与尾盘余量）：工作日 09:00–15:40 Asia/Shanghai。
    窗口外数据不变，用上一轮热缓存即可，降频到低负载。"""
    now = datetime.now(_TZ)
    if now.weekday() >= 5:  # 周六日
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 <= minutes <= 15 * 60 + 40


async def _loop() -> None:
    await asyncio.sleep(float(os.getenv("BOARD_REFRESH_WARMUP", "4")))
    active_interval = float(os.getenv("BOARD_REFRESH_INTERVAL", "60"))
    idle_interval = float(os.getenv("BOARD_REFRESH_IDLE_INTERVAL", "600"))
    while True:
        try:
            await _refresh_cycle()
        except Exception:  # noqa: BLE001
            logger.exception("board refresh cycle failed")
        await asyncio.sleep(active_interval if _in_active_window() else idle_interval)


async def _safe(name: str, coro) -> None:
    try:
        await coro
    except Exception as exc:  # noqa: BLE001
        logger.warning("board refresh [%s] failed: %s", name, exc)


async def _refresh_cycle() -> None:
    """算好一轮所有板块，灌进各自缓存。顺序执行、彼此隔离。"""
    from app import lite_main as m
    from app.core import market_data as md
    from app.routers import insights as ins
    from app.routers import quant as q

    # 1) 实时快照：所有板块的公共底料，强制刷新（ttl=0），保它 <一个周期 旧。
    snapshot: dict = {}
    try:
        snapshot = await md._run_data_task(md._load_realtime_quotes_snapshot, 0, timeout=15.0) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("board refresh [snapshot] failed: %s", exc)

    # 2) 风险扫描（breakdown 破位广度 + 全市场卖出信号）→ 暖 _RISK_SCAN_CACHE
    await _safe("risk-scan", asyncio.to_thread(q._risk_scan_cached, snapshot))

    # 3) 涨停热点（当日）→ 暖 lite_insights_cache（端点自身负责落缓存）
    await _safe("limit-up", ins.lite_limit_up_distribution())

    # 4) 集合竞价 + 行业热力 → 首页这两块原本是冷读（竞价端点前端给到 30s 超时），
    #    一起预热，首页才真的「进入即见全貌」而不是进去等。
    await _safe("call-auction", ins.lite_call_auction())
    await _safe("heatmap", ins.lite_heatmap("industry"))

    # 5) 一键智选（全市场结构因子）→ 走进程池，暖 smart-pool 缓存（内存+持久）
    #    仅交易时段跑：这是最重的一档（~100s），窗口外没必要反复算。
    if _in_active_window():
        await _safe(
            "smart-pool",
            m._compute_lite_smart_pool("balanced", 20, 5000, force_refresh=True),
        )
