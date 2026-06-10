import asyncio
from dataclasses import asdict
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.lite_billing import require_quota
from quantcore.quant import QuantEngine
from quantcore.quant.chart_service import build_chart_payload
from quantcore.quant.data_sources import data_source_status
from quantcore.quant.report_service import build_stock_report
from quantcore.quant.pipeline import run_pipeline, list_runs, get_run, run_t5_review, quick_critic_batch


router = APIRouter(prefix="/api/quant", tags=["quant"])
engine = QuantEngine()


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
        return await asyncio.to_thread(engine.smart_pool, limit, universe_limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/pattern-pool")
async def quant_pattern_pool(limit: int = 20, universe_limit: int = 5000, min_strength: float = 70.0,
                            exclude_fundamental: bool = True):
    try:
        result = await asyncio.to_thread(engine.pattern_pool, limit, universe_limit, min_strength, exclude_fundamental)
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
async def serenity_events(force: bool = False, max_news: int = 30):
    """serenity 事件驱动选股：每日扫新闻→受益股卡片。非阻塞缓存。"""
    try:
        from quantcore.quant.serenity_service import request_events
        return await asyncio.to_thread(request_events, force, max_news)
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
