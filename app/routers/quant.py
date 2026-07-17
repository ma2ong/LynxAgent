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
    """大盘环境标签：全市场 5 日中位涨幅 + 上涨占比 → 偏暖/中性/偏冷 + 仓位建议。"""
    try:
        from quantcore.quant.engine import market_context
        return await _run_light(market_context)
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
async def quant_replay_run(months: int = 12, step: int = 5, top_n: int = 20):
    """启动一次选股规则历史回放（后台线程，防重入；结果落库后由 /replay/results 查询）。"""
    try:
        from quantcore.quant.replay import start_replay_async
        return start_replay_async(
            months=max(1, min(months, 24)), step=max(1, min(step, 20)),
            top_n=max(3, min(top_n, 50)))
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

