"""市场洞察路由：板块龙头、集合竞价、热点新闻、催化剂、事件流、市场情绪、
涨停分布、宏观条、行业热力图。

从 lite_main 拆出。新闻/事件底料在 app/core/news_events，行情与响应缓存在
app/core/market_data，都在模块顶部正常 import。路径不变（无 prefix）。

重板块只读缓存的硬约束在这里同样适用：limit-up / heatmap 这类全市场聚合由
app/core/board_refresh 后台保温，端点命中热缓存即返回。不要把端点改成同步现算
（涨停热点现算约 25s，必超时）。`_enrich_smart_pool_industries` 仍在 lite_main 的
智选池簇里，按需懒导入。
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from app.core.analysis_report import _risk_level
from app.core.market_data import (
    _apply_realtime_quote,
    _cache_get,
    _cache_set,
    _compute_hot_industries,
    _load_industry_map,
    _load_realtime_quotes_snapshot,
    _now_cn,
    _realtime_quotes,
    _run_data_task,
    lite_insights_cache,
)
from app.core.news_events import (
    EVENT_TYPE_LABELS,
    _build_a_share_sentiment,
    _event_relevance_score,
    _fetch_hot_rank_events,
    _is_actionable_hot_event,
    _is_secondary_hot_event,
    _query_news_events,
    _sparkline,
    _watch_symbols,
    ensure_recent_lite_news,
    refresh_lite_news_events,
)

router = APIRouter(tags=["insights"])


@router.get("/api/lite/sector-leaders")
async def lite_sector_leaders():
    """个股深研「按赛道浏览龙头股」：策划赛道 + 龙头实时行情，点击即进深研报告。"""
    from quantcore.quant.sector_leaders import SECTOR_LEADERS, all_leader_codes

    cache_key = "sector-leaders:v1"
    cached = _cache_get(cache_key, 30)
    if cached:
        return cached

    quotes = await _realtime_quotes(all_leader_codes(), allow_snapshot_fallback=True)
    sectors = []
    for sector in SECTOR_LEADERS:
        items = []
        for code, name in sector["leaders"]:
            code = str(code).zfill(6)
            q = quotes.get(code) or {}
            price = q.get("price")
            if price is None:
                price = q.get("close")
            pct = q.get("change_percent")
            if pct is None:
                pct = q.get("pct_chg")
            items.append({
                "code": code,
                "name": q.get("name") or name,
                "price": round(float(price), 2) if price is not None else None,
                "pct_chg": round(float(pct), 2) if pct is not None else None,
            })
        sectors.append({
            "key": sector["key"],
            "name": sector["name"],
            "en": sector["en"],
            "subtitle": sector["subtitle"],
            "items": items,
        })

    payload = {
        "success": True,
        "data": {
            "sectors": sectors,
            "updated_at": datetime.now().strftime("%Y/%m/%d %H:%M:%S"),
            "note": "赛道与龙头为人工策划，实时行情每 30 秒刷新；点击个股进入深研报告。",
        },
        "message": "ok",
    }
    _cache_set(cache_key, payload)
    return payload


async def _auction_with_live_prices(payload: dict[str, Any]) -> dict[str, Any]:
    """给冻结的竞价候选补一份「打开页面这一刻」的价格。

    名单和竞价口径的数字必须冻结（理由见下面的冻结逻辑），但卡片上只有 09:25 的竞价价时，
    用户没法判断现在还追不追得上。live_* 是每次请求现算的旁注，绝不写回冻结存档，
    也不参与排序和留痕。
    """
    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
    candidates = (data or {}).get("buy_candidates") or []
    if not data or not candidates:
        return payload
    try:
        snapshot = await asyncio.to_thread(_load_realtime_quotes_snapshot, 15)
    except Exception:  # noqa: BLE001 — 拿不到实时价就只显示竞价价，不影响名单
        return payload
    if not snapshot:
        return payload

    def _number(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    enriched = []
    for candidate in candidates:
        row = dict(candidate)
        quote = snapshot.get(str(row.get("code") or "").zfill(6)) or {}
        price = _number(quote.get("price") or quote.get("close"))
        if price > 0:
            row["live_price"] = round(price, 2)
            if quote.get("change_percent") is not None:
                row["live_pct"] = round(_number(quote.get("change_percent")), 2)
            auction_price = _number(row.get("price"))
            if auction_price > 0:
                row["change_since_auction"] = round((price / auction_price - 1) * 100, 2)
        enriched.append(row)
    return {
        **payload,
        "data": {
            **data,
            "buy_candidates": enriched,
            "live_price_as_of": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S"),
        },
    }


@router.get("/api/lite/call-auction")
async def lite_call_auction(
    window: int = 5,
    top_k: int = 10,
    open_min: float = 1.5,
    open_max_ratio: float = 0.6,
):
    """集合竞价板块：从全市场快照的今开/昨收推导竞价情绪、热门板块、买入推荐。

    可调参数：window 近段趋势窗口(交易日)、top_k 动态热门板块数、open_min 高开下限%、
    open_max_ratio 高开上限占板块涨停限的比例(自适应 10/20/30% 板)。
    """
    from quantcore.quant.call_auction import compute_call_auction
    from quantcore.quant.sector_leaders import SECTOR_LEADERS

    window = max(2, min(window, 30))
    top_k = max(3, min(top_k, 30))
    open_min = max(0.0, min(open_min, 10.0))
    open_max_ratio = max(0.2, min(open_max_ratio, 0.95))
    # v7：展示口径从「固定前 3 名」改为「全部强推荐档」，旧的冻结存档必须失效重算。
    cache_key = f"call-auction:v7:{window}:{top_k}:{open_min}:{open_max_ratio}"

    # —— 竞价结果日内冻结 ——
    # 症结：本页原来全天每 60 秒重算。09:26 之后快照里的量比/成交额是盘中/全日口径，
    # 拿它们给"竞价买入推荐"打分，名单会随盘面整天漂移——用户上午看到的推荐和收盘时
    # 的推荐不是同一批，且每次重算都在留痕，午后漂进来的票以盘中价混进竞价池的复盘
    # 统计。竞价是 09:25 一锤定音的事件，结果就该在窗口结束后冻结一整天。
    import json as _json
    from quantcore.quant.local_store import get_local_store

    now_cn = datetime.now(ZoneInfo("Asia/Shanghai"))
    today = now_cn.strftime("%Y-%m-%d")
    param_sig = f"v7:{window}:{top_k}:{open_min}:{open_max_ratio}"
    post_auction = now_cn.weekday() < 5 and (now_cn.hour * 60 + now_cn.minute) >= 9 * 60 + 26
    _FROZEN_STATE_KEY = "call_auction_frozen_v1"

    def _load_frozen() -> dict | None:
        try:
            raw = get_local_store().get_state(_FROZEN_STATE_KEY)
            if not raw:
                return None
            blob = _json.loads(raw)
            if blob.get("date") == today and blob.get("params") == param_sig:
                return blob.get("payload")
        except Exception:
            return None
        return None

    if post_auction:
        frozen = await asyncio.to_thread(_load_frozen)
        if frozen:
            return await _auction_with_live_prices(frozen)
    else:
        cached = _cache_get(cache_key, 60)
        if cached:
            return await _auction_with_live_prices(cached)

    snapshot = await asyncio.to_thread(_load_realtime_quotes_snapshot, 60)
    industry_map = await asyncio.to_thread(_load_industry_map)
    hot_industries = await asyncio.to_thread(_compute_hot_industries, industry_map, window=window, top_k=top_k)

    def _bad_forecast() -> set:
        try:
            from quantcore.quant.local_store import get_local_store
            return get_local_store().load_bad_forecast_symbols()
        except Exception:
            return set()

    bad_symbols = await asyncio.to_thread(_bad_forecast)
    # 留痕只在冻结时刻记一次（09:26 后的首次计算）。窗口内(09:15-09:25)名单还在
    # 逐分钟变化，窗口后重算已非竞价口径——两者都不该写复盘留痕。
    freeze_now = post_auction
    # 但「冻结」与「留痕」的门槛不同：显示可以任何时刻冻结，留痕必须紧贴竞价窗口。
    # 当天首次请求若发生在盘中（服务重启、当日新上线），名单是盘中口径，写进 picks_history
    # 会污染竞价池的 T+N 胜率——历史上 auction 池日均 26 条、实际候选仅 3-8 只，就是这么来的。
    record_now = freeze_now and (now_cn.hour * 60 + now_cn.minute) <= 9 * 60 + 40
    result = await asyncio.to_thread(
        compute_call_auction, snapshot, SECTOR_LEADERS,
        industry_map=industry_map, hot_industries=hot_industries, exclude_symbols=bad_symbols,
        open_min=open_min, open_max_ratio=open_max_ratio, record=record_now,
    )

    # 四形态：按需回溯拉东财盘前分时（09:15-09:25 逐分钟虚拟撮合价），给买入候选贴上盘口
    # 形态（抢筹/诱多/洗盘/分歧）。只算候选池这十几只——全市场形态计数对决策没有用处，
    # 而且要几千次请求。盘后照样能算，不依赖后端在竞价窗口在线。
    try:
        from quantcore.quant.auction_tape import classify_symbols, tape_summary
        codes = [str(c.get("code") or "") for c in (result.get("buy_candidates") or [])]
        patterns = await asyncio.to_thread(classify_symbols, codes)
        tape = tape_summary(patterns)
        result["auction_tape"] = {
            "available": tape["available"], "tracked": tape["tracked"],
            "resolved": tape["resolved"], "pattern_counts": tape["pattern_counts"],
            "note": "四形态来自当日 09:15-09:25 逐分钟虚拟撮合价，盘中盘后均可回溯。",
        }
        # 四形态是「盘口提示」而非硬筛：买入候选由强势板块+健康高开决定，形态只做每只的
        # 标注（诱多/分歧标黄提醒）。硬闸门会在多数高开于竞价小幅回落的日子把整张清单清空，
        # 反而丢掉主要输出，故只标注不剔除。
        for c in result.get("buy_candidates") or []:
            pat = patterns.get(str(c.get("code") or "").zfill(6))
            if pat and pat.get("pattern") != "insufficient":
                c["auction_pattern"] = pat
    except Exception:
        pass

    if freeze_now and result.get("available"):
        frozen_at = now_cn.strftime("%H:%M:%S")
        result["frozen_at"] = frozen_at
        # 文案报真实冻结时刻，不能硬写 09:26：冻结发生在「09:26 后的首次计算」，
        # 若当天首次请求发生在盘中（服务重启、当日新上线），名单实际是那一刻的口径，
        # 谎称 09:26 会让用户误以为拿到的是竞价原始名单。
        if frozen_at <= "09:30:00":
            head = f"本日竞价结果已于 {frozen_at} 冻结，全天展示同一名单（竞价 09:25 定价，盘中重算无意义）。"
        else:
            head = (f"本日名单于 {frozen_at} 首次生成并冻结，晚于竞价窗口，"
                    "其量比/成交额为盘中口径而非 09:25 竞价口径，仅供参考。")
        result["note"] = head + str(result.get("note") or "")
    payload = {
        "success": True,
        "data": {**result, "updated_at": datetime.now().strftime("%Y/%m/%d %H:%M:%S")},
        "message": "ok",
    }
    if result.get("available"):
        _cache_set(cache_key, payload)
        if freeze_now:
            def _save_frozen() -> None:
                try:
                    get_local_store().set_state(_FROZEN_STATE_KEY, _json.dumps(
                        {"date": today, "params": param_sig, "payload": payload}, ensure_ascii=False))
                except Exception:
                    pass
            await asyncio.to_thread(_save_frozen)
    return await _auction_with_live_prices(payload)


@router.get("/api/lite/hot-news")
async def lite_hot_news(limit: int = 30):
    cache_key = f"hot-news:sentiment-v2:{limit}"
    cached = _cache_get(cache_key, 300)
    if cached:
        return cached
    await ensure_recent_lite_news()
    raw_events = _query_news_events(limit=260)
    unique_events: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for event in raw_events:
        normalized_title = re.sub(r"\s+", "", str(event.get("title") or ""))
        if not normalized_title or normalized_title in seen_titles:
            continue
        seen_titles.add(normalized_title)
        unique_events.append(event)
    safe_limit = max(1, min(limit, 100))
    primary_events = sorted(
        [event for event in unique_events if _is_actionable_hot_event(event)],
        key=lambda event: (
            1 if event.get("source_type") in {"news", "sentiment", "hot_rank"} else 0,
            _event_relevance_score(event),
        ),
        reverse=True,
    )
    events = primary_events[:safe_limit]
    if len(events) < safe_limit:
        selected_ids = {event["id"] for event in events}
        secondary_events = sorted(
            [event for event in unique_events if event["id"] not in selected_ids and _is_secondary_hot_event(event)],
            key=lambda event: _event_relevance_score(event),
            reverse=True,
        )
        events.extend(secondary_events[: safe_limit - len(events)])
    if len(events) < safe_limit:
        selected_titles = {re.sub(r"\s+", "", str(event.get("title") or "")) for event in events}
        supplement_events = await asyncio.to_thread(_fetch_hot_rank_events, safe_limit - len(events))
        for event in supplement_events:
            normalized_title = re.sub(r"\s+", "", str(event.get("title") or ""))
            if normalized_title and normalized_title not in selected_titles:
                selected_titles.add(normalized_title)
                events.append(event)
            if len(events) >= safe_limit:
                break
    # 不再用合成模板补位：宁可少几条真实快讯，也不拿模拟数据冒充（与 source_note 承诺一致）。
    if events:
        items = []
        for rank, event in enumerate(events, start=1):
            items.append({
                "id": event["id"],
                "rank": rank,
                "title": event["title"],
                "sector": event.get("sector") or EVENT_TYPE_LABELS.get(event.get("event_type", ""), event.get("event_type", "")),
                "sentiment": event["sentiment"],
                "score": round(min(0.99, max(0.05, _event_relevance_score(event) / 7)), 2),
                "importance": event["importance"],
                "source": event["source"],
                "source_type": event["source_type"],
                "publish_time": event["publish_time"],
                "tags": event["tags"],
                "symbols": event["symbols"],
                "stock_names": event["stock_names"],
                "url": event["url"],
            })
    else:
        items = []
    category_counter: dict[str, int] = {}
    for item in items:
        for tag in item.get("tags", [])[:2]:
            category_counter[tag] = category_counter.get(tag, 0) + 1
    categories = [
        {"name": name, "count": count}
        for name, count in sorted(category_counter.items(), key=lambda pair: pair[1], reverse=True)[:12]
    ]
    data = {
        "summary": {
            "report_type": "当前榜单",
            "news_total": len(raw_events) if raw_events else 0,
            "hot_total": len(items),
            "generated_at": datetime.now().astimezone().strftime("%m-%d %H:%M"),
        },
        "tabs": [
            {"name": "热榜", "count": len(items)},
            {"name": "独立", "count": 5},
            {"name": "AI分析", "count": 3},
        ],
        "categories": categories or [
            {"name": "公告", "count": 0},
            {"name": "研报", "count": 0},
            {"name": "市场新闻", "count": 0},
        ],
        "sentiment_analysis": _build_a_share_sentiment([
            event for event in unique_events
            if _is_actionable_hot_event(event) or _is_secondary_hot_event(event)
        ][:160]),
        "items": items,
        "failed_sources": [],
        "source_note": "实时源：东方财富 7x24 快讯（主）、财新市场新闻；强事件公告/研报作补充。不可用源不会用模拟数据冒充。",
    }
    return _cache_set(cache_key, {"success": True, "data": data, "message": "ok"})


async def _enrich_catalysts_realtime(response: dict[str, Any]) -> dict[str, Any]:
    data = dict(response.get("data") or {})
    item_key = "items" if data.get("items") else "top_items"
    items = [dict(item) for item in data.get(item_key) or []]
    quotes = await _realtime_quotes([item.get("symbol") for item in items])
    for item in items:
        symbol = str(item.get("symbol") or "").zfill(6)
        quote = quotes.get(symbol)
        _apply_realtime_quote(item, quote)
        if quote and quote.get("price") is not None:
            item["price"] = quote["price"]
        if quote and quote.get("change_percent") is not None:
            item["change_percent"] = quote["change_percent"]
    if items:
        from app.lite_main import _enrich_smart_pool_industries  # lazy: 智选池簇仍在 lite_main

        # 催化剂是较快端点，行业增强 6s 封顶即可（一键推荐异步任务用默认 20s）。
        items = await _enrich_smart_pool_industries(items, timeout=6.0)
    data[item_key] = items
    enriched = dict(response)
    enriched["data"] = data
    return enriched


@router.get("/api/lite/catalysts")
async def lite_catalysts(window: str = "24h", threshold: float = 1.5, limit: int = 10):
    safe_limit = max(1, min(limit, 20))
    cache_key = f"catalysts:quant-v2:{window}:{threshold}:{safe_limit}"
    cached = _cache_get(cache_key, 300)
    if cached:
        return await _enrich_catalysts_realtime(cached)
    # 新闻预取 12s 封顶：股票池/新闻源首拉慢时不阻塞利好监控出数。
    try:
        await asyncio.wait_for(ensure_recent_lite_news(), timeout=12.0)
    except (asyncio.TimeoutError, Exception):
        pass
    events = _query_news_events(limit=200, sentiment="利好")
    grouped: dict[str, dict[str, Any]] = {}
    for event in events:
        symbols = event.get("symbols") or []
        names = event.get("stock_names") or []
        for idx, symbol in enumerate(symbols):
            if not symbol:
                continue
            current = grouped.setdefault(symbol, {
                "symbol": symbol,
                "name": names[idx] if idx < len(names) else symbol,
                "mentions": 0,
                "hot_score": 0.0,
                "sentiment": 0.0,
                "events": [],
            })
            current["mentions"] += 1
            current["hot_score"] += float(event.get("catalyst_score") or 0)
            current["sentiment"] += max(0, float(event.get("sentiment_score") or 0))
            current["events"].append(event)

    quant_items: list[dict[str, Any]] = []
    # 利好监控是事件驱动：不在请求路径同步跑全市场 smart_pool（本身 30-40s，且 wait_for
    # 无法终止后台线程、会持续抢占 GIL 拖死后端 —— 这正是端点 35s 挂死的根因）。
    # 量化选股的职责在「智能选股」页；这里走利好事件聚合 + 实时报价的快路径。
    quant_candidates: list[dict[str, Any]] = []
    for raw in quant_candidates:
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol") or raw.get("code") or "").strip().zfill(6)
        if not re.fullmatch(r"\d{6}", symbol):
            continue
        score = float(raw.get("score") or raw.get("quant_score") or 0)
        if score < 85:
            continue
        event_data = grouped.get(symbol, {"mentions": 0, "hot_score": 0.0, "sentiment": 0.0, "events": [], "name": raw.get("name") or symbol})
        pct = float(raw.get("pct_chg") or 0)
        factors = raw.get("factors") if isinstance(raw.get("factors"), dict) else {}
        events_for_symbol = list(event_data.get("events") or [])[:3]
        event_bonus = min(18.0, float(event_data.get("hot_score") or 0) * 1.8)
        realtime_bonus = max(0.0, min(15.0, pct * 1.2))
        quant_bonus = max(0.0, score - 80) * 0.55
        reasons = []
        if float(factors.get("trend") or 0) >= 85:
            reasons.append("量化趋势强")
        if float(factors.get("momentum") or 0) >= 85:
            reasons.append("短线动量强")
        if float(raw.get("amount") or 0) >= 1_000_000_000:
            reasons.append(f"成交额{float(raw.get('amount') or 0) / 100000000:.1f}亿")
        reasons.extend([EVENT_TYPE_LABELS.get(event.get("event_type", ""), event.get("event_type", "事件")) for event in events_for_symbol])
        if not reasons:
            reasons = [str(reason) for reason in (raw.get("reasons") or [])[:3]]
        quant_items.append({
            "symbol": symbol,
            "name": raw.get("name") or event_data.get("name") or symbol,
            "score": round(score, 1),
            "signal": raw.get("signal") or "watch",
            "mentions": int(event_data.get("mentions") or 0),
            "hot_score": round(event_bonus + realtime_bonus + quant_bonus, 2),
            "sentiment": round(float(event_data.get("sentiment") or 0) / max(1, int(event_data.get("mentions") or 0)), 2),
            "change_percent": round(pct, 2),
            "price": round(float(raw.get("close") or raw.get("price") or 0), 2),
            "risk_level": _risk_level(float((raw.get("risk") or {}).get("volatility") or 0), float((raw.get("risk") or {}).get("max_drawdown") or 0)),
            "sparkline": _sparkline(symbol, pct),
            "reasons": list(dict.fromkeys([reason for reason in reasons if reason]))[:5],
            "latest_titles": [event["title"] for event in events_for_symbol],
            "updated_at": _now_cn(),
        })

    if quant_items:
        items = sorted(quant_items, key=lambda item: item["hot_score"], reverse=True)[:safe_limit]
        filtered = [item for item in items if item["hot_score"] >= threshold]
        type_counter: dict[str, int] = {}
        source_counter: dict[str, int] = {}
        industry_counter: dict[str, int] = {}
        all_events = _query_news_events(limit=200)
        for event in all_events:
            label = EVENT_TYPE_LABELS.get(event.get("event_type", ""), event.get("event_type", "事件"))
            type_counter[label] = type_counter.get(label, 0) + 1
            source_counter[event["source"]] = source_counter.get(event["source"], 0) + 1
            for name in event.get("stock_names", [])[:1]:
                industry_counter[name] = industry_counter.get(name, 0) + 1
        top_events = sorted(
            [event for event in all_events if _is_actionable_hot_event(event)],
            key=lambda event: _event_relevance_score(event),
            reverse=True,
        )[:6]
        data = {
            "window": window,
            "threshold": threshold,
            "updated_at": datetime.now().astimezone().strftime("%Y/%m/%d %H:%M:%S"),
            "top_items": items,
            "realtime_picks": filtered,
            "categories": {
                "事件": [{"name": key, "count": value} for key, value in sorted(type_counter.items(), key=lambda pair: pair[1], reverse=True)[:8]],
                "标的": [{"name": key, "count": value} for key, value in sorted(industry_counter.items(), key=lambda pair: pair[1], reverse=True)[:8]],
                "来源": [{"name": key, "count": value} for key, value in sorted(source_counter.items(), key=lambda pair: pair[1], reverse=True)[:8]],
            },
            "news_feed": [{
                "id": event["id"],
                "title": event["title"],
                "sector": EVENT_TYPE_LABELS.get(event.get("event_type", ""), event.get("event_type", "")),
                "sentiment": event["sentiment"],
                "score": round(min(0.99, max(0.05, _event_relevance_score(event) / 7)), 2),
                "importance": event["importance"],
                "source": event["source"],
                "publish_time": event["publish_time"],
                "tags": event["tags"],
                "url": event["url"],
            } for event in top_events],
            "source_note": "催化剂监控优先使用量化中心同源智能推荐股票池，再叠加真实公告、研报和新闻事件权重。",
        }
        response = {"success": True, "data": data, "message": "ok"}
        _cache_set(cache_key, response)
        return await _enrich_catalysts_realtime(response)

    # 事件驱动快路径：不调用逐股 analyze（本地数据稀疏时会退化到慢速联网取数、阻塞事件循环）。
    # 价格/涨跌幅统一由下方 _enrich_catalysts_realtime 用腾讯批量报价补齐。
    top_grouped = sorted(grouped.items(), key=lambda kv: kv[1].get("hot_score", 0), reverse=True)[: safe_limit * 2]
    items = []
    for symbol, data in top_grouped:
        events_for_symbol = data["events"][:3]
        mentions = int(data["mentions"])
        hot_score = round(data["hot_score"], 2)
        items.append({
            "symbol": symbol,
            "name": data["name"],
            "score": round(min(95.0, 60 + hot_score * 4), 1),
            "signal": "watch",
            "mentions": mentions,
            "hot_score": hot_score,
            "sentiment": round(data["sentiment"] / max(1, mentions), 2),
            "change_percent": 0.0,
            "price": 0.0,
            "risk_level": "中",
            "sparkline": _sparkline(symbol, 0.0),
            "reasons": [EVENT_TYPE_LABELS.get(event.get("event_type", ""), event.get("event_type", "事件")) for event in events_for_symbol] or ["真实事件进入观察池"],
            "latest_titles": [event["title"] for event in events_for_symbol],
            "updated_at": _now_cn(),
        })
    if not items:
        # 无利好事件时退到自选观察池（零 analyze，价格由实时报价补齐）。
        items = [{
            "symbol": w["symbol"], "name": w["name"], "score": 60.0, "signal": "watch",
            "mentions": 0, "hot_score": round(max(threshold, 1.5), 2), "sentiment": 0.0,
            "change_percent": 0.0, "price": 0.0, "risk_level": "中",
            "sparkline": _sparkline(w["symbol"], 0.0), "reasons": ["自选观察池"],
            "latest_titles": [], "updated_at": _now_cn(),
        } for w in _watch_symbols()][:safe_limit]
    items = sorted(items, key=lambda item: item["hot_score"], reverse=True)[:safe_limit]
    filtered = [item for item in items if item["hot_score"] >= threshold]
    type_counter: dict[str, int] = {}
    source_counter: dict[str, int] = {}
    industry_counter: dict[str, int] = {}
    all_events_fallback = _query_news_events(limit=200)
    for event in all_events_fallback:
        label = EVENT_TYPE_LABELS.get(event.get("event_type", ""), event.get("event_type", "事件"))
        type_counter[label] = type_counter.get(label, 0) + 1
        source_counter[event["source"]] = source_counter.get(event["source"], 0) + 1
        for name in event.get("stock_names", [])[:1]:
            industry_counter[name] = industry_counter.get(name, 0) + 1
    top_events = all_events_fallback[:6]
    data = {
        "window": window,
        "threshold": threshold,
        "updated_at": datetime.now().astimezone().strftime("%Y/%m/%d %H:%M:%S"),
        "top_items": items,
        "realtime_picks": filtered,
        "categories": {
            "事件": [{"name": key, "count": value} for key, value in sorted(type_counter.items(), key=lambda pair: pair[1], reverse=True)[:8]],
            "标的": [{"name": key, "count": value} for key, value in sorted(industry_counter.items(), key=lambda pair: pair[1], reverse=True)[:8]],
            "来源": [{"name": key, "count": value} for key, value in sorted(source_counter.items(), key=lambda pair: pair[1], reverse=True)[:8]],
        },
        "news_feed": [{
            "id": event["id"],
            "title": event["title"],
            "sector": event["event_type"],
            "sentiment": event["sentiment"],
            "score": abs(event["sentiment_score"]),
            "importance": event["importance"],
            "source": event["source"],
            "publish_time": event["publish_time"],
            "tags": event["tags"],
            "url": event["url"],
        } for event in top_events],
        "source_note": "利好强度来自真实事件表：公告、研报、市场新闻，经事件分类和股票映射后聚合。",
    }
    response = {"success": True, "data": data, "message": "ok"}
    _cache_set(cache_key, response)
    return await _enrich_catalysts_realtime(response)


@router.post("/api/lite/news/refresh")
async def lite_news_refresh(limit: int = 180):
    result = await refresh_lite_news_events(limit=max(20, min(limit, 300)))
    return {"success": True, "data": result, "message": "真实新闻/公告/研报源刷新完成"}


@router.get("/api/lite/events")
async def lite_events(limit: int = 100, source_type: str | None = None, sentiment: str | None = None):
    await ensure_recent_lite_news()
    return {
        "success": True,
        "data": {
            "items": _query_news_events(limit=max(1, min(limit, 300)), source_type=source_type, sentiment=sentiment),
        },
        "message": "ok",
    }


@router.get("/api/lite/market-sentiment")
async def lite_market_sentiment(start: str | None = None, end: str | None = None):
    """大盘情绪/市场宽度复盘（基于本地日线，无需 LLM）。"""
    from quantcore.quant.market_sentiment import compute_market_sentiment
    today = datetime.now().strftime("%Y-%m-%d")
    e = end or today
    s = start or (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    cache_key = f"sentiment:{s}:{e}"
    cached = lite_insights_cache.get(cache_key)
    if cached:
        ts, payload = cached
        if (datetime.now(timezone.utc) - ts).total_seconds() < 300:  # 5-min cache
            return {"success": True, "data": payload}
    try:
        realtime_quotes = {}
        if e >= today:
            try:
                realtime_quotes = await _run_data_task(_load_realtime_quotes_snapshot, 60, timeout=8.0)
            except Exception:
                realtime_quotes = {}
        data = await _run_data_task(compute_market_sentiment, s, e, 24, realtime_quotes, timeout=25.0)
        lite_insights_cache[cache_key] = (datetime.now(timezone.utc), data)
        return {"success": True, "data": data}
    except asyncio.TimeoutError:
        if cached:
            _, payload = cached
            data = dict(payload) if isinstance(payload, dict) else payload
            if isinstance(data, dict):
                data["stale"] = True
                data["message"] = "市场情绪计算超时，已返回最近缓存结果。"
            return {"success": True, "data": data, "message": "市场情绪计算超时，已使用缓存"}
        return {"success": False, "data": None, "message": "市场情绪计算超时，请稍后重试"}
    except Exception as exc:
        return {"success": False, "data": None, "message": str(exc)}


@router.get("/api/lite/limit-up")
async def lite_limit_up_distribution(date: str | None = None):
    """涨停热点分布：单日连板梯队 × 概念板块矩阵（基于本地日线）。"""
    from quantcore.quant.limit_up import compute_limit_up_distribution
    from quantcore.quant.limit_up_taxonomy import limit_up_taxonomy_version
    target = date or datetime.now().strftime("%Y-%m-%d")
    cache_key = f"limit_up:{limit_up_taxonomy_version()}:{target}"
    cached = lite_insights_cache.get(cache_key)
    if cached:
        ts, payload = cached
        if (datetime.now(timezone.utc) - ts).total_seconds() < 600:  # 10-min cache
            return {"success": True, "data": payload}
    try:
        realtime_quotes = {}
        today = datetime.now().strftime("%Y-%m-%d")
        if target >= today:
            try:
                realtime_quotes = await _run_data_task(_load_realtime_quotes_snapshot, 30, timeout=8.0)
            except Exception:
                realtime_quotes = {}
        data = await _run_data_task(compute_limit_up_distribution, target, realtime_quotes, timeout=25.0)
        lite_insights_cache[cache_key] = (datetime.now(timezone.utc), data)
        return {"success": True, "data": data}
    except asyncio.TimeoutError:
        if cached:
            _, payload = cached
            data = dict(payload) if isinstance(payload, dict) else payload
            if isinstance(data, dict):
                data["stale"] = True
                data["message"] = "涨停热点计算超时，已返回最近缓存结果。"
            return {"success": True, "data": data, "message": "涨停热点计算超时，已使用缓存"}
        return {"success": False, "data": None, "message": "涨停热点计算超时，请稍后重试"}
    except Exception as exc:
        return {"success": False, "data": None, "message": str(exc)}


# ---- 每日盘报 + 宏观条 ----
@router.get("/api/lite/macro-bar")
async def lite_macro_bar():
    """顶部宏观条：三大指数 + 全市场涨跌家数/两市成交额。60s 缓存。"""
    cached = _cache_get("macro-bar", 60)
    if cached:
        return cached
    # 上游持续故障时全站客户端每 60s 都会打满 10s+8s 超时的重试，挤占数据线程池；
    # 失败结果也短缓存 20s 做退避。
    failed = _cache_get("macro-bar:fail", 20)
    if failed:
        return failed
    from quantcore.quant.macro_bar import fetch_index_quotes
    try:
        indices = await _run_data_task(fetch_index_quotes, timeout=10.0)
    except Exception:
        indices = []
    breadth: dict[str, Any] | None = None
    try:
        snapshot = await _run_data_task(_load_realtime_quotes_snapshot, 60, timeout=8.0)
        if snapshot:
            ups = downs = flats = 0
            total_amount = 0.0
            for q in snapshot.values():
                pct = q.get("change_percent")
                if pct is None:
                    continue
                if pct > 0:
                    ups += 1
                elif pct < 0:
                    downs += 1
                else:
                    flats += 1
                total_amount += float(q.get("amount") or 0)
            breadth = {"up": ups, "down": downs, "flat": flats,
                       "amount_yi": round(total_amount / 1e8)}
    except Exception:
        breadth = None
    payload = {"success": True, "data": {
        "indices": indices, "breadth": breadth,
        "updated_at": datetime.now().strftime("%H:%M:%S"),
    }}
    if indices or breadth:
        _cache_set("macro-bar", payload)
    else:
        _cache_set("macro-bar:fail", payload)
    return payload


def _load_period_returns() -> dict[str, dict[str, float]]:
    """每只股票的 5 日 / 20 日涨跌幅%，供热力图多周期着色。

    复用 recent_returns（45 天窗口 + amount>0 的真实 bar），不另写全表扫描。
    刻意不做「年初至今」那一档：次新股没有基准会大片留白，而本产品的持有窗口是
    T+1~T+5，年初至今回答不了「现在能不能买」。
    """
    from quantcore.quant.local_store import get_local_store

    store = get_local_store()
    out: dict[str, dict[str, float]] = {}
    for window, key in ((5, "pct5"), (20, "pct20")):
        for symbol, pct in (store.recent_returns(window) or {}).items():
            out.setdefault(str(symbol), {})[key] = pct
    return out


def _prev_session_amount_yi(industry: str = "") -> float:
    """上一个交易日的全日成交额（亿），作为「较昨日」的分母。

    用本地日线的**倒数第二个**真实交易日：最后一个可能就是今天、且盘中还没走完，
    拿它当昨日基准会得出恒等于 1 的比值。
    """
    from quantcore.quant.local_store import get_local_store

    def num(value) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    store = get_local_store()
    conn = store._conn()
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM daily_kline WHERE amount > 0 ORDER BY date DESC LIMIT 2")]
    if len(dates) < 2:
        return 0.0
    prev = dates[1]
    if industry:
        syms = {s for s, name in _load_industry_map().items() if name == industry}
        if not syms:
            return 0.0
        total = sum(num(r[1]) for r in conn.execute(
            "SELECT symbol, amount FROM daily_kline WHERE date = ? AND amount > 0", (prev,))
            if str(r[0]) in syms)
    else:
        row = conn.execute(
            "SELECT SUM(amount) FROM daily_kline WHERE date = ? AND amount > 0", (prev,)).fetchone()
        total = num(row[0]) if row else 0.0
    return round(total / 1e8, 2)


@router.get("/api/lite/breadth")
async def lite_breadth():
    """市场宽度：涨跌家数、站上均线占比、20 日新高新低、涨跌停。

    这页是大盘温度标签的证据面：标签答「今天冷不冷」，这里答「凭什么」以及「冷了几天、
    在变好还是变坏」。温度分与 regime 模块同源，同一天不允许两处口径打架。

    与轮动图同样按最新 bar 日期缓存、由 board_refresh 预热：全市场逐日聚合约 17 秒，
    不能改成请求时同步现算。
    """
    from quantcore.quant.breadth import build_breadth
    from quantcore.quant.freshness import mark
    from quantcore.quant.local_store import get_local_store

    store = get_local_store()
    try:
        latest_bar = store.latest_real_bar_date() or ""
    except Exception:  # noqa: BLE001  取不到只影响新鲜度判定，不该阻断
        latest_bar = ""
    cache_key = f"breadth:{latest_bar}"
    cached = _cache_get(cache_key, 43200)
    if cached:
        return cached

    data = await _run_data_task(build_breadth, store, timeout=60.0) or {}
    if not data:
        return {"series": [], "ready": False,
                "message": "日线样本不足，宽度曲线暂不可用"}
    data["ready"] = True
    data["freshness"] = mark(str(data.get("as_of") or ""), latest_bar=latest_bar)
    _cache_set(cache_key, data)
    return data


@router.get("/api/lite/sector-rotation")
async def lite_sector_rotation():
    """板块相对轮动（RRG）：相对强度 × 强度变化，四象限 + 最近八周轨迹。

    与热力图的分工：热力图答「今天谁涨」，本端点答「资金这几周在往哪挪」。后者才是
    审计里唯一站得住的那个量（20 日板块动量），mom20_pct ≥ 0.8 即 sector_hot 命中档。

    整套坐标只依赖日线，一个交易日只会变一次，所以缓存按最新 bar 日期作键、TTL 给足
    半天，由 board_refresh 每日预热一次。计算本身约 14 秒（读 60 万行日线 + 逐日
    横截面标准化），**不能**改成用户请求时同步现算 —— 这是既有的「端点别同步现算」
    约束，涨停热点当年就是栽在这上面。
    """
    from quantcore.quant.freshness import mark
    from quantcore.quant.local_store import get_local_store
    from quantcore.quant.rotation import build_rotation

    store = get_local_store()
    try:
        as_of = store.latest_real_bar_date() or ""
    except Exception:  # noqa: BLE001  取不到日期只影响缓存分代，不该阻断
        as_of = ""
    cache_key = f"sector-rotation:{as_of}"
    cached = _cache_get(cache_key, 43200)
    if cached:
        return cached

    data = await _run_data_task(build_rotation, store, timeout=60.0) or {}
    if not data:
        # 日线不足（新库/同步中）时如实说明，不要返回空图让前端画一张空白坐标系
        return {"as_of": as_of, "items": [], "ready": False,
                "message": "日线样本不足 115 个交易日，轮动图暂不可用"}
    data["ready"] = True
    data["freshness"] = mark(str(data.get("as_of") or ""), latest_bar=as_of)
    _cache_set(cache_key, data)
    return data


@router.get("/api/lite/heatmap")
async def lite_heatmap(level: str = "industry", industry: str = "", nested: bool = False):
    """行业/个股热力图：面积=A股市值（亿，成交额兜底），颜色=当日涨跌幅。60s 缓存。

    市值口径见 quantcore/quant/heatmap.py：只计 A 股股本，A+H 公司会被低估。

    nested=1 时每个行业块带上成分股（两层 treemap），并附市场概览。默认关着：
    盘面总览页只要 128 行的行业聚合，给它塞 5000 只个股是白白多传三倍。

    快照不可用（收盘后/断网）时退回本地日线最新 bar（同收盘快照教训：不读未同步的当日）。
    """
    from quantcore.quant.heatmap import build_heatmap_industry, build_heatmap_stocks

    if level not in ("industry", "stock"):
        raise HTTPException(status_code=400, detail="level 必须是 industry/stock")
    if level == "stock" and not industry.strip():
        raise HTTPException(status_code=400, detail="level=stock 需要 industry 参数")
    cache_key = f"heatmap:{level}:{industry}:{int(nested)}"
    cached = _cache_get(cache_key, 60)
    if cached:
        return cached

    industry_map = await _run_data_task(_load_industry_map, timeout=15.0)
    snapshot: dict[str, dict[str, Any]] = {}
    source = "realtime"
    try:
        snapshot = await _run_data_task(_load_realtime_quotes_snapshot, 60, timeout=8.0) or {}
    except Exception:
        snapshot = {}
    if not snapshot:
        # 日线兜底：伪快照（无市值 -> 面积用成交额）
        source = "daily-kline"
        from quantcore.quant.local_store import get_local_store

        def _fallback() -> dict[str, dict[str, Any]]:
            store = get_local_store()
            names = {str(m.get("symbol")): str(m.get("name") or "") for m in store.load_meta()}
            return {sym: {"name": names.get(sym) or sym, "pct_chg": st["pct"], "amount": st["amount"]}
                    for sym, st in store.latest_daily_stats().items()}

        try:
            snapshot = await _run_data_task(_fallback, timeout=20.0)
        except Exception:
            snapshot = {}

    coverage: dict[str, Any] = {}
    # 这两个聚合要遍历 5000+ 只股票，是纯 Python 循环，不放线程里会把事件循环焊死 ——
    # 2026-08-20 实测 /api/health 最慢被拖到 13.4s，逼近 watchdog 的容忍上限，
    # 再慢一点就会被判定为宕机而触发一次没必要的重启。取数早就走 _run_data_task 了，
    # 唯独这一步是在循环里现算，属于「端点别同步现算」这条既有约束的漏网之处。
    # 多周期涨跌（5日/20日）：本地日线现算，与实时快照的当日涨跌一起下发，
    # 前端切周期只换颜色不重新取数。取不到就退化成只有当日一档，不阻断热力图。
    returns = await _run_data_task(_load_period_returns, timeout=25.0) or {}
    overview: dict[str, Any] = {}
    if level == "industry":
        from quantcore.quant.heatmap import (
            build_heatmap_nested, heatmap_coverage, heatmap_overview,
        )
        builder = build_heatmap_nested if nested else build_heatmap_industry
        items = await _run_data_task(builder, snapshot, industry_map, returns, timeout=30.0)
        coverage = await _run_data_task(heatmap_coverage, snapshot, industry_map, timeout=15.0)
        if nested:
            prev_yi = await _run_data_task(_prev_session_amount_yi, timeout=15.0) or 0.0
            overview = await _run_data_task(
                heatmap_overview, snapshot, industry_map, "", prev_yi, timeout=15.0)
    else:
        from quantcore.quant.heatmap import heatmap_overview
        items = await _run_data_task(
            build_heatmap_stocks, snapshot, industry_map, industry.strip(), returns, timeout=25.0)
        prev_yi = await _run_data_task(_prev_session_amount_yi, industry.strip(), timeout=15.0) or 0.0
        overview = await _run_data_task(
            heatmap_overview, snapshot, industry_map, industry.strip(), prev_yi, timeout=15.0)
    payload = {
        "success": True,
        "data": {"level": level, "industry": industry.strip() or None, "items": items,
                 "source": source, "mapped": len(industry_map), "coverage": coverage,
                 "periods_ready": bool(returns), "nested": bool(nested), "overview": overview,
                 "updated_at": datetime.now().astimezone().strftime("%Y/%m/%d %H:%M:%S")},
    }
    if items:
        _cache_set(cache_key, payload)
    return payload
