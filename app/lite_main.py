from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

# Load .env (JWT_SECRET, MONGO_URI, admin creds) before auth import reads them,
# so logins stay valid across restarts when JWT_SECRET is set.
load_dotenv()

# Hard ceiling on blocking network I/O (akshare/requests without an explicit
# timeout). Without this, a hung upstream call holds its worker thread forever;
# over a long-running process these accumulate and exhaust the threadpool, which
# then blocks ALL endpoints — even local-data ones — and the whole app appears to
# "stop loading". This is a per-read idle timeout: uvicorn's asyncio sockets are
# non-blocking (unaffected), the LLM client sets its own httpx timeout
# (unaffected), and slow-but-progressing responses keep resetting the timer.
import socket as _socket
_socket.setdefaulttimeout(30)

from app.lite_auth import get_current_lite_user, router as lite_auth_router, store
from app.lite_billing import router as billing_router
from app.lite_admin import router as admin_router
from app.lite_notifications import notification_store
from app.core.scan_gate import run_scan
from app.core.engine import get_stock_pool_items, lite_quant_engine
from app.core.analysis_report import _risk_level
from app.core.market_data import (  # 行情/缓存底座：re-export 保持旧的懒导入与测试 patch 目标有效
    _apply_realtime_quote,
    _cache_get,
    _cache_set,
    _compute_hot_industries,
    _load_industry_map,
    _load_realtime_quotes_snapshot,
    _now_cn,
    _persistent_cache_delete_prefix,
    _persistent_cache_get,
    _persistent_cache_set,
    _realtime_quotes,
    _run_data_task,
    _safe_number,
    lite_insights_cache,
)
from app.core.schema import ensure_lite_news_table, init_all as _init_lite_schema
from app.routers.quant import router as quant_router
from quantcore.quant.sync_service import get_sync_service
from quantcore.trading import EasyTraderBridge


# 公网部署（LYNX_PUBLIC=true）：关掉 /docs 与 openapi.json——邀请制站点没必要把完整
# API 面摆给未登录访客；本地开发仍保留，便于调试。
# 变量名带 LYNX_ 前缀：Windows 自带一个系统级 PUBLIC=C:\Users\Public，而 load_dotenv
# 默认不覆盖已存在的环境变量，用 PUBLIC 会被系统变量吃掉、开关永远不生效。
LYNX_PUBLIC = os.getenv("LYNX_PUBLIC", "false").lower() in ("1", "true", "yes")

app = FastAPI(
    title="AStockPick",
    version="0.1.0",
    description="Local SaaS Lite runtime with SQLite auth and protected quant APIs.",
    docs_url=None if LYNX_PUBLIC else "/docs",
    redoc_url=None if LYNX_PUBLIC else "/redoc",
    openapi_url=None if LYNX_PUBLIC else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:5173", "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response

app.include_router(lite_auth_router)
app.include_router(quant_router, dependencies=[Depends(get_current_lite_user)])
app.include_router(billing_router)
app.include_router(admin_router)
from app.routers.notifications import router as notifications_router  # noqa: E402
from app.routers.config import router as config_router  # noqa: E402
from app.routers.paper import router as paper_router  # noqa: E402
from app.routers.favorites import router as favorites_router  # noqa: E402
from app.routers.reports import router as reports_router  # noqa: E402
from app.routers.analysis import router as analysis_router  # noqa: E402
app.include_router(notifications_router)
app.include_router(config_router)
app.include_router(paper_router)
app.include_router(favorites_router)
app.include_router(reports_router)
app.include_router(analysis_router)

# ---- 每日全市场 AI 因子模型刷新（收盘 + 数据同步后入缓存）----
_ml_factor_scheduler: "AsyncIOScheduler | None" = None


async def _refresh_full_market_factor() -> None:
    """后台重算全市场因子模型并写入缓存；offload 到线程，避免阻塞事件循环。"""
    from quantcore.quant.ml.service import run_ml_factor
    try:
        # (universe_limit=0 全市场, horizon=5, k=50, mode, neutralize, retrain_every, min_rows, force)
        await asyncio.to_thread(run_ml_factor, 0, 5, 50, "rolling", True, 20, 250, True)
    except Exception as exc:  # noqa: BLE001
        import warnings
        warnings.warn(f"ML factor daily refresh failed: {exc}", RuntimeWarning, stacklevel=1)


@app.on_event("startup")
async def _start_ml_factor_scheduler() -> None:
    """启动时注册每日全市场因子模型刷新任务（可用环境变量关闭/改时间）。"""
    global _ml_factor_scheduler
    if os.getenv("ML_FACTOR_REFRESH_ENABLED", "true").lower() in ("0", "false", "no"):
        return
    cron = os.getenv("ML_FACTOR_REFRESH_CRON", "0 18 * * 1-5")  # 工作日 18:00，收盘+K线同步之后
    tz = os.getenv("ML_FACTOR_REFRESH_TZ", "Asia/Shanghai")
    _ml_factor_scheduler = AsyncIOScheduler(timezone=tz)
    _ml_factor_scheduler.add_job(
        _refresh_full_market_factor,
        CronTrigger.from_crontab(cron, timezone=tz),
        id="ml_factor_full_market_daily",
        name="全市场AI因子模型每日刷新",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # serenity 事件扫描：每工作日 9:30 / 13:30 刷新一次入缓存
    async def _refresh_serenity_events() -> None:
        from quantcore.quant.serenity_service import run_events_sync
        try:
            await asyncio.to_thread(run_events_sync, True, 30)
        except Exception as exc:  # noqa: BLE001
            import warnings
            warnings.warn(f"serenity daily refresh failed: {exc}", RuntimeWarning, stacklevel=1)

    _ml_factor_scheduler.add_job(
        _refresh_serenity_events,
        CronTrigger.from_crontab(os.getenv("SERENITY_REFRESH_CRON", "30 9,13 * * 1-5"), timezone=tz),
        id="serenity_events_daily",
        name="serenity事件扫描刷新",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    # 回放自愈：进程被杀会留下 status='running' 的僵尸 run；每 10 分钟检查并自动续跑
    # （replay_scan 按 symbol 断点缓存，续跑只做增量；运行中/无僵尸时为 no-op）。
    async def _job_replay_resume() -> None:
        try:
            from quantcore.quant.local_store import get_local_store
            from quantcore.quant.replay import replay_status, start_replay_async
            if replay_status().get("running"):
                return
            row = get_local_store()._conn().execute(
                "SELECT params_json, created_at FROM replay_runs WHERE status='running' "
                "ORDER BY created_at DESC LIMIT 1").fetchone()
            if not row:
                return
            params = json.loads(row[0] or "{}")
            # 续跑必须沿用原 run 的会话轴锚定日，否则 since/cutoff 随今天漂移，
            # param_key 变化会作废全部 replay_scan 断点（老 run 无 anchor 时退回建单日）
            anchor = params.get("anchor") or str(row[1] or "")[:10] or None
            start_replay_async(months=int(params.get("months", 12)),
                               step=int(params.get("step", 5)),
                               top_n=int(params.get("top_n", 20)),
                               anchor=anchor)
        except Exception as exc:  # noqa: BLE001
            import warnings
            warnings.warn(f"replay resume failed: {exc}", RuntimeWarning, stacklevel=1)

    _ml_factor_scheduler.add_job(
        _job_replay_resume, "interval", minutes=10,
        id="replay_resume", name="历史回放断点自愈",
        replace_existing=True, misfire_grace_time=300,
    )

    # 信号统计预热：收盘结算后重算各池 signal-stats 缓存（冷算约 20s/池），
    # 用户点开理由卡「历史表现」时直接命中缓存秒开。
    async def _job_signal_stats_preheat() -> None:
        if not await _is_trading_day_now():
            return
        def _preheat() -> None:
            from quantcore.quant.local_store import get_local_store
            store = get_local_store()
            # 复盘页三个时间窗（冷算 20-110s，不预热则页面首开必超时）
            for days in (7, 30, 90):
                try:
                    store.evaluate_picks(days=days, refresh=True)
                except Exception:
                    continue
            for pool in ("smart", "pattern", "strength", "swing", "auction"):
                try:
                    store.signal_stats(pool, refresh=True)
                except Exception:
                    continue
        await asyncio.to_thread(_preheat)

    _ml_factor_scheduler.add_job(
        _job_signal_stats_preheat,
        CronTrigger.from_crontab(os.getenv("SIGNAL_STATS_CRON", "55 15 * * 1-5"), timezone=tz),
        id="signal_stats_preheat", name="信号统计缓存预热",
        replace_existing=True, misfire_grace_time=3600,
    )

    _ml_factor_scheduler.start()


@app.on_event("startup")
async def _start_board_refresher() -> None:
    """交易时段后台保温各重板块缓存，让首页秒开全貌、各页秒读不超时。"""
    from app.core import board_refresh
    board_refresh.start()


lite_trader_bridge = EasyTraderBridge()
lite_smart_pool_tasks: dict[str, dict[str, Any]] = {}
lite_industry_cache: dict[str, tuple[datetime, str]] = {}
lite_price_alerts: dict[str, dict] = {}  # key: "symbol:direction", value: alert record
EVENT_TYPE_LABELS = {
    "regulatory_risk": "风险",
    "earnings": "业绩",
    "order_contract": "订单合同",
    "capital_action": "资本动作",
    "ma_restructure": "并购重组",
    "research_rating": "研报评级",
    "policy_macro": "政策宏观",
    "announcement": "公告",
    "market_news": "市场新闻",
}


def _check_and_record_price_alert(
    username: str,
    symbol: str,
    stock_name: str,
    price: float,
    alert_high: float | None,
    alert_low: float | None,
) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now(timezone.utc).isoformat()
    if alert_high and price >= alert_high:
        key = f"{username}:{symbol}:high:{today}"
        if key not in lite_price_alerts:
            lite_price_alerts[key] = {
                "username": username, "symbol": symbol, "stock_name": stock_name,
                "direction": "high", "threshold": alert_high,
                "price": price, "triggered_at": now_str,
            }
            notification_store.notify_user(
                username,
                f"价格突破上限：{stock_name or symbol}",
                f"{stock_name or symbol} 当前价 {price:.2f}，已触发上限 {alert_high:.2f}。\n\n仅供研究跟踪，不构成投资建议。",
                type_="price_alert",
                payload={"symbol": symbol, "direction": "high", "price": price, "threshold": alert_high},
                dedupe_key=key,
                send_wechat=True,
            )
    if alert_low and price <= alert_low:
        key = f"{username}:{symbol}:low:{today}"
        if key not in lite_price_alerts:
            lite_price_alerts[key] = {
                "username": username, "symbol": symbol, "stock_name": stock_name,
                "direction": "low", "threshold": alert_low,
                "price": price, "triggered_at": now_str,
            }
            notification_store.notify_user(
                username,
                f"价格跌破下限：{stock_name or symbol}",
                f"{stock_name or symbol} 当前价 {price:.2f}，已触发下限 {alert_low:.2f}。\n\n仅供研究跟踪，不构成投资建议。",
                type_="price_alert",
                payload={"symbol": symbol, "direction": "low", "price": price, "threshold": alert_low},
                dedupe_key=key,
                send_wechat=True,
            )


_init_lite_schema()


@app.get("/", include_in_schema=False)
async def root():
    # 生产（前端已构建）：根路径直接给应用；否则退回 API banner，便于本地探活。
    # index.html 必须 no-cache：发版后若用户拿到缓存的旧壳子，它引用的哈希 JS 已不存在。
    index = Path(__file__).resolve().parent.parent / "frontend" / "dist" / "index.html"
    if index.is_file():
        return FileResponse(str(index), headers={"Cache-Control": "no-cache"})
    return {"name": "AStockPick", "status": "running"}


@app.get("/api/health")
async def health():
    return {"status": "healthy", "service": "saas-lite"}


def _stable_float(text: str, low: float, high: float) -> float:
    seed = sum((idx + 1) * ord(ch) for idx, ch in enumerate(text))
    ratio = (seed % 1000) / 1000
    return round(low + (high - low) * ratio, 2)


def _lite_news_items() -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc).astimezone()
    templates = [
        ("AI应用", "AI应用与算力产业链继续活跃，资金偏好向有业绩兑现能力的环节集中", "利好", ["AI应用", "算力", "机器人"]),
        ("半导体", "半导体设备与存储方向热度回升，关注国产替代与订单兑现节奏", "利好", ["半导体", "设备", "存储"]),
        ("电力", "电力和数据中心能耗主题升温，市场关注算力基础设施配套", "利好", ["电力", "数据中心", "算力"]),
        ("机器人", "机器人板块分化加大，资金更偏好具备量产订单和核心零部件优势的公司", "中性", ["机器人", "自动化"]),
        ("创新药", "创新药事件催化增多，短线波动放大，需区分临床进展和商业化兑现", "中性", ["创新药", "医药"]),
        ("低空经济", "低空经济政策预期反复，适合跟踪订单、牌照和地方试点进度", "中性", ["低空经济", "政策"]),
        ("有色金属", "黄金和铜相关资产受避险与通胀交易影响，趋势延续性取决于外盘价格", "中性", ["黄金", "铜", "资源"]),
        ("消费电子", "端侧AI带动消费电子关注度修复，但持续性仍依赖新品周期", "中性", ["消费电子", "端侧AI"]),
        ("光通信", "CPO、光模块和交换机方向成交活跃，短线核心看高成交标的能否继续放量", "利好", ["CPO", "光模块", "通信"]),
        ("PCB", "高速铜连接与PCB题材延续强势，资金更偏好订单弹性和涨价传导清晰的公司", "利好", ["PCB", "高速铜连接"]),
        ("电力设备", "储能、电网设备和数据中心配电链条分化走强，重点观察放量突破后的承接", "中性", ["储能", "电网", "数据中心"]),
        ("军工", "低空、商业航天和军工电子方向轮动增强，适合结合成交额和板块联动筛选", "中性", ["军工", "低空经济", "商业航天"]),
        ("汽车零部件", "机器人执行器、智能驾驶和一体化压铸相关零部件热度抬升", "中性", ["汽车零部件", "机器人", "智能驾驶"]),
        ("券商", "市场成交额放大时券商弹性增强，但持续性取决于指数和量能共振", "中性", ["证券", "成交额"]),
        ("化工材料", "新材料、氟化工和电子化学品方向局部活跃，需关注价格和订单验证", "中性", ["新材料", "氟化工", "电子化学品"]),
        ("农业", "种业和养殖链短线异动增多，更多适合事件驱动跟踪", "中性", ["种业", "养殖"]),
        ("医药商业", "医药商业和创新药服务链局部修复，短线看政策预期和资金承接", "中性", ["医药", "创新药"]),
        ("家电", "出口链和消费刺激预期带动家电局部走强，但趋势强度需成交额确认", "中性", ["家电", "出口"]),
        ("传媒", "AI视频、游戏和IP方向反复活跃，适合等待放量突破后的确认信号", "中性", ["传媒", "AI视频", "游戏"]),
        ("煤炭", "高股息资源股表现偏防守，短线弹性弱于科技成长方向", "中性", ["煤炭", "高股息"]),
    ]
    items = []
    for idx, (sector, title, sentiment, tags) in enumerate(templates, start=1):
        score = _stable_float(title, 0.1, 0.95)
        items.append({
            "id": f"lite_news_{idx}",
            "rank": idx,
            "title": title,
            "content": title,
            "sector": sector,
            "sentiment": sentiment,
            "sentiment_score": score if sentiment == "利好" else 0,
            "score": score,
            "importance": "high" if score >= 0.72 else "medium" if score >= 0.35 else "low",
            "source": "SaaS Lite",
            "source_type": "news",
            "event_type": "market_news",
            "catalyst_score": max(1.0, score * 4),
            "symbols": [],
            "stock_names": [],
            "publish_time": (now - timedelta(minutes=idx * 11)).isoformat(timespec="seconds"),
            "tags": tags,
            "url": "",
        })
    return items


def _watch_symbols() -> list[dict[str, str]]:
    return [
        {"symbol": "300033", "name": "同花顺"},
        {"symbol": "300024", "name": "机器人"},
        {"symbol": "603986", "name": "兆易创新"},
        {"symbol": "603618", "name": "杭电股份"},
        {"symbol": "600941", "name": "中国移动"},
        {"symbol": "688256", "name": "寒武纪"},
        {"symbol": "600487", "name": "亨通光电"},
        {"symbol": "000988", "name": "华工科技"},
        {"symbol": "002407", "name": "多氟多"},
        {"symbol": "002281", "name": "光迅科技"},
    ]


def _sparkline(symbol: str, change_percent: float) -> list[float]:
    base = _stable_float(symbol, 20, 60)
    values = []
    for idx in range(16):
        step = _stable_float(f"{symbol}-{idx}", -2.5, 3.5)
        drift = change_percent * idx / 16
        base = max(1, base + step + drift)
        values.append(round(base, 2))
    return values


def _clean_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\u3000", " ").replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def _event_id(source: str, title: str, publish_time: str, url: str = "") -> str:
    raw = f"{source}|{title}|{publish_time}|{url}"
    import hashlib

    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()


def _classify_event(title: str, content: str = "", source_type: str = "news") -> dict[str, Any]:
    text = f"{title} {content}"
    rules = [
        ("regulatory_risk", "风险", ["立案", "处罚", "问询", "监管", "退市", "诉讼", "违约", "警示", "风险提示"]),
        ("earnings", "业绩", ["年报", "季报", "盈利", "利润", "营收", "预增", "预减", "扭亏", "亏损"]),
        ("order_contract", "订单合同", ["中标", "订单", "合同", "采购", "供货", "签订", "框架协议"]),
        ("capital_action", "资本动作", ["回购", "增持", "减持", "定增", "融资", "分红", "股权激励"]),
        ("ma_restructure", "并购重组", ["并购", "收购", "重组", "资产注入", "重大资产"]),
        ("research_rating", "研报评级", ["买入", "增持", "推荐", "评级", "目标价", "首次覆盖", "上调", "下调"]),
        ("policy_macro", "政策宏观", ["政策", "会议", "改革", "监管要求", "行业", "出口", "通胀", "利率"]),
    ]
    event_type = "market_news"
    event_label = "市场新闻"
    for key, label, keywords in rules:
        if any(word in text for word in keywords):
            event_type = key
            event_label = label
            break
    if source_type == "announcement" and event_type == "market_news":
        event_type = "announcement"
        event_label = "公告"
    if source_type == "research":
        event_type = "research_rating"
        event_label = "研报评级"

    positive_words = ["利好", "增长", "预增", "扭亏", "中标", "订单", "回购", "增持", "获批", "突破", "买入", "推荐", "上调", "创新高", "提振", "回暖", "企稳", "修复"]
    # 注意「风险」是裸词会误命中「风险偏好/化解风险」等利好语境，改用精确的负面措辞
    negative_words = ["利空", "下滑", "预减", "亏损", "减持", "处罚", "立案", "问询", "诉讼", "退市", "终止", "下调", "风险警示", "退市风险"]
    pos = sum(1 for word in positive_words if word in text)
    neg = sum(1 for word in negative_words if word in text)
    if pos > neg:
        sentiment = "利好"
        score = min(0.95, 0.55 + pos * 0.12 - neg * 0.08)
    elif neg > pos:
        sentiment = "利空"
        score = max(-0.95, -0.55 - neg * 0.12 + pos * 0.08)
    else:
        sentiment = "中性"
        score = 0.0

    high_words = ["重大", "首次", "核心", "突破", "中标", "预增", "处罚", "立案", "退市", "重组", "回购"]
    importance_base = sum(1 for word in high_words if word in text)
    importance = "high" if importance_base >= 2 or abs(score) >= 0.75 else "medium" if importance_base >= 1 or abs(score) >= 0.45 else "low"
    return {
        "event_type": event_type,
        "event_label": event_label,
        "sentiment": sentiment,
        "sentiment_score": round(score, 2),
        "importance": importance,
    }


def _map_symbols(title: str, content: str, explicit: list[dict[str, str]], stock_lookup: dict[str, str]) -> tuple[list[str], list[str]]:
    symbol_map: dict[str, str] = {}
    for item in explicit:
        symbol = str(item.get("symbol") or "").strip()
        name = str(item.get("name") or "").strip()
        if re.fullmatch(r"\d{6}", symbol):
            symbol_map[symbol] = name or stock_lookup.get(symbol, "")
    if symbol_map:
        symbols = list(symbol_map.keys())[:5]
        names = [symbol_map[symbol] or stock_lookup.get(symbol, symbol) for symbol in symbols]
        return symbols, names
    text = f"{title} {content}"
    for symbol, name in stock_lookup.items():
        if len(symbol_map) >= 5:
            break
        if symbol and symbol in text:
            symbol_map.setdefault(symbol, name)
        elif name and len(name) >= 2 and name in text:
            symbol_map.setdefault(symbol, name)
    symbols = list(symbol_map.keys())
    names = [symbol_map[symbol] or stock_lookup.get(symbol, symbol) for symbol in symbols]
    return symbols, names


def _build_event(
    title: str,
    content: str,
    source: str,
    source_type: str,
    publish_time: str,
    url: str = "",
    explicit_symbols: list[dict[str, str]] | None = None,
    stock_lookup: dict[str, str] | None = None,
    raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_title = _clean_text(title)
    clean_content = _clean_text(content)
    stock_lookup = stock_lookup or {}
    if source_type == "announcement" and clean_title:
        prefix = re.split(r"[:：]", clean_title, maxsplit=1)[0].strip()
        if prefix:
            for symbol, name in stock_lookup.items():
                if name == prefix:
                    explicit_symbols = [{"symbol": symbol, "name": name}]
                    break
    symbols, names = _map_symbols(clean_title, clean_content, explicit_symbols or [], stock_lookup)
    classification = _classify_event(clean_title, clean_content, source_type)
    importance_weight = {"high": 1.8, "medium": 1.25, "low": 0.8}[classification["importance"]]
    source_weight = {"announcement": 1.35, "research": 1.25, "sentiment": 1.15, "news": 1.0}.get(source_type, 1.0)
    symbol_weight = 1 + min(len(symbols), 3) * 0.18
    catalyst_score = round((abs(classification["sentiment_score"]) * 5 + 0.8) * importance_weight * source_weight * symbol_weight, 2)
    tags = [classification["event_label"], source]
    tags.extend(names[:3] or symbols[:3])
    return {
        "id": _event_id(source, clean_title, publish_time, url),
        "title": clean_title,
        "content": clean_content,
        "source": source,
        "source_type": source_type,
        "event_type": classification["event_type"],
        "event_label": classification["event_label"],
        "sentiment": classification["sentiment"],
        "sentiment_score": classification["sentiment_score"],
        "importance": classification["importance"],
        "catalyst_score": catalyst_score,
        "symbols": symbols,
        "stock_names": names,
        "tags": tags,
        "url": url,
        "publish_time": publish_time or _now_cn(),
        "raw": raw or {},
    }


def _store_news_events(events: list[dict[str, Any]]) -> int:
    if not events:
        return 0
    ensure_lite_news_table()
    now = _now_cn()
    with store.connect() as conn:
        for event in events:
            conn.execute(
                """
                INSERT OR REPLACE INTO lite_news_events (
                    id, title, content, source, source_type, event_type, sentiment,
                    sentiment_score, importance, catalyst_score, symbols_json,
                    stock_names_json, tags_json, url, publish_time, raw_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM lite_news_events WHERE id = ?), ?), ?)
                """,
                (
                    event["id"],
                    event["title"],
                    event.get("content", ""),
                    event["source"],
                    event["source_type"],
                    event["event_type"],
                    event["sentiment"],
                    float(event["sentiment_score"]),
                    event["importance"],
                    float(event["catalyst_score"]),
                    json.dumps(event.get("symbols", []), ensure_ascii=False),
                    json.dumps(event.get("stock_names", []), ensure_ascii=False),
                    json.dumps(event.get("tags", []), ensure_ascii=False),
                    event.get("url", ""),
                    event["publish_time"],
                    json.dumps(event.get("raw", {}), ensure_ascii=False, default=str),
                    event["id"],
                    now,
                    now,
                ),
            )
        conn.commit()
    return len(events)


def _prune_news_events(keep_days: int = 3) -> None:
    """清理新闻事件表：① 删除过期事件 ② 同源同标题去重（保留发布时间最新一条）。
    防止 publish_time 不稳定的源重复累积、用入库时间挤占按时间排序的查询窗。"""
    ensure_lite_news_table()
    with store.connect() as conn:
        conn.execute(
            "DELETE FROM lite_news_events WHERE substr(publish_time, 1, 10) < date('now', ?)",
            (f"-{keep_days} day",),
        )
        conn.execute(
            """
            DELETE FROM lite_news_events
            WHERE id NOT IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY source, title ORDER BY publish_time DESC, updated_at DESC
                    ) AS rn
                    FROM lite_news_events
                ) WHERE rn = 1
            )
            """
        )
        conn.commit()


def _row_to_event(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"] or "",
        "source": row["source"],
        "source_type": row["source_type"],
        "event_type": row["event_type"],
        "sentiment": row["sentiment"],
        "sentiment_score": float(row["sentiment_score"] or 0),
        "importance": row["importance"],
        "catalyst_score": float(row["catalyst_score"] or 0),
        "symbols": json.loads(row["symbols_json"] or "[]"),
        "stock_names": json.loads(row["stock_names_json"] or "[]"),
        "tags": json.loads(row["tags_json"] or "[]"),
        "url": row["url"] or "",
        "publish_time": row["publish_time"],
    }


def _query_news_events(limit: int = 100, source_type: str | None = None, sentiment: str | None = None) -> list[dict[str, Any]]:
    ensure_lite_news_table()
    sql = "SELECT * FROM lite_news_events"
    params: list[Any] = []
    clauses = []
    if source_type:
        clauses.append("source_type = ?")
        params.append(source_type)
    if sentiment:
        clauses.append("sentiment = ?")
        params.append(sentiment)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY publish_time DESC, catalyst_score DESC LIMIT ?"
    params.append(limit)
    with store.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_event(row) for row in rows]


def _build_a_share_sentiment(events: list[dict[str, Any]]) -> dict[str, Any]:
    if not events:
        return {
            "temperature": 50,
            "stance": "中性",
            "positive": 0,
            "negative": 0,
            "neutral": 0,
            "top_themes": [],
            "risk_flags": [],
            "brief": "暂无足够 A 股舆情事件，先以量化和行情信号为主。",
        }
    pos = sum(1 for item in events if item.get("sentiment") == "利好")
    neg = sum(1 for item in events if item.get("sentiment") == "利空")
    neu = max(0, len(events) - pos - neg)
    score_sum = sum(float(item.get("sentiment_score") or 0) for item in events)
    temperature = int(max(0, min(100, 50 + score_sum / max(1, len(events)) * 35 + (pos - neg) / max(1, len(events)) * 30)))
    stance = "偏热" if temperature >= 65 else "偏冷" if temperature <= 40 else "中性"
    themes: dict[str, dict[str, Any]] = {}
    risk_flags: list[str] = []
    for item in events:
        for tag in (item.get("tags") or [])[:3]:
            bucket = themes.setdefault(str(tag), {"name": str(tag), "count": 0, "score": 0.0})
            bucket["count"] += 1
            bucket["score"] += max(0.1, abs(float(item.get("sentiment_score") or 0)))
        if item.get("sentiment") == "利空" or item.get("event_type") == "regulatory_risk":
            title = str(item.get("title") or "")
            if title:
                risk_flags.append(title)
    top_themes = sorted(themes.values(), key=lambda x: (x["count"], x["score"]), reverse=True)[:6]
    lead = top_themes[0]["name"] if top_themes else "热点扩散"
    brief = f"A股舆情当前{stance}，利好 {pos} 条、利空 {neg} 条；重点看 {lead} 的持续性。"
    return {
        "temperature": temperature,
        "stance": stance,
        "positive": pos,
        "negative": neg,
        "neutral": neu,
        "top_themes": [{"name": x["name"], "count": x["count"], "score": round(x["score"], 2)} for x in top_themes],
        "risk_flags": list(dict.fromkeys(risk_flags))[:5],
        "brief": brief,
    }


HOT_NEWS_RELEVANT_KEYWORDS = (
    "A股", "沪深", "创业板", "科创板", "北交所", "半导体", "芯片", "存储", "算力", "AI",
    "机器人", "低空", "电力", "数据中心", "新能源", "锂电", "光伏", "军工", "通信",
    "PCB", "消费电子", "医药", "创新药", "有色", "黄金", "铜", "证券", "并购", "重组",
    "中标", "订单", "回购", "增持", "业绩", "预增", "目标价", "评级",
)

HOT_NEWS_NOISE_KEYWORDS = (
    "YUAN GUI YANG", "SpaceX", "特朗普", "韩国", "欧洲", "阿曼湾", "液体散货船",
    "Google", "谷歌", "Claude", "美元", "IPO文件", "标普", "信用评级", "房企",
    "美股", "港股", "纳斯达克", "道指", "恒指", "港交所", "美国三大股指",
    "华尔街", "SEC", "NYSE", "NASDAQ", "HKEX",
)

HOT_NEWS_ROUTINE_ANNOUNCEMENT_KEYWORDS = (
    "投资者关系", "活动记录", "管理信息", "管理制度", "薪酬", "接待日", "受托管理",
    "通知债权人", "减持", "回购注销", "限制性股票", "临时受托", "独立董事",
    "重大事项报告制度", "报告制度", "管理办法", "保荐总结报告书", "保荐总结",
    "年度保荐工作报告", "持续督导", "超额奖励", "奖励发放", "权益变动提示性公告",
    "股东大会", "股东会", "董事会决议", "监事会决议", "章程", "修订", "聘任", "辞职",
    "变更会计师", "担保进展", "诉讼进展", "上市公告书",
    "招股说明书", "律师", "审计报告", "评估报告", "募集说明书", "保荐书",
)

HOT_NEWS_STRONG_ANNOUNCEMENT_KEYWORDS = (
    "重大资产重组", "发行股份购买资产", "购买资产", "募集配套资金", "资产重组",
    "控制权变更", "实际控制人变更", "中标", "合同", "订单", "定增", "要约收购",
    "增持", "回购股份", "股份回购", "同意注册", "审核通过", "收购", "资产注入",
)


def _recency_bonus(publish_time: Any) -> float:
    """时效性加权：带时刻且越新越高；纯日期(公告，无时刻)记 0，体现"时效性"。"""
    s = str(publish_time or "").strip()
    if len(s) <= 10:  # 仅 "YYYY-MM-DD"，无时刻 —— 公告类，不给时效加权
        return 0.0
    try:
        dt = datetime.strptime(s.replace("Z", "").replace("T", " ").strip()[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return 0.0
    age_h = max(0.0, (datetime.now() - dt).total_seconds() / 3600.0)
    if age_h <= 2:
        return 1.6
    if age_h <= 6:
        return 1.1
    if age_h <= 24:
        return 0.6
    if age_h <= 72:
        return 0.2
    return 0.0


def _event_relevance_score(event: dict[str, Any]) -> float:
    title = str(event.get("title") or "")
    content = str(event.get("content") or "")
    text = f"{title} {content}"
    symbols = event.get("symbols") or []
    score = 0.0
    if symbols:
        score += 2.2
    source_type = event.get("source_type")
    if source_type in {"news", "sentiment"}:
        score += 1.6  # 快讯是时效主力，给基础权重
    elif source_type in {"announcement", "research"}:
        score += 0.8
    score += _recency_bonus(event.get("publish_time"))
    if event.get("importance") == "high":
        score += 1.0
    elif event.get("importance") == "medium":
        score += 0.4
    score += min(2.0, float(event.get("catalyst_score") or 0) / 5)
    score += min(1.2, sum(1 for word in HOT_NEWS_RELEVANT_KEYWORDS if word in text) * 0.25)
    if event.get("event_type") == "regulatory_risk" or event.get("sentiment") == "利空":
        score -= 1.8
    if any(word in text for word in ("*ST", "退市", "风险警示", "立案", "处罚", "问询")):
        score -= 3.2
    if not symbols and any(word in text for word in HOT_NEWS_NOISE_KEYWORDS):
        score -= 3.0
    if len(title) < 10:
        score -= 1.0
    return round(score, 3)


def _is_actionable_hot_event(event: dict[str, Any]) -> bool:
    title = str(event.get("title") or "")
    content = str(event.get("content") or "")
    text = f"{title} {content}"
    if not title:
        return False
    source_type = event.get("source_type")
    # 公告/研报天然非"热点资讯"：仅当命中强事件词(重组/中标/回购/增持等)才进热榜，
    # 例行件(股东会决议/招股说明书/审计报告…)一律降级，不再霸榜。
    if source_type in {"announcement", "research"}:
        return any(word in text for word in HOT_NEWS_STRONG_ANNOUNCEMENT_KEYWORDS)
    # 财经快讯来自策划好的 A 股 7x24 实时源，默认视为市场相关；仅剔除明显纯海外/无关噪声。
    if any(word in text for word in HOT_NEWS_NOISE_KEYWORDS) and not event.get("symbols"):
        return False
    return True


def _is_secondary_hot_event(event: dict[str, Any]) -> bool:
    title = str(event.get("title") or "")
    content = str(event.get("content") or "")
    text = f"{title} {content}"
    if not title:
        return False
    source_type = event.get("source_type")
    # 公告/研报只有强事件才可作为补充，例行件不回填
    if source_type in {"announcement", "research"} and not any(word in text for word in HOT_NEWS_STRONG_ANNOUNCEMENT_KEYWORDS):
        return False
    if any(word in text for word in HOT_NEWS_ROUTINE_ANNOUNCEMENT_KEYWORDS):
        return False
    if any(word in text for word in HOT_NEWS_NOISE_KEYWORDS):
        return False
    if any(word in text for word in ("*ST", "退市", "风险警示", "立案", "处罚", "问询")):
        return False
    market_scope = any(word in text for word in ("A股", "沪深", "沪指", "深成指", "创业板指", "北交所", "板块", "概念", "涨停", "涨超", "走高", "活跃", "爆发"))
    return bool(event.get("symbols")) or (market_scope and any(word in text for word in HOT_NEWS_RELEVANT_KEYWORDS))


def _fetch_hot_rank_events(limit: int = 20) -> list[dict[str, Any]]:
    try:
        import akshare as ak

        df = ak.stock_hot_rank_em()
    except Exception:
        return []

    events: list[dict[str, Any]] = []
    for _, row in df.head(limit).iterrows():
        raw_symbol = str(row.get("代码") or "").strip()
        symbol = re.sub(r"^(SH|SZ|BJ)", "", raw_symbol, flags=re.IGNORECASE).zfill(6)
        if not re.fullmatch(r"\d{6}", symbol):
            continue
        name = str(row.get("股票名称") or symbol).strip()
        if not symbol or not name:
            continue
        try:
            rank = int(row.get("当前排名") or 0)
        except (TypeError, ValueError):
            rank = 0
        try:
            pct = float(row.get("涨跌幅") or 0)
        except (TypeError, ValueError):
            pct = 0.0
        price = row.get("最新价")
        title = f"东方财富热度榜：{name}（{symbol}）排名第{rank}，最新价{price}，涨跌幅{pct:+.2f}%"
        event = {
            "id": f"hot-rank-{symbol}",
            "title": title,
            "content": title,
            "source": "东方财富热度榜",
            "source_type": "hot_rank",
            "event_type": "market_news",
            "sentiment": "中性" if pct < 3 else "利好",
            "sentiment_score": min(0.8, max(0.05, abs(pct) / 20)),
            "importance": "medium",
            "catalyst_score": max(1.0, min(4.0, abs(pct) / 3)),
            "symbols": [symbol],
            "stock_names": [name],
            "tags": ["热度榜", "市场关注", "实时行情"],
            "url": "",
            "publish_time": datetime.now().astimezone().isoformat(),
            "raw": {},
        }
        events.append(event)
    return events


def _fetch_caixin_market_news(stock_lookup: dict[str, str], limit: int = 60) -> list[dict[str, Any]]:
    import akshare as ak

    df = ak.stock_news_main_cx()
    events = []
    for _, row in df.head(limit).iterrows():
        summary = _clean_text(row.get("summary", ""))
        if not summary:
            continue
        # 用财新自带的真实发布时间（稳定 id、真时效）；缺失则用当天日期，避免
        # 入库时间(_now_cn)既不稳定(每刷一次生成新 id 重复累积)又伪装成"刚刚"霸榜。
        pub_time = _clean_text(row.get("pub_time") or row.get("time") or row.get("date") or "")
        events.append(_build_event(
            title=summary[:90],
            content=summary,
            source="财新",
            source_type="news",
            publish_time=pub_time or datetime.now().strftime("%Y-%m-%d"),
            url=str(row.get("url", "")),
            stock_lookup=stock_lookup,
            raw=row.to_dict(),
        ))
    return events


def _fetch_eastmoney_announcements(stock_lookup: dict[str, str], days: int = 2, limit: int = 120) -> list[dict[str, Any]]:
    events = []
    url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    for offset in range(max(1, days)):
        date = (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")
        params = {
            "sr": "-1",
            "page_size": str(min(limit, 100)),
            "page_index": "1",
            "ann_type": "A",
            "client_source": "web",
            "f_node": "0",
            "s_node": "0",
            "begin_time": date,
            "end_time": date,
        }
        response = requests.get(url, params=params, timeout=12)
        response.raise_for_status()
        data = response.json().get("data") or {}
        for item in (data.get("list") or [])[:limit]:
            explicit = []
            for code in item.get("codes") or []:
                stock_code = str(code.get("stock_code", "")).strip()
                if str(code.get("ann_type", "")).startswith("A") and re.fullmatch(r"\d{6}", stock_code):
                    explicit.append({"symbol": stock_code, "name": str(code.get("short_name", ""))})
            columns = item.get("columns") or []
            event = _build_event(
                title=item.get("title") or item.get("title_ch") or "",
                content=(columns[0].get("column_name") if columns else ""),
                source="东方财富公告",
                source_type="announcement",
                publish_time=str(item.get("notice_date") or item.get("display_time") or date),
                url=f"https://data.eastmoney.com/notices/detail/{explicit[0]['symbol']}/{item.get('art_code')}.html" if explicit else "",
                explicit_symbols=explicit,
                stock_lookup=stock_lookup,
                raw=item,
            )
            if event["title"]:
                events.append(event)
    return events[:limit]


def _fetch_eastmoney_research(stock_lookup: dict[str, str], symbols: list[str], limit_per_symbol: int = 5) -> list[dict[str, Any]]:
    import akshare as ak

    events = []
    for symbol in symbols:
        try:
            df = ak.stock_research_report_em(symbol=symbol)
        except Exception:
            continue
        for _, row in df.head(limit_per_symbol).iterrows():
            report_name = _clean_text(row.get("报告名称", ""))
            if not report_name:
                continue
            stock_code = str(row.get("股票代码") or symbol)
            stock_name = str(row.get("股票简称") or stock_lookup.get(stock_code, ""))
            rating = str(row.get("东财评级") or "")
            org = str(row.get("机构") or "东方财富研报")
            date = str(row.get("日期") or _now_cn())
            title = f"{stock_name or stock_code}：{report_name}"
            content = f"机构：{org}；评级：{rating}；行业：{row.get('行业', '')}"
            events.append(_build_event(
                title=title,
                content=content,
                source="东方财富研报",
                source_type="research",
                publish_time=date,
                url=str(row.get("报告PDF链接") or ""),
                explicit_symbols=[{"symbol": stock_code, "name": stock_name}],
                stock_lookup=stock_lookup,
                raw=row.to_dict(),
            ))
    return events


def _fetch_market_flash_news(stock_lookup: dict[str, str], limit: int = 80) -> list[dict[str, Any]]:
    """东方财富 7x24 实时快讯（带秒级时刻、A 股为主）—— 热榜时效性主力来源。

    直连 np-weblist 接口（与公告抓取同套路），避开 akshare 财联社/全球财经路径
    在本环境的卡死与限流。失败返回空，调用方降级。
    """
    url = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
    params = {
        "client": "web",
        "biz": "web_724",
        "fastColumn": "102",
        "sortEnd": "",
        "pageSize": str(min(max(limit, 20), 100)),
        "req_trace": "1",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        rows = (resp.json().get("data") or {}).get("fastNewsList") or []
    except Exception:
        return []

    events: list[dict[str, Any]] = []
    for item in rows[:limit]:
        title = _clean_text(item.get("title") or "")
        content = _clean_text(item.get("summary") or item.get("digest") or "")
        if not title:
            title = content[:90]
        if not title:
            continue
        events.append(_build_event(
            title=title,
            content=content,
            source="东方财富快讯",
            source_type="news",
            publish_time=str(item.get("showTime") or _now_cn()),
            stock_lookup=stock_lookup,
            raw=item,
        ))
    return events


async def _stock_lookup(limit: int = 6000) -> dict[str, str]:
    lookup = {item["symbol"]: item["name"] for item in _watch_symbols()}
    try:
        for item in await get_stock_pool_items(limit=limit):
            symbol = str(item.get("symbol", "")).strip()
            name = str(item.get("name", "")).strip()
            if symbol and name:
                lookup[symbol] = name
    except Exception:
        pass
    return lookup


async def refresh_lite_news_events(limit: int = 180) -> dict[str, Any]:
    lookup = await _stock_lookup()
    watch_symbols = list(dict.fromkeys([item["symbol"] for item in _watch_symbols()]))
    source_results = []
    all_events: list[dict[str, Any]] = []

    async def collect(name: str, func: Any):
        try:
            events = await asyncio.to_thread(func)
            all_events.extend(events)
            source_results.append({"source": name, "success": True, "count": len(events)})
        except Exception as exc:
            source_results.append({"source": name, "success": False, "count": 0, "error": str(exc)})

    await asyncio.gather(
        collect("东方财富快讯", lambda: _fetch_market_flash_news(lookup, limit=80)),
        collect("财新市场新闻", lambda: _fetch_caixin_market_news(lookup, limit=60)),
        collect("东方财富公告", lambda: _fetch_eastmoney_announcements(lookup, days=2, limit=80)),
        collect("东方财富研报", lambda: _fetch_eastmoney_research(lookup, watch_symbols[:8], limit_per_symbol=4)),
    )
    # 按发布时间倒序后截断：带时刻的快讯天然排在纯日期公告之前，保住最新资讯不被截掉
    all_events.sort(key=lambda e: str(e.get("publish_time") or ""), reverse=True)
    saved = _store_news_events(all_events[:limit])
    _prune_news_events(keep_days=3)  # 去重 + 清过期，防止源累积挤占查询窗
    lite_insights_cache.clear()
    _persistent_cache_delete_prefix("smart-pool:")
    return {
        "saved": saved,
        "sources": source_results,
        "updated_at": _now_cn(),
    }


async def ensure_recent_lite_news() -> None:
    latest = _query_news_events(limit=1)
    if latest:
        try:
            latest_time = datetime.fromisoformat(str(latest[0]["publish_time"]).replace("Z", "+00:00"))
            now = datetime.now(latest_time.tzinfo) if latest_time.tzinfo else datetime.now()
            if now - latest_time < timedelta(minutes=20):
                return
        except Exception:
            return
    await refresh_lite_news_events()


def _build_catalyst_items(limit: int = 10) -> list[dict[str, Any]]:
    items = []
    for meta in _watch_symbols():
        symbol = meta["symbol"]
        try:
            quant = asdict(lite_quant_engine.analyze(symbol))
        except Exception:
            quant = {}
        latest = quant.get("latest") or {}
        factors = quant.get("factors") or {}
        risk = quant.get("risk") or {}
        score = float(quant.get("score") or _stable_float(symbol, 55, 82))
        pct = float(latest.get("pct_change") or _stable_float(symbol + "pct", -5.5, 9.8))
        sentiment = round(
            (score / 100 * 0.45)
            + (max(-8, min(12, pct)) + 8) / 20 * 0.25
            + float(factors.get("momentum") or 50) / 100 * 0.2
            + float(factors.get("liquidity") or 50) / 100 * 0.1,
            2,
        )
        mentions = max(2, int(_stable_float(symbol + "mentions", 3, 18)))
        catalyst = round(mentions * sentiment, 2)
        items.append({
            "symbol": symbol,
            "name": meta["name"],
            "score": round(score, 1),
            "signal": quant.get("signal") or "watch",
            "mentions": mentions,
            "hot_score": round(catalyst, 2),
            "sentiment": sentiment,
            "change_percent": round(pct, 2),
            "price": round(float(latest.get("close") or latest.get("price") or _stable_float(symbol + "price", 5, 450)), 2),
            "risk_level": _risk_level(float(risk.get("volatility") or 0), float(risk.get("max_drawdown") or 0)),
            "sparkline": _sparkline(symbol, pct),
            "reasons": _catalyst_reasons(factors, pct, score),
            "updated_at": _now_cn(),
        })
    return sorted(items, key=lambda item: item["hot_score"], reverse=True)[:limit]


def _catalyst_reasons(factors: dict[str, Any], pct: float, score: float) -> list[str]:
    reasons = []
    if float(factors.get("momentum") or 0) >= 70:
        reasons.append("动量因子强")
    if float(factors.get("liquidity") or 0) >= 70:
        reasons.append("成交活跃")
    if pct >= 5:
        reasons.append("涨幅靠前")
    if score >= 72:
        reasons.append("综合评分进入候选区")
    if not reasons:
        reasons.append("热度进入观察池")
    return reasons[:3]


SMART_POOL_RECOMMENDER = {
    "name": "全市场综合优选",
    "description": "系统自动优先筛短中期进攻型股票，并叠加 AI 因子模型 Top-K 排名作为机器学习评分因子。",
    "weights": {
        "quant": 0.18,
        "ai_factor": 0.10,
        "trend": 0.25,
        "momentum": 0.22,
        "rsi": 0.06,
        "trigger": 0.16,
        "catalyst": 0.05,
        "risk": 0.01,
        "liquidity": 0.01,
    },
}


DEFAULT_SMART_POOL_UNIVERSE = [
    {"symbol": "300750", "name": "宁德时代"},
    {"symbol": "601919", "name": "中远海控"},
    {"symbol": "002594", "name": "比亚迪"},
    {"symbol": "601899", "name": "紫金矿业"},
    {"symbol": "688981", "name": "中芯国际"},
    {"symbol": "601012", "name": "隆基绿能"},
    {"symbol": "002475", "name": "立讯精密"},
    {"symbol": "300124", "name": "汇川技术"},
    {"symbol": "300033", "name": "同花顺"},
    {"symbol": "300024", "name": "机器人"},
    {"symbol": "603986", "name": "兆易创新"},
    {"symbol": "603618", "name": "杭电股份"},
    {"symbol": "688256", "name": "寒武纪"},
    {"symbol": "600487", "name": "亨通光电"},
    {"symbol": "000988", "name": "华工科技"},
    {"symbol": "002407", "name": "多氟多"},
    {"symbol": "002281", "name": "光迅科技"},
    {"symbol": "002747", "name": "埃斯顿"},
    {"symbol": "002979", "name": "雷赛智能"},
    {"symbol": "603416", "name": "信捷电气"},
    {"symbol": "300276", "name": "三丰智能"},
]

SLOW_DEFENSIVE_SYMBOLS = {
    "600900", "600887", "000333", "600519", "000858", "600036", "000001", "601318", "300760"
}


SMART_SYMBOL_THEMES = {
    "600519": "白酒消费",
    "000001": "银行",
    "300750": "新能源电池",
    "601318": "保险金融",
    "600036": "银行",
    "601919": "航运",
    "600900": "电力",
    "600276": "创新药",
    "000858": "白酒消费",
    "002594": "新能源汽车",
    "002415": "安防科技",
    "000333": "家电",
    "601899": "有色金属",
    "600887": "食品饮料",
    "603259": "医药外包",
    "300760": "医疗器械",
    "688981": "半导体",
    "601012": "光伏",
    "002475": "消费电子",
    "300124": "工业自动化",
    "300033": "金融科技",
    "300024": "机器人",
    "603986": "半导体",
    "603618": "电力设备",
    "600941": "通信运营",
    "688256": "AI芯片",
    "600487": "光通信",
    "000988": "光通信",
    "002407": "新能源材料",
    "002281": "光通信",
    "002747": "机器人自动化",
    "002979": "运动控制",
    "300276": "智能装备",
    "603416": "工业自动化",
    "300483": "天然气",
    "600339": "油气工程",
    "600580": "电力通信",
    "600050": "通信运营",
    "601728": "通信运营",
    "688361": "工业机器人",
    "601138": "消费电子",
    "002230": "算力服务器",
    "000977": "算力服务器",
    "603019": "工业软件",
    "002156": "工业机器人",
    "002008": "高端装备",
    "300308": "AI应用",
    "000404": "家电零部件",
    "000338": "商用车/动力总成",
    "000099": "航空运输/低空经济",
    "688062": "生物制品",
    "688019": "半导体材料",
    "688051": "物联网/环保信息化",
    "688035": "电子化学品",
    "688041": "算力芯片",
    "300031": "游戏传媒",
    "688036": "消费电子",
    "000025": "汽车服务",
    "688018": "物联网芯片",
    "000026": "珠宝钟表",
    "300002": "软件服务",
    "600022": "钢铁",
    "300003": "医疗器械",
    "000006": "房地产",
    "600060": "电子视像",
    "000066": "计算机设备",
    "920088": "专精特新",
    "688008": "科技软件",
    "688006": "软件服务",
    "920001": "通信设备",
    "688045": "半导体设备",
    "920047": "专精特新",
    "920060": "专精特新",
    "000333": "白色家电",
    "000099": "航空运输/低空经济",
}


SMART_NAME_THEME_RULES = [
    (("银行", "证券", "保险"), "金融"),
    (("移动", "电信", "联通", "通信", "光迅", "光电", "光库"), "通信设备"),
    (("机器人", "智能", "埃斯顿", "汇川", "信捷", "雷赛"), "机器人自动化"),
    (("电气", "电力", "能源", "电网", "电缆"), "电力设备"),
    (("芯片", "半导体", "微", "电子"), "半导体"),
    (("科技", "软件", "信息", "数据"), "科技软件"),
    (("医药", "生物", "医疗", "药"), "医药生物"),
    (("汽车", "锂", "电池", "新能", "时代"), "新能源车"),
    (("白酒", "茅台", "五粮液"), "白酒消费"),
    (("有色", "黄金", "铜", "铝", "锂业", "矿业"), "资源金属"),
    (("海直", "航空", "航天", "机场"), "航空运输"),
    (("动力", "柴油", "重汽", "发动机"), "汽车零部件"),
    (("家电", "电器", "华意", "海尔", "美的"), "家用电器"),
    (("半导体", "芯片", "微", "集成", "海光", "寒武"), "半导体"),
    (("生物", "医疗", "医药", "制药", "迈威"), "医药生物"),
    (("物流", "航运", "海控", "港口"), "交通运输"),
    (("传媒", "游戏", "影视"), "传媒娱乐"),
    (("钢铁", "稀土", "钨", "铜", "铝", "黄金"), "周期资源"),
]


def _industry_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "nan" or text in {"--", "-"}:
        return ""
    return text


def _pick_cninfo_industry(df: Any) -> str:
    if df is None or getattr(df, "empty", True):
        return ""
    rows = df.copy()
    if "变更日期" in rows.columns:
        rows = rows.sort_values("变更日期", ascending=False)
    preferred_standards = ("巨潮行业分类标准", "中证行业分类标准", "中国上市公司协会上市公司行业分类标准")
    for standard in preferred_standards:
        subset = rows[rows["分类标准"].astype(str).str.contains(standard, na=False)] if "分类标准" in rows.columns else rows
        for _, row in subset.iterrows():
            medium = _industry_text(row.get("行业中类"))
            large = _industry_text(row.get("行业大类"))
            if medium:
                return medium
            if large:
                return large
    for _, row in rows.iterrows():
        for column in ("行业中类", "行业大类", "行业次类", "行业门类"):
            industry = _industry_text(row.get(column))
            if industry:
                return industry
    return ""


def _fetch_cninfo_industry(symbol: str) -> str:
    import akshare as ak

    end_date = datetime.now().strftime("%Y%m%d")
    df = ak.stock_industry_change_cninfo(symbol=symbol, start_date="20100101", end_date=end_date)
    return _pick_cninfo_industry(df)


async def _resolve_real_industry(symbol: str, name: str, event_labels: set[str]) -> str:
    if symbol in SMART_SYMBOL_THEMES:
        return SMART_SYMBOL_THEMES[symbol]
    cached = lite_industry_cache.get(symbol)
    if cached and datetime.now() - cached[0] < timedelta(days=7):
        return cached[1]
    industry = ""
    try:
        industry = await asyncio.wait_for(asyncio.to_thread(_fetch_cninfo_industry, symbol), timeout=5)
    except Exception:
        industry = ""
    if not industry:
        industry = _smart_pool_theme(symbol, name, event_labels)
    lite_industry_cache[symbol] = (datetime.now(), industry)
    return industry


async def _enrich_smart_pool_industries(items: list[dict[str, Any]], timeout: float = 20.0) -> list[dict[str, Any]]:
    # 每只 _resolve_real_industry 已带 5s 超时、并行 gather；外层只设宽松总超时兜底，
    # 不要太短（之前 6s 会把 ~20 只冷缓存的行业增强截断，导致行业回退成「A股」）。
    try:
        industries = await asyncio.wait_for(
            asyncio.gather(
                *[
                    _resolve_real_industry(item["symbol"], item.get("name") or item["symbol"], set(item.get("event_labels") or []))
                    for item in items
                ],
                return_exceptions=True,
            ),
            timeout=timeout,
        )
    except (asyncio.TimeoutError, Exception):
        return items
    for item, industry in zip(items, industries):
        if isinstance(industry, str) and industry:
            item["industry"] = industry
            item["board"] = industry
    return items


def _smart_pool_grade(score: float) -> str:
    if score >= 85:
        return "核心候选"
    return "高质量候选"


def _smart_pool_theme(symbol: str, name: str, event_labels: set[str]) -> str:
    if symbol in SMART_SYMBOL_THEMES:
        return SMART_SYMBOL_THEMES[symbol]
    for keywords, theme in SMART_NAME_THEME_RULES:
        if any(word in name for word in keywords):
            return theme
    if event_labels:
        useful = [label for label in event_labels if label not in {"公告", "市场新闻"}]
        if useful:
            return " / ".join(useful[:2])
    return "行业待识别"


def _risk_quality_score(risk: dict[str, Any]) -> float:
    volatility = abs(float(risk.get("volatility") or 0))
    drawdown = abs(float(risk.get("max_drawdown") or 0))
    return round(max(0, min(100, 100 - volatility * 110 - drawdown * 75)), 2)


def _short_term_attack_gate(
    symbol: str,
    trend: float,
    momentum: float,
    rsi: float,
    pct_chg: float,
    risk_score: float,
    catalyst_score: float,
) -> tuple[bool, list[str]]:
    failed: list[str] = []
    if trend < 72:
        failed.append("趋势未达短中期进攻门槛")
    if momentum < 70:
        failed.append("动量不足")
    if rsi < 42:
        failed.append("RSI偏弱")
    if rsi > 92:
        failed.append("RSI过热")
    if pct_chg <= -6 and trend < 82 and momentum < 82 and catalyst_score < 20:
        failed.append("实时深跌且趋势/动量未确认修复")
    if pct_chg < -2 and trend < 85 and momentum < 85 and catalyst_score < 20:
        failed.append("低开走弱且缺少趋势/事件确认")
    if risk_score < 8:
        failed.append("回撤/波动风险过高")
    if symbol.startswith(("8", "4", "9")):
        failed.append("北交所/低流动性标的默认不进主推荐")
    if symbol in SLOW_DEFENSIVE_SYMBOLS and not (trend >= 88 and momentum >= 88 and pct_chg >= 1.2):
        failed.append("防守慢股，缺少短线爆发确认")
    return not failed, failed


def _secondary_attack_gate(
    symbol: str,
    trend: float,
    momentum: float,
    rsi: float,
    pct_chg: float,
    risk_score: float,
    catalyst_score: float,
) -> bool:
    if symbol in SLOW_DEFENSIVE_SYMBOLS:
        return False
    if symbol.startswith(("8", "4", "9")):
        return False
    if trend < 66 or momentum < 64:
        return False
    if rsi < 38 or rsi > 90:
        return False
    if pct_chg < -3.5 and trend < 82 and momentum < 82 and catalyst_score < 15:
        return False
    if risk_score < 0:
        return False
    return True


def _entry_trigger_score(factors: dict[str, Any], latest: dict[str, Any], risk_score: float, catalyst_score: float) -> tuple[float, list[str]]:
    trend = float(factors.get("trend") or 0)
    momentum = float(factors.get("momentum") or 0)
    rsi = float(factors.get("rsi") or 0)
    liquidity = float(factors.get("liquidity") or 0)
    pct_chg = float(latest.get("pct_change") or 0)

    triggers: list[str] = []
    if trend >= 88:
        triggers.append(f"趋势强突破 {trend:.0f}")
    elif trend >= 75:
        triggers.append(f"趋势突破 {trend:.0f}")
    if momentum >= 88:
        triggers.append(f"短中期动量爆发 {momentum:.0f}")
    elif momentum >= 75:
        triggers.append(f"动量共振 {momentum:.0f}")
    if 52 <= rsi <= 72:
        triggers.append(f"RSI处于上攻区 {rsi:.0f}")
    elif 72 < rsi <= 88:
        triggers.append(f"RSI强势但未极端 {rsi:.0f}")
    if pct_chg >= 3:
        triggers.append(f"实时放量上涨 {pct_chg:+.2f}%")
    elif pct_chg >= 0.8:
        triggers.append(f"实时启动 {pct_chg:+.2f}%")
    elif 0 <= pct_chg < 0.8 and trend >= 85 and momentum >= 85:
        triggers.append("强趋势低位蓄势")
    if liquidity >= 65:
        triggers.append(f"成交额流动性达标 {liquidity:.0f}")
    if risk_score >= 35:
        triggers.append(f"回撤风险可控 {risk_score:.0f}")
    if catalyst_score > 0:
        triggers.append(f"真实事件催化 {catalyst_score:.0f}")

    score = min(100.0, len(triggers) * 13 + max(0, trend - 65) * 0.35 + max(0, momentum - 65) * 0.3)
    return round(score, 1), triggers


def _smart_pool_candidates(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    ordered: dict[str, str] = {}
    for event in events:
        if event.get("sentiment") == "利空":
            continue
        symbols = event.get("symbols") or []
        names = event.get("stock_names") or []
        for idx, symbol in enumerate(symbols):
            if symbol:
                ordered.setdefault(symbol, names[idx] if idx < len(names) else symbol)
    for item in _watch_symbols() + DEFAULT_SMART_POOL_UNIVERSE:
        ordered.setdefault(item["symbol"], item.get("name") or item["symbol"])
    return [{"symbol": symbol, "name": name} for symbol, name in ordered.items()]


def _load_ai_factor_pool(limit: int, universe_limit: int) -> dict[str, Any]:
    """Load cached LightGBM Top-K picks for one-click smart-pool scoring.

    Missing cache starts the background factor job and returns quickly.
    """
    try:
        from quantcore.quant.ml.service import request_ml_factor

        result = request_ml_factor(
            universe_limit=max(100, min(int(universe_limit or 500), 5000)),
            horizon=5,
            k=max(50, min(200, limit * 4)),
            mode="rolling",
            neutralize=True,
            retrain_every=20,
            min_rows=250,
            force=False,
        )
    except Exception as exc:
        return {"status": "error", "error": str(exc), "scores": {}, "picks": []}

    if result.get("status") not in {None, "ready"} or result.get("error"):
        return {"status": result.get("status") or "computing", "error": result.get("error"), "scores": {}, "picks": []}

    picks = list(result.get("picks") or [])
    total = max(1, len(picks) - 1)
    scores: dict[str, dict[str, Any]] = {}
    normalized_picks: list[dict[str, str]] = []
    for idx, pick in enumerate(picks, start=1):
        symbol = str(pick.get("symbol") or "").strip().zfill(6)
        if not re.fullmatch(r"\d{6}", symbol):
            continue
        rank_score = round(100.0 - ((idx - 1) / total) * 30.0, 1) if len(picks) > 1 else 100.0
        scores[symbol] = {
            "score": rank_score,
            "rank": idx,
            "raw_score": pick.get("score"),
            "pick_date": result.get("pick_date"),
        }
        normalized_picks.append({"symbol": symbol, "name": str(pick.get("name") or symbol)})
    return {
        "status": "ready",
        "scores": scores,
        "picks": normalized_picks,
        "pick_date": result.get("pick_date"),
        "universe": result.get("universe"),
    }


def _ai_factor_proxy_score(factors: dict[str, Any], ml_features: dict[str, Any] | None = None) -> float:
    if ml_features and ml_features.get("feature_score") is not None:
        try:
            return round(float(ml_features.get("feature_score") or 0), 1)
        except (TypeError, ValueError):
            pass
    try:
        trend = float(factors.get("trend") or 0)
        momentum = float(factors.get("momentum") or 0)
        liquidity = float(factors.get("liquidity") or 0)
        risk = float(factors.get("risk_control") or 0)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(100.0, trend * 0.35 + momentum * 0.30 + liquidity * 0.20 + risk * 0.15)), 1)


async def _smart_pool_quant(symbol: str) -> dict[str, Any]:
    return await asyncio.to_thread(lambda target=symbol: asdict(lite_quant_engine.analyze(target)))


def _env_position_gate(mkt: dict[str, Any] | None) -> dict[str, Any]:
    """大盘环境 → 单票建议仓位闸门（②a）。环境是全市场同值，池级算一次、每票同用。

    回放实证：弱市短线信号系统性失效、偏冷期超额贴零——所以偏冷自动把单票仓位砍到
    「只观察」，不是靠用户自觉。系数用于前端把「满仓基准」折算成建议仓位。
    """
    state = (mkt or {}).get("state") or "中性"
    temp = float((mkt or {}).get("temp") or 50.0)
    if state == "偏冷":
        return {"state": state, "temp": temp, "coefficient": 0.3, "label": "轻仓 · 只观察",
                "note": "大盘偏冷，弱市短线信号系统性失效（回放偏冷期超额贴零）。建议单票仓位 ≤3 成，或仅观察等企稳。"}
    if state == "偏暖":
        return {"state": state, "temp": temp, "coefficient": 1.0, "label": "可正常参与",
                "note": "大盘偏暖，赚钱效应尚可。仍按纪律控制单票仓位、分批介入。"}
    return {"state": state, "temp": temp, "coefficient": 0.6, "label": "半仓以内",
            "note": "大盘中性，方向不明。单票仓位建议 ≤5 成，优先强势主线、回踩企稳再介入。"}


async def _enrich_smart_pool_realtime(response: dict[str, Any]) -> dict[str, Any]:
    data = dict(response.get("data") or {})
    items = [dict(item) for item in data.get("items") or []]
    # ②a 环境仓位闸门：用当前全市场快照算环境，缓存命中的旧池也会拿到当下的仓位建议。
    # 环境全市场同值，45s 缓存一次，避免每次 smart-pool 请求都重算 market_context 拖慢秒读。
    gate: dict[str, Any] = _cache_get("env_position_gate", 45) or {}
    if not gate:
        try:
            _snap = await _run_data_task(_load_realtime_quotes_snapshot, 30, timeout=8.0)
            from quantcore.quant.engine import market_context as _market_context
            _mkt = await _run_data_task(_market_context, _snap, timeout=10.0)
            gate = _env_position_gate(_mkt)
        except Exception:
            gate = _env_position_gate(None)
        _cache_set("env_position_gate", gate)
    quotes = await _realtime_quotes(
        [item.get("symbol") or item.get("code") for item in items],
        allow_snapshot_fallback=False,
    )
    quote_updated_at = None
    for item in items:
        symbol = str(item.get("symbol") or item.get("code") or "").zfill(6)
        quote = quotes.get(symbol)
        _apply_realtime_quote(item, quote)
        # 涨停标记跟着实时涨跌幅走：缓存命中时价格会刷新，扫描那一刻的旧标记会撒谎。
        if item.get("pct_chg") is not None:
            from quantcore.quant.engine import is_limit_up as _is_limit_up
            item["limit_up"] = _is_limit_up(symbol, item.get("pct_chg"))
        # 实时价刷新后按同一 ATR 重算交易计划，保持买点贴合现价。
        tp = item.get("trade_plan")
        if isinstance(tp, dict) and tp.get("buy_price") and item.get("close"):
            try:
                from quantcore.quant.factors import trade_plan as _trade_plan
                item["trade_plan"] = _trade_plan(float(item["close"]), float(tp.get("atr") or 0.0))
            except Exception:
                pass
        if quote and quote.get("change_percent") is not None and item.get("reasons"):
            item["reasons"] = [
                f"实时涨跌幅 {quote['change_percent']:+.2f}%" if str(reason).startswith("实时涨跌幅 ") else reason
                for reason in item["reasons"]
            ]
        if quote and quote.get("updated_at"):
            quote_updated_at = quote["updated_at"]
        # ②a 把环境仓位闸门写进每票交易计划，理由卡直接可读。
        if gate:
            item["env_position"] = {"label": gate.get("label"), "coefficient": gate.get("coefficient")}
            if isinstance(item.get("trade_plan"), dict):
                item["trade_plan"]["env_position"] = gate.get("label")
                item["trade_plan"]["env_note"] = gate.get("note")
    if quote_updated_at:
        data["updated_at"] = quote_updated_at
        data["quote_updated_at"] = quote_updated_at
        data["price_source"] = "实时行情"
    data["position_gate"] = gate
    data["items"] = items
    enriched = dict(response)
    enriched["data"] = data
    return enriched


def _confluence_enrich_items(items: list[dict[str, Any]]) -> None:
    """把「形态智选」「强势股」并入一键推荐：给每只结构因子入选票补低位形态识别 +
    相对强度维度，形态/强度共振时给一个展示用加成分并据此微调排序。

    方案 A（经 A/B 回放定调）：结构因子分是 proven 买入信号，保持为展示的「量化分」不动
    （不绑死评分刻度）；形态是低位反转确认、强度是趋势/位置确认，都是骨架之上的加分标签，
    不喧宾夺主、不引入新票。只对最终 ≤50 只入选票就地计算，成本可控。
    """
    from quantcore.quant.data import load_local_kline
    from quantcore.quant.integrations import recognize_patterns
    from quantcore.quant.relative_strength import compute_strength_metrics
    for item in items:
        symbol = str(item.get("symbol") or item.get("code") or "").zfill(6)
        try:
            data = load_local_kline(symbol, days=540)
        except Exception:
            data = None
        if data is None or getattr(data, "empty", True):
            continue
        try:
            rec = recognize_patterns(symbol, data)
            matched = [p for p in rec.patterns
                       if p.get("active") and float(p.get("strength") or 0) >= 70.0]
        except Exception:
            matched = []
        item["patterns"] = matched
        # ③a 七不买硬闸门：kline 已加载，顺带跑风险检查，重度(risk_count≥2=回避)后续剔除。
        try:
            from quantcore.quant.risk_check import check_risks as _check_risks
            _rc = _check_risks(symbol, str(item.get("name") or symbol), data)
            item["risk_check"] = {
                "risk_count": int(_rc.get("risk_count") or 0),
                "advice": _rc.get("advice") or "",
                "flags": [f.get("name") for f in (_rc.get("flags") or []) if f.get("level") == "risk"],
            }
        except Exception:
            item["risk_check"] = {"risk_count": 0}
        try:
            sm = compute_strength_metrics(data)
        except Exception:
            sm = None
        tags: list[str] = []
        bonus = 0.0
        if matched:
            tags.append("形态")
            bonus += 1.5
        if sm:
            item["strength"] = {
                "dist_from_low": sm["dist_from_low"], "adr": sm["adr"],
                "above_ema8": sm["above_ema8"], "above_ema21": sm["above_ema21"],
                "ema_stack": sm["ema_stack"],
            }
            if sm["above_ema8"] and sm["above_ema21"]:
                tags.append("强度")
                bonus += 1.0
                if sm["ema_stack"]:
                    bonus += 0.5
        if matched and sm and sm.get("above_ema8") and sm.get("above_ema21"):
            bonus += 1.0  # 结构 + 形态 + 强度三重共振
        if bonus:
            item["confluence_bonus"] = round(min(bonus, 4.0), 1)
            item["confluence_tags"] = tags
        # ①c 双确认：结构因子(池底座) + 低位形态 = 双确认；再叠相对强度 = 三重确认。
        # 交集是最高把握子集——回放里结构分本就 proven，形态是低位反转确认。
        item["dual_confirm"] = "形态" in tags
        item["triple_confirm"] = "形态" in tags and "强度" in tags
    # 排序键 = 结构分 + 共振加成（加成小、只让共振票在近分档里上浮，量化分本身不改）
    items.sort(key=lambda it: float(it.get("smart_score") or it.get("score") or 0)
               + float(it.get("confluence_bonus") or 0), reverse=True)


async def _apply_confluence(response: dict[str, Any]) -> None:
    """在缓存前给智能池 items 就地补形态/强度共振（重活丢线程池，缓存命中不再重算）。"""
    data = response.get("data") or {}
    items = data.get("items") or []
    if items:
        try:
            await asyncio.to_thread(_confluence_enrich_items, items)
        except Exception as exc:  # noqa: BLE001 — 融合富化失败不能阻断推荐主流程
            print(f"confluence enrich failed: {exc}")
        # ③a 风控硬闸门：命中重度风险(七不买 risk_count≥2=回避)的直接剔除出池——
        # 宁可没得选也不给雷票。被剔的记数+样例，前端弱市空池时弹窗说明。
        kept = [it for it in items if int((it.get("risk_check") or {}).get("risk_count") or 0) < 2]
        excluded = [it for it in items if int((it.get("risk_check") or {}).get("risk_count") or 0) >= 2]
        data["items"] = kept
        data["excluded_severe_count"] = len(excluded)
        data["excluded_severe_samples"] = [
            {"name": it.get("name"), "symbol": it.get("symbol") or it.get("code"),
             "reason": (it.get("risk_check") or {}).get("advice") or "命中七不买重度风险"}
            for it in excluded[:5]
        ]
        items = kept
        # ①c 双确认子集计数，供前端展示/筛选
        data["dual_confirm_count"] = sum(1 for it in items if it.get("dual_confirm"))
        data["triple_confirm_count"] = sum(1 for it in items if it.get("triple_confirm"))


def _smart_pool_task_update(task_id: str | None, **patch: Any) -> None:
    if not task_id:
        return
    task = lite_smart_pool_tasks.get(task_id)
    if not task:
        return
    task.update(patch)
    task["updated_at"] = datetime.now().astimezone().isoformat()


def _smart_pool_task_cleanup(max_items: int = 20) -> None:
    if len(lite_smart_pool_tasks) <= max_items:
        return
    removable = sorted(
        lite_smart_pool_tasks.items(),
        key=lambda kv: str(kv[1].get("updated_at") or kv[1].get("created_at") or ""),
    )
    for task_id, task in removable[: max(0, len(lite_smart_pool_tasks) - max_items)]:
        if task.get("status") != "running":
            lite_smart_pool_tasks.pop(task_id, None)


async def _compute_lite_swing_pool(
    limit: int,
    universe: int,
    cache_key: str,
    task_id: str | None = None,
) -> dict[str, Any]:
    """短线波段档：调用量化引擎 6 维共振扫描，映射为智能推荐池响应（带 ATR 买卖点）。"""
    _smart_pool_task_update(task_id, progress=30, phase="swing", message="扫描短线波段共振信号")
    swing = await run_scan(
        "swing_pool", limit=limit, universe_limit=universe
    )
    items: list[dict[str, Any]] = []
    for raw in swing.get("items") or []:
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol") or raw.get("code") or "").strip().zfill(6)
        if not re.fullmatch(r"\d{6}", symbol):
            continue
        score = float(raw.get("score") or 0)
        items.append({
            "symbol": symbol,
            "code": symbol,
            "name": raw.get("name") or symbol,
            "market": raw.get("market") or "A股",
            "industry": raw.get("industry") or raw.get("board") or "",
            "close": float(raw.get("close") or 0),
            "pct_chg": float(raw.get("pct_chg") or 0),
            "amount": float(raw.get("amount") or 0),
            "score": score,
            "smart_score": score,
            "quant_score": score,
            "swing_score": float(raw.get("swing_score") or score),
            "swing_dims": raw.get("swing_dims") or {},
            "hold_hint": raw.get("hold_hint") or "1-3 日",
            "grade": "短线波段",
            "signal": raw.get("signal") or "",
            "reasons": [str(r) for r in (raw.get("reasons") or []) if r][:8],
            "trade_plan": raw.get("trade_plan") or {},
        })
    items = await _enrich_smart_pool_industries(items[:limit])
    try:
        from quantcore.quant import industry as _industry
        await asyncio.to_thread(_industry.enrich_industries, items)
    except Exception:
        pass
    response = {
        "strategy": "swing_short",
        "preset": {
            "name": "短线波段共振",
            "description": "RSI 超卖 + KDJ/MACD 金叉 + 布林下轨 + 放量 + 资金代理 6 维共振，偏好 1-3 日低吸反弹，附 ATR 买卖点。",
        },
        "updated_at": datetime.now().strftime("%Y/%m/%d %H:%M:%S"),
        "universe_size": swing.get("universe_size") or len(items),
        "analyzed": swing.get("analyzed") or len(items),
        "matched": swing.get("matched") or len(items),
        "items": items,
        "note": swing.get("note"),
        "dimensions": swing.get("dimensions") or [],
        "source_note": "短线波段共振模型，研究与模拟使用，不构成投资建议。",
    }
    wrapped = {"success": True, "data": response, "message": "ok"}
    if items:
        _persistent_cache_set(cache_key, wrapped)
        _cache_set(cache_key, wrapped)
    _smart_pool_task_update(task_id, progress=95, phase="realtime", message="模型完成，刷新实时价格")
    return await _enrich_smart_pool_realtime(wrapped)


async def _compute_lite_smart_pool(
    strategy: str = "balanced",
    limit: int = 30,
    universe_limit: int = 500,
    task_id: str | None = None,
    force_refresh: bool = False,
    cache_only: bool = False,
) -> dict[str, Any]:
    from quantcore.quant.local_store import get_local_store

    preset = SMART_POOL_RECOMMENDER
    safe_limit = max(5, min(limit, 50))
    # 全市场评分（与回放口径一致：回放在全市场上取每期 top-N）。v3 结构因子分走进程池，
    # 3700 只约 32 秒，5000 只的上限扛得住；旧的 1200 截断会让线上池和回放验证的池不是一回事。
    safe_universe = max(safe_limit * 2, min(universe_limit, 5000))
    # 评分公式版本进 cache key：换公式必须换 key，否则旧公式的缓存结果会被继续端上来。
    daily_as_of = get_local_store().latest_real_bar_date() or "unknown"
    cache_key = (
        f"smart-pool:factor-v5-structure:{daily_as_of}:"
        f"{strategy}:{safe_limit}:{safe_universe}"
    )
    _smart_pool_task_update(
        task_id,
        progress=5,
        phase="cache",
        message="手动刷新：跳过旧名单缓存" if force_refresh else "检查最近智能推荐缓存",
    )
    if not force_refresh:
        cached = _cache_get(cache_key, 900)
        if cached:
            _smart_pool_task_update(task_id, progress=95, phase="realtime", message="缓存命中，刷新实时价格")
            return await _enrich_smart_pool_realtime(cached)
        # cache_only（进页面秒显）时放宽到一天，宁可端出稍旧名单也不现算阻塞。
        persistent_cached = _persistent_cache_get(cache_key, 86400 if cache_only else 3600)
        if persistent_cached:
            _cache_set(cache_key, persistent_cached)
            _smart_pool_task_update(task_id, progress=95, phase="realtime", message="历史缓存命中，刷新实时价格")
            return await _enrich_smart_pool_realtime(persistent_cached)
        if cache_only:
            # 彻底冷缓存：不现算(否则阻塞~100s)，返回 warming 占位，交给后台保温器算好。
            return {"items": [], "universe_size": 0, "analyzed": 0, "source": "warming",
                    "warming": True, "daily_as_of": daily_as_of, "market_context": {}}

    # 短线波段档：6 维共振低吸选股，独立于动量/AI 因子路径。
    if strategy == "swing_short":
        return await _compute_lite_swing_pool(safe_limit, safe_universe, cache_key, task_id)

    _smart_pool_task_update(task_id, progress=14, phase="ai_factor", message="读取 AI 因子候选池")
    ai_factor_pool = await asyncio.to_thread(_load_ai_factor_pool, safe_limit, safe_universe)
    ai_factor_scores: dict[str, dict[str, Any]] = ai_factor_pool.get("scores") or {}

    # Keep the stock-screening smart pool aligned with Quant Center's one-click recommendation.
    try:
        def to_float(value: Any, default: float = 0.0) -> float:
            try:
                if value in (None, "", "-"):
                    return default
                return float(value)
            except (TypeError, ValueError):
                return default

        _smart_pool_task_update(task_id, progress=28, phase="quant_center", message="调用量化中心一键推荐模型")
        quant_pool = await run_scan(
            "smart_pool",
            limit=safe_limit,
            universe_limit=safe_universe,
        )

        items: list[dict[str, Any]] = []
        for raw in quant_pool.get("items") or []:
            if not isinstance(raw, dict):
                continue
            factors = raw.get("factors") if isinstance(raw.get("factors"), dict) else {}
            symbol = str(raw.get("symbol") or raw.get("code") or "").strip().zfill(6)
            if not re.fullmatch(r"\d{6}", symbol):
                continue
            score = to_float(raw.get("score") or raw.get("quant_score"), 0)
            # 不设绝对分数闸门：回放验证的策略是「每期买评分 top-N」，不是「买分数 >X 的」。
            # 绑死绝对刻度的阈值一换评分公式就失效——v3 结构因子分上限约 80，旧的 <80 过滤
            # 会把整池清空（用户点一键推荐什么都看不到）。排序取前 N 由引擎负责，弱市该少买
            # 由大盘环境横幅提示，不靠隐藏结果。
            close = to_float(raw.get("close"), 0)
            pct_chg = to_float(raw.get("pct_chg"), 0)
            amount = to_float(raw.get("amount"), 0)
            reasons = [str(item) for item in (raw.get("reasons") or []) if item]
            if amount > 0 and not any("成交" in reason for reason in reasons):
                reasons.insert(1, f"成交额 {amount / 100000000:.2f} 亿")
            ai_factor = ai_factor_scores.get(symbol)
            ml_features = (raw.get("integrations") or {}).get("ml_features") if isinstance(raw.get("integrations"), dict) else {}
            ai_factor_score = float(ai_factor.get("score") or 0) if ai_factor else _ai_factor_proxy_score(factors, ml_features or {})
            display_score = round(score * 0.9 + ai_factor_score * 0.1, 1) if ai_factor_score else score
            if ai_factor:
                reasons.insert(0, f"AI因子TopK第{ai_factor.get('rank')}名 {ai_factor_score:.0f}")
            elif ai_factor_score:
                reasons.insert(0, f"AI因子即时分 {ai_factor_score:.0f}")
            item = {
                "symbol": symbol,
                "code": symbol,
                "name": raw.get("name") or symbol,
                "market": raw.get("market") or "A股",
                "industry": raw.get("industry") or raw.get("market") or "",
                "close": close,
                "pct_chg": pct_chg,
                "amount": amount,
                "smart_score": display_score,
                "raw_score": score,
                "score": display_score,
                "quant_score": score,
                "daily_structure_score": to_float(raw.get("daily_structure_score"), score),
                "intraday_strength_score": to_float(raw.get("intraday_strength_score"), score),
                "ai_factor_score": round(ai_factor_score, 1),
                "ai_factor_rank": ai_factor.get("rank") if ai_factor else None,
                "ai_factor_source": "lightgbm_topk" if ai_factor else "ml_feature_proxy",
                "trigger_score": to_float(factors.get("trend"), 0),
                "catalyst_score": to_float(raw.get("catalyst_score"), 0),
                "risk_score": to_float(factors.get("risk_control"), 0),
                "liquidity_score": to_float(factors.get("liquidity"), 0),
                "grade": "核心候选" if display_score >= 90 else "高质量候选" if display_score >= 85 else "重点观察",
                "signal": raw.get("signal") or "",
                "limit_up": bool(raw.get("limit_up")),
                "reasons": reasons[:8],
                "latest_events": raw.get("latest_events") or [],
                "forecast": raw.get("forecast") or {},
                "patterns": raw.get("patterns") or [],
                "trade_plan": raw.get("trade_plan") or {},
            }
            items.append(item)

        items.sort(key=lambda item: float(item.get("smart_score") or 0), reverse=True)
        items = await _enrich_smart_pool_industries(items[:safe_limit])
        # 用可靠的 cninfo 行业模块再兜底一遍：把仍为「A股/行业待识别」等占位值的补成真实行业。
        try:
            from quantcore.quant import industry as _industry
            await asyncio.to_thread(_industry.enrich_industries, items)
        except Exception:
            pass
        if ai_factor_pool.get("status") != "ready":
            response = {
                "strategy": "quant_center_smart_pool",
                "preset": {
                    "name": "全市场综合优选",
                    "description": "复用量化中心一键智能推荐模型，横向比较量化分、趋势、动量、流动性、实时强度和风险控制后生成候选股票池。",
                },
                "updated_at": datetime.now().strftime("%Y/%m/%d %H:%M:%S"),
                "universe_size": quant_pool.get("universe_size") or len(items),
                "analyzed": quant_pool.get("analyzed") or len(items),
                "daily_as_of": quant_pool.get("daily_as_of") or daily_as_of,
                "realtime_as_of": quant_pool.get("realtime_as_of") or "",
                "ranking_basis": quant_pool.get("ranking_basis") or "",
                "force_refreshed": force_refresh,
                "items": items,
                "ai_factor": {
                    "status": ai_factor_pool.get("status"),
                    "pick_date": ai_factor_pool.get("pick_date"),
                    "universe": ai_factor_pool.get("universe"),
                },
                "source_note": "同源于量化中心智能推荐，并把 AI 因子模型 Top-K 排名作为评分因子；已并入形态识别与相对强度共振标签；研究与模拟使用，不构成投资建议。",
            }
            wrapped_response = {"success": True, "data": response, "message": "ok"}
            await _apply_confluence(wrapped_response)
            _persistent_cache_set(cache_key, wrapped_response)
            _cache_set(cache_key, wrapped_response)
            _smart_pool_task_update(task_id, progress=95, phase="realtime", message="模型完成，刷新实时价格")
            return await _enrich_smart_pool_realtime(wrapped_response)
    except Exception as exc:
        print(f"Quant Center smart pool failed, falling back to lite smart pool: {exc}")

    _smart_pool_task_update(task_id, progress=34, phase="events", message="读取新闻事件和实时活跃股票")
    await ensure_recent_lite_news()
    events = _query_news_events(limit=220)
    event_map: dict[str, dict[str, Any]] = {}
    for event in events:
        for symbol in event.get("symbols") or []:
            bucket = event_map.setdefault(symbol, {"score": 0.0, "titles": [], "labels": set(), "sentiment": 0.0})
            sentiment_score = float(event.get("sentiment_score") or 0)
            bucket["score"] += max(0, float(event.get("catalyst_score") or 0))
            bucket["sentiment"] += sentiment_score
            bucket["titles"].append(event.get("title") or "")
            bucket["labels"].add(EVENT_TYPE_LABELS.get(event.get("event_type", ""), event.get("event_type", "事件")))

    candidates_by_symbol = {item["symbol"]: item for item in _smart_pool_candidates(events)}
    for item in ai_factor_pool.get("picks") or []:
        candidates_by_symbol.setdefault(item["symbol"], item)
    realtime_snapshot: dict[str, dict[str, Any]] = {}
    try:
        realtime_snapshot = await asyncio.wait_for(
            asyncio.to_thread(_load_realtime_quotes_snapshot, 10), timeout=10.0
        )
        by_gain = sorted(
            realtime_snapshot.values(),
            key=lambda quote: float(quote.get("change_percent") or quote.get("pct_chg") or -99),
            reverse=True,
        )
        by_amount = sorted(
            realtime_snapshot.values(),
            key=lambda quote: float(quote.get("amount") or 0),
            reverse=True,
        )
        by_activity = sorted(
            realtime_snapshot.values(),
            key=lambda quote: (
                float(quote.get("volume_ratio") or 0),
                float(quote.get("turnover_rate") or 0),
                float(quote.get("amplitude") or 0),
                float(quote.get("amount") or 0),
            ),
            reverse=True,
        )
        active_quotes_by_symbol: dict[str, dict[str, Any]] = {}
        for quote in by_gain[:220] + by_amount[:220] + by_activity[:220]:
            symbol = str(quote.get("symbol") or quote.get("code") or "").strip()
            if re.fullmatch(r"\d{6}", symbol):
                active_quotes_by_symbol.setdefault(symbol, quote)
        for quote in active_quotes_by_symbol.values():
            symbol = str(quote.get("symbol") or quote.get("code") or "").strip()
            name = str(quote.get("name") or symbol).strip()
            amount = float(quote.get("amount") or 0)
            if not re.fullmatch(r"\d{6}", symbol):
                continue
            if "ST" in name.upper() or symbol.startswith(("8", "4", "9")):
                continue
            if amount < 30_000_000:
                continue
            candidates_by_symbol.setdefault(symbol, {"symbol": symbol, "name": name})
    except Exception:
        pass
    try:
        pool_items = await get_stock_pool_items(limit=6000)
        buckets = {
            "sh_main": [],
            "sz_main": [],
            "gem": [],
            "star": [],
            "beijing": [],
        }
        for item in pool_items:
            symbol = str(item.get("symbol", "")).strip()
            if not re.fullmatch(r"\d{6}", symbol):
                continue
            if symbol.startswith("6") and not symbol.startswith("688"):
                buckets["sh_main"].append(item)
            elif symbol.startswith(("000", "001", "002", "003")):
                buckets["sz_main"].append(item)
            elif symbol.startswith("300"):
                buckets["gem"].append(item)
            elif symbol.startswith("688"):
                buckets["star"].append(item)
            elif symbol.startswith(("8", "4", "9")):
                buckets["beijing"].append(item)
        for idx in range(220):
            for bucket in buckets.values():
                if idx < len(bucket):
                    item = bucket[idx]
                    symbol = str(item.get("symbol", "")).strip()
                    candidates_by_symbol.setdefault(symbol, {"symbol": symbol, "name": item.get("name") or symbol})
    except Exception:
        pass
    candidates = list(candidates_by_symbol.values())[:min(safe_universe, 620)]
    _smart_pool_task_update(
        task_id,
        progress=52,
        phase="quotes",
        message=f"抽取 {len(candidates)} 只候选并叠加实时行情",
    )
    candidate_quotes = await _realtime_quotes([item["symbol"] for item in candidates])
    analyze_semaphore = asyncio.Semaphore(40)

    async def analyze_candidate(meta: dict[str, str]) -> dict[str, Any] | None:
        symbol = meta["symbol"]
        async with analyze_semaphore:
            try:
                quant = await asyncio.wait_for(_smart_pool_quant(symbol), timeout=15)
            except Exception:
                return None
        factors = quant.get("factors") or {}
        risk = quant.get("risk") or {}
        latest = quant.get("latest") or {}
        catalyst = event_map.get(symbol, {"score": 0.0, "titles": [], "labels": set(), "sentiment": 0.0})
        catalyst_score = min(100.0, float(catalyst["score"]) * 8)
        event_labels = catalyst["labels"]
        risk_score = _risk_quality_score(risk)
        liquidity_score = float(factors.get("liquidity") or 50)
        quant_score = float(quant.get("score") or 0)
        ai_factor = ai_factor_scores.get(symbol)
        ml_features = (quant.get("integrations") or {}).get("ml_features") or {}
        ai_factor_score = float(ai_factor.get("score") or 0) if ai_factor else _ai_factor_proxy_score(factors, ml_features)
        trend_score = float(factors.get("trend") or 0)
        momentum_score = float(factors.get("momentum") or 0)
        rsi_score = float(factors.get("rsi") or 0)
        quote = candidate_quotes.get(symbol)
        display_name = (quote.get("name") if quote else None) or meta.get("name") or symbol
        if "ST" in str(display_name).upper():
            return None
        if quote:
            latest = dict(latest)
            if quote.get("price") is not None:
                latest["close"] = quote["price"]
            if quote.get("change_percent") is not None:
                latest["pct_change"] = quote["change_percent"]
            if quote.get("amount") is not None:
                latest["amount"] = quote["amount"]
            if quote.get("volume") is not None:
                latest["volume"] = quote["volume"]
        pct_chg = float(latest.get("pct_change") or 0)
        trigger_score, triggers = _entry_trigger_score(factors, latest, risk_score, catalyst_score)
        passed_gate, gate_failures = _short_term_attack_gate(
            symbol, trend_score, momentum_score, rsi_score, pct_chg, risk_score, catalyst_score
        )
        secondary_passed = False
        if not passed_gate:
            secondary_passed = _secondary_attack_gate(
                symbol, trend_score, momentum_score, rsi_score, pct_chg, risk_score, catalyst_score
            )
        if not passed_gate and not secondary_passed:
            return None
        weights = preset["weights"]
        final_score = round(
            quant_score * weights["quant"]
            + ai_factor_score * weights["ai_factor"]
            + trend_score * weights["trend"]
            + momentum_score * weights["momentum"]
            + rsi_score * weights["rsi"]
            + trigger_score * weights["trigger"]
            + catalyst_score * weights["catalyst"]
            + risk_score * weights["risk"]
            + liquidity_score * weights["liquidity"],
            1,
        )
        reasons = []
        reasons.extend(triggers)
        reasons.append(f"实时涨跌幅 {pct_chg:+.2f}%")
        if quant_score >= 72:
            reasons.append(f"量化分 {quant_score:.1f}")
        if ai_factor:
            reasons.append(f"AI因子TopK第{ai_factor.get('rank')}名 {ai_factor_score:.0f}")
        elif ai_factor_score:
            reasons.append(f"AI因子即时分 {ai_factor_score:.0f}")
        if trend_score >= 70:
            reasons.append(f"趋势因子 {trend_score:.0f}")
        if momentum_score >= 70:
            reasons.append(f"动量因子 {momentum_score:.0f}")
        if rsi_score >= 70:
            reasons.append(f"RSI偏强 {rsi_score:.0f}")
        elif rsi_score <= 30:
            reasons.append(f"RSI偏低 {rsi_score:.0f}")
        if risk_score >= 35:
            reasons.append(f"风控通过 {risk_score:.0f}")
        if catalyst_score > 0:
            labels = [label for label in catalyst["labels"] if label]
            reasons.append(f"近期真实事件：{'、'.join(labels[:2])}")
        if not reasons:
            reasons.append("进入基础观察池，需等待更强信号")
        reasons = list(dict.fromkeys(reasons))
        return {
            "symbol": symbol,
            "code": symbol,
            "name": display_name,
            "market": "A股",
            "industry": _smart_pool_theme(symbol, display_name, event_labels),
            "close": latest.get("close"),
            "pct_chg": latest.get("pct_change"),
            "amount": latest.get("amount"),
            "volume": latest.get("volume"),
            "smart_score": final_score,
            "score": final_score,
            "grade": _smart_pool_grade(final_score),
            "attack_tier": "primary" if passed_gate else "secondary",
            "signal": quant.get("signal") or "watch",
            "quant_score": round(quant_score, 1),
            "ai_factor_score": round(ai_factor_score, 1),
            "ai_factor_rank": ai_factor.get("rank") if ai_factor else None,
            "ai_factor_source": "lightgbm_topk" if ai_factor else "ml_feature_proxy",
            "trigger_score": trigger_score,
            "catalyst_score": round(catalyst_score, 1),
            "risk_score": risk_score,
            "liquidity_score": round(liquidity_score, 1),
            "factors": factors,
            "risk": risk,
            "reasons": reasons[:7],
            "gate_failures": gate_failures,
            "latest_events": list(dict.fromkeys([title for title in catalyst["titles"] if title]))[:3],
        }

    _smart_pool_task_update(task_id, progress=66, phase="scoring", message="并行计算量化分、动量、风险和触发器")
    analyzed = await asyncio.gather(*(analyze_candidate(candidate) for candidate in candidates))
    _smart_pool_task_update(task_id, progress=86, phase="ranking", message="排序候选池并补齐行业板块")
    primary_items = [item for item in analyzed if item and item.get("attack_tier") == "primary"]
    secondary_items = [item for item in analyzed if item and item.get("attack_tier") == "secondary"]
    items = primary_items
    if len(items) < safe_limit:
        secondary_items.sort(key=lambda item: item["smart_score"], reverse=True)
        items = items + secondary_items[: safe_limit - len(items)]
    items.sort(key=lambda item: item["smart_score"], reverse=True)
    if items:
        selected = items[:safe_limit]
        raw_scores = [float(item["smart_score"]) for item in selected]
        low_score = min(raw_scores)
        high_score = max(raw_scores)
        score_span = max(1.0, high_score - low_score)
        for rank, item in enumerate(selected, start=1):
            raw_score = float(item["smart_score"])
            trigger_score = float(item.get("trigger_score") or 0)
            calibrated = 80 + ((raw_score - low_score) / score_span) * 12 + min(2.0, trigger_score / 50)
            display_score = round(max(80.0, min(95.0, calibrated - (rank - 1) * 0.06)), 1)
            item["raw_score"] = round(raw_score, 1)
            item["smart_score"] = display_score
            item["score"] = display_score
            item["grade"] = _smart_pool_grade(display_score)
            item["rank"] = rank
        selected.sort(key=lambda item: item["smart_score"], reverse=True)
        for rank, item in enumerate(selected, start=1):
            item["rank"] = rank
        items = selected
    items = [item for item in items if float(item.get("smart_score") or 0) >= 80]
    for rank, item in enumerate(items, start=1):
        item["rank"] = rank
    items = await _enrich_smart_pool_industries(items[:safe_limit])
    data = {
        "strategy": "all_market_recommend",
        "preset": preset,
        "updated_at": datetime.now().astimezone().strftime("%Y/%m/%d %H:%M:%S"),
        "daily_as_of": daily_as_of,
        "realtime_as_of": datetime.now().astimezone().strftime("%Y/%m/%d %H:%M:%S")
        if realtime_snapshot
        else "",
        "ranking_basis": "按日K结构因子分排序（回放验证口径）；盘中强度仅作参考展示，不参与排序",
        "force_refreshed": force_refresh,
        "universe_size": len(candidates),
        "ai_factor": {
            "status": ai_factor_pool.get("status"),
            "pick_date": ai_factor_pool.get("pick_date"),
            "universe": ai_factor_pool.get("universe"),
        },
        "items": items,
        "source_note": "智能股票池由真实新闻/公告/研报事件、A股基础股票池、量化因子、AI因子模型和入场触发器综合生成；已并入形态识别与相对强度共振标签；仅供研究，不承诺收益。",
    }
    response = {"success": True, "data": data, "message": "ok"}
    await _apply_confluence(response)
    _persistent_cache_set(cache_key, response)
    _cache_set(cache_key, response)
    _smart_pool_task_update(task_id, progress=95, phase="realtime", message="推荐池完成，刷新实时价格")
    return await _enrich_smart_pool_realtime(response)


@app.get("/api/lite/smart-pool")
async def lite_smart_pool(strategy: str = "balanced", limit: int = 30, universe_limit: int = 500,
                          cache_only: bool = False):
    return await _compute_lite_smart_pool(strategy, limit, universe_limit, cache_only=cache_only)


async def _run_lite_smart_pool_task(task_id: str, strategy: str, limit: int,
                                    universe_limit: int, force_refresh: bool = True) -> None:
    try:
        _smart_pool_task_update(task_id, status="running", progress=2, phase="queued", message="任务已进入后台")
        result = await _compute_lite_smart_pool(
            strategy, limit, universe_limit, task_id=task_id, force_refresh=force_refresh
        )
        lite_smart_pool_tasks[task_id].update(
            {
                "status": "completed",
                "progress": 100,
                "phase": "completed",
                "message": "智能推荐池已生成",
                "result": result,
                "finished_at": datetime.now().astimezone().isoformat(),
                "updated_at": datetime.now().astimezone().isoformat(),
            }
        )
    except Exception as exc:  # noqa: BLE001
        lite_smart_pool_tasks[task_id].update(
            {
                "status": "failed",
                "progress": 100,
                "phase": "failed",
                "message": f"智能推荐失败：{exc}",
                "error": str(exc),
                "finished_at": datetime.now().astimezone().isoformat(),
                "updated_at": datetime.now().astimezone().isoformat(),
            }
        )


@app.post("/api/lite/smart-pool/tasks")
async def start_lite_smart_pool_task(strategy: str = "balanced", limit: int = 30,
                                     universe_limit: int = 500, force_refresh: bool = True):
    safe_limit = max(5, min(limit, 50))
    # 与 _compute_lite_smart_pool 同口径：全市场评分，才和回放验证的池是同一个池
    safe_universe = max(safe_limit * 2, min(universe_limit, 5000))
    # 去重：同参数任务已在排队/运行则直接复用，避免连点堆叠多个全市场扫描把线程池占满。
    for existing in lite_smart_pool_tasks.values():
        if (
            existing.get("status") in ("queued", "running")
            and existing.get("strategy") == strategy
            and existing.get("limit") == safe_limit
            and existing.get("universe_limit") == safe_universe
            and bool(existing.get("force_refresh", True)) == force_refresh
        ):
            return {"success": True, "data": existing, "message": "reused"}
    _smart_pool_task_cleanup()
    task_id = "smart_" + secrets.token_hex(8)
    now = datetime.now().astimezone().isoformat()
    lite_smart_pool_tasks[task_id] = {
        "task_id": task_id,
        "status": "queued",
        "progress": 1,
        "phase": "queued",
        "message": "后台智能推荐任务已创建",
        "strategy": strategy,
        "limit": safe_limit,
        "universe_limit": safe_universe,
        "force_refresh": force_refresh,
        "created_at": now,
        "updated_at": now,
    }
    asyncio.create_task(
        _run_lite_smart_pool_task(
            task_id, strategy, safe_limit, safe_universe, force_refresh=force_refresh
        )
    )
    return {"success": True, "data": lite_smart_pool_tasks[task_id], "message": "started"}


@app.get("/api/lite/smart-pool/tasks/{task_id}")
async def lite_smart_pool_task_status(task_id: str):
    task = lite_smart_pool_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="智能推荐任务不存在或已过期")
    return {"success": True, "data": task, "message": "ok"}


# ---- 形态扫描：后台任务化 ----
# 全市场形态扫描要 90 秒。同步返回意味着：① 用户对着转圈干等 ② 任何反向代理都会把它
# 掐断（Vercel 边缘约 30s、Cloudflare 100s——90s 是压着线过的）。改成「起任务 + 轮询」，
# 与智能选股同一套模式，代理只看到几十毫秒的短请求。
lite_pattern_pool_tasks: dict[str, dict[str, Any]] = {}


def _pattern_task_cleanup(keep: int = 12) -> None:
    finished = [(t.get("finished_at") or "", tid) for tid, t in lite_pattern_pool_tasks.items()
                if t.get("status") in ("completed", "failed")]
    for _, tid in sorted(finished)[:-keep] if len(finished) > keep else []:
        lite_pattern_pool_tasks.pop(tid, None)


async def _run_lite_pattern_pool_task(task_id: str, limit: int, universe_limit: int,
                                      min_strength: float, exclude_fundamental: bool) -> None:
    task = lite_pattern_pool_tasks[task_id]
    try:
        task.update({"status": "running", "progress": 5, "phase": "scan",
                     "message": "全市场形态扫描中（约 1-2 分钟）"})
        result = await run_scan("pattern_pool", limit=limit, universe_limit=universe_limit,
                                min_strength=min_strength, exclude_fundamental=exclude_fundamental)
        items = result.get("items") if isinstance(result, dict) else None
        if items:
            try:
                from quantcore.quant import industry as _industry
                await asyncio.to_thread(_industry.enrich_industries, items)
            except Exception:
                pass
        task.update({"status": "completed", "progress": 100, "phase": "completed",
                     "message": "形态扫描完成", "result": result,
                     "finished_at": datetime.now().astimezone().isoformat()})
    except Exception as exc:  # noqa: BLE001
        task.update({"status": "failed", "progress": 100, "phase": "failed",
                     "message": f"形态扫描失败：{exc}", "error": str(exc),
                     "finished_at": datetime.now().astimezone().isoformat()})


@app.post("/api/lite/pattern-pool/tasks")
async def start_lite_pattern_pool_task(limit: int = 20, universe_limit: int = 5000,
                                       min_strength: float = 70.0, exclude_fundamental: bool = True):
    safe_limit = max(5, min(limit, 50))
    safe_universe = max(safe_limit * 2, min(universe_limit, 5000))
    # 连点去重：同参数任务在跑就复用，避免堆叠多个全市场扫描
    for existing in lite_pattern_pool_tasks.values():
        if (existing.get("status") in ("queued", "running")
                and existing.get("limit") == safe_limit
                and existing.get("universe_limit") == safe_universe
                and existing.get("min_strength") == min_strength):
            return {"success": True, "data": existing, "message": "reused"}
    _pattern_task_cleanup()
    task_id = "pattern_" + secrets.token_hex(8)
    now = datetime.now().astimezone().isoformat()
    lite_pattern_pool_tasks[task_id] = {
        "task_id": task_id, "status": "queued", "progress": 1, "phase": "queued",
        "message": "后台形态扫描任务已创建",
        "limit": safe_limit, "universe_limit": safe_universe, "min_strength": min_strength,
        "created_at": now, "updated_at": now,
    }
    asyncio.create_task(_run_lite_pattern_pool_task(
        task_id, safe_limit, safe_universe, min_strength, exclude_fundamental))
    return {"success": True, "data": lite_pattern_pool_tasks[task_id], "message": "started"}


@app.get("/api/lite/pattern-pool/tasks/{task_id}")
async def lite_pattern_pool_task_status(task_id: str):
    task = lite_pattern_pool_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="形态扫描任务不存在或已过期")
    return {"success": True, "data": task, "message": "ok"}


@app.get("/api/lite/sector-leaders")
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


@app.get("/api/lite/call-auction")
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
    cache_key = f"call-auction:v5:{window}:{top_k}:{open_min}:{open_max_ratio}"
    cached = _cache_get(cache_key, 60)
    if cached:
        return cached

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
    result = await asyncio.to_thread(
        compute_call_auction, snapshot, SECTOR_LEADERS,
        industry_map=industry_map, hot_industries=hot_industries, exclude_symbols=bad_symbols,
        open_min=open_min, open_max_ratio=open_max_ratio,
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

    payload = {
        "success": True,
        "data": {**result, "updated_at": datetime.now().strftime("%Y/%m/%d %H:%M:%S")},
        "message": "ok",
    }
    if result.get("available"):
        _cache_set(cache_key, payload)
    return payload


@app.get("/api/lite/hot-news")
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
        # 催化剂是较快端点，行业增强 6s 封顶即可（一键推荐异步任务用默认 20s）。
        items = await _enrich_smart_pool_industries(items, timeout=6.0)
    data[item_key] = items
    enriched = dict(response)
    enriched["data"] = data
    return enriched


@app.get("/api/lite/catalysts")
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


@app.post("/api/lite/news/refresh")
async def lite_news_refresh(limit: int = 180):
    result = await refresh_lite_news_events(limit=max(20, min(limit, 300)))
    return {"success": True, "data": result, "message": "真实新闻/公告/研报源刷新完成"}


@app.get("/api/lite/events")
async def lite_events(limit: int = 100, source_type: str | None = None, sentiment: str | None = None):
    await ensure_recent_lite_news()
    return {
        "success": True,
        "data": {
            "items": _query_news_events(limit=max(1, min(limit, 300)), source_type=source_type, sentiment=sentiment),
        },
        "message": "ok",
    }


@app.get("/api/lite/market-sentiment")
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


@app.get("/api/lite/limit-up")
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
async def _is_trading_day_now() -> bool:
    """用实时快照的行情时间判断今天是否交易日：节假日快照时间停留在上一交易日，
    此时 cron 不应把旧行情落成当日盘报。仅在明确看到 stale 时间戳时返回 False；
    快照拿不到/无时间戳则 fail-open 视为交易日，宁多生成不漏。"""
    try:
        snapshot = await _run_data_task(_load_realtime_quotes_snapshot, 300, timeout=8.0)
    except Exception:
        return True
    if not snapshot:
        return True
    quote_dates: set[str] = set()
    for q in list(snapshot.values())[:200]:
        m = re.match(r"(\d{4})/(\d{2})/(\d{2})", str(q.get("updated_at") or ""))
        if m:
            quote_dates.add(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    if not quote_dates:
        return True
    return datetime.now().strftime("%Y-%m-%d") in quote_dates


@app.get("/api/lite/macro-bar")
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


@app.get("/api/lite/heatmap")
async def lite_heatmap(level: str = "industry", industry: str = ""):
    """行业/个股热力图：面积=总市值（亿，成交额兜底），颜色=当日涨跌幅。60s 缓存。

    快照不可用（收盘后/断网）时退回本地日线最新 bar（同收盘快照教训：不读未同步的当日）。
    """
    from quantcore.quant.heatmap import build_heatmap_industry, build_heatmap_stocks

    if level not in ("industry", "stock"):
        raise HTTPException(status_code=400, detail="level 必须是 industry/stock")
    if level == "stock" and not industry.strip():
        raise HTTPException(status_code=400, detail="level=stock 需要 industry 参数")
    cache_key = f"heatmap:{level}:{industry}"
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
    if level == "industry":
        from quantcore.quant.heatmap import heatmap_coverage
        items = build_heatmap_industry(snapshot, industry_map)
        coverage = heatmap_coverage(snapshot, industry_map)
    else:
        items = build_heatmap_stocks(snapshot, industry_map, industry.strip())
    payload = {
        "success": True,
        "data": {"level": level, "industry": industry.strip() or None, "items": items,
                 "source": source, "mapped": len(industry_map), "coverage": coverage,
                 "updated_at": datetime.now().astimezone().strftime("%Y/%m/%d %H:%M:%S")},
    }
    if items:
        _cache_set(cache_key, payload)
    return payload


@app.get("/api/system/config/validate")
async def validate_config():
    membership_ready = bool(os.getenv("LYNX_MEMBERSHIP_WECHAT", "").strip() or os.getenv("LYNX_MEMBERSHIP_QR_URL", "").strip())
    icp_ready = bool(os.getenv("LYNX_ICP_BEIAN", "").strip())
    payment_ready = bool(os.getenv("LYNX_PAYMENT_PROVIDER", "").strip())
    wechat_push_ready = bool(
        os.getenv("SERVERCHAN_SENDKEY", "").strip()
        or os.getenv("SERVERCHAN_KEY", "").strip()
        or os.getenv("PUSHPLUS_TOKEN", "").strip()
    )
    jwt_ready = bool(os.getenv("JWT_SECRET", "").strip())
    checks = [
        {
            "key": "membership_upgrade",
            "label": "会员收款码/运营微信",
            "ok": membership_ready,
            "required": True,
            "message": "配置 LYNX_MEMBERSHIP_WECHAT 或 LYNX_MEMBERSHIP_QR_URL 后，会员页会展示真实开通信息。",
        },
        {
            "key": "icp",
            "label": "ICP备案",
            "ok": icp_ready,
            "required": True,
            "message": "公开部署前配置 LYNX_ICP_BEIAN，并在页面页脚展示备案号。",
        },
        {
            "key": "payment_provider",
            "label": "正式支付",
            "ok": payment_ready,
            "required": False,
            "message": "M1 可人工开通；正式收款前配置 LYNX_PAYMENT_PROVIDER 并接入支付回调。",
        },
        {
            "key": "wechat_push",
            "label": "微信推送全局 token",
            "ok": wechat_push_ready,
            "required": False,
            "message": "可用用户自绑定；如需后台统一推送，配置 SERVERCHAN_SENDKEY 或 PUSHPLUS_TOKEN。",
        },
        {
            "key": "jwt_secret",
            "label": "登录签名密钥",
            "ok": jwt_ready,
            "required": True,
            "message": "生产环境必须配置 JWT_SECRET，避免重启后登录失效或使用默认密钥。",
        },
    ]
    valid = all(item["ok"] for item in checks if item["required"])
    return {
        "success": True,
        "data": {
            "valid": valid,
            "mode": "saas-lite",
            "storage": "sqlite",
            "checks": checks,
            "warnings": [
                item["message"] for item in checks
                if not item["ok"] and (item["required"] or item["key"] in {"payment_provider", "wechat_push"})
            ] + ["SaaS Lite 使用本地 SQLite，不启用 MongoDB/Redis 队列。"],
        },
        "message": "ready" if valid else "commercial config incomplete",
    }


@app.websocket("/api/ws/notifications")
async def websocket_notifications(websocket: WebSocket, token: str | None = None):
    await websocket.accept()
    await websocket.send_json({"type": "connected", "mode": "saas-lite", "token_received": bool(token)})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        return


@app.websocket("/api/ws/tasks/{task_id}")
async def websocket_task_updates(websocket: WebSocket, task_id: str, token: str | None = None):
    await websocket.accept()
    await websocket.send_json({"type": "connected", "mode": "saas-lite", "task_id": task_id, "token_received": bool(token)})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        return


@app.get("/api/lite/alerts")
async def get_price_alerts(user: dict[str, Any] = Depends(get_current_lite_user)):
    alerts = [
        item for item in lite_price_alerts.values()
        if item.get("username") == user["username"]
    ]
    return {"success": True, "data": alerts, "message": "ok"}


@app.delete("/api/lite/alerts/{symbol}")
async def dismiss_price_alert(
    symbol: str,
    direction: str = "high",
    user: dict[str, Any] = Depends(get_current_lite_user),
):
    prefix = f"{user['username']}:{symbol}:{direction}:"
    for key in list(lite_price_alerts.keys()):
        if key.startswith(prefix):
            lite_price_alerts.pop(key, None)
    return {"success": True, "data": None, "message": "已清除提醒"}


@app.get("/api/news-data/latest")
async def latest_news(limit: int = 10, hours_back: int = 24):
    return {
        "success": True,
        "data": {"items": [], "news": [], "limit": limit, "hours_back": hours_back},
        "message": "SaaS Lite 暂无新闻快讯",
    }


@app.get("/api/sync/multi-source/status")
async def multi_source_status():
    return {
        "success": True,
        "data": {"running": False, "status": "idle", "last_sync": None},
        "message": "SaaS Lite 未运行同步任务",
    }


@app.get("/api/sync/multi-source/sources/status")
async def multi_source_sources_status():
    from quantcore.quant.data_sources import data_source_status

    status = await asyncio.to_thread(data_source_status)
    return {
        "success": True,
        "data": [
            {
                "name": item["key"],
                "display_name": item["name"],
                "priority": item["priority"],
                "available": item["enabled"],
                "description": item["notes"],
                "token_source": "env",
                "capabilities": item["capabilities"],
            }
            for item in status["sources"]
        ],
        "message": "SaaS Lite 多数据源状态",
    }


@app.post("/api/lite/datalake/sync")
async def lite_datalake_sync(full: bool = False):
    svc = get_sync_service()
    status = await asyncio.to_thread(svc.run_sync, full, False)
    return {"success": True, "data": status}


@app.get("/api/lite/datalake/sync/status")
async def lite_datalake_sync_status():
    svc = get_sync_service()
    return {"success": True, "data": svc.status()}


@app.get("/api/lite/stock-names")
async def lite_stock_names(codes: str = ""):
    """批量「代码→名称」本地查询（stock_meta），用于个股深研最近搜索等显示名称+代码。"""
    code_list = [c.strip().zfill(6) for c in codes.split(",") if c.strip()][:50]
    if not code_list:
        return {"success": True, "data": {}}
    from quantcore.quant.local_store import get_local_store
    conn = get_local_store()._conn()
    placeholders = ",".join("?" * len(code_list))
    rows = conn.execute(
        f"SELECT symbol, name FROM stock_meta WHERE symbol IN ({placeholders})", code_list
    ).fetchall()
    return {"success": True, "data": {str(s).zfill(6): (n or "") for s, n in rows}}


@app.get("/api/lite/datalake/health")
async def lite_datalake_health(auto_start: bool = True):
    svc = get_sync_service()
    status = svc.status()
    health = dict(status.get("health") or {})
    auto_started = False
    should_full_sync = auto_start and not health.get("ready") and not status.get("running")
    should_incremental_sync = auto_start and health.get("needs_incremental_sync") and not status.get("running")
    if should_full_sync or should_incremental_sync:
        store = svc.store
        now = datetime.now()
        attempt_key = "last_auto_full_sync_attempt" if should_full_sync else "last_auto_incremental_sync_attempt"
        last_attempt = store.get_state(attempt_key) or ""
        can_start = True
        if last_attempt:
            try:
                can_start = (now - datetime.fromisoformat(last_attempt)).total_seconds() >= 1800
            except Exception:
                can_start = True
        if can_start:
            store.set_state(attempt_key, now.isoformat(timespec="seconds"))
            status = await asyncio.to_thread(svc.run_sync, should_full_sync, False)
            health = dict(status.get("health") or health)
            auto_started = True
    health["sync_running"] = bool(status.get("running"))
    health["sync_phase"] = status.get("phase")
    health["sync_done"] = status.get("done")
    health["sync_total"] = status.get("total")
    health["sync_errors_count"] = status.get("errors_count")
    health["last_full_sync"] = status.get("last_full_sync")
    health["last_incremental_sync"] = status.get("last_incremental_sync")
    health["auto_started"] = auto_started
    return {"success": True, "data": health}


@app.get("/api/lite/datalake/sources/health")
async def lite_datalake_sources_health():
    from quantcore.quant.data_sources import data_source_health

    sync_status = get_sync_service().status()
    health = data_source_health(sync_status.get("health") or {})
    health["sync"] = {
        "running": bool(sync_status.get("running")),
        "phase": sync_status.get("phase"),
        "done": sync_status.get("done"),
        "total": sync_status.get("total"),
        "errors_count": sync_status.get("errors_count"),
        "last_error": sync_status.get("last_error"),
        "last_full_sync": sync_status.get("last_full_sync"),
        "last_incremental_sync": sync_status.get("last_incremental_sync"),
    }
    return {"success": True, "data": health}


@app.get("/api/quant/stock-analysis/{symbol}")
async def stock_analysis(symbol: str, current_user=Depends(get_current_lite_user)):
    """合并个股研报 + 技术分析，供新版「个股分析」页使用。
    深度多智能体分析仍走原有 /api/analysis/single 异步入口。
    """
    from quantcore.quant.report_service import build_stock_report
    clean_symbol = str(symbol or "").strip().zfill(6)
    try:
        report = await asyncio.to_thread(build_stock_report, clean_symbol)
    except Exception as exc:
        report = {"available": False, "error": str(exc)}
    try:
        quotes = await _realtime_quotes([clean_symbol])
        quote = quotes.get(clean_symbol)
        if quote and report.get("available"):
            header = dict(report.get("header") or {})
            price = quote.get("price") if quote.get("price") is not None else quote.get("close")
            pct = quote.get("change_percent") if quote.get("change_percent") is not None else quote.get("pct_chg")
            if quote.get("name"):
                header["name"] = quote["name"]
            if price is not None:
                header["last_price"] = round(float(price), 2)
            if pct is not None:
                header["pct_chg"] = round(float(pct), 2)
            for key in ("pe", "pb", "total_mv", "circ_mv"):
                if quote.get(key) is not None:
                    header[key] = quote[key]
            # PEG 用实时 PE 二次重算（研报阶段拿不到实时 PE），growth 沿用研报里的净利同比。
            if quote.get("pe") is not None:
                from quantcore.quant.report_service import valuation_peg
                pv = report.get("peg_valuation") or {}
                report["peg_valuation"] = valuation_peg(quote.get("pe"), pv.get("growth"))
            if quote.get("total_mv"):
                total_mv = float(quote["total_mv"])
                header["market_cap_yi"] = round(total_mv / 1e8 if total_mv > 1_000_000 else total_mv, 2)
            header["quote_source"] = quote.get("quote_source")
            header["quote_updated_at"] = quote.get("updated_at")
            report["header"] = header
            report["realtime_quote"] = {
                "price": header.get("last_price"),
                "change": quote.get("change"),
                "change_percent": header.get("pct_chg"),
                "volume": quote.get("volume"),
                "amount": quote.get("amount"),
                "turnover_rate": quote.get("turnover_rate"),
                "amplitude": quote.get("amplitude"),
                "updated_at": quote.get("updated_at"),
                "source": quote.get("quote_source"),
            }
            rating = dict(report.get("rating") or {})
            if price:
                price_f = float(price)
                # 用实时价重算交易计划：复用研报里已算好的 ATR（盈亏比与依据不变），
                # 而不是粗暴套固定 ±10%/+15%。
                from quantcore.quant.factors import trade_plan as _trade_plan
                prev_plan = report.get("trade_plan") or {}
                plan = _trade_plan(price_f, float(prev_plan.get("atr") or 0.0))
                if plan:
                    rating["entry_low"] = round(price_f * 0.99, 2)
                    rating["entry_high"] = round(price_f * 1.01, 2)
                    rating["stop_loss"] = plan.get("stop_loss")
                    rating["target"] = plan.get("take_profit")
                    rating["risk_reward_ratio"] = plan.get("risk_reward_ratio")
                    report["trade_plan"] = plan
                report["rating"] = rating
    except Exception:
        pass
    return {"success": True, "data": report}


# ---- 前端静态托管（生产：单一来源，隧道只需暴露一个端口）----
# 必须放在所有 API 路由之后：SPA 兜底路由会吞掉未匹配路径，提前挂载会遮住 /api/*。
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

# 隧道回源的每一跳都要跨公网（实测本机 12ms、经边缘 800ms+），静态资源必须让
# Cloudflare 边缘缓存住。vite 产物文件名带内容哈希，改了内容就换名字，可以放心长缓存；
# index.html 绝不能缓存，否则发版后用户一直拿到旧壳子、引用已不存在的旧 JS。
_ASSET_CACHE = "public, max-age=31536000, immutable"
_HTML_CACHE = "no-cache"

if _FRONTEND_DIST.is_dir():
    class _CachedAssets(StaticFiles):
        def file_response(self, *args, **kwargs):  # type: ignore[override]
            resp = super().file_response(*args, **kwargs)
            resp.headers["Cache-Control"] = _ASSET_CACHE
            return resp

    app.mount("/assets", _CachedAssets(directory=str(_FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):
        """SPA 兜底：/api/* 与已注册路由不会走到这里；其余路径一律返回 index.html
        （前端 history 路由自行解析），静态文件存在则直接返回。"""
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = _FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate), headers={"Cache-Control": _ASSET_CACHE})
        return FileResponse(str(_FRONTEND_DIST / "index.html"),
                            headers={"Cache-Control": _HTML_CACHE})
