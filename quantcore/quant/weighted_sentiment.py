"""多源加权情绪打分（子项目 C）。

一个核心 `_blend`（各源归一到 0-100，固定权重加权，缺源剔除并对剩余源重归一）+ 四个薄适配器：
  - stock_sentiment(symbol)        个股加权情绪分（个股研报页）
  - market_weighted_sentiment()    大盘多源加权情绪（大盘情绪页，宽度温度的多源升级）
  - sector_weighted_sentiment_rank() 板块加权情绪排行（大盘情绪页）
  - stock_sentiment_from_signals(...) 纯函数核，复用已算信号供 smart-pool 因子场景（不重复取数）

全程本地 + 现有缓存，无 LLM。新闻情绪走利好/利空词典。
唯一可能贵的「全市场个股新闻」用 with_news=False 一刀切开（避免全市场拉取）。
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# 利好/利空词典（A 股财经新闻常见措辞，纯关键词匹配）
_BULLISH = (
    "涨停", "大涨", "利好", "增持", "回购", "中标", "签约", "订单", "预增", "扭亏",
    "新高", "突破", "合作", "获批", "量产", "涨价", "龙头", "重组", "注入", "并购",
    "提价", "超预期", "放量", "热点", "受益",
)
_BEARISH = (
    "跌停", "大跌", "利空", "减持", "质押", "违规", "处罚", "亏损", "预亏", "下滑",
    "退市", "立案", "诉讼", "商誉", "爆雷", "下调", "风险", "问询", "解禁", "套现",
    "终止", "停牌", "破发", "踩雷", "出货",
)

_LEVELS = ((70, "高涨"), (60, "偏暖"), (40, "中性"), (30, "偏冷"), (0, "低迷"))


def _level(score: float) -> str:
    for threshold, label in _LEVELS:
        if score >= threshold:
            return label
    return "低迷"


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _norm_return(pct: Optional[float]) -> Optional[float]:
    """涨跌幅 % → 0-100，0%→50，±15%→约 88/12（tanh 平滑）。None 透传。"""
    if pct is None:
        return None
    return round(50 + 50 * math.tanh(pct / 15.0), 1)


def _norm_netflow(net: Optional[float], ref: float) -> Optional[float]:
    """净流入（亿）→ 0-100，0→50，±ref→约 76/24。None 透传。"""
    if net is None or ref <= 0:
        return None
    return round(50 + 50 * math.tanh(net / ref), 1)


def _news_score(titles: Optional[List[str]]) -> Optional[float]:
    """标题列表 → 0-100。无标题或无情绪词命中 → None（不可评估，剔除）。"""
    if not titles:
        return None
    pos = neg = 0
    for t in titles:
        text = str(t or "")
        pos += sum(1 for w in _BULLISH if w in text)
        neg += sum(1 for w in _BEARISH if w in text)
    if pos == 0 and neg == 0:
        return None
    return round(50 + 50 * math.tanh((pos - neg) / 3.0), 1)


def _blend(components: List[Dict[str, object]]) -> Dict[str, object]:
    """components: [{name, score(0-100 或 None), weight}]。

    剔除 score 为 None 的源，对剩余源权重重归一后加权求和。
    返回 {score, level, breakdown:[{name, score, weight, contribution}]}。全缺 → score=None。
    """
    present = [c for c in components if c.get("score") is not None]
    total_w = sum(float(c["weight"]) for c in present)
    if not present or total_w <= 0:
        return {"score": None, "level": "中性", "breakdown": []}
    breakdown = []
    score = 0.0
    for c in present:
        w = float(c["weight"]) / total_w
        s = float(c["score"])
        contrib = round(s * w, 1)
        score += s * w
        breakdown.append({"name": c["name"], "score": round(s, 1), "weight": round(w, 3), "contribution": contrib})
    score = round(_clamp(score), 1)
    return {"score": score, "level": _level(score), "breakdown": breakdown}


# ---- ① 个股 ----------------------------------------------------------------

_STOCK_WEIGHTS = {"tech": 0.30, "ret": 0.20, "news": 0.25, "lhb": 0.15, "fund": 0.10}


def stock_sentiment_from_signals(
    factors: Optional[Dict[str, object]],
    ret5: Optional[float],
    ret20: Optional[float],
    news_titles: Optional[List[str]] = None,
    lhb_net: Optional[float] = None,
    fund_net: Optional[float] = None,
) -> Dict[str, object]:
    """纯函数核：仅归一 + _blend，不取数。供因子场景复用已算信号（with_news 关时 news_titles=None）。"""
    tech = None
    if factors:
        vals = [factors.get("trend"), factors.get("momentum")]
        nums = [float(v) for v in vals if isinstance(v, (int, float))]
        tech = round(sum(nums) / len(nums), 1) if nums else None
    # 近期涨跌：5 日为主、20 日为辅
    ret_pct = None
    if ret5 is not None and ret20 is not None:
        ret_pct = ret5 * 0.6 + ret20 * 0.4
    elif ret5 is not None:
        ret_pct = ret5
    elif ret20 is not None:
        ret_pct = ret20
    return _blend([
        {"name": "技术动量", "score": tech, "weight": _STOCK_WEIGHTS["tech"]},
        {"name": "近期涨跌", "score": _norm_return(ret_pct), "weight": _STOCK_WEIGHTS["ret"]},
        {"name": "新闻情绪", "score": _news_score(news_titles), "weight": _STOCK_WEIGHTS["news"]},
        {"name": "龙虎榜", "score": _norm_netflow(lhb_net, 2.0), "weight": _STOCK_WEIGHTS["lhb"]},
        {"name": "资金流", "score": _norm_netflow(fund_net, 2.0), "weight": _STOCK_WEIGHTS["fund"]},
    ])


def _local_name(symbol: str) -> str:
    try:
        from .local_store import get_local_store
        row = get_local_store()._conn().execute(
            "SELECT name FROM stock_meta WHERE symbol=?", (symbol,)
        ).fetchone()
        return str(row[0] or "").strip() if row else ""
    except Exception:
        return ""


def _returns(close) -> tuple[Optional[float], Optional[float]]:
    n = len(close)
    last = float(close.iloc[-1])
    ret5 = round((last / float(close.iloc[-6]) - 1) * 100, 2) if n >= 6 and float(close.iloc[-6]) else None
    ret20 = round((last / float(close.iloc[-21]) - 1) * 100, 2) if n >= 21 and float(close.iloc[-21]) else None
    return ret5, ret20


def _lhb_net(symbol: str) -> Optional[float]:
    try:
        from .dragon_tiger import dragon_tiger_seats
        seats = dragon_tiger_seats(symbol)
        if seats.get("empty"):
            return None
        buy = sum(float(x.get("buy_yi", 0)) for x in seats.get("buy", []))
        sell = sum(float(x.get("sell_yi", 0)) for x in seats.get("sell", []))
        return round(buy - sell, 2)
    except Exception:
        return None


def _fund_net(symbol: str) -> Optional[float]:
    try:
        from .capital_flow import individual_fund_flow_rank
        rank = individual_fund_flow_rank(limit=5000)
        if rank.get("empty"):
            return None
        for r in rank.get("rows", []):
            if str(r.get("symbol", "")).zfill(6) == symbol:
                return float(r.get("net_yi", 0))
    except Exception:
        return None
    return None


def stock_sentiment(symbol: str, with_news: bool = True) -> Dict[str, object]:
    """个股加权情绪分。无本地行情 → {empty, message}。"""
    symbol = str(symbol).strip().zfill(6) if str(symbol).strip().isdigit() else str(symbol).strip()
    try:
        from .data import load_local_kline
        from .factors import compute_factor_scores
        data = load_local_kline(symbol, days=60)
    except Exception:
        data = None
    if data is None or getattr(data, "empty", True):
        return {"empty": True, "message": "无该股本地行情，无法计算情绪分"}

    try:
        factors = compute_factor_scores(data)
    except Exception:
        factors = {}
    ret5, ret20 = _returns(data["close"])

    titles = None
    if with_news:
        try:
            from .news import stock_news
            titles = [n.get("title", "") for n in stock_news(symbol, days=7, limit=15)]
        except Exception:
            titles = None

    result = stock_sentiment_from_signals(factors, ret5, ret20, titles, _lhb_net(symbol), _fund_net(symbol))
    if result.get("score") is None:
        return {"empty": True, "message": "情绪信号不足，无法评分"}
    name = _local_name(symbol) or symbol
    result.update({"empty": False, "symbol": symbol, "name": name})
    result["summary"] = f"{name} 加权情绪 {result['score']} 分（{result['level']}）。"
    return result


# ---- ② 大盘 ----------------------------------------------------------------

_MARKET_WEIGHTS = {"breadth": 0.30, "advdec": 0.25, "limit": 0.25, "fund": 0.10, "news": 0.10}


def _industry_total_net() -> Optional[float]:
    try:
        from .capital_flow import industry_fund_flow_rank
        rank = industry_fund_flow_rank()
        if rank.get("empty"):
            return None
        return round(sum(float(r.get("net_yi", 0)) for r in rank.get("rows", [])), 2)
    except Exception:
        return None


def _market_news_titles(limit: int = 200) -> Optional[List[str]]:
    try:
        from .news import market_news_flow
        return [n.get("title", "") for n in market_news_flow(limit=limit)]
    except Exception:
        return None


def market_weighted_sentiment() -> Dict[str, object]:
    """大盘多源加权情绪：宽度温度 + 资金流 + 新闻热度的加权升级。"""
    try:
        from .market_sentiment import compute_market_sentiment
        today = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        ms = compute_market_sentiment(start, today, 24, None)
    except Exception:
        ms = {"empty": True}
    if ms.get("empty"):
        return {"empty": True, "message": "无本地行情，无法计算大盘情绪"}
    kpi = ms.get("kpi", {})

    breadth = kpi.get("above_ma5_pct")
    ratio = kpi.get("adv_dec_ratio")
    advdec = round(min(100.0, ratio / 3.0 * 100), 1) if ratio else None
    up, down = kpi.get("limit_up", 0), kpi.get("limit_down", 0)
    limit_strength = round(up / (up + down) * 100, 1) if (up + down) else None

    blended = _blend([
        {"name": "宽度(>MA5)", "score": breadth, "weight": _MARKET_WEIGHTS["breadth"]},
        {"name": "涨跌比", "score": advdec, "weight": _MARKET_WEIGHTS["advdec"]},
        {"name": "涨跌停强弱", "score": limit_strength, "weight": _MARKET_WEIGHTS["limit"]},
        {"name": "行业资金合计", "score": _norm_netflow(_industry_total_net(), 200.0), "weight": _MARKET_WEIGHTS["fund"]},
        {"name": "市场新闻热度", "score": _news_score(_market_news_titles()), "weight": _MARKET_WEIGHTS["news"]},
    ])
    if blended.get("score") is None:
        return {"empty": True, "message": "情绪信号不足"}
    blended["empty"] = False
    blended["base_temperature"] = kpi.get("sentiment_temperature")
    blended["as_of"] = ms.get("as_of")
    blended["summary"] = (
        f"大盘加权情绪 {blended['score']} 分（{blended['level']}），"
        f"对比宽度温度 {kpi.get('sentiment_temperature')}。"
    )
    return blended


# ---- ③ 板块 ----------------------------------------------------------------

_SECTOR_WEIGHTS = {"fund": 0.45, "pct": 0.30, "news": 0.25}


def sector_weighted_sentiment_rank(limit: int = 20) -> Dict[str, object]:
    """板块加权情绪排行：行业资金流 + 涨跌幅 + 板块新闻提及。"""
    try:
        from .capital_flow import industry_fund_flow_rank
        rank = industry_fund_flow_rank()
    except Exception:
        rank = {"empty": True}
    if rank.get("empty"):
        return {"empty": True, "message": "无行业资金流数据", "rows": []}

    titles = _market_news_titles() or []
    rows = []
    for r in rank.get("rows", []):
        name = str(r.get("name", ""))
        if not name:
            continue
        net_yi = float(r.get("net_yi", 0))
        pct = float(r.get("pct", 0))
        sector_titles = [t for t in titles if name and name in str(t)]
        blended = _blend([
            {"name": "资金流", "score": _norm_netflow(net_yi, 10.0), "weight": _SECTOR_WEIGHTS["fund"]},
            {"name": "涨跌幅", "score": _norm_return(pct), "weight": _SECTOR_WEIGHTS["pct"]},
            {"name": "新闻提及", "score": _news_score(sector_titles), "weight": _SECTOR_WEIGHTS["news"]},
        ])
        if blended.get("score") is None:
            continue
        rows.append({
            "name": name,
            "score": blended["score"],
            "level": blended["level"],
            "breakdown": blended["breakdown"],
            "net_yi": round(net_yi, 2),
            "pct": round(pct, 2),
        })
    rows.sort(key=lambda x: x["score"], reverse=True)
    return {"empty": False, "rows": rows[:limit]}
