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
from typing import Any, Dict, List, Tuple

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


SECTOR_MIN_MEMBERS = 3   # 成分不足这个数的板块不参与强弱排序（一只涨停票不是一个板块）
SECTOR_MIN_RANKED = 5    # 可排板块少于这个数就没有横截面可言


def sector_rank(stats: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    """{板块: {"mean": 当日涨幅均值, "count": 成分数}} → {板块: 横截面分位 0..1}。

    为什么用分位而不是涨幅本身：涨幅是有量纲的，得挑一个系数把它换算成分数，而任何
    系数都会在某个涨幅上饱和（雷达原来 clip 在 ±18，板块涨过 3% 就再也拉不开差距）。
    分位天然无量纲——不管今天是普涨还是普跌，"最强的那批板块"总能被排出来。

    可排板块不足或成分不足的一律给 0.5（中性），返回值只包含 stats 里出现过的板块。
    """
    import bisect

    ranked = sorted(
        _f_num(s.get("mean")) for s in stats.values()
        if int(s.get("count") or 0) >= SECTOR_MIN_MEMBERS
    )
    out: Dict[str, float] = {}
    for name, stat in stats.items():
        if len(ranked) < SECTOR_MIN_RANKED or int(stat.get("count") or 0) < SECTOR_MIN_MEMBERS:
            out[name] = 0.5
            continue
        mean = _f_num(stat.get("mean"))
        below = bisect.bisect_left(ranked, mean)
        above = bisect.bisect_right(ranked, mean)
        out[name] = (below + above) / 2 / len(ranked)
    return out


def sector_stats_from_quotes(
    quotes: Dict[str, Dict[str, Any]],
    industry_by_symbol: Dict[str, str],
) -> Dict[str, Dict[str, float]]:
    """全市场实时快照 → {板块: {"mean", "count"}}。用于「今天哪些板块在爆发」。"""
    buckets: Dict[str, List[float]] = {}
    for symbol, quote in quotes.items():
        industry = industry_by_symbol.get(str(symbol).zfill(6))
        if not industry or not isinstance(quote, dict):
            continue
        pct = quote.get("change_percent")
        if pct is None:
            pct = quote.get("pct_chg")
        if pct is None:
            continue
        try:
            buckets.setdefault(industry, []).append(float(pct))
        except (TypeError, ValueError):
            continue
    return {
        name: {"mean": sum(values) / len(values), "count": len(values)}
        for name, values in buckets.items()
    }


def live_theme_ranks(
    quotes: Dict[str, Dict[str, Any]],
    bucket_maps: List[Dict[str, str]],
) -> Dict[str, Tuple[float, str]]:
    """{代码: (当日主题强度分位 0..1, 主题名)}。多套分桶各算各的，每只票取**最强**的那个。

    bucket_maps 依次传行业映射与概念映射：
    - 行业（申万 128 个）覆盖全市场，粗粒度；
    - 概念（东财 89 个热门板块）只覆盖约 4100 只，但粒度对得上真实主线 ——
      2026-08-05 实测存储芯片 +6.34%(分位 99.4)、MLCC +6.03%(98.2)，而它们所属的
      半导体行业 +5.78%、元件 +4.98%。用户嘴里的"热点"说的是前者。

    只在**可排**的桶之间取 max：成分不足的桶 sector_rank 会给 0.5，若把它一并纳入 max，
    冷门行业的票会被一个中性概念凭空托到 0.5，等于把负信号抹平。所以先按成分数筛掉。
    """
    out: Dict[str, Tuple[float, str]] = {}
    for bucket_by_symbol in bucket_maps:
        if not bucket_by_symbol:
            continue
        stats = sector_stats_from_quotes(quotes, bucket_by_symbol)
        ranks = sector_rank(stats)
        for symbol, bucket in bucket_by_symbol.items():
            if int(stats.get(bucket, {}).get("count") or 0) < SECTOR_MIN_MEMBERS:
                continue
            rank = ranks.get(bucket)
            if rank is None:
                continue
            code = str(symbol).zfill(6)
            if code not in out or rank > out[code][0]:
                out[code] = (float(rank), str(bucket))
    return out


def _f_num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def industry_stage_scores(
    stage_inputs: Dict[str, Dict[str, float]],
    industry_by_symbol: Dict[str, str],
    min_members: int = 3,
) -> Dict[str, float]:
    """板块量价阶段分（0-100，未做百分位）。

    按量价关系判阶段：缩量回撤=散户在交筹码(可潜伏)，放量启动=大资金进场(要买)，
    高位放量且已大涨=主升段(要买)。

    **2026-08-05 改：主升段由罚 30 分改为奖 12 分**（按 Allen 的决定：不惩罚正在
    爆发的板块）。要说明的是这条在当日实测里 **0/126 个行业触发**，属于聊胜于无的
    表态性改动 —— 「慢半拍」的真因不在这里。

    真因是**本函数的输入天然落后一个交易日**：`stage_inputs` 只取 amount>0 的真实
    bar，跳过盘中占位 bar，所以最新收盘永远是昨天。2026-08-05 当天半导体全市场
    +4.55%，而本因子给它的百分位是 4.8（因为"截至昨天"它最弱）；软件开发百分位 96
    （因为"截至昨天"它最强）—— 用户看到的正是这个。要跟上当日热点必须把盘中的
    板块涨幅混进来，那一层在 engine 的实时重排里做，不在这个日线函数里。

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
            score += 12.0                            # 主升段：高位 + 放量 + 已大涨
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
