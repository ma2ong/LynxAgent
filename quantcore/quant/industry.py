"""个股行业解析（巨潮 cninfo 行业变更接口，带进程缓存 + 超时）。

stock_meta.industry 常为空，导致选股/形态结果的「行业/板块」显示为「-」。本模块复用
一键推荐已验证可用的 cninfo 取数逻辑，为结果项按需补全行业，并对网络做硬超时保护、
绝不拖死调用端点。
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

_cache: Dict[str, tuple] = {}  # symbol -> (ts, industry)
_TTL = timedelta(days=7)
_lock = threading.Lock()
_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="industry")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MAP_PATH = os.environ.get("INDUSTRY_MAP_PATH", str(_PROJECT_ROOT / "runtime" / "industry_map.json"))
_map_cache: tuple[float, Dict[str, str]] | None = None  # (mtime, mapping)


def industry_map() -> Dict[str, str]:
    """全市场 代码->行业 映射（读 app 侧维护的 runtime/industry_map.json，按 mtime 失效）。

    这是给"要按行业聚合"的批量场景用的，不走 cninfo 逐只补全。之所以不读
    stock_meta.industry：那一列实测 100% 为空（见模块开头），拿它做行业聚合会得到
    单一空行业 → 任何行业因子恒等于中性值，静默失效。
    """
    global _map_cache
    try:
        mtime = os.path.getmtime(_MAP_PATH)
    except OSError:
        return {}
    if _map_cache and _map_cache[0] == mtime:
        return _map_cache[1]
    try:
        with open(_MAP_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return _map_cache[1] if _map_cache else {}
    mapping = {
        str(k).zfill(6): _industry_text(v)
        for k, v in (raw or {}).items()
        if _industry_text(v)
    }
    _map_cache = (mtime, mapping)
    return mapping


def industry_stage_scores(
    stage_inputs: Dict[str, Dict[str, float]],
    industry_by_symbol: Dict[str, str],
    min_members: int = 3,
) -> Dict[str, float]:
    """板块量价阶段分（0-100，未做百分位）。

    换掉了"行业近5日涨幅百分位"那版纯动量口径。纯涨幅只回答"涨了多少"，不回答
    "涨到哪个阶段了"——它给分最高的恰恰是放量赶顶的板块，而那是最该躲的位置。
    这里按量价关系判阶段：缩量回撤=散户在交筹码(可潜伏)，放量启动=大资金进场(要买)，
    高位放量且已大涨=赶顶(要罚)。

    输入是全市场个股的 stage_inputs，按行业等权聚合；成分不足 min_members 的行业
    不出分（单只票的波动不配代表一个板块）。
    """
    members: Dict[str, List[Dict[str, float]]] = {}
    for symbol, row in stage_inputs.items():
        ind = industry_by_symbol.get(symbol)
        if ind:
            members.setdefault(ind, []).append(row)

    out: Dict[str, float] = {}
    for ind, rows in members.items():
        if len(rows) < min_members:
            continue
        mom5 = _mean([_pct(r["close"], r["close_5"]) for r in rows])
        mom20 = _mean([_pct(r["close"], r["close_20"]) for r in rows])
        pos = _mean([
            (r["close"] - r["low"]) / (r["high"] - r["low"])
            for r in rows if r["high"] > r["low"]
        ])
        amt5 = sum(r["amt5"] for r in rows)
        amt20 = sum(r["amt20"] for r in rows)
        # 量能扩张：近5日日均额 / 近20日日均额 - 1。>0 放量，<0 缩量。
        vol_exp = ((amt5 / 5) / (amt20 / 20) - 1) if amt20 > 0 else 0.0

        score = 50.0
        score += _clip(vol_exp, -0.5, 1.0) * 40      # 放量加分、缩量减分
        score += _clip(mom5, -8.0, 8.0) * 1.8        # 近期在涨加分
        if pos > 0.85 and vol_exp > 0.30 and mom20 > 12.0:
            score -= 30.0                            # 放量赶顶：高位 + 放量 + 已大涨
        if pos < 0.40 and vol_exp < -0.15:
            score += 8.0                             # 缩量回撤到低位：可潜伏，等它放量
        out[ind] = _clip(score, 0.0, 100.0)
    return out


def _pct(now: float, base: float) -> float:
    return (now / base - 1) * 100 if base > 0 else 0.0


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


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
    preferred = ("巨潮行业分类标准", "中证行业分类标准", "中国上市公司协会上市公司行业分类标准")
    for standard in preferred:
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


def _fetch_cninfo(symbol: str) -> str:
    import akshare as ak

    # cninfo 接口无超时控制，用 socket 默认超时封顶，避免限流时长时间阻塞工作线程。
    _old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(6)
    try:
        df = ak.stock_industry_change_cninfo(
            symbol=str(symbol).strip().zfill(6),
            start_date="20100101",
            end_date=datetime.now().strftime("%Y%m%d"),
        )
    finally:
        socket.setdefaulttimeout(_old)
    return _pick_cninfo_industry(df)


def get_industry(symbol: str) -> str:
    """返回单只股票的行业（7 天进程缓存）；失败返回空串。"""
    sym = str(symbol or "").strip().zfill(6)
    if not sym.isdigit():
        return ""
    now = datetime.now()
    with _lock:
        cached = _cache.get(sym)
        if cached and now - cached[0] < _TTL:
            return cached[1]
    try:
        industry = _fetch_cninfo(sym)
    except Exception:
        industry = ""
    with _lock:
        _cache[sym] = (now, industry)
    return industry


# 非真实行业的占位值：这些都视为「未解析」，需要用 cninfo 重新补全。
_PLACEHOLDERS = {"", "-", "A股", "行业待识别", "待识别", "未识别", "其他"}


def _needs_industry(it: Dict[str, Any]) -> bool:
    val = str(it.get("industry") or it.get("board") or "").strip()
    return val in _PLACEHOLDERS


def enrich_industries(items: List[Dict[str, Any]], timeout: float = 20.0) -> List[Dict[str, Any]]:
    """给结果项批量补全 industry/board（未填或占位值的），并行 + 整体超时，绝不拖死端点。"""
    if not items:
        return items
    targets = [it for it in items if isinstance(it, dict) and _needs_industry(it)]
    if not targets:
        return items
    syms = {str(it.get("symbol") or it.get("code") or "").strip().zfill(6) for it in targets}
    syms = {s for s in syms if s.isdigit()}
    if not syms:
        return items

    futures = {sym: _POOL.submit(get_industry, sym) for sym in syms}
    deadline = time.time() + timeout
    resolved: Dict[str, str] = {}
    for sym, fut in futures.items():
        try:
            resolved[sym] = fut.result(timeout=max(0.1, deadline - time.time()))
        except Exception:
            resolved[sym] = ""
    for it in targets:
        sym = str(it.get("symbol") or it.get("code") or "").strip().zfill(6)
        industry = resolved.get(sym)
        if industry:
            it["industry"] = industry
            it["board"] = industry
    return items
