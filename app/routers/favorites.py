"""自选股 + 组合诊断 + 价格预警刷新路由（从 lite_main 拆出）。

store/鉴权直接从 app.lite_auth 导入；lite_main 的共享 helper 在各 handler 内懒导入，
避免与 lite_main 成环（同 paper 模式）。路径不变（无 prefix）。/api/tags/ 是个空 stub，一并落此。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.lite_auth import get_current_lite_user, store

router = APIRouter(tags=["favorites"])


class LiteFavoriteRequest(BaseModel):
    symbol: str | None = None
    stock_code: str | None = None
    stock_name: str | None = None
    market: str | None = "A股"
    tags: list[str] | None = None
    notes: str | None = None
    alert_price_high: float | None = None
    alert_price_low: float | None = None


@router.get("/api/favorites/")
async def favorites(user: dict[str, Any] = Depends(get_current_lite_user)):
    from app.lite_main import (_realtime_quotes, _resolve_real_industry, _apply_realtime_quote, _safe_number, ensure_lite_favorites_table, _check_and_record_price_alert, resolve_stock, lite_quant_engine)  # noqa: F401,E501
    ensure_lite_favorites_table()
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM lite_favorites WHERE username = ? ORDER BY added_at DESC",
            (user["username"],),
        ).fetchall()
    items = []
    for row in rows:
        try:
            tags = json.loads(row["tags_json"] or "[]")
        except json.JSONDecodeError:
            tags = []
        items.append({
            "symbol": row["stock_code"],
            "stock_code": row["stock_code"],
            "stock_name": row["stock_name"],
            "market": row["market"],
            "tags": tags if isinstance(tags, list) else [],
            "notes": row["notes"] or "",
            "added_price": row["added_price"] if "added_price" in row.keys() else None,
            "alert_price_high": row["alert_price_high"] if "alert_price_high" in row.keys() else None,
            "alert_price_low": row["alert_price_low"] if "alert_price_low" in row.keys() else None,
            "added_at": row["added_at"],
        })
    quotes = await _realtime_quotes([item["stock_code"] for item in items])
    industries = await asyncio.gather(
        *[
            _resolve_real_industry(item["stock_code"], item.get("stock_name") or item["stock_code"], set())
            for item in items
        ],
        return_exceptions=True,
    ) if items else []
    added_price_backfills: list[tuple[float, str]] = []
    for item in items:
        quote = quotes.get(item["stock_code"])
        _apply_realtime_quote(item, quote)
        if quote and quote.get("price") is not None:
            item["current_price"] = quote["price"]
        if quote and quote.get("change_percent") is not None:
            item["change_percent"] = quote["change_percent"]
        current_price = _safe_number(item.get("current_price"))
        added_price = _safe_number(item.get("added_price"))
        if current_price and (not added_price or added_price <= 0):
            added_price = current_price
            item["added_price"] = added_price
            added_price_backfills.append((added_price, item["stock_code"]))
        if current_price and added_price and added_price > 0:
            item["change_since_added_percent"] = round((current_price / added_price - 1) * 100, 2)
    for item, industry in zip(items, industries):
        if isinstance(industry, str) and industry:
            item["board"] = industry
            item["industry"] = industry
    if added_price_backfills:
        now = datetime.now(timezone.utc).isoformat()
        with store.connect() as conn:
            conn.executemany(
                "UPDATE lite_favorites SET added_price = ?, updated_at = ? WHERE username = ? AND stock_code = ? AND (added_price IS NULL OR added_price <= 0)",
                [(price, now, user["username"], code) for price, code in added_price_backfills],
            )
            conn.commit()
    return {"success": True, "data": items, "message": "ok"}


def _portfolio_max_drawdown(values: list[float]) -> float:
    peak = None
    max_dd = 0.0
    for value in values:
        if value <= 0:
            continue
        peak = value if peak is None else max(peak, value)
        if peak:
            max_dd = min(max_dd, value / peak - 1)
    return max_dd


async def _favorite_portfolio_items(username: str) -> list[dict[str, Any]]:
    from app.lite_main import (_realtime_quotes, _resolve_real_industry, _apply_realtime_quote, _safe_number, ensure_lite_favorites_table, _check_and_record_price_alert, resolve_stock, lite_quant_engine)  # noqa: F401,E501
    ensure_lite_favorites_table()
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM lite_favorites WHERE username = ? ORDER BY added_at DESC",
            (username,),
        ).fetchall()
    return [
        {
            "symbol": row["stock_code"],
            "name": row["stock_name"],
            "market": row["market"],
            "added_price": row["added_price"] if "added_price" in row.keys() else None,
            "added_at": row["added_at"],
        }
        for row in rows
    ]


@router.get("/api/favorites/portfolio/diagnostics")
async def favorites_portfolio_diagnostics(user: dict[str, Any] = Depends(get_current_lite_user)):
    from app.lite_main import (_realtime_quotes, _resolve_real_industry, _apply_realtime_quote, _safe_number, ensure_lite_favorites_table, _check_and_record_price_alert, resolve_stock, lite_quant_engine)  # noqa: F401,E501
    import pandas as pd
    from quantcore.quant.data import load_local_kline

    items = await _favorite_portfolio_items(user["username"])
    if not items:
        return {
            "success": True,
            "data": {
                "score": 0,
                "grade": "暂无自选",
                "summary": "添加自选股后，系统会自动评估组合风险、行业集中度和关注权重。",
                "items": [],
                "industry_exposure": [],
                "correlation_pairs": [],
                "risk_flags": [],
                "suggested_actions": ["先添加 5-12 只候选股，再进行组合体检。"],
            },
            "message": "ok",
        }

    symbols = [item["symbol"] for item in items]
    quotes = await _realtime_quotes(symbols)
    industry_results = await asyncio.gather(
        *[_resolve_real_industry(item["symbol"], item["name"], set()) for item in items],
        return_exceptions=True,
    )
    async def load_quant(symbol: str) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(lambda target=symbol: asdict(lite_quant_engine.analyze(target))),
                timeout=8,
            )
        except Exception:
            return {"score": 0, "factors": {}, "risk": {}, "latest": {}}

    async def load_returns(symbol: str) -> tuple[str, Any]:
        try:
            df = await asyncio.wait_for(asyncio.to_thread(load_local_kline, symbol, 180), timeout=3)
            return symbol, df
        except Exception:
            return symbol, None

    quant_results = await asyncio.gather(*(load_quant(item["symbol"]) for item in items))
    kline_results = dict(await asyncio.gather(*(load_returns(item["symbol"]) for item in items)))

    frames: dict[str, pd.Series] = {}
    analyzed_items: list[dict[str, Any]] = []
    for item, industry_result, quant in zip(items, industry_results, quant_results):
        symbol = item["symbol"]
        name = item["name"] or symbol
        industry = industry_result if isinstance(industry_result, str) and industry_result else "未分类"
        quote = quotes.get(symbol) or {}
        factors = quant.get("factors") or {}
        risk = quant.get("risk") or {}
        df = kline_results.get(symbol)
        if df is not None and not df.empty and "close" in df.columns:
            close = pd.to_numeric(df["close"], errors="coerce").dropna()
            returns = close.pct_change().dropna().tail(120)
            if len(returns) >= 20:
                frames[symbol] = returns.reset_index(drop=True)
        volatility = abs(float(risk.get("volatility") or 0))
        max_drawdown = float(risk.get("max_drawdown") or 0)
        score = float(quant.get("score") or 0)
        trend = float(factors.get("trend") or 0)
        momentum = float(factors.get("momentum") or 0)
        risk_control = float(factors.get("risk_control") or 0)
        suggested_weight = 0.0
        if score >= 82 and risk_control >= 65 and abs(max_drawdown) <= 0.18:
            suggested_weight = 0.12
        elif score >= 72 and risk_control >= 50 and abs(max_drawdown) <= 0.25:
            suggested_weight = 0.08
        elif score >= 62:
            suggested_weight = 0.05
        risk_tags: list[str] = []
        if volatility >= 0.45:
            risk_tags.append("高波动")
        if abs(max_drawdown) >= 0.28:
            risk_tags.append("回撤偏大")
        if risk_control < 45:
            risk_tags.append("风控弱")
        if trend < 50 and momentum < 50:
            risk_tags.append("趋势动量弱")
        analyzed_items.append(
            {
                "symbol": symbol,
                "name": name,
                "industry": industry,
                "current_price": quote.get("price") or quote.get("close"),
                "change_percent": quote.get("change_percent") if quote.get("change_percent") is not None else quote.get("pct_chg"),
                "quant_score": round(score, 1),
                "trend": round(trend, 1),
                "momentum": round(momentum, 1),
                "risk_control": round(risk_control, 1),
                "volatility": round(volatility, 4),
                "max_drawdown": round(max_drawdown, 4),
                "suggested_weight": round(suggested_weight, 4),
                "risk_tags": risk_tags,
            }
        )

    n = len(analyzed_items)
    equal_weight = 1 / n if n else 0
    industry_counts: dict[str, int] = {}
    industry_weights: dict[str, float] = {}
    for item in analyzed_items:
        industry = item["industry"] or "未分类"
        industry_counts[industry] = industry_counts.get(industry, 0) + 1
        industry_weights[industry] = industry_weights.get(industry, 0.0) + equal_weight
    industry_exposure = [
        {"industry": industry, "count": industry_counts[industry], "weight": round(weight, 4)}
        for industry, weight in sorted(industry_weights.items(), key=lambda kv: kv[1], reverse=True)
    ]
    top_industry_weight = max(industry_weights.values(), default=0.0)

    aligned = pd.DataFrame(frames)
    if not aligned.empty:
        aligned = aligned.dropna(axis=0, how="any")
    avg_corr = 0.0
    corr_pairs: list[dict[str, Any]] = []
    if aligned.shape[1] >= 2 and len(aligned) >= 20:
        corr = aligned.corr().fillna(0)
        vals = []
        for i, left in enumerate(corr.columns):
            for right in corr.columns[i + 1:]:
                value = float(corr.loc[left, right])
                vals.append(value)
                if value >= 0.72:
                    left_name = next((item["name"] for item in analyzed_items if item["symbol"] == left), left)
                    right_name = next((item["name"] for item in analyzed_items if item["symbol"] == right), right)
                    corr_pairs.append({"left": left, "left_name": left_name, "right": right, "right_name": right_name, "correlation": round(value, 3)})
        avg_corr = round(sum(vals) / len(vals), 3) if vals else 0.0
        corr_pairs = sorted(corr_pairs, key=lambda item: item["correlation"], reverse=True)[:8]

    portfolio_volatility = 0.0
    portfolio_max_drawdown = 0.0
    return_coverage = (len(frames) / n) if n else 0.0
    if not aligned.empty and aligned.shape[1] >= 1:
        portfolio_returns = aligned.mean(axis=1)
        portfolio_volatility = float(portfolio_returns.std() * (252 ** 0.5)) if len(portfolio_returns) > 1 else 0.0
        equity = (1 + portfolio_returns).cumprod().tolist()
        portfolio_max_drawdown = _portfolio_max_drawdown([float(v) for v in equity])

    risk_flags: list[str] = []
    suggested_actions: list[str] = []
    if n < 5:
        risk_flags.append("自选股数量少于 5 只，组合分散度不足。")
        suggested_actions.append("补充不同主题/行业的候选股，避免组合只押单一方向。")
    if return_coverage < 0.60:
        risk_flags.append(f"历史收益覆盖率约 {return_coverage:.0%}，波动/回撤/相关性结论置信度偏低。")
        suggested_actions.append("先到数据中心完成本地 K 线同步，再重新做组合体检。")
    if top_industry_weight >= 0.45:
        top_industry = industry_exposure[0]["industry"] if industry_exposure else "单一行业"
        risk_flags.append(f"{top_industry} 权重约 {top_industry_weight:.0%}，行业集中度偏高。")
        suggested_actions.append("降低最高行业权重，或加入低相关行业作为对冲观察。")
    if avg_corr >= 0.65:
        risk_flags.append(f"组合平均相关性 {avg_corr:.2f}，同涨同跌风险较高。")
        suggested_actions.append("优先剔除高度相关且量化分较低的重复标的。")
    if portfolio_max_drawdown <= -0.22:
        risk_flags.append(f"等权历史最大回撤约 {portfolio_max_drawdown:.1%}，回撤压力偏大。")
        suggested_actions.append("把高回撤个股的关注权重降到低位。")
    weak_count = sum(1 for item in analyzed_items if item["risk_control"] < 45 or item["quant_score"] < 60)
    if weak_count:
        risk_flags.append(f"{weak_count} 只股票量化/风控偏弱，需要降级为观察。")
        suggested_actions.append("先处理风控弱、趋势动量弱的股票，再考虑新增标的。")
    if not risk_flags:
        suggested_actions.append("组合暂无硬性风险，维持分散跟踪；单票关注权重仍不宜过高。")

    score = 100
    score -= max(0, n < 5) * 15
    score -= min(20, max(0.0, 0.60 - return_coverage) * 40)
    score -= min(25, max(0.0, top_industry_weight - 0.30) * 100)
    score -= min(20, max(0.0, avg_corr - 0.45) * 50)
    score -= min(20, abs(min(0.0, portfolio_max_drawdown)) * 55)
    score -= min(20, weak_count * 5)
    score = round(max(0.0, min(100.0, score)), 1)
    grade = "健康" if score >= 80 else "需要优化" if score >= 60 else "风险偏高"

    return {
        "success": True,
        "data": {
            "score": score,
            "grade": grade,
            "summary": f"当前自选 {n} 只，等权组合年化波动约 {portfolio_volatility:.1%}，最大回撤约 {portfolio_max_drawdown:.1%}，平均相关性 {avg_corr:.2f}。",
            "assumption": "按自选股等权观察测算，关注权重是研究约束，不是自动下单。",
            "portfolio": {
                "count": n,
                "equal_weight": round(equal_weight, 4),
                "volatility": round(portfolio_volatility, 4),
                "max_drawdown": round(portfolio_max_drawdown, 4),
                "avg_correlation": avg_corr,
                "top_industry_weight": round(top_industry_weight, 4),
                "return_coverage": round(return_coverage, 4),
            },
            "items": sorted(analyzed_items, key=lambda item: (item["suggested_weight"], item["quant_score"]), reverse=True),
            "industry_exposure": industry_exposure,
            "correlation_pairs": corr_pairs,
            "risk_flags": risk_flags,
            "suggested_actions": suggested_actions[:5],
            "updated_at": datetime.now().astimezone().strftime("%Y/%m/%d %H:%M:%S"),
        },
        "message": "ok",
    }


@router.post("/api/favorites/")
async def add_favorite(payload: LiteFavoriteRequest, user: dict[str, Any] = Depends(get_current_lite_user)):
    from app.lite_main import (_realtime_quotes, _resolve_real_industry, _apply_realtime_quote, _safe_number, ensure_lite_favorites_table, _check_and_record_price_alert, resolve_stock, lite_quant_engine)  # noqa: F401,E501
    raw_query = (payload.symbol or payload.stock_code or payload.stock_name or "").strip()
    if not raw_query:
        return {"success": False, "data": None, "message": "请输入股票代码或股票名称", "code": 400}

    stock = await resolve_stock(raw_query, payload.market)
    if not stock and payload.stock_name:
        stock = await resolve_stock(payload.stock_name, payload.market)
    if not stock:
        return {"success": False, "data": None, "message": f"未找到匹配股票：{raw_query}", "code": 404}

    now = datetime.now(timezone.utc).isoformat()
    added_price = None
    try:
        quote = (await _realtime_quotes([stock["symbol"]])).get(stock["symbol"])
        added_price = _safe_number((quote or {}).get("price") or (quote or {}).get("close"))
    except Exception:
        added_price = None
    ensure_lite_favorites_table()
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO lite_favorites (
                username, stock_code, stock_name, market, tags_json, notes, added_price,
                alert_price_high, alert_price_low, added_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(username, stock_code) DO UPDATE SET
                stock_name = excluded.stock_name,
                market = excluded.market,
                tags_json = excluded.tags_json,
                notes = excluded.notes,
                alert_price_high = excluded.alert_price_high,
                alert_price_low = excluded.alert_price_low,
                updated_at = excluded.updated_at
            """,
            (
                user["username"],
                stock["symbol"],
                stock["name"],
                stock.get("market") or payload.market or "A股",
                json.dumps(payload.tags or [], ensure_ascii=False),
                payload.notes or "",
                added_price,
                payload.alert_price_high,
                payload.alert_price_low,
                now,
                now,
            ),
        )
        conn.commit()

    return {
        "success": True,
        "data": {"message": "添加成功", "symbol": stock["symbol"], "stock_code": stock["symbol"], "stock_name": stock["name"]},
        "message": "添加成功",
    }


@router.get("/api/favorites/check/{symbol}")
async def check_favorite(symbol: str, user: dict[str, Any] = Depends(get_current_lite_user)):
    from app.lite_main import (_realtime_quotes, _resolve_real_industry, _apply_realtime_quote, _safe_number, ensure_lite_favorites_table, _check_and_record_price_alert, resolve_stock, lite_quant_engine)  # noqa: F401,E501
    ensure_lite_favorites_table()
    with store.connect() as conn:
        row = conn.execute(
            "SELECT stock_code FROM lite_favorites WHERE username = ? AND stock_code = ?",
            (user["username"], symbol),
        ).fetchone()
    return {"success": True, "data": {"symbol": symbol, "stock_code": symbol, "is_favorite": bool(row)}, "message": "ok"}


@router.get("/api/favorites/tags")
async def favorite_tags():
    from app.lite_main import (_realtime_quotes, _resolve_real_industry, _apply_realtime_quote, _safe_number, ensure_lite_favorites_table, _check_and_record_price_alert, resolve_stock, lite_quant_engine)  # noqa: F401,E501
    return {"success": True, "data": [], "message": "ok"}


@router.get("/api/tags/")
async def tags():
    from app.lite_main import (_realtime_quotes, _resolve_real_industry, _apply_realtime_quote, _safe_number, ensure_lite_favorites_table, _check_and_record_price_alert, resolve_stock, lite_quant_engine)  # noqa: F401,E501
    return {"success": True, "data": [], "message": "ok"}


@router.put("/api/favorites/{symbol}")
async def update_favorite(symbol: str, payload: LiteFavoriteRequest, user: dict[str, Any] = Depends(get_current_lite_user)):
    from app.lite_main import (_realtime_quotes, _resolve_real_industry, _apply_realtime_quote, _safe_number, ensure_lite_favorites_table, _check_and_record_price_alert, resolve_stock, lite_quant_engine)  # noqa: F401,E501
    now = datetime.now(timezone.utc).isoformat()
    ensure_lite_favorites_table()
    with store.connect() as conn:
        conn.execute(
            """
            UPDATE lite_favorites
            SET tags_json = ?, notes = ?, alert_price_high = ?, alert_price_low = ?, updated_at = ?
            WHERE username = ? AND stock_code = ?
            """,
            (
                json.dumps(payload.tags or [], ensure_ascii=False),
                payload.notes or "",
                payload.alert_price_high,
                payload.alert_price_low,
                now,
                user["username"],
                symbol,
            ),
        )
        conn.commit()
    return {"success": True, "data": {"message": "保存成功", "symbol": symbol, "stock_code": symbol}, "message": "保存成功"}


@router.delete("/api/favorites/{symbol}")
async def remove_favorite(symbol: str, user: dict[str, Any] = Depends(get_current_lite_user)):
    from app.lite_main import (_realtime_quotes, _resolve_real_industry, _apply_realtime_quote, _safe_number, ensure_lite_favorites_table, _check_and_record_price_alert, resolve_stock, lite_quant_engine)  # noqa: F401,E501
    ensure_lite_favorites_table()
    with store.connect() as conn:
        conn.execute(
            "DELETE FROM lite_favorites WHERE username = ? AND stock_code = ?",
            (user["username"], symbol),
        )
        conn.commit()
    return {"success": True, "data": {"message": "移除成功", "symbol": symbol, "stock_code": symbol}, "message": "移除成功"}


@router.post("/api/favorites/sync-realtime")
async def favorites_sync_realtime(user: dict[str, Any] = Depends(get_current_lite_user)):
    from app.lite_main import (_realtime_quotes, _resolve_real_industry, _apply_realtime_quote, _safe_number, ensure_lite_favorites_table, _check_and_record_price_alert, resolve_stock, lite_quant_engine)  # noqa: F401,E501
    ensure_lite_favorites_table()
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT stock_code, stock_name, alert_price_high, alert_price_low FROM lite_favorites WHERE username = ? ORDER BY added_at DESC",
            (user["username"],),
        ).fetchall()
    symbols = [row["stock_code"] for row in rows]
    quotes = await _realtime_quotes(symbols)
    for row in rows:
        symbol = row["stock_code"]
        price = quotes.get(symbol, {}).get("price")
        if price:
            _check_and_record_price_alert(
                username=user["username"],
                symbol=symbol,
                stock_name=row["stock_name"],
                price=float(price),
                alert_high=row["alert_price_high"],
                alert_low=row["alert_price_low"],
            )
    return {
        "success": True,
        "data": {
            "total": len(symbols),
            "success_count": len(quotes),
            "failed_count": max(0, len(symbols) - len(quotes)),
            "symbols": list(quotes.keys()),
            "data_source": "akshare.stock_zh_a_spot_em",
            "updated_at": datetime.now().astimezone().strftime("%Y/%m/%d %H:%M:%S"),
            "message": "已刷新实时行情快照",
        },
        "message": "ok",
    }
