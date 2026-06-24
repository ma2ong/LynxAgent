"""个股行业解析（巨潮 cninfo 行业变更接口，带进程缓存 + 超时）。

stock_meta.industry 常为空，导致选股/形态结果的「行业/板块」显示为「-」。本模块复用
一键推荐已验证可用的 cninfo 取数逻辑，为结果项按需补全行业，并对网络做硬超时保护、
绝不拖死调用端点。
"""
from __future__ import annotations

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Dict, List

_cache: Dict[str, tuple] = {}  # symbol -> (ts, industry)
_TTL = timedelta(days=7)
_lock = threading.Lock()
_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="industry")


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
