"""评委打分（子项目 B）：5 位不同风格投资人对个股各打 0-100 分 + 立场 + 一句话理由，聚合共识分与分歧度。

设计：
- 一次 LLM(chat_json) 调用拿 5 条评分；无密钥/失败 → {empty, message}，调用方据此显示引导。
- 上下文 = 本地名称/行业 + 财务摘要(fundamentals) + 轻量量化信号(本地 kline 合成的趋势/动量/RSI + 近 60 日涨幅)。
- 纯组装，不改 DeepAnalysisFramework，不留存历史。
"""
from __future__ import annotations

import threading

from typing import Dict, List, Optional

from . import llm
from .data import load_local_kline
from .factors import compute_factor_scores, composite_score
from .fundamentals import fundamentals as fetch_fundamentals

_EMPTY = {"empty": True, "message": "评委打分需要配置 LLM 密钥，当前不可用"}

# 5 位评委人设（风格 key 用于前端配色/分组）
_PERSONAS = [
    {"persona": "价值派", "style": "value", "desc": "巴菲特式基本面，看护城河/ROE/现金流/估值安全边际"},
    {"persona": "趋势派", "style": "trend", "desc": "右侧动量交易者，看趋势强弱/量价配合，回避下跌趋势"},
    {"persona": "游资派", "style": "hot_money", "desc": "情绪短线，看题材热度/资金博弈/弹性，厌恶滞涨"},
    {"persona": "逆向派", "style": "contrarian", "desc": "左侧低估买入，看错杀/低位/预期差，回避追高"},
    {"persona": "量化派", "style": "quant", "desc": "因子中性，看多因子综合打分，不带情绪"},
]


def _name_industry(symbol: str) -> Dict[str, str]:
    try:
        from .local_store import get_local_store
        row = get_local_store()._conn().execute(
            "SELECT name, industry FROM stock_meta WHERE symbol=?", (symbol,)
        ).fetchone()
        if row:
            return {"name": str(row[0] or "").strip(), "industry": str(row[1] or "").strip()}
    except Exception:
        pass
    return {"name": "", "industry": ""}


def _signal_context(symbol: str) -> Dict[str, object]:
    """轻量量化信号：趋势/动量/RSI 评分 + 近 60 日累计涨幅 + 最新价。本地无数据则空。"""
    try:
        data = load_local_kline(symbol, days=120)
    except Exception:
        data = None
    if data is None or getattr(data, "empty", True):
        return {}
    try:
        factors = compute_factor_scores(data)
        score = composite_score(factors)
    except Exception:
        factors, score = {}, None
    close = data["close"]
    last = round(float(close.iloc[-1]), 2)
    chg60 = None
    if len(close) >= 61:
        base = float(close.iloc[-61])
        if base:
            chg60 = round((last / base - 1) * 100, 1)
    return {
        "last_price": last,
        "chg_60d": chg60,
        "composite": round(score, 1) if score is not None else None,
        "trend": factors.get("trend"),
        "momentum": factors.get("momentum"),
        "rsi": factors.get("rsi"),
    }


def _build_prompt(symbol: str, meta: Dict[str, str], fund: Dict, sig: Dict[str, object]) -> str:
    persona_lines = "\n".join(f"  - {p['persona']}（{p['style']}）：{p['desc']}" for p in _PERSONAS)
    return (
        f"对 A 股【{meta.get('name') or symbol}（{symbol}）】，由以下 5 位不同风格的投资人各独立打分。\n"
        f"行业：{meta.get('industry') or '未知'}\n"
        f"量化信号：综合分 {sig.get('composite')}/100，趋势 {sig.get('trend')}，动量 {sig.get('momentum')}，"
        f"RSI {sig.get('rsi')}，近 60 日涨幅 {sig.get('chg_60d')}%，最新价 {sig.get('last_price')}。\n"
        f"财务：营收同比 {fund.get('revenue_yoy')}%，净利同比 {fund.get('net_profit_yoy')}%，"
        f"毛利率 {fund.get('gross_margin')}%，净利率 {fund.get('net_margin')}%，ROE {fund.get('roe')}%，"
        f"每股收益 {fund.get('eps')}。\n"
        "评委：\n" + persona_lines + "\n"
        "每位评委按其风格给 0-100 分（越高越看好），给出立场（看多/中性/看空）和一句不超过 30 字的理由。\n"
        '只输出 JSON：{"verdicts":[{"persona":"价值派","style":"value","score":72,'
        '"stance":"看多","reason":"..."}, ...]}，必须 5 条、style 用上面给定值，基于给定数据不要编造具体数字。'
    )


def _normalize_score(v) -> float:
    try:
        s = float(v)
    except (TypeError, ValueError):
        return 50.0
    return max(0.0, min(100.0, s))


def _normalize_stance(v) -> str:
    s = str(v or "").strip()
    if "多" in s or s.lower() in ("bull", "buy", "positive"):
        return "看多"
    if "空" in s or s.lower() in ("bear", "sell", "negative"):
        return "看空"
    return "中性"


def investor_panel(symbol: str) -> Dict[str, object]:
    """5 评委打分 + 聚合。无 LLM/失败 → {empty, message}。"""
    symbol = str(symbol).strip().zfill(6) if str(symbol).strip().isdigit() else str(symbol).strip()
    if not llm.available():
        return dict(_EMPTY)

    meta = _name_industry(symbol)
    try:
        fund = fetch_fundamentals(symbol) or {}
    except Exception:
        fund = {}
    sig = _signal_context(symbol)

    result = llm.chat_json(
        _build_prompt(symbol, meta, fund, sig),
        system="你是 A 股资深投研主持人，让 5 位不同流派投资人各自独立、互不趋同地打分，立场要有分歧。只基于给定数据。",
    )
    verdicts_raw = (result or {}).get("verdicts") if isinstance(result, dict) else None
    if not verdicts_raw:
        return {**_EMPTY, "message": "评委打分生成失败，请稍后重试"}

    style_to_persona = {p["style"]: p["persona"] for p in _PERSONAS}
    verdicts: List[Dict[str, object]] = []
    for item in verdicts_raw[:5]:
        if not isinstance(item, dict):
            continue
        style = str(item.get("style") or "").strip()
        score = _normalize_score(item.get("score"))
        stance = _normalize_stance(item.get("stance"))
        verdicts.append({
            "persona": str(item.get("persona") or style_to_persona.get(style) or "评委").strip(),
            "style": style or "quant",
            "score": round(score, 1),
            "stance": stance,
            "reason": str(item.get("reason") or "").strip()[:60],
        })
    if not verdicts:
        return {**_EMPTY, "message": "评委打分生成失败，请稍后重试"}

    scores = [v["score"] for v in verdicts]
    consensus = round(sum(scores) / len(scores), 1)
    divergence = round(max(scores) - min(scores), 1)
    bull_count = sum(1 for v in verdicts if v["stance"] == "看多")
    bear_count = sum(1 for v in verdicts if v["stance"] == "看空")

    if consensus >= 65 and bull_count > bear_count:
        summary = f"共识偏多（{consensus} 分），{bull_count} 位看多/{bear_count} 位看空，分歧 {divergence} 分。"
    elif consensus <= 45 and bear_count > bull_count:
        summary = f"共识偏空（{consensus} 分），{bear_count} 位看空/{bull_count} 位看多，分歧 {divergence} 分。"
    else:
        summary = f"分歧较大（共识 {consensus} 分，极差 {divergence} 分），看多 {bull_count}/看空 {bear_count}。"

    return {
        "empty": False,
        "symbol": symbol,
        "name": meta.get("name") or symbol,
        "consensus_score": consensus,
        "divergence": divergence,
        "bull_count": bull_count,
        "bear_count": bear_count,
        "verdicts": verdicts,
        "summary": summary,
    }


from .local_store import get_local_store

# 批量评分：跨请求去重（同一 symbol 只允许一个在途评分）
_PANEL_LOCK = threading.Lock()
_PANEL_INFLIGHT: set = set()


def run_panel_batch(date: str, symbols: List[str]) -> int:
    """顺序为缺评分的 symbol 打分并落库，返回新打分数量。

    - 已有当日评分/正在评分中的跳过（跨池复用，控 LLM 成本）；
    - 单线程顺序调用（每只一次 LLM），失败的静默跳过下次再补；
    - 供 API 层丢进后台线程执行，勿在请求路径同步调用。
    """
    store = get_local_store()
    scored = set(store.load_panel_scores(date, symbols).keys())
    with _PANEL_LOCK:
        todo = [s for s in symbols if s not in scored and s not in _PANEL_INFLIGHT]
        _PANEL_INFLIGHT.update(todo)
    done = 0
    try:
        for symbol in todo:
            try:
                result = investor_panel(symbol)
                if isinstance(result, dict) and not result.get("empty"):
                    store.save_panel_score(date, symbol, result)
                    done += 1
            except Exception:
                continue
    finally:
        with _PANEL_LOCK:
            _PANEL_INFLIGHT.difference_update(todo)
    return done
