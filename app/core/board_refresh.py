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
_snapshot_capture_date: str | None = None
_panel_batch_date: str | None = None
_TZ = ZoneInfo("Asia/Shanghai")

# 盘中快照落库时点。选 14:30：全天成交额已走掉约九成，量能推算误差小；
# 又留出 30 分钟，让「盘中生成名单」这条路真的还来得及下单。
_SNAPSHOT_CAPTURE_HHMM = os.getenv("INTRADAY_SNAPSHOT_AT", "14:30")


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


def _capture_intraday_snapshot(snapshot: dict) -> None:
    """交易日过了 14:30 就把这份全市场快照落一次库，每天只落第一份。

    为什么要这份数据：排序现在只吃最近完整日线（`factors.py` 用 amount>0 滤掉当日
    占位 bar），所以名单在两个收盘之间是冻结的。要判断「盘中就生成名单」是否更好，
    必须有盘中的 point-in-time 行情历史 —— 库里没有，`intraday_signal_events` 只覆盖
    爆发候选。先攒数据，够了再谈改排序。**本函数不参与任何排序，纯记录。**

    时间与日期一律取快照自报的时间戳，不用本机时钟：节假日/休市时快照停在上一交易日，
    按本机日期算会把上一交易日的收盘当成「今天 14:30」写进去，一条假样本毁一天。
    """
    global _snapshot_capture_date

    from quantcore.quant.engine import _snapshot_stamp
    from quantcore.quant.local_store import get_local_store

    if not snapshot:
        return
    snap_date, snap_time = _snapshot_stamp(snapshot)
    today = datetime.now(_TZ).strftime("%Y-%m-%d")
    # 快照日期必须就是今天：不等于今天说明是休市日的陈旧快照，不采。
    if not snap_date or snap_date != today or _snapshot_capture_date == snap_date:
        return
    if not snap_time or snap_time < _SNAPSHOT_CAPTURE_HHMM:
        return

    written = get_local_store().record_intraday_snapshot(
        snap_date, f"{snap_date}T{snap_time}", snapshot
    )
    _snapshot_capture_date = snap_date
    logger.info("intraday snapshot captured: %s %s, %d symbols", snap_date, snap_time, written)


def _run_daily_panel_batch() -> None:
    """每交易日给当日留痕名单补一次五方判读，落进 panel_scores。

    为什么必须后台跑：这份评分原本只在用户打开推荐页、前端调 /panel/batch 时才触发。
    2026-08-06 把「五方判读」那一列从表格撤掉后就没人再触发它了，数据会当场断流 ——
    而我们要的正是长期积累，好用 experiments/panel_eval.py 回答「共识分高的票后续是不是
    真的更好」。有答案之前它不参与任何排序，只是记账。

    每天只跑一次；LLM 单线程顺序打分，20 只要几分钟，所以丢线程里跑、不占刷新循环。
    """
    global _panel_batch_date

    from quantcore.quant import llm
    from quantcore.quant.investor_panel import run_panel_batch
    from quantcore.quant.local_store import get_local_store

    today = datetime.now(_TZ).strftime("%Y-%m-%d")
    if _panel_batch_date == today or not llm.available():
        return
    store = get_local_store()
    symbols = store.load_picks_symbols(today, "smart", 20)
    if not symbols:
        return  # 今天还没留痕，下一轮再来
    _panel_batch_date = today
    done = run_panel_batch(today, symbols)
    logger.warning("五方判读留痕：%s 当日名单 %d 只，新打分 %d 只", today, len(symbols), done)


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

    # 1.5) 盘中快照落库：每交易日一次，为「盘中生成名单」攒 point-in-time 样本。
    await _safe("intraday-snapshot", asyncio.to_thread(_capture_intraday_snapshot, snapshot))

    # 1.6) 五方判读留痕：每交易日给当日名单补一次分，供日后回答它有没有预测力。
    await _safe("panel-batch", asyncio.to_thread(_run_daily_panel_batch))

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
            # 参数必须与端点默认值一致：cache_key 含 limit 与 universe，
            # 预热用 10/5000 而用户默认请求 20/10000 会全部未命中，等于没预热。
            result = await m._compute_lite_smart_pool(
                "balanced",
                20,
                10000,
                force_refresh=False,
            )
            if m._smart_pool_response_has_items(result):
                _smart_pool_warm_date = today
        except Exception as exc:  # noqa: BLE001
            logger.warning("board refresh [smart-pool] failed: %s", exc)
