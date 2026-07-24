import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.lite_auth import get_current_lite_user
from app.lite_billing import require_quota
from app.core.scan_gate import run_scan
from quantcore.quant import QuantEngine
from quantcore.quant.chart_service import build_chart_payload
from quantcore.quant.data_sources import data_source_status
from quantcore.quant.report_service import build_stock_report
from quantcore.quant.pipeline import run_pipeline, list_runs, get_run, run_t5_review, quick_critic_batch
from quantcore.quant.investor_panel import investor_panel, run_panel_batch
from quantcore.quant.red_flags import red_flag_scan


router = APIRouter(prefix="/api/quant", tags=["quant"])
engine = QuantEngine()

# 轻量本地读接口（market-context / picks-stats）专用线程池：
# 默认执行器会被重扫描/数据同步占满导致这些秒级查询排队饿死，独立小池彻底隔离。
_light_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="quant-light")


async def _run_light(func, *args):
    return await asyncio.get_running_loop().run_in_executor(_light_executor, lambda: func(*args))


# 五方判读批量评分专用（LLM 顺序调用，单线程防限流），与轻量读接口隔离
_panel_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="panel-batch")


class QuantAnalyzeRequest(BaseModel):
    symbol: str = Field(..., description="股票代码，例如 600519 / 000001 / AAPL")
    start_date: Optional[str] = Field(None, description="开始日期 YYYY-MM-DD")
    end_date: Optional[str] = Field(None, description="结束日期 YYYY-MM-DD")


class QuantScreenRequest(BaseModel):
    symbols: List[str] = Field(..., min_length=1, description="股票代码列表")
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    limit: int = Field(30, ge=1, le=200)


class QuantBacktestRequest(BaseModel):
    symbol: str
    strategy: str = Field("ma_volume", description="single strategy name (used when 'strategies' is empty)")
    strategies: Optional[List[str]] = Field(None, description="compose multiple strategies into one signal")
    combine: str = Field("and", description="and/or/majority — how to combine 'strategies'")
    stop_loss_pct: float = Field(0.0, ge=0, le=0.5, description="0 disables; e.g. 0.08 = exit on 8% drawdown from entry")
    engine: str = Field("vector", description="vector/backtrader/akquant")
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    initial_cash: float = Field(100000.0, gt=0)


class QuantPoolRequest(BaseModel):
    limit: int = Field(200, ge=1, le=5000)


class QuantResearchRequest(BaseModel):
    symbols: List[str] = Field(..., min_length=1, max_length=50)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    initial_cash: float = Field(100000.0, gt=0)


class QuantForecastRequest(BaseModel):
    symbol: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    horizon: int = Field(10, ge=1, le=120)


def _get_optional_db():
    try:
        from app.core.database import get_mongo_db

        return get_mongo_db()
    except Exception:
        return None


@router.post("/analyze")
async def analyze_stock(req: QuantAnalyzeRequest):
    try:
        result = await asyncio.to_thread(engine.analyze, req.symbol, req.start_date, req.end_date)
        return asdict(result)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/kline")
async def quant_kline(symbol: str, name: str = "", days: int = 250):
    try:
        return await asyncio.to_thread(build_chart_payload, symbol, name, days)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/capabilities")
async def quant_capabilities():
    return engine.capabilities()


@router.get("/data-sources")
async def quant_data_sources():
    return await asyncio.to_thread(data_source_status)


@router.get("/smart-pool")
async def quant_smart_pool(limit: int = 20, universe_limit: int = 300):
    try:
        return await run_scan(engine.smart_pool, limit, universe_limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/pattern-pool")
async def quant_pattern_pool(limit: int = 20, universe_limit: int = 5000, min_strength: float = 70.0,
                            exclude_fundamental: bool = True):
    try:
        result = await run_scan(engine.pattern_pool, limit, universe_limit, min_strength, exclude_fundamental)
        # 补全「行业/板块」：stock_meta 行业常为空，按 cninfo 给返回项补行业（并行+整体超时，不拖死端点）。
        items = result.get("items") if isinstance(result, dict) else None
        if items:
            try:
                from quantcore.quant import industry as _industry
                await asyncio.to_thread(_industry.enrich_industries, items)
            except Exception:
                pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/rs-pool")
async def quant_rs_pool(limit: int = 30, universe_limit: int = 5000,
                        dist_min: float = 70.0, adr_min: float = 4.5,
                        require_ema: bool = True, exclude_fundamental: bool = True):
    """相对强度筛选器（强势股研究清单）。"""
    try:
        result = await run_scan(engine.strength_pool, limit, universe_limit,
                                dist_min, adr_min, require_ema, exclude_fundamental)
        items = result.get("items") if isinstance(result, dict) else None
        if items:
            try:
                from quantcore.quant import industry as _industry
                await asyncio.to_thread(_industry.enrich_industries, items)
            except Exception:
                pass
            # 补「领先/落后板块」标：读当日板块轮动缓存（两者都跑过当天才有；缺失则不打标）
            try:
                from quantcore.quant.engine import _SECTOR_ROTATION_CACHE
                leaders = _SECTOR_ROTATION_CACHE.get("leaders") or set()
                laggards = _SECTOR_ROTATION_CACHE.get("laggards") or set()
                for it in items:
                    ind = it.get("industry") or ""
                    it["sector_pos"] = "leading" if ind in leaders else ("lagging" if ind in laggards else "")
            except Exception:
                pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/forecast")
async def forecast_stock(req: QuantForecastRequest):
    try:
        result = await asyncio.to_thread(engine.forecast, req.symbol, req.start_date, req.end_date, req.horizon)
        return asdict(result)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/patterns")
async def recognize_stock_patterns(req: QuantAnalyzeRequest):
    try:
        result = await asyncio.to_thread(engine.patterns, req.symbol, req.start_date, req.end_date)
        return asdict(result)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/screen")
async def screen_stocks(req: QuantScreenRequest):
    try:
        return await asyncio.to_thread(engine.screen, req.symbols, req.start_date, req.end_date, req.limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ml/factor-model")
async def ml_factor_model(
    universe_limit: int = 500,
    horizon: int = 5,
    k: int = 50,
    mode: str = "rolling",
    neutralize: bool = True,
    retrain_every: int = 20,
    force: bool = False,
    user: dict = require_quota("factor_model", feature="lab", cost=0),
):
    """LightGBM 因子模型：滚动再训练 + Top-K 选股 + 回测净值。

    非阻塞：命中缓存返回 status=ready，否则后台计算返回 status=computing（前端轮询）。
    universe_limit<=0 表示全市场。结果缓存 6 小时。
    """
    try:
        from quantcore.quant.ml.service import request_ml_factor
        return await asyncio.to_thread(
            request_ml_factor, universe_limit, horizon, k, mode, neutralize, retrain_every, 250, force
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class SerenityDeepRequest(BaseModel):
    theme: str
    event: str = ""
    beneficiaries: List[dict] = Field(default_factory=list)


@router.get("/serenity/events")
async def serenity_events(force: bool = False, max_news: int = 10,
                          user: dict = Depends(get_current_lite_user)):
    """serenity 事件驱动选股：每日扫新闻→受益股卡片。非阻塞缓存。"""
    try:
        from quantcore.quant.serenity_service import request_events
        if not user.get("is_admin"):
            force = False
        max_news = max(1, min(int(max_news), 10))
        result = await asyncio.to_thread(request_events, force, max_news)
        if isinstance(result, dict) and result.get("status") == "ready":
            try:
                from app.lite_notifications import notification_store
                await asyncio.to_thread(notification_store.notify_favorite_catalysts, result.get("events") or [])
            except Exception:
                pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/serenity/deep")
async def serenity_deep(req: SerenityDeepRequest,
                        user: dict = require_quota("serenity_deep", feature="serenity_deep")):
    """对某题材跑完整 serenity 5 步深度报告。"""
    try:
        from quantcore.quant.serenity_service import deep_for_theme
        return await asyncio.to_thread(deep_for_theme, req.theme, req.event, req.beneficiaries)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/backtest")
async def backtest_strategy(req: QuantBacktestRequest,
                            user: dict = require_quota("backtest", feature="lab", cost=0)):
    try:
        result = await asyncio.to_thread(
            engine.backtest,
            req.symbol,
            req.strategy,
            req.start_date,
            req.end_date,
            req.initial_cash,
            req.engine,
            req.strategies,
            req.combine,
            req.stop_loss_pct,
        )
        return asdict(result)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/pool")
async def get_stock_pool(limit: int = 200):
    try:
        return await engine.stock_pool(db=_get_optional_db(), limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/datalake/sync")
async def sync_datalake(req: QuantPoolRequest):
    try:
        return await engine.sync_stock_pool(db=_get_optional_db(), limit=req.limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/research")
async def research_factors(req: QuantResearchRequest,
                           user: dict = require_quota("research", feature="lab", cost=0)):
    try:
        return await asyncio.to_thread(engine.research_factors, req.symbols, req.start_date, req.end_date, req.initial_cash)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---- 个股 AI 研报 (feature A) ----
@router.get("/report")
async def quant_report(symbol: str, user: dict = require_quota("stock_report")):
    try:
        return await asyncio.to_thread(build_stock_report, symbol)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---- 多 Agent 选股流水线 (feature B) ----
class PipelineRunRequest(BaseModel):
    universe: Optional[List[str]] = None
    max_candidates: int = Field(40, ge=1, le=200)


@router.post("/pipeline/run")
async def quant_pipeline_run(req: PipelineRunRequest,
                             user: dict = require_quota("pipeline", feature="lab")):
    try:
        return await asyncio.to_thread(run_pipeline, req.universe, req.max_candidates, True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/pipeline/runs")
async def quant_pipeline_runs():
    return {"runs": list_runs()}


@router.get("/pipeline/runs/{run_id}")
async def quant_pipeline_run_detail(run_id: str):
    try:
        return get_run(run_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/pipeline/t5-review")
async def quant_pipeline_t5(run_id: Optional[str] = None,
                            user: dict = require_quota("pipeline", feature="lab")):
    from quantcore.quant.pipeline.orchestrator import RUNS_DIR
    import os
    run_dir = os.path.join(RUNS_DIR, run_id) if run_id else None
    return await asyncio.to_thread(run_t5_review, run_dir, None)


class QuickCriticRequest(BaseModel):
    symbols: List[str] = Field(..., min_length=1, max_length=100)
    names: Optional[dict] = None


@router.post("/pipeline/quick-critic")
async def pipeline_quick_critic(req: QuickCriticRequest,
                                user: dict = require_quota("pipeline", feature="lab")):
    """对给定股票列表做快速规则 critic 打分，供一键推荐/形态智选结果富集 AI 评审分。"""
    try:
        return await asyncio.to_thread(quick_critic_batch, req.symbols, req.names)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---- 资金面：资金流向 / 龙虎榜 / 财经日历（纯本地数据，不计费）----
@router.get("/market-context")
async def quant_market_context():
    """大盘环境标签：赚钱效应（逐日中位+广度加权温度）+ 指数口径 + 仓位建议。

    传入全市场实时快照，让当日盘中直接进入温度计算——否则日线要等收盘同步，
    横幅会整个交易日停在昨天，与顶部宏观条的实时指数同屏打架。
    快照拿不到时 market_context 自动退回日线口径。
    """
    try:
        from quantcore.quant.engine import market_context

        snapshot = {}
        try:  # 延迟导入：lite_main 在启动时 import 本模块，顶层导入会成环
            from app.lite_main import _load_realtime_quotes_snapshot
            snapshot = await asyncio.wait_for(
                asyncio.to_thread(_load_realtime_quotes_snapshot, 30), timeout=8.0) or {}
        except Exception:
            snapshot = {}
        return await _run_light(market_context, snapshot)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/symbol-lookup")
async def quant_symbol_lookup(q: str, limit: int = 8):
    """代码/名称模糊查询 → 候选股列表。个股深研支持"名称或代码"二选一输入靠它解析。"""
    kw = str(q or "").strip()
    if not kw:
        return {"items": []}

    def _run():
        from quantcore.quant.local_store import get_local_store
        meta = get_local_store().load_meta()
        kw_low = kw.lower()
        exact, prefix, contains = [], [], []
        for m in meta:
            sym = str(m.get("symbol") or "")
            name = str(m.get("name") or "")
            row = {"symbol": sym, "name": name}
            if sym == kw or name == kw:
                exact.append(row)
            elif sym.startswith(kw) or name.startswith(kw):
                prefix.append(row)
            elif kw_low in sym.lower() or kw_low in name.lower():
                contains.append(row)
        return {"items": (exact + prefix + contains)[: max(1, min(limit, 20))]}

    try:
        return await _run_light(_run)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _load_snapshot() -> dict:
    try:
        from app.lite_main import _load_realtime_quotes_snapshot
        return await asyncio.wait_for(
            asyncio.to_thread(_load_realtime_quotes_snapshot, 30), timeout=8.0) or {}
    except Exception:
        return {}


def _cold_excess() -> Optional[float]:
    """回放偏冷期一键智选池 T+5 平均超额，作市场风险的历史锚。取不到返回 None。"""
    try:
        from quantcore.quant.replay import latest_replay_summary
        summary = latest_replay_summary() or {}
        for p in summary.get("pools", []):
            if p.get("pool") == "smart":
                for r in p.get("regimes", []):
                    if r.get("regime") == "偏冷":
                        return float(r.get("avg_excess"))
    except Exception:
        pass
    return None


@router.get("/risk-alert")
async def quant_risk_alert():
    """市场级风险仪表：赚钱效应温度/连续走弱/跌停潮/广度骤降 → 风险等级 + 明确仓位动作。"""
    try:
        from quantcore.quant.engine import market_context
        from quantcore.quant.risk_alert import market_risk_gauge

        snapshot = await _load_snapshot()
        ctx = await _run_light(market_context, snapshot) or {}
        daily = ctx.get("daily") or []
        temp = float(ctx.get("temp") if ctx.get("temp") is not None else 50.0)

        limitdown_share = None
        if snapshot:
            severe = total = 0
            for q in snapshot.values():
                pct = q.get("change_percent")
                if pct is None:
                    pct = q.get("pct_chg")
                if pct is None:
                    continue
                total += 1
                if float(pct) <= -9.0:
                    severe += 1
            if total >= 500:
                limitdown_share = severe / total

        # 破位广度：全市场跌破 MA10&MA20 占比（结构性下跌强度）。复用卖出扫描缓存，
        # 缓存冷时才现算一次 breakdown_metrics（单次窗口查询，秒级）
        def _breakdown_share():
            from quantcore.quant.local_store import get_local_store
            cached = _RISK_SCAN_CACHE.get("data")
            import time as _t
            if cached and _t.time() - _RISK_SCAN_CACHE.get("at", 0) < 600 and cached.get("universe"):
                return (cached.get("breakdown_count") or 0) / cached["universe"]
            metrics = get_local_store().breakdown_metrics()
            if not metrics:
                return None
            broke = sum(1 for m in metrics.values()
                        if m["close"] > 0 and m["close"] < m["ma10"] and m["close"] < m["ma20"])
            return broke / len(metrics)
        try:
            breakdown_share = await _run_light(_breakdown_share)
        except Exception:
            breakdown_share = None

        gauge = market_risk_gauge(daily, temp, limitdown_share=limitdown_share,
                                  breakdown_share=breakdown_share, cold_excess=_cold_excess())
        # 环境标签与横幅同源，一并回传方便前端对齐
        gauge["market_state"] = ctx.get("state")
        gauge["as_of"] = ctx.get("as_of")
        gauge["intraday"] = ctx.get("intraday")
        return gauge
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


_RISK_SCAN_CACHE: dict = {}


@router.get("/risk-scan")
async def quant_risk_scan(limit: int = 200):
    """全市场卖出信号扫描：破位下行（跌破 MA10/MA20）+ 问题股（ST/退市/预亏），10 分钟缓存。"""
    import time as _time
    cached = _RISK_SCAN_CACHE.get("data")
    if cached and _time.time() - _RISK_SCAN_CACHE.get("at", 0) < 600:
        return cached

    def _run():
        from quantcore.quant.local_store import get_local_store
        from quantcore.quant.risk_alert import scan_sell_signals
        store = get_local_store()
        metrics = store.breakdown_metrics()
        names = {str(m.get("symbol")): str(m.get("name") or "") for m in store.load_meta()}
        try:
            bad = store.load_bad_forecast_symbols()
        except Exception:
            bad = set()
        result = scan_sell_signals(metrics, names, bad_forecast=bad, limit=limit)
        result["as_of"] = ""
        try:
            result["as_of"] = store.latest_real_bar_date() or ""
        except Exception:
            pass
        result["universe"] = len(metrics)
        return result

    try:
        data = await _run_light(_run)
        _RISK_SCAN_CACHE["data"], _RISK_SCAN_CACHE["at"] = data, _time.time()
        return data
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/picks/stats")
async def quant_picks_stats(days: int = 30, pool: str = ""):
    """选股留痕复盘：各池 T+1/T+3/T+5 真实胜率与平均收益（数据来自每日扫描自动留痕）。"""
    try:
        from quantcore.quant.local_store import get_local_store
        safe_days = max(1, min(days, 120))
        return await _run_light(get_local_store().evaluate_picks, safe_days, pool or None)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/risk-check")
async def quant_risk_check(symbol: str):
    """七不买体检：对任意个股跑规则化风险检查（日线 + 实时行情 + 业绩预告标记）。"""
    sym = str(symbol or "").strip().zfill(6)
    if not sym.strip("0") or len(sym) != 6:
        raise HTTPException(status_code=400, detail="无效股票代码")

    def _run(snapshot):
        from quantcore.quant.data import load_local_kline
        from quantcore.quant.decision import stock_decision
        from quantcore.quant.engine import _fetch_tencent_quotes, market_context
        from quantcore.quant.local_store import get_local_store

        df = load_local_kline(sym, days=200)
        if df is None or len(df) < 20:
            raise ValueError("本地日线不足，请先在数据中心同步该股行情")
        quote = (_fetch_tencent_quotes([sym]) or {}).get(sym) or {}
        try:
            bad = sym in get_local_store().load_bad_forecast_symbols()
        except Exception:
            bad = False
        env, temp = "", None
        try:
            # 与顶部横幅传同一份快照，否则同一时刻个股深研说偏冷、横幅说中性
            ctx = market_context(snapshot) or {}
            env = str(ctx.get("state") or "")
            temp = ctx.get("temp")
        except Exception:
            pass
        name = str(quote.get("name") or "")
        return stock_decision(sym, name, df, quote=quote, market_env=env,
                              bad_forecast=bad, market_temp=temp)

    snapshot = {}
    try:
        from app.lite_main import _load_realtime_quotes_snapshot
        snapshot = await asyncio.wait_for(
            asyncio.to_thread(_load_realtime_quotes_snapshot, 30), timeout=8.0) or {}
    except Exception:
        snapshot = {}
    try:
        return await asyncio.to_thread(_run, snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/signal-stats")
async def quant_signal_stats(pool: str = "smart", days: int = 90):
    """信号历史表现（入选理由卡）：池级留痕/回放双口径 + 形态级 T+5 超额聚合。"""
    try:
        from quantcore.quant.local_store import get_local_store
        safe_days = max(7, min(days, 180))
        return await _run_light(get_local_store().signal_stats, pool, safe_days)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/replay/run")
async def quant_replay_run(months: int = 12, step: int = 5, top_n: int = 20, anchor: str = ""):
    """启动一次选股规则历史回放（后台线程，防重入；结果落库后由 /replay/results 查询）。

    anchor（YYYY-MM-DD，可选）：会话轴锚定日。跑 A/B 时传旧 run 的锚定日可复现同一条轴，
    让两次运行只差评分器版本（run_replay 本就支持，此处透传）。
    """
    try:
        from datetime import date as _date
        from quantcore.quant.replay import start_replay_async
        anchor_val = None
        if anchor:
            _date.fromisoformat(anchor)  # 非法日期直接 400
            anchor_val = anchor
        return start_replay_async(
            months=max(1, min(months, 24)), step=max(1, min(step, 20)),
            top_n=max(3, min(top_n, 50)), anchor=anchor_val)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"anchor 需为 YYYY-MM-DD：{exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/replay/status")
async def quant_replay_status():
    from quantcore.quant.replay import replay_status
    return replay_status()


@router.get("/replay/results")
async def quant_replay_results():
    """最近一次完成的回放汇总：各池月度超额胜率与累计超额曲线。"""
    try:
        from quantcore.quant.replay import latest_replay_summary
        return await _run_light(latest_replay_summary) or {}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---- 聪明钱：活跃席位 / 席位胜率 / 基金重仓（akshare，6h 缓存，不计费）----


# ---- 个股深研增强：评委打分 / 红旗快查（走 LLM，计入配额）----
@router.get("/stock/investor-panel")
async def quant_investor_panel(symbol: str, user: dict = require_quota("investor_panel")):
    return await asyncio.to_thread(investor_panel, symbol)


_PANEL_POOLS = ("smart", "pattern", "swing", "auction")


@router.get("/panel/batch")
async def quant_panel_batch(pool: str = "smart", limit: int = 20):
    """当日选股池候选的五方判读批量评分：返回已有评分，缺的丢后台补打（不阻塞）。"""
    from datetime import datetime
    from quantcore.quant import llm
    from quantcore.quant.local_store import get_local_store

    if pool not in _PANEL_POOLS:
        raise HTTPException(status_code=400, detail=f"pool 必须是 {'/'.join(_PANEL_POOLS)}")
    limit = max(1, min(limit, 20))
    today = datetime.now().strftime("%Y-%m-%d")
    store = get_local_store()
    symbols = await _run_light(store.load_picks_symbols, today, pool, limit)
    if not symbols:
        return {"success": True, "data": {"date": today, "pool": pool, "items": {},
                                          "pending": 0, "llm": llm.available(),
                                          "message": "今日该池暂无留痕候选，先跑一次选股"}}
    scores = await _run_light(store.load_panel_scores, today, symbols)
    pending = [s for s in symbols if s not in scores]
    if pending and llm.available():
        _panel_executor.submit(run_panel_batch, today, pending)
    return {"success": True, "data": {
        "date": today, "pool": pool, "items": scores,
        "pending": len(pending) if llm.available() else 0,
        "llm": llm.available(),
    }}


@router.get("/stock/red-flags")
async def quant_red_flags(symbol: str, user: dict = require_quota("red_flags")):
    return await asyncio.to_thread(red_flag_scan, symbol)


# ---- 加权情绪：个股 / 大盘 / 板块（纯本地，无 LLM，不计费）----

