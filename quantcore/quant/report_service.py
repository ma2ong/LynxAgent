"""个股 AI 研报组装服务（功能A）。

一页纸研报：评级 / 核心摘要 / 目标价森林图 / 关键指标(当日) / 技术+基本面评分 /
AI 投资观点 / 核心财务摘要 / 市场表现 / 机构持仓 / 近 7 天新闻。

设计：
- 技术面评分复用 quant.factors 那套合成（compute_factor_scores -> composite_score），不另写。
- 数据先本地库后回退 akshare（load_local_kline -> fetch_stock_dataframe）。
- AI 观点先判 llm.available()，无密钥/出错时用规则降级，绝不抛栈。
- 每个子区块单独 try，失败返回占位（"暂无"），整体永不崩溃。
- 返回纯 JSON-able dict，键英文 snake_case，值含中文文本。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import pandas as pd

from . import llm
from .data import default_start_date, fetch_stock_dataframe, load_local_kline
from .factors import (
    composite_score,
    compute_factor_scores,
    enrich_indicators,
    risk_metrics,
    signal_from_score,
)
from .fundamentals import fundamentals as fetch_fundamentals
from .fundamentals import profile as fetch_profile
from .news import stock_news


def _f(value, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _pct_change(curr: float, base: float) -> Optional[float]:
    if base in (None, 0) or curr is None:
        return None
    return round((curr / base - 1) * 100, 2)


def _load_kline(symbol: str) -> pd.DataFrame:
    """先本地库，再回退 akshare/yfinance；都失败返回空 DataFrame。"""
    try:
        df = load_local_kline(symbol, days=420)
        if df is not None and not df.empty:
            return df
    except Exception:
        pass
    try:
        df = fetch_stock_dataframe(symbol, default_start_date(420), None)
        if df is not None and not df.empty:
            return df
    except Exception:
        pass
    return pd.DataFrame()


# --- 子区块 -------------------------------------------------------------

def _header(symbol: str, prof: Dict, data: pd.DataFrame) -> Dict[str, object]:
    last_close = _f(data["close"].iloc[-1]) if not data.empty else None
    return {
        "symbol": symbol,
        "name": (prof.get("name") or "").strip() or symbol,
        "industry": (prof.get("industry") or "").strip() or "暂无",
        "last_price": round(last_close, 2) if last_close else None,
        "total_mv": prof.get("total_mv"),
    }


def _key_indicators(data: pd.DataFrame) -> List[Dict[str, object]]:
    """关键指标(当日)：指标 / 数值 / 较昨 / 较上周 / 较上月。"""
    if data.empty:
        return []
    close = data["close"]
    n = len(close)

    def cmp(curr: float):
        prev = _f(close.iloc[-2]) if n >= 2 else None
        week = _f(close.iloc[-6]) if n >= 6 else None
        month = _f(close.iloc[-23]) if n >= 23 else None
        return {
            "vs_yesterday": _pct_change(curr, prev),
            "vs_week": _pct_change(curr, week),
            "vs_month": _pct_change(curr, month),
        }

    last = data.iloc[-1]
    last_close = _f(last["close"])
    rows: List[Dict[str, object]] = []
    rows.append({"name": "收盘价", "value": round(last_close, 2), **cmp(last_close)})

    vol = _f(last.get("volume", 0))
    if vol:
        rows.append({
            "name": "成交量",
            "value": round(vol, 0),
            "vs_yesterday": _pct_change(vol, _f(data["volume"].iloc[-2])) if n >= 2 and "volume" in data else None,
            "vs_week": _pct_change(vol, _f(data["volume"].iloc[-6])) if n >= 6 and "volume" in data else None,
            "vs_month": _pct_change(vol, _f(data["volume"].iloc[-23])) if n >= 23 and "volume" in data else None,
        })
    amount = _f(last.get("amount", 0))
    if amount:
        rows.append({
            "name": "成交额(亿)",
            "value": round(amount / 1e8, 2),
            "vs_yesterday": _pct_change(amount, _f(data["amount"].iloc[-2])) if n >= 2 and "amount" in data else None,
            "vs_week": _pct_change(amount, _f(data["amount"].iloc[-6])) if n >= 6 and "amount" in data else None,
            "vs_month": _pct_change(amount, _f(data["amount"].iloc[-23])) if n >= 23 and "amount" in data else None,
        })

    enriched = enrich_indicators(data)
    rsi = _f(enriched["rsi14"].dropna().iloc[-1], 50.0) if not enriched["rsi14"].dropna().empty else None
    if rsi is not None:
        rows.append({"name": "RSI14", "value": round(rsi, 2), "vs_yesterday": None, "vs_week": None, "vs_month": None})
    return rows


def _market_performance(data: pd.DataFrame) -> Dict[str, Optional[float]]:
    """市场表现%：1日/5日/1月/3月/年初至今/1年。"""
    if data.empty:
        return {}
    close = data["close"]
    curr = _f(close.iloc[-1])
    n = len(close)

    def back(days_idx: int) -> Optional[float]:
        if n > days_idx:
            return _pct_change(curr, _f(close.iloc[-1 - days_idx]))
        return None

    # 年初至今：取当年首个交易日
    ytd = None
    try:
        last_date = pd.to_datetime(data["date"].iloc[-1])
        year_mask = pd.to_datetime(data["date"]).dt.year == last_date.year
        if year_mask.any():
            ytd = _pct_change(curr, _f(close[year_mask].iloc[0]))
    except Exception:
        ytd = None

    return {
        "d1": back(1),
        "d5": back(5),
        "m1": back(22),
        "m3": back(66),
        "ytd": ytd,
        "y1": back(243),
    }


def _valuation_forest(last_close: float, tech_score: float, fund_score: float, volatility: float) -> Dict[str, object]:
    """目标价森林图 + 评级区间。

    以当前价为锚，用 综合分(技术+基本面)/100 决定方向偏移，波动率决定区间宽度：
      中性中枢 = 当前价 * (1 + bias)，bias = (avg_score-50)/50 * 0.10  （±10% 内）
      区间半宽 = 当前价 * vol_band，vol_band 由年化波动率给出（夹在 6%~25%）
      激进上移半个带宽，保守下移半个带宽。
    评级区间：买入 < 中性下沿 / 观察 中性区间内 / 风险 > 中性上沿。
    """
    if not last_close or last_close <= 0:
        return {"available": False, "note": "暂无"}

    avg = (tech_score + fund_score) / 2.0
    bias = max(-0.10, min(0.10, (avg - 50) / 50 * 0.10))
    center = last_close * (1 + bias)
    vol_band = max(0.06, min(0.25, volatility * 0.45)) if volatility else 0.10
    half = center * vol_band

    neutral = (round(center - half, 2), round(center + half, 2))
    aggressive = (round(center, 2), round(center + 2 * half, 2))
    conservative = (round(center - 2 * half, 2), round(center, 2))

    if last_close < neutral[0]:
        position = "买入"
        position_note = "当前价低于中性区间下沿，处于买入区间"
    elif last_close > neutral[1]:
        position = "风险"
        position_note = "当前价高于中性区间上沿，处于风险区间"
    else:
        position = "观察"
        position_note = "当前价处于中性区间内，处于观察区间"

    return {
        "available": True,
        "current_price": round(last_close, 2),
        "rows": [
            {"label": "激进买入", "low": aggressive[0], "high": aggressive[1]},
            {"label": "中性预期", "low": neutral[0], "high": neutral[1]},
            {"label": "保守预期", "low": conservative[0], "high": conservative[1]},
            {"label": "当前价格", "low": round(last_close, 2), "high": round(last_close, 2)},
        ],
        "rating_bands": {
            "buy_below": neutral[0],
            "watch_low": neutral[0],
            "watch_high": neutral[1],
            "risk_above": neutral[1],
        },
        "position": position,
        "position_note": position_note,
    }


def _ai_view(header: Dict, tech_score: float, fund_score: float, signal: str,
             fund: Dict, perf: Dict) -> Dict[str, object]:
    """AI 投资观点：看多逻辑 / 风险提示 / 关键催化。优先 LLM，降级走规则。"""
    name = header.get("name")
    industry = header.get("industry")

    if llm.available():
        prompt = (
            f"为 A 股【{name}（{header.get('symbol')}）】生成一页纸研报的投资观点。\n"
            f"所属行业：{industry}；技术面评分 {tech_score}/100；基本面评分 {fund_score}/100；"
            f"综合信号：{signal}。\n"
            f"近期表现：1日 {perf.get('d1')}% / 1月 {perf.get('m1')}% / 年初至今 {perf.get('ytd')}%。\n"
            f"财务：营收同比 {fund.get('revenue_yoy')}%，净利同比 {fund.get('net_profit_yoy')}%，"
            f"毛利率 {fund.get('gross_margin')}%，ROE {fund.get('roe')}%。\n"
            "请输出 JSON：{\"bull\":[3条看多逻辑],\"risk\":[3条风险提示],\"catalyst\":[2-3条关键催化]}，"
            "每条不超过 30 字，基于给定数据，不要编造具体数字。"
        )
        result = llm.chat_json(prompt, system="你是严谨的A股卖方分析师，只基于给定数据，不夸大。")
        if isinstance(result, dict) and (result.get("bull") or result.get("risk")):
            return {
                "source": "llm",
                "bull": [str(x) for x in (result.get("bull") or [])][:4],
                "risk": [str(x) for x in (result.get("risk") or [])][:4],
                "catalyst": [str(x) for x in (result.get("catalyst") or [])][:4],
            }

    # --- 规则降级 ---
    bull, risk, catalyst = [], [], []
    if tech_score >= 62:
        bull.append(f"技术面评分 {tech_score:.0f}，趋势与动量偏强")
    if fund_score >= 60:
        bull.append(f"基本面评分 {fund_score:.0f}，盈利质量较好")
    if (fund.get("revenue_yoy") or 0) > 0:
        bull.append(f"营收同比 +{fund.get('revenue_yoy')}%，处于扩张")
    if not bull:
        bull.append("当前各项评分中性，缺乏明确做多驱动")

    if tech_score < 45:
        risk.append(f"技术面评分仅 {tech_score:.0f}，短期偏弱")
    if (fund.get("net_profit_yoy") or 0) < 0:
        risk.append(f"净利同比 {fund.get('net_profit_yoy')}%，盈利承压")
    if (perf.get("ytd") or 0) > 40:
        risk.append("年内涨幅较大，注意获利回吐")
    if not risk:
        risk.append("宏观与板块波动可能带来回撤")

    if (fund.get("net_profit_yoy") or 0) > 0:
        catalyst.append("业绩持续兑现，关注下期财报")
    catalyst.append(f"{industry}板块景气度变化")
    if signal in ("buy", "strong_buy"):
        catalyst.append("量价信号转强，关注突破确认")

    return {"source": "rule", "bull": bull[:4], "risk": risk[:4], "catalyst": catalyst[:4]}


def _financial_summary(fund: Dict) -> Dict[str, object]:
    """核心财务摘要表（部分字段 akshare 不提供时返回 None）。"""
    if not fund:
        return {"available": False, "rows": [], "note": "暂无"}
    rows = [
        {"name": "营业收入", "value": fund.get("revenue"), "yoy": fund.get("revenue_yoy")},
        {"name": "归母净利润", "value": fund.get("net_profit"), "yoy": fund.get("net_profit_yoy")},
        {"name": "毛利率(%)", "value": fund.get("gross_margin"), "yoy": None},
        {"name": "净利率(%)", "value": fund.get("net_margin"), "yoy": None},
        {"name": "ROE(%)", "value": fund.get("roe"), "yoy": None},
        {"name": "经营现金流", "value": fund.get("operating_cashflow"), "yoy": None},
        {"name": "每股收益(元)", "value": fund.get("eps"), "yoy": None},
    ]
    return {"available": any(r["value"] is not None for r in rows), "rows": rows}


def _fundamental_score(fund: Dict) -> float:
    """从财务指标合成 0-100 基本面评分（简单规则；无数据时给中性 50）。"""
    if not fund:
        return 50.0
    score = 50.0
    roe = fund.get("roe")
    if roe is not None:
        score += max(-15, min(20, (roe - 8) * 1.2))
    nm = fund.get("net_margin")
    if nm is not None:
        score += max(-10, min(15, (nm - 8) * 0.8))
    rev_yoy = fund.get("revenue_yoy")
    if rev_yoy is not None:
        score += max(-12, min(12, rev_yoy * 0.3))
    np_yoy = fund.get("net_profit_yoy")
    if np_yoy is not None:
        score += max(-15, min(15, np_yoy * 0.25))
    return round(max(0.0, min(100.0, score)), 1)


_SIGNAL_LABEL = {"strong_buy": "强烈买入", "buy": "买入", "hold": "观察", "avoid": "回避"}


def _core_summary(header: Dict, tech_score: float, fund_score: float, signal: str, perf: Dict) -> str:
    rating = _SIGNAL_LABEL.get(signal, signal)
    parts = [
        f"{header.get('name')}（{header.get('symbol')}，{header.get('industry')}）综合评级：{rating}。",
        f"技术面 {tech_score:.0f}/100、基本面 {fund_score:.0f}/100。",
    ]
    if perf.get("ytd") is not None:
        parts.append(f"年初至今 {perf['ytd']:+.1f}%。")
    return "".join(parts)


# --- 主入口 -------------------------------------------------------------

def build_stock_report(symbol: str) -> Dict[str, object]:
    """组装个股一页纸研报。任何子区块失败都降级为占位，整体不抛栈。"""
    symbol = str(symbol).strip().zfill(6) if str(symbol).strip().isdigit() else str(symbol).strip()

    try:
        prof = fetch_profile(symbol)
    except Exception:
        prof = {}
    try:
        fund = fetch_fundamentals(symbol)
    except Exception:
        fund = {}

    data = _load_kline(symbol)
    has_data = not data.empty

    # 技术面评分（复用 factors 合成）
    if has_data:
        try:
            factors = compute_factor_scores(data)
            tech_score = composite_score(factors)
        except Exception:
            factors, tech_score = {}, 50.0
        try:
            risk = risk_metrics(data)
            volatility = abs(_f(risk.get("volatility"), 0.0))
        except Exception:
            risk, volatility = {}, 0.0
    else:
        factors, tech_score, risk, volatility = {}, 50.0, {}, 0.0

    fund_score = _fundamental_score(fund)
    signal = signal_from_score(tech_score)

    header = _header(symbol, prof, data)
    last_close = header.get("last_price") or 0.0
    perf = _market_performance(data) if has_data else {}

    try:
        valuation = _valuation_forest(_f(last_close), tech_score, fund_score, volatility)
    except Exception:
        valuation = {"available": False, "note": "暂无"}

    try:
        ai_view = _ai_view(header, tech_score, fund_score, signal, fund, perf)
    except Exception:
        ai_view = {"source": "rule", "bull": ["暂无"], "risk": ["暂无"], "catalyst": ["暂无"]}

    try:
        news = stock_news(symbol, days=7, limit=10)
    except Exception:
        news = []

    return {
        "available": has_data or bool(prof) or bool(fund),
        "header": header,
        "rating": {
            "signal": signal,
            "label": _SIGNAL_LABEL.get(signal, signal),
            "position": valuation.get("position") if valuation.get("available") else None,
            "position_note": valuation.get("position_note") if valuation.get("available") else None,
            "rating_bands": valuation.get("rating_bands") if valuation.get("available") else None,
        },
        "core_summary": _core_summary(header, tech_score, fund_score, signal, perf),
        "valuation_forest": valuation,
        "key_indicators": _key_indicators(data) if has_data else [],
        "scores": {
            "technical": tech_score,
            "fundamental": fund_score,
            "factors": factors,
        },
        "ai_view": ai_view,
        "financial_summary": _financial_summary(fund),
        "market_performance": perf,
        "institution_holdings": {"available": False, "note": "暂无"},
        "news": news,
        "disclaimer": "本研报由量化模型与公开数据自动生成，仅供研究参考，不构成投资建议。",
    }


# ============================================================================
# 待合并：在 app/routers/quant.py 末尾增加如下路由片段（本文件严禁直接改路由）：
#
#   from quantcore.quant.report_service import build_stock_report
#
#   @router.get("/report")
#   async def quant_report(symbol: str):
#       try:
#           return await asyncio.to_thread(build_stock_report, symbol)
#       except Exception as exc:
#           raise HTTPException(status_code=400, detail=str(exc)) from exc
# ============================================================================
