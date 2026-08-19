"""评委打分（子项目 B）：5 位不同风格投资人对个股各打 0-100 分 + 立场 + 一句话理由，聚合共识分与分歧度。

设计：
- 五派各自按规则打分（见 panel_rules），不调 LLM；数据不足 → {empty, message}。
- 上下文 = 本地名称/行业 + 财务摘要(fundamentals) + 轻量量化信号(本地 kline 合成的趋势/动量/RSI + 近 60 日涨幅)。
- 纯组装，不改 DeepAnalysisFramework，不留存历史。
"""
from __future__ import annotations

import threading

from typing import Dict, List, Optional

from .data import load_local_kline
from .factors import compute_factor_scores, composite_score
from .panel_rules import score_panel
from .fundamentals import fundamentals as fetch_fundamentals

_EMPTY = {"empty": True, "message": "评分所需数据不足"}

# 5 位评委人设（风格 key 用于前端配色/分组）。真正的打分逻辑在 panel_rules，
# 这里保留描述文本是因为前端还在用它做说明。
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


def investor_panel(symbol: str) -> Dict[str, object]:
    """5 评委打分 + 聚合。规则实现，不调 LLM，同样输入永远同样输出。

    2026-08-19 从 LLM 换成规则（理由见 panel_rules 的模块注释）：那五个「人格」本来
    就是五种因子偏好，数据产品里全都有；换掉之后可复现、可回测、零成本，而且
    panel_eval 的「共识分到底有没有预测力」这个实验第一次真正能做 —— 非确定性的
    输入根本没法做这个实验。
    """
    symbol = str(symbol).strip().zfill(6) if str(symbol).strip().isdigit() else str(symbol).strip()

    meta = _name_industry(symbol)
    try:
        fund = fetch_fundamentals(symbol) or {}
    except Exception:
        fund = {}
    sig = _signal_context(symbol)
    if not sig:
        return {**_EMPTY, "message": "本地缺少该股日线数据，无法评分"}

    try:
        data = load_local_kline(symbol, days=120)
        factors = compute_factor_scores(data) if data is not None and not getattr(data, "empty", True) else {}
    except Exception:
        factors = {}

    panel = score_panel(
        factors=factors,
        fundamentals=fund,
        composite=sig.get("composite"),
        chg60=sig.get("chg_60d"),
    )
    return {
        "empty": False,
        "symbol": symbol,
        "name": meta.get("name") or symbol,
        "consensus_score": panel["consensus"],
        "divergence": panel["divergence"],
        "bull_count": panel["bull"],
        "bear_count": panel["bear"],
        "verdicts": panel["verdicts"],
        "summary": panel["summary"],
        "method": panel["method"],
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
