from __future__ import annotations

import asyncio
import json
import re
import secrets
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# Load .env (JWT_SECRET, MONGO_URI, admin creds) before auth import reads them,
# so logins stay valid across restarts when JWT_SECRET is set.
load_dotenv()

from app.lite_auth import get_current_lite_user, router as lite_auth_router, store
from app.routers.quant import router as quant_router
from quantcore.quant import QuantEngine
from quantcore.quant.sync_service import get_sync_service
from quantcore.trading import EasyTraderBridge, EasyTraderOrder


app = FastAPI(
    title="TradingAgents SaaS Lite",
    version="0.1.0",
    description="Local SaaS Lite runtime with SQLite auth and protected quant APIs.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(lite_auth_router)
app.include_router(quant_router, dependencies=[Depends(get_current_lite_user)])

lite_quant_engine = QuantEngine()
lite_trader_bridge = EasyTraderBridge()
lite_analysis_tasks: dict[str, dict[str, Any]] = {}
lite_insights_cache: dict[str, tuple[datetime, Any]] = {}
lite_realtime_quotes_cache: tuple[datetime, dict[str, dict[str, Any]]] | None = None
_quotes_loading: bool = False
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


class LiteDeepAnalysisLLM:
    """Deterministic chat adapter for Claude's deep-analysis framework in SaaS Lite."""

    def __init__(self, code: str, name: str):
        self.code = code
        self.name = name
        self.call_count = 0

    def chat(self, prompt: str) -> str:
        # The deep-analysis framework fires these prompts CONCURRENTLY (ThreadPoolExecutor),
        # so responses must be keyed by prompt content — never by call order.
        self.call_count += 1

        if "产业链" in prompt:
            return json.dumps(
                {
                    "chain": {
                        "upstream": [{"name": "上游原材料/核心零部件", "companies": ["行业供应商", "核心设备商"]}],
                        "midstream": [{"name": "公司所在主营环节", "companies": [self.name]}],
                        "downstream": [{"name": "下游客户/应用场景", "companies": ["产业客户", "终端渠道"]}],
                    },
                    "peers": [
                        {"code": "同业A", "name": "可比公司A", "pe": 28.0, "roe": 12.0},
                        {"code": "同业B", "name": "可比公司B", "pe": 32.0, "roe": 10.5},
                        {"code": "同业C", "name": "可比公司C", "pe": 24.0, "roe": 14.0},
                    ],
                    "position": "midstream",
                    "moat": "核心壁垒需要结合产品竞争力、客户结构、成本控制和行业周期验证；短中期更应关注订单、价格和资金行为是否共振。",
                },
                ensure_ascii=False,
            )
        if "打分" in prompt or "fundamental" in prompt:
            return json.dumps(
                {
                    "fundamental": 68,
                    "governance": 65,
                    "competitive": 70,
                    "growth": 72,
                    "valuation": 62,
                    "rationale": "质量评分采用 Lite 默认估计，重点用于补齐深度框架结构，最终判断仍叠加量化趋势和风险控制。",
                },
                ensure_ascii=False,
            )
        if "投资风险" in prompt:
            return json.dumps(
                [
                    {"risk": "趋势失效风险", "mitigation": "若跌破关键均线或量能明显萎缩，应降低仓位或等待重新放量确认。"},
                    {"risk": "事件兑现不及预期", "mitigation": "跟踪公告、业绩、订单和行业政策，避免只凭题材热度追高。"},
                    {"risk": "波动放大风险", "mitigation": "控制单票仓位，使用分批入场和明确止损线。"},
                ],
                ensure_ascii=False,
            )
        if "跟踪计划" in prompt:
            return json.dumps(
                {
                    "metrics": [
                        {"name": "趋势结构", "threshold": "短中期均线保持多头或回踩后重新放量"},
                        {"name": "量能确认", "threshold": "成交额和量比维持活跃，不能缩量冲高"},
                        {"name": "风险阈值", "threshold": "最大回撤和波动率不继续恶化"},
                    ],
                    "next_review": "下一个交易日收盘后复盘量价结构，遇重大公告时立即复盘。",
                },
                ensure_ascii=False,
            )
        if "综合评级" in prompt:
            return "持有"
        # macro and any other free-text prompt
        return f"{self.name}（{self.code}）的宏观分析应结合行业景气、政策方向、利率环境和资金偏好判断，当前结论以公开数据和量化信号为主。"


class LiteNoopCacheManager:
    def get_cached_daily(self, code: str) -> None:
        return None

    def cache_daily(self, code: str, df: Any, ttl: int | None = None) -> None:
        return None


class LiteFavoriteRequest(BaseModel):
    symbol: str | None = None
    stock_code: str | None = None
    stock_name: str | None = None
    market: str | None = "A股"
    tags: list[str] | None = None
    notes: str | None = None
    alert_price_high: float | None = None
    alert_price_low: float | None = None


class LitePaperOrderRequest(BaseModel):
    code: str
    side: str
    quantity: int
    analysis_id: str | None = None
    execution_mode: str = "paper"


def ensure_lite_favorites_table() -> None:
    with store.connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lite_favorites (
                username TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                stock_name TEXT NOT NULL,
                market TEXT NOT NULL,
                tags_json TEXT NOT NULL DEFAULT '[]',
                notes TEXT,
                added_price REAL,
                added_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (username, stock_code)
            )
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(lite_favorites)").fetchall()}
        if "added_price" not in columns:
            conn.execute("ALTER TABLE lite_favorites ADD COLUMN added_price REAL")
        if "alert_price_high" not in columns:
            conn.execute("ALTER TABLE lite_favorites ADD COLUMN alert_price_high REAL")
        if "alert_price_low" not in columns:
            conn.execute("ALTER TABLE lite_favorites ADD COLUMN alert_price_low REAL")
        conn.commit()


def ensure_lite_news_table() -> None:
    with store.connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lite_news_events (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT,
                source TEXT NOT NULL,
                source_type TEXT NOT NULL,
                event_type TEXT NOT NULL,
                sentiment TEXT NOT NULL,
                sentiment_score REAL NOT NULL,
                importance TEXT NOT NULL,
                catalyst_score REAL NOT NULL,
                symbols_json TEXT NOT NULL DEFAULT '[]',
                stock_names_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]',
                url TEXT,
                publish_time TEXT NOT NULL,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lite_news_publish_time ON lite_news_events(publish_time DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lite_news_source_type ON lite_news_events(source_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lite_news_sentiment ON lite_news_events(sentiment)")
        conn.commit()


def ensure_lite_cache_table() -> None:
    with store.connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lite_response_cache (
                cache_key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def ensure_lite_paper_tables() -> None:
    with store.connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lite_paper_accounts (
                username TEXT PRIMARY KEY,
                cash REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lite_paper_positions (
                username TEXT NOT NULL,
                code TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                avg_cost REAL NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (username, code)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lite_paper_orders (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                code TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                amount REAL NOT NULL,
                status TEXT NOT NULL,
                execution_mode TEXT NOT NULL,
                bridge_json TEXT,
                analysis_id TEXT,
                created_at TEXT NOT NULL,
                filled_at TEXT
            )
            """
        )
        conn.commit()


def ensure_lite_analysis_history_table() -> None:
    with store.connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lite_analysis_history (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                symbol TEXT NOT NULL,
                stock_name TEXT,
                market TEXT NOT NULL DEFAULT 'A股',
                overall_rating TEXT,
                score REAL,
                analyzed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_user ON lite_analysis_history(username, analyzed_at DESC)"
        )
        conn.commit()


def ensure_lite_report_fts_table() -> None:
    with store.connect() as conn:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS lite_report_fts USING fts5(
                report_id UNINDEXED,
                symbol,
                stock_name,
                rating,
                content,
                tokenize='unicode61'
            )
            """
        )
        conn.commit()


def _save_analysis_history(
    username: str,
    symbol: str,
    stock_name: str | None,
    market: str,
    overall_rating: str | None,
    score: float | None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    record_id = secrets.token_hex(8)
    try:
        ensure_lite_analysis_history_table()
        with store.connect() as conn:
            conn.execute(
                """INSERT INTO lite_analysis_history
                   (id, username, symbol, stock_name, market, overall_rating, score, analyzed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (record_id, username, symbol, stock_name, market, overall_rating, score, now),
            )
            conn.commit()
    except Exception:
        pass  # history write failure is non-fatal


def _index_report_fts(
    report_id: str,
    symbol: str,
    stock_name: str,
    rating: str,
    content: str,
) -> None:
    try:
        ensure_lite_report_fts_table()
        with store.connect() as conn:
            conn.execute(
                "DELETE FROM lite_report_fts WHERE report_id = ?", (report_id,)
            )
            conn.execute(
                "INSERT INTO lite_report_fts (report_id, symbol, stock_name, rating, content) VALUES (?, ?, ?, ?, ?)",
                (report_id, symbol, stock_name, rating, content),
            )
            conn.commit()
    except Exception:
        pass


def _search_reports_fts(query: str, limit: int = 20) -> list[dict]:
    try:
        ensure_lite_report_fts_table()
        like_pat = f"%{query}%"
        with store.connect() as conn:
            rows = conn.execute(
                """SELECT report_id, symbol, stock_name, rating
                   FROM lite_report_fts
                   WHERE symbol = ?
                      OR stock_name LIKE ?
                      OR content LIKE ?
                   LIMIT ?""",
                (query, like_pat, like_pat, limit),
            ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return []


def _check_and_record_price_alert(
    symbol: str,
    price: float,
    alert_high: float | None,
    alert_low: float | None,
) -> None:
    now_str = datetime.now(timezone.utc).isoformat()
    if alert_high and price >= alert_high:
        key = f"{symbol}:high"
        if key not in lite_price_alerts:
            lite_price_alerts[key] = {
                "symbol": symbol, "direction": "high", "threshold": alert_high,
                "price": price, "triggered_at": now_str,
            }
    if alert_low and price <= alert_low:
        key = f"{symbol}:low"
        if key not in lite_price_alerts:
            lite_price_alerts[key] = {
                "symbol": symbol, "direction": "low", "threshold": alert_low,
                "price": price, "triggered_at": now_str,
            }


try:
    ensure_lite_favorites_table()
    ensure_lite_news_table()
    ensure_lite_cache_table()
    ensure_lite_paper_tables()
    ensure_lite_analysis_history_table()
    ensure_lite_report_fts_table()
except Exception as _init_err:
    import warnings as _w
    _w.warn(f"SQLite DB init failed at startup: {_init_err}", RuntimeWarning, stacklevel=1)


@app.get("/")
async def root():
    return {
        "name": "TradingAgents SaaS Lite",
        "status": "running",
        "docs_url": "/docs",
    }


@app.get("/api/health")
async def health():
    return {"status": "healthy", "service": "saas-lite"}


def _cache_get(key: str, ttl_seconds: int) -> Any | None:
    cached = lite_insights_cache.get(key)
    if not cached:
        return None
    created_at, value = cached
    if datetime.now(timezone.utc) - created_at > timedelta(seconds=ttl_seconds):
        lite_insights_cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any) -> Any:
    lite_insights_cache[key] = (datetime.now(timezone.utc), value)
    return value


def _persistent_cache_get(key: str, ttl_seconds: int) -> Any | None:
    ensure_lite_cache_table()
    with store.connect() as conn:
        row = conn.execute(
            "SELECT payload_json, created_at FROM lite_response_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
    if not row:
        return None
    try:
        created_at = datetime.fromisoformat(row["created_at"])
    except ValueError:
        return None
    if datetime.now(timezone.utc) - created_at > timedelta(seconds=ttl_seconds):
        return None
    try:
        return json.loads(row["payload_json"])
    except json.JSONDecodeError:
        return None


def _persistent_cache_set(key: str, value: Any) -> Any:
    ensure_lite_cache_table()
    created_at = datetime.now(timezone.utc).isoformat()
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO lite_response_cache (cache_key, payload_json, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                payload_json = excluded.payload_json,
                created_at = excluded.created_at
            """,
            (key, json.dumps(value, ensure_ascii=False, default=str), created_at),
        )
        conn.commit()
    return value


def _persistent_cache_delete_prefix(prefix: str) -> None:
    ensure_lite_cache_table()
    with store.connect() as conn:
        conn.execute("DELETE FROM lite_response_cache WHERE cache_key LIKE ?", (f"{prefix}%",))
        conn.commit()


def _now_cn() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _stable_float(text: str, low: float, high: float) -> float:
    seed = sum((idx + 1) * ord(ch) for idx, ch in enumerate(text))
    ratio = (seed % 1000) / 1000
    return round(low + (high - low) * ratio, 2)


def _safe_number(value: Any) -> float | None:
    try:
        if value in ("", "-", None):
            return None
        number = float(value)
        if number != number:
            return None
        return number
    except (TypeError, ValueError):
        return None


def _market_quote_code(symbol: str) -> str:
    clean_symbol = str(symbol).strip().zfill(6)
    if clean_symbol.startswith(("6", "9")):
        return f"sh{clean_symbol}"
    if clean_symbol.startswith(("8", "4")):
        return f"bj{clean_symbol}"
    return f"sz{clean_symbol}"


def _parse_tencent_quote_time(value: str) -> str:
    if re.fullmatch(r"\d{14}", value or ""):
        return f"{value[0:4]}/{value[4:6]}/{value[6:8]} {value[8:10]}:{value[10:12]}:{value[12:14]}"
    return datetime.now().astimezone().strftime("%Y/%m/%d %H:%M:%S")


def _fetch_tencent_realtime_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    clean_symbols = [symbol for symbol in dict.fromkeys(symbols) if re.fullmatch(r"\d{6}", symbol)]
    if not clean_symbols:
        return {}

    snapshot: dict[str, dict[str, Any]] = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    for start in range(0, len(clean_symbols), 80):
        chunk = clean_symbols[start:start + 80]
        query = ",".join(_market_quote_code(symbol) for symbol in chunk)
        response = requests.get(f"https://qt.gtimg.cn/q={query}", headers=headers, timeout=10)
        response.encoding = "gbk"
        response.raise_for_status()
        for match in re.finditer(r'v_(?:sh|sz|bj)(\d{6})="([^"]*)"', response.text):
            symbol = match.group(1)
            fields = match.group(2).split("~")
            if len(fields) < 35:
                continue
            price = _safe_number(fields[3])
            pct = _safe_number(fields[32])
            prev_close = _safe_number(fields[4])
            volume_hands = _safe_number(fields[36]) if len(fields) > 36 else None
            amount_10k = _safe_number(fields[37]) if len(fields) > 37 else None
            snapshot[symbol] = {
                "symbol": symbol,
                "code": symbol,
                "name": fields[1].strip() or symbol,
                "price": price,
                "close": price,
                "current_price": price,
                "change_percent": pct,
                "pct_chg": pct,
                "change": _safe_number(fields[31]),
                "open": _safe_number(fields[5]),
                "high": _safe_number(fields[33]),
                "low": _safe_number(fields[34]),
                "prev_close": prev_close,
                "volume": volume_hands * 100 if volume_hands is not None else None,
                "amount": amount_10k * 10000 if amount_10k is not None else None,
                "turnover_rate": _safe_number(fields[38]) if len(fields) > 38 else None,
                "amplitude": _safe_number(fields[43]) if len(fields) > 43 else None,
                "pe": _safe_number(fields[39]) if len(fields) > 39 else None,
                "total_mv": _safe_number(fields[44]) if len(fields) > 44 else None,
                "circ_mv": _safe_number(fields[45]) if len(fields) > 45 else None,
                "updated_at": _parse_tencent_quote_time(fields[30]),
                "quote_source": "tencent.realtime",
            }
    return snapshot


def _load_realtime_quotes_snapshot(ttl_seconds: int = 3) -> dict[str, dict[str, Any]]:
    global lite_realtime_quotes_cache, _quotes_loading
    now = datetime.now(timezone.utc)
    if lite_realtime_quotes_cache:
        created_at, snapshot = lite_realtime_quotes_cache
        if now - created_at <= timedelta(seconds=ttl_seconds):
            return snapshot
    if _quotes_loading:
        return lite_realtime_quotes_cache[1] if lite_realtime_quotes_cache else {}
    _quotes_loading = True
    try:
        return _do_load_realtime_quotes_snapshot()
    finally:
        _quotes_loading = False


def _do_load_realtime_quotes_snapshot() -> dict[str, dict[str, Any]]:
    global lite_realtime_quotes_cache
    now = datetime.now(timezone.utc)

    import akshare as ak

    source_name = "akshare.stock_zh_a_spot"
    try:
        df = ak.stock_zh_a_spot()
    except Exception:
        source_name = "akshare.stock_zh_a_spot_em"
        df = ak.stock_zh_a_spot_em()

    snapshot: dict[str, dict[str, Any]] = {}
    updated_at = datetime.now().astimezone().strftime("%Y/%m/%d %H:%M:%S")
    for _, row in df.iterrows():
        raw_symbol = str(row.get("代码", "")).strip()
        symbol_match = re.search(r"(\d{6})$", raw_symbol)
        symbol = symbol_match.group(1) if symbol_match else raw_symbol.zfill(6)
        if not re.fullmatch(r"\d{6}", symbol):
            continue
        row_time = str(row.get("时间戳", "")).strip()
        quote_updated_at = f"{datetime.now().astimezone().strftime('%Y/%m/%d')} {row_time}" if row_time else updated_at
        price = _safe_number(row.get("最新价"))
        pct = _safe_number(row.get("涨跌幅"))
        prev_close = _safe_number(row.get("昨收"))
        snapshot[symbol] = {
            "symbol": symbol,
            "code": symbol,
            "name": str(row.get("名称", "")).strip() or symbol,
            "price": price,
            "close": price,
            "current_price": price,
            "change_percent": pct,
            "pct_chg": pct,
            "change": _safe_number(row.get("涨跌额")),
            "open": _safe_number(row.get("今开")),
            "high": _safe_number(row.get("最高")),
            "low": _safe_number(row.get("最低")),
            "prev_close": prev_close,
            "volume": _safe_number(row.get("成交量")),
            "amount": _safe_number(row.get("成交额")),
            "turnover_rate": _safe_number(row.get("换手率")),
            "amplitude": _safe_number(row.get("振幅")),
            "volume_ratio": _safe_number(row.get("量比")),
            "pe": _safe_number(row.get("市盈率-动态")),
            "pb": _safe_number(row.get("市净率")),
            "total_mv": _safe_number(row.get("总市值")),
            "circ_mv": _safe_number(row.get("流通市值")),
            "updated_at": quote_updated_at,
            "quote_source": source_name,
        }
    lite_realtime_quotes_cache = (now, snapshot)
    return snapshot


async def _realtime_quotes(symbols: list[str] | set[str] | tuple[str, ...]) -> dict[str, dict[str, Any]]:
    clean_symbols = [str(symbol).strip().zfill(6) for symbol in symbols if re.fullmatch(r"\d{6}", str(symbol).strip().zfill(6))]
    if not clean_symbols:
        return {}
    quotes: dict[str, dict[str, Any]] = {}
    try:
        quotes = await asyncio.to_thread(_fetch_tencent_realtime_quotes, clean_symbols)
    except Exception:
        quotes = {}
    missing_symbols = [symbol for symbol in clean_symbols if symbol not in quotes]
    if missing_symbols:
        try:
            snapshot = await asyncio.to_thread(_load_realtime_quotes_snapshot)
            quotes.update({symbol: snapshot[symbol] for symbol in missing_symbols if symbol in snapshot})
        except Exception:
            pass
    return quotes


def _apply_realtime_quote(item: dict[str, Any], quote: dict[str, Any] | None) -> dict[str, Any]:
    if not quote:
        return item
    price = quote.get("price") if quote.get("price") is not None else quote.get("close")
    pct = quote.get("change_percent") if quote.get("change_percent") is not None else quote.get("pct_chg")
    if price is not None:
        item["close"] = price
        item["price"] = price
        item["current_price"] = price
    if pct is not None:
        item["pct_chg"] = pct
        item["change_percent"] = pct
    for key in ("name", "volume", "amount", "turnover_rate", "amplitude", "volume_ratio", "open", "high", "low", "prev_close", "updated_at", "quote_source"):
        if quote.get(key) is not None:
            item[key] = quote[key]
    if quote.get("name"):
        item["stock_name"] = quote["name"]
    return item


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

    positive_words = ["利好", "增长", "预增", "扭亏", "中标", "订单", "回购", "增持", "获批", "突破", "买入", "推荐", "上调", "创新高"]
    negative_words = ["利空", "下滑", "预减", "亏损", "减持", "处罚", "立案", "问询", "诉讼", "退市", "终止", "下调", "风险"]
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
    "股东大会", "董事会决议", "监事会决议", "章程", "修订", "聘任", "辞职",
    "变更会计师", "担保进展", "诉讼进展", "上市公告书",
)

HOT_NEWS_STRONG_ANNOUNCEMENT_KEYWORDS = (
    "重大资产重组", "发行股份购买资产", "控制权变更", "中标", "合同", "订单",
    "增持", "回购股份", "股份回购", "同意注册", "审核通过", "收购", "资产注入",
)


def _event_relevance_score(event: dict[str, Any]) -> float:
    title = str(event.get("title") or "")
    content = str(event.get("content") or "")
    text = f"{title} {content}"
    symbols = event.get("symbols") or []
    score = 0.0
    if symbols:
        score += 2.6
    if event.get("source_type") in {"announcement", "research"}:
        score += 1.4
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
    if any(word in text for word in HOT_NEWS_ROUTINE_ANNOUNCEMENT_KEYWORDS) and not any(word in text for word in HOT_NEWS_STRONG_ANNOUNCEMENT_KEYWORDS):
        return False
    if event.get("symbols"):
        return True
    if any(word in text for word in HOT_NEWS_NOISE_KEYWORDS):
        return False
    has_market_scope = any(word in text for word in ("A股", "沪指", "深成指", "创业板指", "北交所"))
    has_intraday_sector_move = (
        any(word in text for word in ("板块", "概念", "盘中"))
        and any(word in text for word in ("涨停", "涨超", "走高", "活跃", "爆发", "震荡走强"))
    )
    if not (has_market_scope or has_intraday_sector_move):
        return False
    return any(word in text for word in HOT_NEWS_RELEVANT_KEYWORDS)


def _is_secondary_hot_event(event: dict[str, Any]) -> bool:
    title = str(event.get("title") or "")
    content = str(event.get("content") or "")
    text = f"{title} {content}"
    if not title:
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
        events.append(_build_event(
            title=summary[:90],
            content=summary,
            source="财新",
            source_type="news",
            publish_time=_now_cn(),
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
        collect("财新市场新闻", lambda: _fetch_caixin_market_news(lookup, limit=60)),
        collect("东方财富公告", lambda: _fetch_eastmoney_announcements(lookup, days=2, limit=80)),
        collect("东方财富研报", lambda: _fetch_eastmoney_research(lookup, watch_symbols[:8], limit_per_symbol=4)),
    )
    saved = _store_news_events(all_events[:limit])
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
    "description": "系统自动优先筛短中期进攻型股票，要求趋势、动量、入场触发和实时涨跌幅同时达标，剔除低波慢股和缺少爆发信号的防守型股票。",
    "weights": {
        "quant": 0.20,
        "trend": 0.27,
        "momentum": 0.24,
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


async def _enrich_smart_pool_industries(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    industries = await asyncio.gather(
        *[
            _resolve_real_industry(item["symbol"], item.get("name") or item["symbol"], set(item.get("event_labels") or []))
            for item in items
        ],
        return_exceptions=True,
    )
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


async def _smart_pool_quant(symbol: str) -> dict[str, Any]:
    return await asyncio.to_thread(lambda target=symbol: asdict(lite_quant_engine.analyze(target)))


async def _enrich_smart_pool_realtime(response: dict[str, Any]) -> dict[str, Any]:
    data = dict(response.get("data") or {})
    items = [dict(item) for item in data.get("items") or []]
    quotes = await _realtime_quotes([item.get("symbol") or item.get("code") for item in items])
    quote_updated_at = None
    for item in items:
        symbol = str(item.get("symbol") or item.get("code") or "").zfill(6)
        quote = quotes.get(symbol)
        _apply_realtime_quote(item, quote)
        if quote and quote.get("change_percent") is not None and item.get("reasons"):
            item["reasons"] = [
                f"实时涨跌幅 {quote['change_percent']:+.2f}%" if str(reason).startswith("实时涨跌幅 ") else reason
                for reason in item["reasons"]
            ]
        if quote and quote.get("updated_at"):
            quote_updated_at = quote["updated_at"]
    if quote_updated_at:
        data["updated_at"] = quote_updated_at
        data["quote_updated_at"] = quote_updated_at
        data["price_source"] = "实时行情"
    data["items"] = items
    enriched = dict(response)
    enriched["data"] = data
    return enriched


@app.get("/api/lite/smart-pool")
async def lite_smart_pool(strategy: str = "balanced", limit: int = 30, universe_limit: int = 500):
    preset = SMART_POOL_RECOMMENDER
    safe_limit = max(5, min(limit, 50))
    safe_universe = max(safe_limit * 2, min(universe_limit, 5000))

    # Keep the stock-screening smart pool aligned with Quant Center's one-click recommendation.
    try:
        def to_float(value: Any, default: float = 0.0) -> float:
            try:
                if value in (None, "", "-"):
                    return default
                return float(value)
            except (TypeError, ValueError):
                return default

        quant_pool = await asyncio.to_thread(
            lite_quant_engine.smart_pool,
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
            if score < 80:
                continue
            close = to_float(raw.get("close"), 0)
            pct_chg = to_float(raw.get("pct_chg"), 0)
            amount = to_float(raw.get("amount"), 0)
            reasons = [str(item) for item in (raw.get("reasons") or []) if item]
            if amount > 0 and not any("成交" in reason for reason in reasons):
                reasons.insert(1, f"成交额 {amount / 100000000:.2f} 亿")
            item = {
                "symbol": symbol,
                "code": symbol,
                "name": raw.get("name") or symbol,
                "market": raw.get("market") or "A股",
                "industry": raw.get("industry") or raw.get("market") or "",
                "close": close,
                "pct_chg": pct_chg,
                "amount": amount,
                "smart_score": score,
                "raw_score": score,
                "score": score,
                "quant_score": score,
                "trigger_score": to_float(factors.get("trend"), 0),
                "catalyst_score": to_float(raw.get("catalyst_score"), 0),
                "risk_score": to_float(factors.get("risk_control"), 0),
                "liquidity_score": to_float(factors.get("liquidity"), 0),
                "grade": "核心候选" if score >= 90 else "高质量候选" if score >= 85 else "重点观察",
                "signal": raw.get("signal") or "",
                "reasons": reasons[:8],
                "latest_events": raw.get("latest_events") or [],
                "forecast": raw.get("forecast") or {},
                "patterns": raw.get("patterns") or [],
            }
            items.append(item)

        items.sort(key=lambda item: float(item.get("smart_score") or 0), reverse=True)
        items = await _enrich_smart_pool_industries(items[:safe_limit])
        return {
            "strategy": "quant_center_smart_pool",
            "preset": {
                "name": "全市场综合优选",
                "description": "复用量化中心一键智能推荐模型，横向比较量化分、趋势、动量、流动性、实时强度和风险控制后生成候选股票池。",
            },
            "updated_at": datetime.now().strftime("%Y/%m/%d %H:%M:%S"),
            "universe_size": quant_pool.get("universe_size") or len(items),
            "analyzed": quant_pool.get("analyzed") or len(items),
            "items": items,
            "source_note": "同源于量化中心智能推荐，仅展示综合评分 80 分以上候选；研究与模拟使用，不构成投资建议。",
        }
    except Exception as exc:
        print(f"Quant Center smart pool failed, falling back to lite smart pool: {exc}")

    cache_key = f"smart-pool:attack-v15-multifactor-ranked80-industry:{safe_limit}"
    cached = _cache_get(cache_key, 900)
    if cached:
        return await _enrich_smart_pool_realtime(cached)
    persistent_cached = _persistent_cache_get(cache_key, 3600)
    if persistent_cached:
        _cache_set(cache_key, persistent_cached)
        return await _enrich_smart_pool_realtime(persistent_cached)

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
    try:
        realtime_snapshot = await asyncio.to_thread(_load_realtime_quotes_snapshot, 10)
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
    candidates = list(candidates_by_symbol.values())[:620]
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

    analyzed = await asyncio.gather(*(analyze_candidate(candidate) for candidate in candidates))
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
        "universe_size": len(candidates),
        "items": items,
        "source_note": "智能股票池由真实新闻/公告/研报事件、A股基础股票池、量化因子和入场触发器综合生成；仅供研究，不承诺收益。",
    }
    response = {"success": True, "data": data, "message": "ok"}
    _persistent_cache_set(cache_key, response)
    _cache_set(cache_key, response)
    return await _enrich_smart_pool_realtime(response)


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
        key=lambda event: _event_relevance_score(event),
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
    if len(events) < safe_limit:
        selected_titles = {re.sub(r"\s+", "", str(event.get("title") or "")) for event in events}
        for item in _lite_news_items():
            normalized_title = re.sub(r"\s+", "", str(item.get("title") or ""))
            if normalized_title and normalized_title not in selected_titles:
                selected_titles.add(normalized_title)
                events.append(item)
            if len(events) >= safe_limit:
                break
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
        items = _lite_news_items()[: max(1, min(limit, 100))]
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
        "source_note": "真实源：东方财富公告、东方财富研报、财新市场新闻；不可用源不会用模拟数据冒充。",
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
        items = await _enrich_smart_pool_industries(items)
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
    await ensure_recent_lite_news()
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
    try:
        quant_pool = await asyncio.to_thread(lite_quant_engine.smart_pool, limit=max(20, safe_limit * 2), universe_limit=5000)
        quant_candidates = quant_pool.get("items") or []
    except Exception:
        quant_candidates = []
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
            "source_note": "利好监控优先使用量化中心同源智能推荐股票池，再叠加真实公告、研报和新闻事件权重。",
        }
        response = {"success": True, "data": data, "message": "ok"}
        _cache_set(cache_key, response)
        return await _enrich_catalysts_realtime(response)

    items = []
    for symbol, data in grouped.items():
        try:
            quant = asdict(lite_quant_engine.analyze(symbol))
        except Exception:
            quant = {}
        latest = quant.get("latest") or {}
        score = float(quant.get("score") or 0)
        pct = float(latest.get("pct_change") or _stable_float(symbol + "pct", -4, 6))
        events_for_symbol = data["events"][:3]
        hot_score = round(data["hot_score"], 2)
        items.append({
            "symbol": symbol,
            "name": data["name"],
            "score": round(score, 1),
            "signal": quant.get("signal") or "watch",
            "mentions": data["mentions"],
            "hot_score": hot_score,
            "sentiment": round(data["sentiment"] / max(1, data["mentions"]), 2),
            "change_percent": round(pct, 2),
            "price": round(float(latest.get("close") or latest.get("price") or _stable_float(symbol + "price", 5, 450)), 2),
            "risk_level": _risk_level(float((quant.get("risk") or {}).get("volatility") or 0), float((quant.get("risk") or {}).get("max_drawdown") or 0)),
            "sparkline": _sparkline(symbol, pct),
            "reasons": [EVENT_TYPE_LABELS.get(event.get("event_type", ""), event.get("event_type", "事件")) for event in events_for_symbol] or ["真实事件进入观察池"],
            "latest_titles": [event["title"] for event in events_for_symbol],
            "updated_at": _now_cn(),
        })
    if not items:
        items = await asyncio.to_thread(_build_catalyst_items, safe_limit)
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
    try:
        realtime_quotes = {}
        if e >= today:
            try:
                realtime_quotes = await asyncio.to_thread(_load_realtime_quotes_snapshot, 60)
            except Exception:
                realtime_quotes = {}
        data = await asyncio.to_thread(compute_market_sentiment, s, e, 24, realtime_quotes)
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "data": None, "message": str(exc)}


@app.get("/api/lite/limit-up")
async def lite_limit_up_distribution(date: str | None = None):
    """涨停热点分布：单日连板梯队 × 概念板块矩阵（基于本地日线）。"""
    from quantcore.quant.limit_up import compute_limit_up_distribution
    target = date or datetime.now().strftime("%Y-%m-%d")
    try:
        data = await asyncio.to_thread(compute_limit_up_distribution, target)
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "data": None, "message": str(exc)}


@app.get("/api/system/config/validate")
async def validate_config():
    return {
        "success": True,
        "data": {
            "valid": True,
            "mode": "saas-lite",
            "storage": "sqlite",
            "warnings": ["SaaS Lite 使用本地 SQLite，不启用 MongoDB/Redis 队列。"],
        },
        "message": "saas-lite mode",
    }


@app.get("/api/config/settings")
async def config_settings():
    return {
        "success": True,
        "data": {
            "quick_analysis_model": "qwen-turbo",
            "deep_analysis_model": "qwen-max",
            "default_market": "A股",
            "default_depth": 3,
            "runtime_mode": "saas-lite",
        },
        "message": "SaaS Lite 默认配置",
    }


@app.put("/api/config/settings")
async def update_config_settings(settings: dict[str, Any]):
    return {"success": True, "data": {"message": "SaaS Lite 已接收配置", "settings": settings}, "message": "ok"}


@app.get("/api/config/llm")
async def config_llm():
    return {
        "success": True,
        "data": [
            {
                "provider": "qwen",
                "model_name": "qwen-turbo",
                "model_display_name": "qwen-turbo",
                "max_tokens": 4096,
                "temperature": 0.7,
                "timeout": 60,
                "retry_times": 2,
                "enabled": True,
                "description": "SaaS Lite 默认快速分析模型",
                "capability_level": 2,
                "suitable_roles": ["quick_analysis", "both"],
                "features": ["fast_response", "cost_effective"],
                "recommended_depths": ["快速", "基础", "标准"],
                "performance_metrics": {"speed": 5, "cost": 5, "quality": 3},
            },
            {
                "provider": "qwen",
                "model_name": "qwen-max",
                "model_display_name": "qwen-max",
                "max_tokens": 8192,
                "temperature": 0.5,
                "timeout": 120,
                "retry_times": 2,
                "enabled": True,
                "description": "SaaS Lite 默认深度分析模型",
                "capability_level": 4,
                "suitable_roles": ["deep_analysis", "both"],
                "features": ["reasoning", "long_context"],
                "recommended_depths": ["标准", "深度", "全面"],
                "performance_metrics": {"speed": 3, "cost": 3, "quality": 4},
            },
        ],
        "message": "SaaS Lite 默认模型列表",
    }


@app.post("/api/model-capabilities/recommend")
async def recommend_models(payload: dict[str, Any]):
    depth = payload.get("research_depth") or "标准"
    return {
        "success": True,
        "data": {
            "research_depth": depth,
            "quick_analysis_model": "qwen-turbo",
            "deep_analysis_model": "qwen-max",
            "recommendations": {
                "quick_model": "qwen-turbo",
                "deep_model": "qwen-max",
            },
            "reason": "SaaS Lite 使用本地默认模型配置，重点保证页面流程可运行。",
        },
        "message": "ok",
    }


@app.get("/api/notifications/unread_count")
async def unread_count():
    return {"success": True, "data": {"count": 0}, "message": "ok"}


@app.get("/api/notifications")
async def notifications():
    return {"success": True, "data": {"items": [], "total": 0}, "message": "ok"}


@app.post("/api/notifications/{notification_id}/read")
async def read_notification(notification_id: str):
    return {"success": True, "data": {"id": notification_id}, "message": "ok"}


@app.post("/api/notifications/read_all")
async def read_all_notifications():
    return {"success": True, "data": None, "message": "ok"}


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


def _paper_account_row(username: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM lite_paper_accounts WHERE username = ?", (username,)).fetchone()
        if not row:
            conn.execute(
                """
                INSERT INTO lite_paper_accounts (username, cash, realized_pnl, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (username, 1_000_000.0, 0.0, now, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM lite_paper_accounts WHERE username = ?", (username,)).fetchone()
    return dict(row)


async def _paper_quote_price(code: str) -> float:
    quotes = await _realtime_quotes([code])
    quote = quotes.get(code) or {}
    price = quote.get("price") or quote.get("close") or quote.get("current_price")
    if price is None:
        raise ValueError(f"无法获取 {code} 的实时价格，暂不能模拟成交")
    price_float = float(price)
    if price_float <= 0:
        raise ValueError(f"{code} 的实时价格无效，暂不能模拟成交")
    return round(price_float, 3)


async def _paper_positions(username: str) -> list[dict[str, Any]]:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT code, quantity, avg_cost, updated_at FROM lite_paper_positions WHERE username = ? ORDER BY updated_at DESC",
            (username,),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            last_price = await _paper_quote_price(item["code"])
        except Exception:
            last_price = None
        qty = int(item["quantity"])
        avg_cost = float(item["avg_cost"])
        market_value = round((last_price or avg_cost) * qty, 2)
        item.update(
            {
                "market": "CN",
                "currency": "CNY",
                "available_qty": qty,
                "last_price": last_price,
                "market_value": market_value,
                "unrealized_pnl": None if last_price is None else round((last_price - avg_cost) * qty, 2),
            }
        )
        items.append(item)
    return items


async def _paper_account_summary(username: str) -> dict[str, Any]:
    account = _paper_account_row(username)
    positions = await _paper_positions(username)
    positions_value = round(sum(float(item.get("market_value") or 0.0) for item in positions), 2)
    cash = round(float(account["cash"]), 2)
    return {
        "cash": {"CNY": cash},
        "positions_value": {"CNY": positions_value},
        "equity": {"CNY": round(cash + positions_value, 2)},
        "realized_pnl": {"CNY": round(float(account["realized_pnl"]), 2)},
        "updated_at": account["updated_at"],
    }


@app.get("/api/paper/account")
async def paper_account(user: dict[str, Any] = Depends(get_current_lite_user)):
    username = user["username"]
    return {
        "success": True,
        "data": {
            "account": await _paper_account_summary(username),
            "positions": await _paper_positions(username),
        },
        "message": "SaaS Lite paper account",
    }


@app.get("/api/paper/positions")
async def paper_positions(user: dict[str, Any] = Depends(get_current_lite_user)):
    return {"success": True, "data": {"items": await _paper_positions(user["username"])}, "message": "ok"}


@app.get("/api/paper/orders")
async def paper_orders(limit: int = 50, user: dict[str, Any] = Depends(get_current_lite_user)):
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT id, code, side, quantity, price, amount, status, execution_mode,
                   bridge_json, analysis_id, created_at, filled_at
            FROM lite_paper_orders
            WHERE username = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user["username"], max(1, min(int(limit), 200))),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["market"] = "CN"
        item["currency"] = "CNY"
        if item.get("bridge_json"):
            try:
                item["bridge"] = json.loads(item["bridge_json"])
            except json.JSONDecodeError:
                item["bridge"] = None
        item.pop("bridge_json", None)
        items.append(item)
    return {"success": True, "data": {"items": items, "limit": limit}, "message": "ok"}


@app.get("/api/paper/trader/status")
async def paper_trader_status(user: dict[str, Any] = Depends(get_current_lite_user)):
    return {"success": True, "data": asdict(lite_trader_bridge.status()), "message": "ok"}


@app.post("/api/paper/order")
async def paper_order(payload: LitePaperOrderRequest, user: dict[str, Any] = Depends(get_current_lite_user)):
    username = user["username"]
    code = payload.code.strip().upper()
    if not re.fullmatch(r"\d{6}", code):
        return {"success": False, "data": None, "message": "SaaS Lite 当前模拟交易先支持 A 股 6 位代码", "code": 400}
    side = payload.side.lower()
    if side not in {"buy", "sell"}:
        return {"success": False, "data": None, "message": "交易方向只能是 buy 或 sell", "code": 400}
    qty = int(payload.quantity)
    if qty <= 0:
        return {"success": False, "data": None, "message": "数量必须大于 0", "code": 400}

    try:
        price = await _paper_quote_price(code)
    except Exception as exc:
        return {"success": False, "data": None, "message": str(exc), "code": 400}
    order = EasyTraderOrder(code=code, side=side, quantity=qty, price=price, market="CN")
    bridge_intent = lite_trader_bridge.build_order_intent(order)
    issues = bridge_intent["risk_checks"]["issues"]
    if issues:
        return {"success": False, "data": {"issues": issues}, "message": "风控检查未通过", "code": 400}

    now = datetime.now(timezone.utc).isoformat()
    order_id = "paper_" + secrets.token_hex(8)
    amount = round(price * qty, 2)
    account = _paper_account_row(username)

    with store.connect() as conn:
        pos = conn.execute(
            "SELECT * FROM lite_paper_positions WHERE username = ? AND code = ?",
            (username, code),
        ).fetchone()
        if side == "buy":
            cash = float(account["cash"])
            if cash < amount:
                return {"success": False, "data": None, "message": f"可用资金不足：需要 {amount:.2f}，当前 {cash:.2f}", "code": 400}
            conn.execute(
                "UPDATE lite_paper_accounts SET cash = ?, updated_at = ? WHERE username = ?",
                (round(cash - amount, 2), now, username),
            )
            if pos:
                old_qty = int(pos["quantity"])
                old_cost = float(pos["avg_cost"])
                new_qty = old_qty + qty
                new_avg = round((old_cost * old_qty + price * qty) / new_qty, 4)
                conn.execute(
                    "UPDATE lite_paper_positions SET quantity = ?, avg_cost = ?, updated_at = ? WHERE username = ? AND code = ?",
                    (new_qty, new_avg, now, username, code),
                )
            else:
                conn.execute(
                    "INSERT INTO lite_paper_positions (username, code, quantity, avg_cost, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (username, code, qty, price, now),
                )
        else:
            if not pos or int(pos["quantity"]) < qty:
                return {"success": False, "data": None, "message": "可卖持仓不足", "code": 400}
            old_qty = int(pos["quantity"])
            avg_cost = float(pos["avg_cost"])
            new_qty = old_qty - qty
            pnl = round((price - avg_cost) * qty, 2)
            conn.execute(
                "UPDATE lite_paper_accounts SET cash = cash + ?, realized_pnl = realized_pnl + ?, updated_at = ? WHERE username = ?",
                (amount, pnl, now, username),
            )
            if new_qty == 0:
                conn.execute("DELETE FROM lite_paper_positions WHERE username = ? AND code = ?", (username, code))
            else:
                conn.execute(
                    "UPDATE lite_paper_positions SET quantity = ?, updated_at = ? WHERE username = ? AND code = ?",
                    (new_qty, now, username, code),
                )
        order_doc = {
            "id": order_id,
            "code": code,
            "side": side,
            "quantity": qty,
            "price": price,
            "amount": amount,
            "status": "filled",
            "execution_mode": "paper",
            "bridge": bridge_intent,
            "analysis_id": payload.analysis_id,
            "created_at": now,
            "filled_at": now,
            "market": "CN",
            "currency": "CNY",
        }
        conn.execute(
            """
            INSERT INTO lite_paper_orders (
                id, username, code, side, quantity, price, amount, status,
                execution_mode, bridge_json, analysis_id, created_at, filled_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                username,
                code,
                side,
                qty,
                price,
                amount,
                "filled",
                "paper",
                json.dumps(bridge_intent, ensure_ascii=False),
                payload.analysis_id,
                now,
                now,
            ),
        )
        conn.commit()

    return {"success": True, "data": {"order": order_doc}, "message": "模拟成交成功，实盘交易桥未自动执行"}


@app.post("/api/paper/reset")
async def paper_reset(confirm: bool = False, user: dict[str, Any] = Depends(get_current_lite_user)):
    if not confirm:
        return {"success": False, "data": None, "message": "请设置 confirm=true 以确认重置", "code": 400}
    username = user["username"]
    with store.connect() as conn:
        conn.execute("DELETE FROM lite_paper_accounts WHERE username = ?", (username,))
        conn.execute("DELETE FROM lite_paper_positions WHERE username = ?", (username,))
        conn.execute("DELETE FROM lite_paper_orders WHERE username = ?", (username,))
        conn.commit()
    _paper_account_row(username)
    return {"success": True, "data": {"message": "reset", "cash": 1000000.0, "confirm": confirm}, "message": "ok"}


@app.get("/api/analysis/user/history")
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


@app.get("/api/analysis/tasks")
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


@app.post("/api/analysis/single")
async def single_analysis(req: LiteSingleAnalysisRequest, user: dict[str, Any] = Depends(get_current_lite_user)):
    raw_symbol = (req.symbol or req.stock_code or "").strip()
    if not raw_symbol:
        return {"success": False, "data": None, "message": "请输入股票代码", "code": 400}

    task_id = "lite_" + secrets.token_hex(8)
    now = datetime.now(timezone.utc).isoformat()
    symbol = raw_symbol  # ensure always bound even if try block raises before assignment

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
        status = "completed"
        error_message = None
        progress = 100
        current_step = "SaaS Lite 量化分析已完成"
        _save_analysis_history(
            username=user["username"],
            symbol=symbol,
            stock_name=stock_meta.get("name") if stock_meta else None,
            market=(req.parameters or {}).get("market_type", "A股"),
            overall_rating=result.get("overall_rating") if result else None,
            score=result.get("quant_score") if result else None,
        )
        if result:
            report_content = " ".join([
                result.get("macro", ""),
                result.get("moat", ""),
                str(result.get("overall_rating", "")),
            ])
            _index_report_fts(
                report_id=task_id,
                symbol=symbol,
                stock_name=stock_meta.get("name", symbol) if stock_meta else symbol,
                rating=result.get("overall_rating", ""),
                content=report_content,
            )
    except Exception as exc:
        result = None
        status = "failed"
        error_message = str(exc)
        progress = 100
        current_step = "SaaS Lite 量化分析失败"

    lite_analysis_tasks[task_id] = {
        "task_id": task_id,
        "analysis_id": task_id,
        "symbol": symbol,
        "stock_symbol": symbol,
        "status": status,
        "progress": progress,
        "progress_percentage": progress,
        "current_step": current_step,
        "message": current_step,
        "error_message": error_message,
        "result_data": result,
        "created_at": now,
        "updated_at": now,
    }

    return {
        "success": True,
        "data": {"task_id": task_id, "analysis_id": task_id, "status": status},
        "message": "SaaS Lite 分析任务已完成" if status == "completed" else "SaaS Lite 分析任务失败",
    }


@app.post("/api/analysis/batch")
async def batch_analysis(req: LiteBatchAnalysisRequest):
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


@app.get("/api/analysis/tasks/{task_id}/status")
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


@app.get("/api/analysis/tasks/{task_id}/result")
async def analysis_task_result(task_id: str):
    task = lite_analysis_tasks.get(task_id)
    if not task:
        return {"success": False, "data": None, "message": "任务不存在", "code": 404}
    return {"success": True, "data": task.get("result_data"), "message": "ok"}


async def get_stock_pool_items(limit: int = 6000) -> list[dict[str, Any]]:
    pool = await lite_quant_engine.stock_pool(limit=limit)
    items = pool.get("items") or []
    return [item for item in items if item.get("symbol")]


async def resolve_stock(query: str, market: str | None = "A股") -> dict[str, Any] | None:
    clean = str(query or "").strip()
    if not clean:
        return None
    clean_lower = clean.lower()
    items = await get_stock_pool_items()

    for item in items:
        if str(item.get("symbol", "")).lower() == clean_lower:
            return {"symbol": item["symbol"], "name": item.get("name") or item["symbol"], "market": item.get("market") or market or "A股"}

    for item in items:
        if str(item.get("name", "")).lower() == clean_lower:
            return {"symbol": item["symbol"], "name": item.get("name") or item["symbol"], "market": item.get("market") or market or "A股"}

    for item in items:
        name = str(item.get("name", "")).lower()
        symbol = str(item.get("symbol", "")).lower()
        if clean_lower in name or clean_lower in symbol:
            return {"symbol": item["symbol"], "name": item.get("name") or item["symbol"], "market": item.get("market") or market or "A股"}

    return None


@app.get("/api/stock-data/basic-info/{query}")
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


@app.get("/api/stocks/{symbol}/quote")
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


@app.get("/api/analysis/search")
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


def _fmt_score(value: Any) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "0.0"


def _score_profile(score: float) -> dict[str, str]:
    if score >= 85:
        return {
            "grade": "A",
            "label": "高确定性强势",
            "stance": "趋势质量和因子共振较强，适合纳入核心候选，但仍要防止高位拥挤后的快速回撤。",
            "bias": "强势优先",
        }
    if score >= 78:
        return {
            "grade": "A-",
            "label": "强趋势候选",
            "stance": "分数已经进入强势区间，核心问题不是能不能买，而是买点、仓位和止损是否有纪律。",
            "bias": "积极但不追满",
        }
    if score >= 72:
        return {
            "grade": "B+",
            "label": "中高胜率候选",
            "stance": "趋势和动量有优势，但尚未达到无脑强势，适合等待回踩确认或突破放量后分批参与。",
            "bias": "偏积极",
        }
    if score >= 65:
        return {
            "grade": "B",
            "label": "机会型观察",
            "stance": "有可交易信号，但确定性来自局部因子，若风控或RSI拖累，需要降低仓位预期。",
            "bias": "谨慎参与",
        }
    if score >= 58:
        return {
            "grade": "C+",
            "label": "结构分歧",
            "stance": "部分指标支持交易，整体胜率一般，适合观察或极小仓试错，不适合当作主线品种。",
            "bias": "观察优先",
        }
    if score >= 50:
        return {
            "grade": "C",
            "label": "中性偏弱",
            "stance": "缺少足够的趋势或资金确认，当前更适合等待下一轮数据改善。",
            "bias": "暂不主动",
        }
    return {
        "grade": "D",
        "label": "弱势回避",
        "stance": "主要因子不足，除非有明确基本面催化或极强反转信号，否则不建议参与。",
        "bias": "回避",
    }


def _rsi_profile(rsi: float) -> str:
    if rsi >= 85:
        return "RSI处于极高位，短线筹码明显拥挤，追涨的回撤代价偏高。"
    if rsi >= 70:
        return "RSI偏高，说明买盘强但短线已不便宜，更适合等回踩或盘中分歧。"
    if rsi >= 55:
        return "RSI处于偏强区间，动量仍在，但没有明显过热。"
    if rsi >= 45:
        return "RSI中性，价格方向更多依赖趋势延续和成交确认。"
    if rsi >= 30:
        return "RSI偏弱，短线反弹可能存在，但主动买入胜率需要趋势配合。"
    return "RSI低位，存在技术修复空间，但也说明近期承压明显，不能只按低位反弹处理。"


def _risk_level(volatility: float, max_drawdown: float) -> str:
    if volatility >= 0.45 or max_drawdown <= -0.30:
        return "高"
    if volatility >= 0.25 or max_drawdown <= -0.18:
        return "中"
    return "低"


def _risk_profile(risk_level: str, volatility: float, max_drawdown: float, sharpe: float) -> str:
    parts = []
    if risk_level == "高":
        parts.append("风险等级为高，说明它不是稳健低波动品种，仓位和止损比方向判断更重要")
    elif risk_level == "中":
        parts.append("风险等级为中，波动可接受但仍需要避免单次重仓")
    else:
        parts.append("风险等级为低，价格波动相对可控")

    if max_drawdown <= -0.35:
        parts.append("历史最大回撤很深，若买点追高，账户体验会明显恶化")
    elif max_drawdown <= -0.25:
        parts.append("最大回撤偏大，适合用分批和移动止损控制风险")
    else:
        parts.append("最大回撤压力相对温和")

    if sharpe >= 2.5:
        parts.append("夏普较高，说明单位波动带来的收益效率较好")
    elif sharpe >= 1.2:
        parts.append("夏普处于可用区间，但不是无风险信号")
    else:
        parts.append("夏普偏低，波动没有被收益充分补偿")
    return "；".join(parts) + "。"


def _trade_plan(score: float, signal: str, risk_level: str, rsi: float) -> dict[str, str]:
    normalized_signal = str(signal or "").lower()
    if score >= 78 or normalized_signal == "strong_buy":
        if risk_level == "高" or rsi >= 70:
            return {
                "action": "强势跟踪，等待回踩或放量突破确认",
                "position": "单票初始仓位建议 10%-15%，确认后再加到 20% 以内",
                "stop": "跌破短期关键均线或回撤超过 7%-10% 应减仓，不建议满仓硬扛",
            }
        return {
            "action": "可作为重点候选，分批建仓",
            "position": "初始仓位 15%-20%，走势确认后再提高",
            "stop": "用最近一轮震荡低点或 6%-8% 回撤作为失效线",
        }
    if score >= 72:
        return {
            "action": "偏积极，但买点要挑剔",
            "position": "初始仓位 8%-12%，突破或回踩承接确认后再加仓",
            "stop": "若放量跌破近期支撑，先降仓而不是补仓",
        }
    if score >= 65:
        return {
            "action": "小仓试错或加入观察池",
            "position": "仓位控制在 5%-8%，只适合有明确交易计划时参与",
            "stop": "若趋势因子转弱或回撤扩大，应退出观察",
        }
    if score >= 58:
        return {
            "action": "观察为主，等待信号强化",
            "position": "不建议主动建仓，可用 0%-5% 试错仓验证判断",
            "stop": "没有量价改善前不加仓",
        }
    return {
        "action": "暂不参与",
        "position": "保持空仓或仅保留已有底仓",
        "stop": "只有评分重新回到 65 以上并出现成交确认时再评估",
    }


def _fmt_price(value: Any) -> str:
    number = _safe_number(value)
    return "-" if number is None else f"{number:.2f}"


def _fmt_pct(value: Any) -> str:
    number = _safe_number(value)
    if number is None:
        return "-"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.2f}%"


def _fmt_amount_cn(value: Any) -> str:
    number = _safe_number(value)
    if number is None or number <= 0:
        return "-"
    if number >= 100_000_000:
        return f"{number / 100_000_000:.2f}亿"
    if number >= 10_000:
        return f"{number / 10_000:.2f}万"
    return f"{number:.0f}"


def _professional_position_label(score: float, trend: float, momentum: float, risk_level: str) -> str:
    if score >= 82 and trend >= 78 and momentum >= 75 and risk_level != "高":
        return "强势候选"
    if score >= 72 and trend >= 65 and momentum >= 60:
        return "重点跟踪"
    if score >= 60:
        return "观察修复"
    return "暂不参与"


def _build_professional_single_stock_report(
    *,
    symbol: str,
    stock_name: str,
    result: dict[str, Any],
    quant_result: dict[str, Any],
    quote: dict[str, Any] | None,
    technical_snapshot: dict[str, Any],
) -> dict[str, str]:
    latest = quant_result.get("latest") or {}
    factors = quant_result.get("factors") or {}
    risk = quant_result.get("risk") or {}
    deep = result.get("deep_analysis") or {}
    quote = quote or {}

    price = quote.get("price") or quote.get("close") or result.get("current_price") or latest.get("close")
    pct = quote.get("change_percent")
    if pct is None:
        pct = quote.get("pct_chg") if quote else latest.get("pct_change")
    amount = quote.get("amount") or latest.get("amount")
    volume = quote.get("volume") or latest.get("volume")
    score = float(result.get("overall_score") or quant_result.get("score") or 0)
    signal = str(quant_result.get("signal") or result.get("signal") or "neutral")
    trend = float(factors.get("trend") or 0)
    momentum = float(factors.get("momentum") or 0)
    rsi = float(factors.get("rsi") or 0)
    liquidity = float(factors.get("liquidity") or 0)
    risk_control = float(factors.get("risk_control") or 0)
    volatility = float(risk.get("volatility") or 0)
    max_drawdown = float(risk.get("max_drawdown") or 0)
    sharpe = float(risk.get("sharpe") or 0)
    risk_level = _risk_level(volatility, max_drawdown)
    label = _professional_position_label(score, trend, momentum, risk_level)

    ma5 = technical_snapshot.get("ma5")
    ma10 = technical_snapshot.get("ma10")
    ma20 = technical_snapshot.get("ma20")
    ma30 = technical_snapshot.get("ma30")
    ma60 = technical_snapshot.get("ma60")
    high_60 = technical_snapshot.get("high_60")
    low_20 = technical_snapshot.get("low_20")
    prev_high = technical_snapshot.get("prev_high")
    last_close = _safe_number(price) or _safe_number(latest.get("close")) or 0
    level_values = [
        _safe_number(prev_high),
        _safe_number(ma5),
        _safe_number(ma10),
        _safe_number(ma20),
        _safe_number(low_20),
    ]
    usable_supports = [
        value for value in level_values
        if value is not None and last_close and last_close * 0.82 <= value <= last_close * 0.995
    ]
    support = max(usable_supports) if usable_supports else (last_close * 0.92 if last_close else 0)
    hard_stop = min(support * 0.985, last_close * 0.90) if last_close and support else support
    reclaim_candidates = [
        value for value in [_safe_number(prev_high), _safe_number(ma5), _safe_number(ma10)]
        if value is not None and last_close and last_close * 0.95 <= value <= last_close * 1.08
    ]
    reclaim = max(reclaim_candidates) if reclaim_candidates else (last_close * 1.01 if last_close else 0)
    breakout_candidates = [
        value for value in [_safe_number(high_60), reclaim]
        if value is not None and last_close and value >= last_close * 1.005
    ]
    breakout = min(breakout_candidates) if breakout_candidates else (last_close * 1.03 if last_close else reclaim)
    support_gap = ((last_close - support) / last_close * 100) if last_close and support else 0
    stop_gap = ((last_close - hard_stop) / last_close * 100) if last_close and hard_stop else 0
    reclaim_gap = ((reclaim - last_close) / last_close * 100) if last_close and reclaim else 0
    breakout_gap = ((breakout - last_close) / last_close * 100) if last_close and breakout else 0

    # 额外指标（净室实现的 ATR/KDJ/ADX，从量化引擎 latest 快照读取）
    atr_pct = float(latest.get("atr_pct") or 0)
    adx_value = float(latest.get("adx") or 0)
    kdj_j_value = latest.get("kdj_j")
    chandelier_stop = _safe_number(latest.get("chandelier_stop")) or 0
    kdj_state = ""
    if kdj_j_value is not None:
        jv = float(kdj_j_value)
        kdj_state = f"KDJ 的 J={jv:.0f}（{'超买区，注意回踩' if jv > 100 else '超卖区，关注反弹' if jv < 0 else '中性'}）。"
    adx_state = f"ADX {adx_value:.0f}（{'趋势明确' if adx_value >= 25 else '震荡为主' if adx_value < 20 else '趋势中等'}）。" if adx_value else ""
    atr_state = f"ATR 波动约 {atr_pct:.1f}%。" if atr_pct else ""
    mfi_value = float(latest.get("mfi") or 0)
    cmf_value = float(latest.get("cmf") or 0)
    mf_state = ""
    if mfi_value or cmf_value:
        flow = "资金净流入" if cmf_value > 0.05 else ("资金净流出" if cmf_value < -0.05 else "资金中性")
        mf_state = f"资金流 MFI {mfi_value:.0f}、CMF {cmf_value:+.2f}（{flow}）。"
    cci_v = float(latest.get("cci") or 0)
    wr_v = float(latest.get("williams_r") or 0)
    aroon_up_v = float(latest.get("aroon_up") or 0)
    aroon_down_v = float(latest.get("aroon_down") or 0)
    stochrsi_v = float(latest.get("stochrsi") or 0)
    obv_rising = bool(latest.get("obv_rising"))
    mom_bits = []
    if cci_v:
        mom_bits.append(f"CCI {cci_v:.0f}")
    if wr_v:
        mom_bits.append(f"威廉%R {wr_v:.0f}")
    if stochrsi_v:
        mom_bits.append(f"StochRSI {stochrsi_v:.0f}")
    if aroon_up_v or aroon_down_v:
        mom_bits.append(f"Aroon 上{aroon_up_v:.0f}/下{aroon_down_v:.0f}")
    mom_bits.append("OBV 上升" if obv_rising else "OBV 走平/下降")
    mom_state = ("动量补充：" + "、".join(mom_bits) + "。") if mom_bits else ""
    wyckoff = (quant_result.get("integrations") or {}).get("wyckoff") or {}
    wyckoff_line = ""
    if wyckoff:
        wyckoff_phase = str(wyckoff.get("phase") or "neutral-range")
        wyckoff_bias = str(wyckoff.get("bias") or "neutral")
        wyckoff_score = float(wyckoff.get("score") or 50)
        wyckoff_reasons = "；".join(str(item) for item in (wyckoff.get("reasons") or [])[:2])
        wyckoff_line = (
            f"Wyckoff/VSA：{wyckoff_phase}，bias={wyckoff_bias}，score={wyckoff_score:.0f}。"
            f"{wyckoff_reasons}。"
        )
    ml_features = (quant_result.get("integrations") or {}).get("ml_features") or {}
    ml_line = ""
    if ml_features:
        ml_line = (
            f"ML特征摘要：feature_score={float(ml_features.get('feature_score') or 50):.0f}，"
            f"趋势持续性={float(ml_features.get('trend_persistence') or 50):.0f}，"
            f"波动分位={float(ml_features.get('volatility_rank') or 50):.0f}。"
        )
    extra_ind_line = kdj_state + adx_state + atr_state + mf_state + mom_state + wyckoff_line + ml_line

    above_parts = []
    below_parts = []
    for name, value in [("5日线", ma5), ("10日线", ma10), ("20日线", ma20), ("30日线", ma30), ("60日线", ma60)]:
        number = _safe_number(value)
        if number is None or not last_close:
            continue
        (above_parts if last_close >= number else below_parts).append(f"{name}{number:.2f}")

    quality = deep.get("quality_score") or {}
    scenarios = deep.get("scenarios") or {}
    base_scenario = scenarios.get("base") or {}
    fundamental_points = []
    if quality:
        numeric_scores = [float(v) for v in quality.values() if isinstance(v, (int, float))]
        if numeric_scores:
            fundamental_points.append(f"Claude 深度质量均分约 {sum(numeric_scores) / len(numeric_scores):.1f}。")
        if quality.get("rationale"):
            fundamental_points.append(str(quality["rationale"]))
    if base_scenario.get("target_price") is not None:
        fundamental_points.append(f"财务情景测算的基准目标价约 {base_scenario.get('target_price')}，该值来自财报收入、利润率、EPS/股本和估值推导。")
    if not fundamental_points:
        fundamental_points.append("当前基本面层以量化和可用公开数据为主，若财报/公告数据不足，系统不会用固定假设伪造结论。")

    conclusion = (
        f"结论：{stock_name}（{symbol}）当前属于“{label}”。"
        f"量化综合评分 {score:.1f}，交易信号 {signal}。"
        f"它不是只看涨跌幅就能判断的票，关键要看趋势是否延续、短中期动量是否共振、成交额是否继续活跃，以及回撤风险有没有扩大。"
    )
    if label in {"强势候选", "重点跟踪"}:
        conclusion += f" 短线核心看 {support:.2f} 附近是否有承接、{reclaim:.2f} 能否站稳；这些价位都按当前价附近重新计算，不再拿远端均线当止损。"
    else:
        conclusion += f" 现在更适合等确认，不能因为单日反弹就直接追；若跌破 {hard_stop:.2f}，短线风险就应该升级。"

    realtime_section = (
        f"截至 {quote.get('updated_at') or result.get('quote_updated_at') or result.get('updated_at') or '-'}：\n\n"
        f"- 股价：{_fmt_price(price)}\n"
        f"- 涨跌幅：{_fmt_pct(pct)}\n"
        f"- 成交额：{_fmt_amount_cn(amount)}\n"
        f"- 成交量：{_fmt_amount_cn(volume)}\n"
        f"- 数据源：{quote.get('quote_source') or result.get('quote_source') or 'quant/realtime'}"
    )

    vertical_section = (
        "一、纵向走势：\n\n"
        f"当前价格 {_fmt_price(price)}。"
        f"位于上方的均线：{'、'.join(above_parts) if above_parts else '暂无'}；"
        f"仍未站上的均线：{'、'.join(below_parts) if below_parts else '暂无'}。\n\n"
        f"趋势因子 {trend:.0f}，动量因子 {momentum:.0f}，RSI {rsi:.0f}，流动性 {liquidity:.0f}，风控 {risk_control:.0f}。"
        f"{extra_ind_line}"
        f"{_rsi_profile(rsi)} {_risk_profile(risk_level, volatility, max_drawdown, sharpe)}\n\n"
        f"关键价位：短线承接位 {support:.2f}（距当前约 {support_gap:.1f}%），战术止损位 {hard_stop:.2f}（距当前约 {stop_gap:.1f}%），站稳确认位 {reclaim:.2f}（距当前约 {reclaim_gap:+.1f}%），加速确认位 {breakout:.2f}（距当前约 {breakout_gap:+.1f}%）。"
    )

    fundamental_section = "二、基本面和深度框架：\n\n" + "\n".join(f"- {item}" for item in fundamental_points)

    execution_section = (
        "三、接下来怎么做：\n\n"
        f"- 已持有：重点看 {support:.2f} 附近的承接；跌破 {hard_stop:.2f} 就不是“继续看看”，应先控制仓位。\n"
        f"- 未持有：不建议在涨幅已大时直接追。更好的入场是回踩 {support:.2f} 附近有承接，或放量站稳 {reclaim:.2f} 后再确认。\n"
        f"- 如果突破 {breakout:.2f} 且成交额同步放大，才说明短中期趋势有继续走一段的概率。\n"
        "- 若量化评分跌破 70、动量转弱或风控因子明显下降，应把它从进攻候选降级为观察。"
        + (f"\n- ATR 自适应止损：吊灯止损位约 {chandelier_stop:.2f}（22 日最高 − 3×ATR），跌破视为趋势止损，比固定百分比更贴合个股波动。" if chandelier_stop else "")
    )

    final_report = "\n\n".join([
        conclusion,
        realtime_section,
        fundamental_section,
        vertical_section,
        execution_section,
        "说明：以上是系统基于实时行情、历史量价、量化因子和深度分析框架生成的交易研究结果，不构成任何保证收益的承诺。",
    ])

    return {
        "summary": conclusion,
        "recommendation": execution_section,
        "professional_single_stock_analysis": final_report,
        "technical_analysis": vertical_section,
        "fundamental_analysis": fundamental_section,
        "final_trade_decision": final_report,
    }


async def enrich_lite_result_with_professional_analysis(
    symbol: str,
    result: dict[str, Any],
    quant_result: dict[str, Any],
    stock_meta: dict[str, Any] | None,
    quote: dict[str, Any] | None,
) -> dict[str, Any]:
    stock_name = (quote or {}).get("name") or (stock_meta or {}).get("name") or result.get("stock_name") or symbol
    technical_snapshot: dict[str, Any] = {}
    try:
        from quantcore.quant.data import default_start_date, fetch_stock_dataframe, normalize_ohlcv

        df = await asyncio.wait_for(
            asyncio.to_thread(fetch_stock_dataframe, symbol, default_start_date(260), None),
            timeout=20,
        )
        data = normalize_ohlcv(df)
        if not data.empty:
            technical_snapshot = {
                "ma5": float(data["close"].rolling(5).mean().iloc[-1]),
                "ma10": float(data["close"].rolling(10).mean().iloc[-1]),
                "ma20": float(data["close"].rolling(20).mean().iloc[-1]),
                "ma30": float(data["close"].rolling(30).mean().iloc[-1]),
                "ma60": float(data["close"].rolling(60).mean().iloc[-1]),
                "high_60": float(data["high"].rolling(60).max().iloc[-1]),
                "low_20": float(data["low"].rolling(20).min().iloc[-1]),
                "prev_high": float(data["high"].iloc[-2]) if len(data) >= 2 else float(data["high"].iloc[-1]),
            }
    except Exception as exc:
        result["professional_analysis_data_warning"] = str(exc)

    professional = _build_professional_single_stock_report(
        symbol=symbol,
        stock_name=stock_name,
        result=result,
        quant_result=quant_result,
        quote=quote,
        technical_snapshot=technical_snapshot,
    )
    reports = dict(result.get("reports") or {})
    reports["professional_single_stock_analysis"] = professional["professional_single_stock_analysis"]
    reports["technical_analysis"] = professional["technical_analysis"]
    reports["fundamental_analysis"] = professional["fundamental_analysis"]
    reports["final_trade_decision"] = professional["final_trade_decision"]
    result["reports"] = reports
    result["summary"] = professional["summary"]
    result["recommendation"] = professional["recommendation"]
    result["technical_analysis"] = professional["technical_analysis"]
    result["fundamental_analysis"] = professional["fundamental_analysis"]
    result["analysis_engine"] = f"{result.get('analysis_engine') or 'saas-lite-quant'} + professional-single-stock"
    result["professional_analysis"] = {
        "technical_snapshot": technical_snapshot,
    }
    return result


def build_lite_analysis_result(
    task_id: str,
    symbol: str,
    quant_result: dict[str, Any],
    parameters: dict[str, Any],
    now: str,
    stock_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    latest = quant_result.get("latest") or {}
    factors = quant_result.get("factors") or {}
    risk = quant_result.get("risk") or {}
    score = float(quant_result.get("score") or 0)
    signal = quant_result.get("signal") or "neutral"
    factor_items = [
        ("趋势", float(factors.get("trend") or 0)),
        ("动量", float(factors.get("momentum") or 0)),
        ("RSI", float(factors.get("rsi") or 0)),
        ("风控", float(factors.get("risk_control") or 0)),
        ("流动性", float(factors.get("liquidity") or 0)),
    ]
    strengths = [name for name, value in factor_items if value >= 70]
    weaknesses = [name for name, value in factor_items if value < 45]
    strength_text = "、".join(strengths) if strengths else "暂无特别突出的强项"
    weakness_text = "、".join(weaknesses) if weaknesses else "暂无明显短板"
    volatility = float(risk.get("volatility") or 0)
    max_drawdown = float(risk.get("max_drawdown") or 0)
    sharpe = float(risk.get("sharpe") or 0)
    trend_value = float(factors.get("trend") or 0)
    momentum_value = float(factors.get("momentum") or 0)
    rsi_value = float(factors.get("rsi") or 0)
    liquidity_value = float(factors.get("liquidity") or 0)
    risk_control_value = float(factors.get("risk_control") or 0)
    risk_level = _risk_level(volatility, max_drawdown)
    profile = _score_profile(score)
    rsi_text = _rsi_profile(rsi_value)
    risk_text = _risk_profile(risk_level, volatility, max_drawdown, sharpe)
    plan = _trade_plan(score, str(signal), risk_level, rsi_value)
    trend_view = "趋势强势" if trend_value >= 70 else "趋势中性" if trend_value >= 45 else "趋势偏弱"
    momentum_view = "短中期动量强" if momentum_value >= 70 else "动量一般" if momentum_value >= 45 else "动量偏弱"

    # 额外指标（ATR/KDJ/ADX，来自量化引擎的 latest 快照）
    atr_pct = float(latest.get("atr_pct") or 0)
    chandelier_stop = float(latest.get("chandelier_stop") or 0)
    adx_value = float(latest.get("adx") or 0)
    kdj_j_value = latest.get("kdj_j")
    kdj_text = ""
    if kdj_j_value is not None:
        jv = float(kdj_j_value)
        kdj_state = "超买区，注意回踩" if jv > 100 else ("超卖区，关注反弹" if jv < 0 else "中性区")
        kdj_text = f"KDJ 的 J 值 {jv:.0f}（{kdj_state}）。"
    adx_text = ""
    if adx_value:
        adx_state = "趋势明确" if adx_value >= 25 else ("趋势偏弱/震荡为主" if adx_value < 20 else "趋势中等")
        adx_text = f"ADX {adx_value:.0f}（{adx_state}）。"
    extra_ind_text = f"{kdj_text}{adx_text}" + (f"波动性 ATR≈{atr_pct:.1f}%。" if atr_pct else "")

    technical = (
        f"技术结构判断：{trend_view}，{momentum_view}。"
        f"趋势因子 {_fmt_score(trend_value)}，动量因子 {_fmt_score(momentum_value)}，"
        f"RSI 因子 {_fmt_score(rsi_value)}，流动性因子 {_fmt_score(liquidity_value)}。"
        f"{rsi_text} {extra_ind_text}"
    )
    stock_name = (stock_meta or {}).get("name") or latest.get("name") or symbol
    score_line = f"{profile['grade']} / {profile['label']}，综合评分 {score:.1f}"
    summary = (
        f"{symbol}（{stock_name}）完成 SaaS Lite 单股量化画像。"
        f"{score_line}，交易信号为 {signal}。"
        f"主要优势是{strength_text}，主要短板是{weakness_text}。"
        f"{profile['stance']} {rsi_text} {risk_text}"
    )
    fundamental = (
        "SaaS Lite 当前以行情、量价和本地量化因子为主，未接入完整利润表、资产负债表和估值数据库，"
        f"因此基本面结论只作为低置信度辅助。当前流动性因子为 {_fmt_score(liquidity_value)}，"
        "若后续接入完整财务源，应重点补充收入增速、ROE、毛利率、现金流、估值分位和行业景气度。"
    )
    sentiment = (
        "Lite 模式未启用新闻和社媒情绪队列，本段用价格行为替代情绪观察。"
        f"当前市场行为显示：{trend_view}、{momentum_view}；"
        "若RSI继续上行但成交不能放大，情绪可能从强势转为拥挤。"
    )
    news = (
        "当前 Lite 后端未连接新闻归档，暂不对公告、研报和舆情事件做结论。"
        "生产版建议接入公告、交易所问询、行业政策和主流财经新闻，避免只凭量价信号做决策。"
    )
    atr_stop_line = (
        f"\n\nATR 自适应止损参考：吊灯止损位约 {chandelier_stop:.2f}（= 22 日最高价 − 3×ATR），"
        "跌破即视为趋势止损，比固定百分比更贴合个股波动。"
        if chandelier_stop > 0 else ""
    )
    investment_plan = (
        f"操作倾向：{plan['action']}。\n\n"
        f"仓位建议：{plan['position']}。\n\n"
        f"风控规则：{plan['stop']}。{atr_stop_line}\n\n"
        "适用前提：趋势因子维持在当前水平附近，且最大回撤没有继续扩大。"
    )
    research_team_decision = (
        f"多头理由：{strength_text}支撑当前评分，说明价格结构里有可交易的一面。\n\n"
        f"空头理由：{weakness_text}和{risk_level}风险等级限制了仓位上限，"
        "如果买点过高，收益风险比会变差。\n\n"
        f"研究结论：{profile['bias']}。这不是只看 buy/strong_buy 的机械信号，"
        "需要把评分区间和风险结构一起看。"
    )
    trader_plan = (
        f"交易执行：{plan['action']}。\n\n"
        "入场条件：优先选择回踩不破、缩量企稳后重新放量，或突破前高并伴随成交确认。\n\n"
        f"仓位节奏：{plan['position']}。\n\n"
        f"失效条件：{plan['stop']}。"
    )
    risk_management = (
        f"风险评级：{risk_level}。最大回撤约 {max_drawdown:.2%}，年化波动率约 {volatility:.2%}，夏普约 {sharpe:.2f}。\n\n"
        f"{risk_text}\n\n"
        f"风控因子 {_fmt_score(risk_control_value)}，若该项低于 45，应把它视为限制仓位的硬条件，而不是普通扣分项。"
    )
    final_decision = (
        f"最终结论：{profile['bias']}，但执行上按“{plan['action']}”处理。"
        f"{symbol} 当前不是一句简单的 {signal} 就能概括："
        f"评分 {score:.1f} 说明它处在“{profile['label']}”区间，"
        f"强项为{strength_text}，短板为{weakness_text}。"
        f"建议按计划交易，核心是控制仓位和失效线：{plan['position']}；{plan['stop']}。"
    )

    return {
        "analysis_id": task_id,
        "task_id": task_id,
        "symbol": symbol,
        "stock_symbol": symbol,
        "stock_code": symbol,
        "stock_name": stock_name,
        "market_type": (parameters or {}).get("market_type", "A股"),
        "analysis_date": (parameters or {}).get("analysis_date") or now[:10],
        "analysis_type": "saas-lite-quant",
        "current_price": latest.get("close") or latest.get("price") or 0,
        "price_change": latest.get("change") or 0,
        "price_change_percent": latest.get("pct_change") or 0,
        "volume": latest.get("volume") or 0,
        "summary": summary,
        "technical_analysis": technical,
        "fundamental_analysis": fundamental,
        "sentiment_analysis": sentiment,
        "news_analysis": news,
        "recommendation": final_decision,
        "risk_assessment": risk_text,
        "technical_score": score,
        "fundamental_score": min(100, max(0, score * 0.8)),
        "sentiment_score": 50,
        "overall_score": score,
        "data_sources": ["akshare", "local-quant-engine"],
        "llm_provider": "saas-lite",
        "llm_model": "local-quant",
        "analysis_duration": 1,
        "reports": {
            "summary": summary,
            "technical_analysis": technical,
            "fundamental_analysis": fundamental,
            "sentiment_analysis": sentiment,
            "news_analysis": news,
            "risk_assessment": risk_management,
            "market_report": technical,
            "fundamentals_report": fundamental,
            "news_report": news,
            "sentiment_report": sentiment,
            "investment_plan": investment_plan,
            "research_team_decision": research_team_decision,
            "trader_investment_plan": trader_plan,
            "risk_management_decision": risk_management,
            "final_trade_decision": final_decision,
        },
        "state": {
            "quant_result": quant_result,
            "parameters": parameters,
        },
        "created_at": now,
        "updated_at": now,
    }


def _normalize_deep_rating(rating: str) -> str:
    rating_map = {
        "涔板叆": "买入",
        "鎸佹湁": "持有",
        "瑙傚療": "观察",
        "鍥為伩": "回避",
        "买入": "买入",
        "持有": "持有",
        "观察": "观察",
        "回避": "回避",
    }
    return rating_map.get(str(rating or "").strip(), "观察")


def _format_deep_chain(chain: dict[str, Any]) -> str:
    if not chain:
        return "暂无产业链结构化数据。"
    labels = {"upstream": "上游", "midstream": "中游/公司环节", "downstream": "下游"}
    sections: list[str] = []
    for key in ("upstream", "midstream", "downstream"):
        entries = chain.get(key) or []
        if not entries:
            continue
        lines = []
        for item in entries:
            name = item.get("name") if isinstance(item, dict) else str(item)
            companies = item.get("companies") if isinstance(item, dict) else []
            company_text = "、".join([str(company) for company in companies]) if companies else "暂无代表公司"
            lines.append(f"- {name}：{company_text}")
        sections.append(f"### {labels[key]}\n" + "\n".join(lines))
    return "\n\n".join(sections) if sections else "暂无产业链结构化数据。"


def _format_deep_quality(quality: dict[str, Any]) -> str:
    if not quality:
        return "暂无质量评分。"
    labels = {
        "fundamental": "基本面",
        "governance": "治理质量",
        "competitive": "竞争力",
        "growth": "成长性",
        "valuation": "估值合理性",
    }
    lines = [f"- {labels[key]}：{quality.get(key)}" for key in labels if quality.get(key) is not None]
    rationale = quality.get("rationale")
    if rationale:
        lines.append(f"\n结论：{rationale}")
    return "\n".join(lines)


def _format_deep_scenarios(scenarios: dict[str, Any]) -> str:
    if not scenarios:
        return "暂无情景测算数据。"
    labels = {"bear": "保守情景", "base": "中性情景", "bull": "乐观情景"}
    lines = []
    for key, label in labels.items():
        item = scenarios.get(key) or {}
        if item:
            lines.append(
                f"- {label}：收入 {item.get('revenue', '-')}，净利润 {item.get('net_profit', '-')}，目标价格 {item.get('target_price', '-')}"
            )
    return "\n".join(lines) if lines else "暂无情景测算数据。"


def _format_deep_risks(risks: list[dict[str, Any]]) -> str:
    if not risks:
        return "暂无结构化风险清单。"
    return "\n".join([f"- {item.get('risk', '风险')}：{item.get('mitigation', '暂无应对策略')}" for item in risks])


def _format_deep_peers(peers: list[dict[str, Any]]) -> str:
    if not peers:
        return "暂无可比公司数据。"
    return "\n".join(
        [
            f"- {item.get('name', '-') or '-'}（{item.get('code', '-') or '-'}）：PE {item.get('pe', '-')}，ROE {item.get('roe', '-')}"
            for item in peers
        ]
    )


def _format_deep_tracking(plan: dict[str, Any]) -> str:
    metrics = plan.get("metrics") if isinstance(plan, dict) else []
    lines = [f"- {item.get('name', '指标')}：{item.get('threshold', '-')}" for item in metrics or []]
    next_review = plan.get("next_review") if isinstance(plan, dict) else ""
    if next_review:
        lines.append(f"\n复盘节奏：{next_review}")
    return "\n".join(lines) if lines else "暂无跟踪计划。"


def _deep_action_to_decision(rating: str, current_price: Any) -> dict[str, Any]:
    action_map = {
        "买入": "积极关注",
        "持有": "继续跟踪",
        "观察": "等待确认",
        "回避": "暂不参与",
    }
    confidence_map = {"买入": 0.72, "持有": 0.62, "观察": 0.52, "回避": 0.42}
    return {
        "action": action_map.get(rating, "等待确认"),
        "target_price": current_price or "-",
        "confidence": confidence_map.get(rating, 0.5),
        "risk_score": 0.45 if rating in {"买入", "持有"} else 0.62,
        "reasoning": f"Claude 深度分析给出“{rating}”倾向，SaaS Lite 量化层负责校验趋势、动量、RSI、流动性和回撤风险。",
    }


async def enrich_lite_result_with_deep_analysis(
    task_id: str,
    symbol: str,
    result: dict[str, Any],
    parameters: dict[str, Any],
    stock_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stock_name = (stock_meta or {}).get("name") or result.get("stock_name") or symbol
    try:
        import quantcore.analysis.deep_analysis.framework as deep_framework_module
        from quantcore.analysis.report.html_generator import HTMLReportGenerator

        deep_framework_module.CacheManager = LiteNoopCacheManager
        deep_llm = LiteDeepAnalysisLLM(symbol, stock_name)
        framework = deep_framework_module.DeepAnalysisFramework(llm_client=deep_llm)
        deep_result = await asyncio.wait_for(
            asyncio.to_thread(framework.analyze, symbol, stock_name),
            timeout=90,
        )
        report_path = f"reports/{symbol}_deep_report.html"
        try:
            await asyncio.to_thread(HTMLReportGenerator().generate, deep_result, report_path)
            result["html_report_url"] = f"/reports/{symbol}_deep_report.html"
        except Exception as exc:
            result["html_report_error"] = str(exc)
    except Exception as exc:
        result["deep_analysis_error"] = str(exc)
        result["analysis_engine"] = "saas-lite-quant"
        return result

    rating = _normalize_deep_rating(deep_result.get("overall_rating", ""))
    quality = deep_result.get("quality_score") or {}
    risks = deep_result.get("risks") or []
    peers = deep_result.get("peers") or []
    tracking_plan = deep_result.get("tracking_plan") or {}
    industry_chain = deep_result.get("industry") or {}

    result["analysis_type"] = "saas-lite-quant+claude-deep-analysis"
    result["analysis_engine"] = "Claude DeepAnalysisFramework + SaaS Lite QuantEngine"
    result["llm_provider"] = "claude-deep-framework"
    result["llm_model"] = parameters.get("deep_analysis_model") or "lite-deterministic-adapter"
    result["model_info"] = "Claude 8步深度分析 + SaaS Lite量化画像"
    result["deep_rating"] = rating
    result["deep_analysis"] = deep_result
    result["decision"] = _deep_action_to_decision(rating, result.get("current_price"))

    original_summary = result.get("summary") or ""
    result["summary"] = (
        f"{symbol}（{stock_name}）已完成 Claude 8步深度分析与 SaaS Lite 量化画像。"
        f"深度评级为“{rating}”，量化综合评分为 {float(result.get('overall_score') or 0):.1f}。"
        f"{original_summary}"
    )
    result["recommendation"] = (
        f"综合结论：{rating}。执行上不要只看评级，应同时满足趋势、动量、成交额、RSI 和风险控制。"
        f"若量化信号转弱或风险指标恶化，应以风控优先。"
    )
    result["fundamental_analysis"] = _format_deep_quality(quality)
    result["risk_assessment"] = _format_deep_risks(risks)

    reports = dict(result.get("reports") or {})
    reports.update(
        {
            "deep_macro_positioning": deep_result.get("macro") or "暂无宏观定位。",
            "deep_industry_chain": _format_deep_chain(industry_chain),
            "deep_quality_score": _format_deep_quality(quality),
            "deep_scenario_analysis": _format_deep_scenarios(deep_result.get("scenarios") or {}),
            "deep_risk_checklist": _format_deep_risks(risks),
            "deep_tracking_plan": _format_deep_tracking(tracking_plan),
            "deep_final_rating": f"Claude 深度评级：{rating}\n\n护城河判断：{deep_result.get('moat') or '暂无'}",
        }
    )
    result["reports"] = reports
    result["state"] = {
        **(result.get("state") or {}),
        "deep_analysis": deep_result,
        "deep_report_task_id": task_id,
    }
    return result


@app.get("/api/favorites/")
async def favorites(user: dict[str, Any] = Depends(get_current_lite_user)):
    ensure_lite_favorites_table()
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM lite_favorites WHERE username = ? ORDER BY added_at DESC",
            (user["username"],),
        ).fetchall()
    items = []
    for row in rows:
        items.append({
            "symbol": row["stock_code"],
            "stock_code": row["stock_code"],
            "stock_name": row["stock_name"],
            "market": row["market"],
            "tags": [],
            "notes": row["notes"] or "",
            "added_price": row["added_price"] if "added_price" in row.keys() else None,
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


@app.post("/api/favorites/")
async def add_favorite(payload: LiteFavoriteRequest, user: dict[str, Any] = Depends(get_current_lite_user)):
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
                username, stock_code, stock_name, market, tags_json, notes, added_price, added_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(username, stock_code) DO UPDATE SET
                stock_name = excluded.stock_name,
                market = excluded.market,
                tags_json = excluded.tags_json,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                user["username"],
                stock["symbol"],
                stock["name"],
                stock.get("market") or payload.market or "A股",
                "[]",
                payload.notes or "",
                added_price,
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


@app.get("/api/favorites/check/{symbol}")
async def check_favorite(symbol: str, user: dict[str, Any] = Depends(get_current_lite_user)):
    ensure_lite_favorites_table()
    with store.connect() as conn:
        row = conn.execute(
            "SELECT stock_code FROM lite_favorites WHERE username = ? AND stock_code = ?",
            (user["username"], symbol),
        ).fetchone()
    return {"success": True, "data": {"symbol": symbol, "stock_code": symbol, "is_favorite": bool(row)}, "message": "ok"}


@app.get("/api/favorites/tags")
async def favorite_tags():
    return {"success": True, "data": [], "message": "ok"}


@app.get("/api/tags/")
async def tags():
    return {"success": True, "data": [], "message": "ok"}


@app.put("/api/favorites/{symbol}")
async def update_favorite(symbol: str, payload: LiteFavoriteRequest, user: dict[str, Any] = Depends(get_current_lite_user)):
    now = datetime.now(timezone.utc).isoformat()
    ensure_lite_favorites_table()
    with store.connect() as conn:
        conn.execute(
            "UPDATE lite_favorites SET notes = ?, updated_at = ? WHERE username = ? AND stock_code = ?",
            (payload.notes or "", now, user["username"], symbol),
        )
        conn.commit()
    return {"success": True, "data": {"message": "保存成功", "symbol": symbol, "stock_code": symbol}, "message": "保存成功"}


@app.delete("/api/favorites/{symbol}")
async def remove_favorite(symbol: str, user: dict[str, Any] = Depends(get_current_lite_user)):
    ensure_lite_favorites_table()
    with store.connect() as conn:
        conn.execute(
            "DELETE FROM lite_favorites WHERE username = ? AND stock_code = ?",
            (user["username"], symbol),
        )
        conn.commit()
    return {"success": True, "data": {"message": "移除成功", "symbol": symbol, "stock_code": symbol}, "message": "移除成功"}


@app.post("/api/favorites/sync-realtime")
async def favorites_sync_realtime(user: dict[str, Any] = Depends(get_current_lite_user)):
    ensure_lite_favorites_table()
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT stock_code, alert_price_high, alert_price_low FROM lite_favorites WHERE username = ? ORDER BY added_at DESC",
            (user["username"],),
        ).fetchall()
    symbols = [row["stock_code"] for row in rows]
    quotes = await _realtime_quotes(symbols)
    for row in rows:
        symbol = row["stock_code"]
        price = quotes.get(symbol, {}).get("price")
        if price:
            _check_and_record_price_alert(
                symbol=symbol,
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


@app.get("/api/lite/alerts")
async def get_price_alerts(user: dict[str, Any] = Depends(get_current_lite_user)):
    alerts = list(lite_price_alerts.values())
    return {"success": True, "data": alerts, "message": "ok"}


@app.delete("/api/lite/alerts/{symbol}")
async def dismiss_price_alert(
    symbol: str,
    direction: str = "high",
    user: dict[str, Any] = Depends(get_current_lite_user),
):
    key = f"{symbol}:{direction}"
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


@app.get("/api/reports/list")
async def reports_list(page: int = 1, page_size: int = 20):
    return {
        "success": True,
        "data": {"reports": [], "total": 0, "page": page, "page_size": page_size},
        "message": "SaaS Lite 未连接 MongoDB，报告列表为空",
    }


@app.get("/api/reports/search")
async def reports_search(q: str = "", limit: int = 20):
    if not q.strip():
        return {"success": False, "data": [], "message": "请输入搜索关键词"}
    results = _search_reports_fts(q.strip(), limit)
    return {"success": True, "data": results, "message": f"共 {len(results)} 条结果"}


@app.post("/api/lite/datalake/sync")
async def lite_datalake_sync(full: bool = False):
    svc = get_sync_service()
    status = await asyncio.to_thread(svc.run_sync, full, False)
    return {"success": True, "data": status}


@app.get("/api/lite/datalake/sync/status")
async def lite_datalake_sync_status():
    svc = get_sync_service()
    return {"success": True, "data": svc.status()}


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
