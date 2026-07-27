"""新闻事件流水线：抓取 → 归类 → 映射个股 → 落库 → 检索，以及催化剂榜的构建。

从 lite_main 抽出。这一簇是「洞察」页面（热点新闻/催化剂/事件流/市场情绪）的全部
底料，也被首页与盘报复用，因此放 core 而不是跟路由绑死。

数据源是多路并联的 best-effort：财新、东财公告/研报、快讯、热搜榜，任何一路失败
都只是少一部分事件，不影响整体。事件落 SQLite（lite_news_events），保留 3 天。

`_stable_float` / `_sparkline` / `_lite_news_items` 是无外部数据时的确定性兜底
（按文本哈希生成稳定数值），保证页面永远有结构可渲染——不是真实行情，别拿去做决策。
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from app.core.analysis_report import _risk_level
from app.core.engine import get_stock_pool_items, lite_quant_engine
from app.core.market_data import _now_cn, _persistent_cache_delete_prefix, lite_insights_cache
from app.core.schema import ensure_lite_news_table
from app.lite_auth import store


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
