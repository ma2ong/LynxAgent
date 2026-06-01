import asyncio
from dataclasses import asdict
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from quantcore.quant import QuantEngine
from quantcore.quant.chart_service import build_chart_payload


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
    strategy: str = Field("ma_volume", description="ma_volume/turtle_breakout/rps_breakout/high_tight_flag/limit_up_washout/multi_ma_breakout")
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
        # 复用 smart-pool 的行业富集（cninfo，7天缓存）填充返回结果的行业/板块
        try:
            from app.lite_main import _enrich_smart_pool_industries
            result["items"] = await _enrich_smart_pool_industries(result.get("items", []))
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


@router.post("/backtest")
async def backtest_strategy(req: QuantBacktestRequest):
    try:
        result = await asyncio.to_thread(
            engine.backtest,
            req.symbol,
            req.strategy,
            req.start_date,
            req.end_date,
            req.initial_cash,
            req.engine,
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
async def research_factors(req: QuantResearchRequest):
    try:
        return await asyncio.to_thread(engine.research_factors, req.symbols, req.start_date, req.end_date, req.initial_cash)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
