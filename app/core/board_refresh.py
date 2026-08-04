"""交易时段后台保温各重板块缓存 —— 让端点秒读、首页进入即见全貌。

问题：实时快照/市场环境/风险扫描/涨停热点/一键智选 这些重板块，原本都在用户
点进去时才现算全市场（breakdown ~10s、涨停热点 ~25s、一键智选 ~100s），冷缓存
必超时，首页更没法一次性并发加载全部。

对策：一个后台 asyncio 循环，交易时段周期性 proactively 把实时板块算好塞进它们
各自既有的缓存；smart-pool 的日 K 结构池每天只预热一次，避免与用户点击争抢推荐锁。
端点保持「只读缓存、冷则秒回 warming」，于是永不超时；首页 mount 时的并发拉取
全部命中热缓存 → 一次看到全貌，且实时板块数据至多约一个刷新周期旧。

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
_smart_pool_warm_date: str | None = None
_last_auto_sync_attempt: float = 0.0
_TZ = ZoneInfo("Asia/Shanghai")


def _latest_bar_is_stale(store) -> bool:
    """本地日线是否落后于「最近一个应已收盘的交易日」。

    背景：日线增量同步原本只在用户打开数据中心页(/datalake/health?auto_start)时触发。
    没人开那页，daily_kline 就停在几天前——结构因子在同一份旧 K 线上反复评分，
    一键智选的名单自然「来来去去就那几只」，且缓存 key 里的 daily_as_of 不变，
    连强制刷新都算不出新结果。这是选股僵化的第一根因。

    节假日无法本地判定，宁可误报（增量同步幂等且有 4h 尝试节流，空跑无害）。
    """
    from datetime import date, datetime as dt, time as dtime, timedelta

    latest = ""
    try:
        latest = store.latest_real_bar_date() or ""
    except Exception:
        return False
    if not latest:
        return False  # 空库走全量同步路径，不归这里管
    ref = date.today()
    now = dt.now(_TZ)
    # 今天的日线要 15:15 后才算「应已存在」；周末回退到最近的工作日
    if ref.weekday() >= 5 or now.time() < dtime(15, 15):
        ref = ref - timedelta(days=1)
    while ref.weekday() >= 5:
        ref = ref - timedelta(days=1)
    return latest < ref.isoformat()


async def _maybe_auto_sync() -> bool:
    """需要时在后台拉起日线增量同步。返回 True 表示「正在同步中」。"""
    global _last_auto_sync_attempt
    import time as _time

    from quantcore.quant.sync_service import get_sync_service

    try:
        svc = get_sync_service()
        status = svc.status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("board refresh [auto-sync] status failed: %s", exc)
        return False
    if status.get("running"):
        return True
    health = dict(status.get("health") or {})
    stale = bool(health.get("needs_incremental_sync")) or _latest_bar_is_stale(svc.store)
    if not stale:
        return False
    # 4 小时尝试节流：节假日误判 stale 时不至于反复空跑全市场
    if _time.time() - _last_auto_sync_attempt < 4 * 3600:
        return False
    _last_auto_sync_attempt = _time.time()
    try:
        await asyncio.to_thread(svc.run_sync, False, False)  # 非阻塞：同步跑在它自己的后台线程
        logger.info("board refresh [auto-sync] incremental sync started (stale local kline)")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("board refresh [auto-sync] start failed: %s", exc)
        return False


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
    global _smart_pool_warm_date

    from app import lite_main as m
    from app.core import market_data as md
    from app.routers import insights as ins
    from app.routers import quant as q

    # 0) 日线新鲜度自愈：不再依赖用户打开数据中心页才同步。同步进行中则本轮跳过
    #    智选池预热，等新 K 线落库后下一轮再预热（缓存 key 含 daily_as_of，自动换代）。
    syncing = await _maybe_auto_sync()

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

    # 5) 一键智选的结构因子基于完整日 K，每个交易日只需预热一次。
    #    盘中实时价与时机层由用户请求刷新；后台每 60 秒进入推荐锁会让用户点击无谓排队。
    today = datetime.now(_TZ).strftime("%Y-%m-%d")
    if _in_active_window() and _smart_pool_warm_date != today and not syncing:
        try:
            result = await m._compute_lite_smart_pool(
                "balanced",
                10,
                5000,
                force_refresh=False,
            )
            if m._smart_pool_response_has_items(result):
                _smart_pool_warm_date = today
        except Exception as exc:  # noqa: BLE001
            logger.warning("board refresh [smart-pool] failed: %s", exc)
