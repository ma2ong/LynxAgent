"""个股分析路由：分析历史、单股/批量深度分析任务、个股检索与实时报价。

从 lite_main 拆出。任务态 `lite_analysis_tasks` 是这组独有的进程内状态，随路由一起
搬来；研报生成与深度富化在 app/core/analysis_report，历史/索引落库在
app/core/analysis_store，引擎与名录查询在 app/core/engine——都在模块顶部正常 import，
不再需要懒导入绕环。路径不变（无 prefix）。

单股大模型深度分析（POST /api/analysis/single）2026-09-29 随所有 AI 功能停用，只返回停用提示。
"""
from __future__ import annotations

import secrets
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.analysis_report import (
    build_lite_analysis_result,
    enrich_lite_result_with_deep_analysis,
    enrich_lite_result_with_professional_analysis,
)
from app.core.engine import get_stock_pool_items, lite_quant_engine, resolve_stock
from app.core.market_data import _apply_realtime_quote, _realtime_quotes
from app.core.schema import ensure_lite_analysis_history_table
from app.lite_auth import get_current_lite_user, store
from app.lite_billing import billing

router = APIRouter(tags=["analysis"])

lite_analysis_tasks: dict[str, dict[str, Any]] = {}


class LiteSingleAnalysisRequest(BaseModel):
    symbol: str | None = None
    stock_code: str | None = None
    parameters: dict[str, Any] | None = None


class LiteBatchAnalysisRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    symbols: list[str] | None = None
    stock_codes: list[str] | None = None
    parameters: dict[str, Any] | None = None


@router.get("/api/analysis/user/history")
async def analysis_history(
    page: int = 1,
    page_size: int = 20,
    user: dict[str, Any] = Depends(get_current_lite_user),
):
    ensure_lite_analysis_history_table()
    offset = (page - 1) * page_size
    with store.connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM lite_analysis_history WHERE username = ?",
            (user["username"],),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM lite_analysis_history WHERE username = ? ORDER BY analyzed_at DESC LIMIT ? OFFSET ?",
            (user["username"], page_size, offset),
        ).fetchall()
    items = [dict(row) for row in rows]
    return {
        "success": True,
        "data": {"items": items, "total": total, "page": page, "page_size": page_size},
        "message": "ok",
    }


@router.get("/api/analysis/tasks")
async def analysis_tasks(limit: int = 10, offset: int = 0):
    tasks = list(lite_analysis_tasks.values())
    return {
        "success": True,
        "data": {
            "tasks": tasks[offset : offset + limit],
            "items": tasks[offset : offset + limit],
            "total": len(tasks),
            "limit": limit,
            "offset": offset,
        },
        "message": "SaaS Lite 暂无后台分析任务",
    }


@router.post("/api/analysis/single")
async def single_analysis(req: LiteSingleAnalysisRequest):
    """大模型深度分析（用户自带密钥）2026-09-29 随所有 AI 功能一并停用。"""
    return {"success": False, "data": None, "code": "ai_disabled", "message": "AI 深度分析已停用"}


@router.post("/api/analysis/batch")
async def batch_analysis(req: LiteBatchAnalysisRequest, user: dict[str, Any] = Depends(get_current_lite_user)):
    raw_symbols = req.symbols or req.stock_codes or []
    symbols = []
    for item in raw_symbols:
        clean = str(item or "").strip()
        if clean and clean not in symbols:
            symbols.append(clean)
    if not symbols:
        return {"success": False, "data": None, "message": "请输入股票代码", "code": 400}
    if len(symbols) > 20:
        return {"success": False, "data": None, "message": "SaaS Lite 单次批量分析最多支持 20 只", "code": 400}

    billing.record(user["id"], "deep_analysis", n=len(symbols))

    batch_id = "batch_" + secrets.token_hex(8)
    now = datetime.now(timezone.utc).isoformat()
    task_ids: list[str] = []
    mapping: list[dict[str, Any]] = []

    for raw_symbol in symbols:
        task_id = "lite_" + secrets.token_hex(8)
        symbol = raw_symbol
        stock_meta = None
        result = None
        error_message = None
        status = "completed"
        current_step = "SaaS Lite 批量量化分析已完成"
        try:
            stock_meta = await resolve_stock(raw_symbol, (req.parameters or {}).get("market_type", "A股"))
            symbol = stock_meta["symbol"] if stock_meta else raw_symbol
            quant_result = asdict(lite_quant_engine.analyze(symbol))
            result = build_lite_analysis_result(task_id, symbol, quant_result, req.parameters or {}, now, stock_meta)
            result = await enrich_lite_result_with_deep_analysis(task_id, symbol, result, req.parameters or {}, stock_meta)
            quotes = await _realtime_quotes([symbol])
            quote = quotes.get(symbol)
            if quote and result:
                price = quote.get("price") or quote.get("close")
                pct = quote.get("change_percent") if quote.get("change_percent") is not None else quote.get("pct_chg")
                if price is not None:
                    result["current_price"] = price
                if pct is not None:
                    result["price_change_percent"] = pct
                if quote.get("change") is not None:
                    result["price_change"] = quote["change"]
                if quote.get("volume") is not None:
                    result["volume"] = quote["volume"]
                result["quote_source"] = quote.get("quote_source")
                result["quote_updated_at"] = quote.get("updated_at")
            if result:
                result = await enrich_lite_result_with_professional_analysis(symbol, result, quant_result, stock_meta, quote)
        except Exception as exc:
            status = "failed"
            error_message = str(exc)
            current_step = "SaaS Lite 批量量化分析失败"

        lite_analysis_tasks[task_id] = {
            "task_id": task_id,
            "analysis_id": task_id,
            "batch_id": batch_id,
            "symbol": symbol,
            "stock_symbol": symbol,
            "stock_name": (stock_meta or {}).get("name") or symbol,
            "status": status,
            "progress": 100,
            "progress_percentage": 100,
            "current_step": current_step,
            "message": current_step,
            "error_message": error_message,
            "result_data": result,
            "created_at": now,
            "updated_at": now,
        }
        task_ids.append(task_id)
        mapping.append({
            "symbol": symbol,
            "stock_code": symbol,
            "stock_name": (stock_meta or {}).get("name") or symbol,
            "task_id": task_id,
            "analysis_id": task_id,
            "status": status,
            "error_message": error_message,
        })

    return {
        "success": True,
        "data": {
            "batch_id": batch_id,
            "total_tasks": len(task_ids),
            "task_ids": task_ids,
            "mapping": mapping,
            "status": "completed",
        },
        "message": f"SaaS Lite 批量分析已完成：{len(task_ids)} 只",
    }


@router.get("/api/analysis/tasks/{task_id}/status")
async def analysis_task_status(task_id: str):
    task = lite_analysis_tasks.get(task_id)
    if not task:
        return {
            "success": True,
            "data": {
                "task_id": task_id,
                "analysis_id": task_id,
                "status": "failed",
                "progress": 100,
                "progress_percentage": 100,
                "current_step": "SaaS Lite 任务已过期",
                "message": "SaaS Lite 使用内存任务状态，后端重启后旧任务需要重新分析。",
                "error_message": "任务已过期，请重新分析。",
                "result_data": None,
            },
            "message": "任务已过期",
        }
    return {"success": True, "data": task, "message": "ok"}


@router.get("/api/analysis/tasks/{task_id}/result")
async def analysis_task_result(task_id: str):
    task = lite_analysis_tasks.get(task_id)
    if not task:
        return {"success": False, "data": None, "message": "任务不存在", "code": 404}
    return {"success": True, "data": task.get("result_data"), "message": "ok"}


@router.get("/api/stock-data/basic-info/{query}")
async def stock_basic_info(query: str, market: str | None = "A股"):
    stock = await resolve_stock(query, market)
    if not stock:
        return {"success": False, "data": None, "message": "未找到匹配股票", "code": 404}
    quotes = await _realtime_quotes([stock["symbol"]])
    quote = quotes.get(stock["symbol"], {})
    data = {
        "symbol": stock["symbol"],
        "stock_code": stock["symbol"],
        "name": quote.get("name") or stock["name"],
        "stock_name": quote.get("name") or stock["name"],
        "market": stock["market"],
    }
    _apply_realtime_quote(data, quote)
    return {
        "success": True,
        "data": data,
        "message": "ok",
    }


@router.get("/api/stocks/{symbol}/quote")
async def stock_quote(symbol: str):
    stock = await resolve_stock(symbol, "A股")
    clean_symbol = (stock or {}).get("symbol") or str(symbol).strip().zfill(6)
    quotes = await _realtime_quotes([clean_symbol])
    quote = quotes.get(clean_symbol)
    if not quote:
        try:
            quant = asdict(lite_quant_engine.analyze(clean_symbol))
            latest = quant.get("latest") or {}
            quote = {
                "symbol": clean_symbol,
                "code": clean_symbol,
                "name": (stock or {}).get("name") or clean_symbol,
                "price": latest.get("close"),
                "close": latest.get("close"),
                "change_percent": latest.get("pct_change"),
                "pct_chg": latest.get("pct_change"),
                "open": latest.get("open"),
                "high": latest.get("high"),
                "low": latest.get("low"),
                "prev_close": latest.get("prev_close"),
                "volume": latest.get("volume"),
                "amount": latest.get("amount"),
                "updated_at": datetime.now().astimezone().strftime("%Y/%m/%d %H:%M:%S"),
                "quote_source": "historical_latest_fallback",
            }
        except Exception:
            return {"success": False, "data": None, "message": "未找到实时行情", "code": 404}
    quote["name"] = quote.get("name") or (stock or {}).get("name") or clean_symbol
    quote["market"] = (stock or {}).get("market") or "A股"
    return {"success": True, "data": quote, "message": "ok"}


@router.get("/api/analysis/search")
async def analysis_search(query: str, market: str | None = "A股"):
    clean = str(query or "").strip().lower()
    if not clean:
        return {"success": True, "data": [], "message": "ok"}
    items = await get_stock_pool_items()
    matches = []
    for item in items:
        symbol = str(item.get("symbol", ""))
        name = str(item.get("name", ""))
        if clean in symbol.lower() or clean in name.lower():
            matches.append({
                "symbol": symbol,
                "stock_code": symbol,
                "name": name,
                "stock_name": name,
                "market": item.get("market") or market or "A股",
                "type": "stock",
            })
        if len(matches) >= 20:
            break
    return {"success": True, "data": matches, "message": "ok"}
