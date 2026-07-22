from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests

from .backtest import run_long_only_backtest
from .backtrader_adapter import BacktraderUnavailable, run_backtrader_backtest
from .data import _fetch_from_akshare, default_start_date, fetch_stock_dataframe, load_local_kline, normalize_ohlcv
from .datalake import AKShareDataLake
from .local_store import get_local_store
from .screening import REASON_LABELS, exclusion_reason
from .factor_agent import FactorResearchAgent
from .factors import composite_score, compute_factor_scores, indicator_snapshot, latest_adx, latest_atr, ml_feature_snapshot, risk_metrics, signal_from_score, swing_short_score, trade_plan
from .hmm import multi_asset_hmm
from .integrations import integration_capabilities, kronos_style_forecast, recognize_patterns, run_akquant_backtest_adapter
from .models import BacktestResult, ForecastResult, PatternRecognitionResult, QuantAnalysisResult, QuantPick
from .strategies import STRATEGIES, resolve_strategy
from .wyckoff import analyze_wyckoff


def _market_quote_code(symbol: str) -> str:
    clean = symbol.strip().zfill(6)
    if clean.startswith(("6", "9")):
        return f"sh{clean}"
    if clean.startswith(("8", "4")):
        return f"bj{clean}"
    return f"sz{clean}"


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value in ("", "-", None):
            return default
        number = float(value)
        return default if number != number else number
    except (TypeError, ValueError):
        return default


import math


def _json_safe(value):
    """递归把 NaN/Inf 替换为 0，避免 FastAPI JSON 序列化报 500。"""
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


# 单一定义在轻量的 risk_check（worker 子进程 import 链考虑），此处再导出保持既有引用不变
from .risk_check import board_limit_pct  # noqa: E402


def is_limit_up(symbol: str, pct_chg: float) -> bool:
    """当前是否封在涨停（含接近涨停）。

    回放数据：形态池 42.6% 的入选票入选当日已涨停——按展示的收盘价根本买不到。
    标注出来，用户才知道这些票要按次日开盘价入场（历史上超额仍在，但会缩水）。
    """
    return _safe_float(pct_chg, 0) >= board_limit_pct(symbol) * 0.98


# 历史 K 线按 (symbol, 天数, 当日) 缓存，让形态扫描的重复运行秒级返回（冷启动仍需逐只抓取）
_HIST_CACHE: Dict[str, Tuple[str, "pd.DataFrame"]] = {}


def _hist_dataframe(symbol: str, days: int) -> "pd.DataFrame":
    """直连 akshare 抓取并缓存当日历史 K 线；失败返回空 DataFrame，不走 yfinance 慢回退。"""
    today = date.today().isoformat()
    key = f"{symbol}:{days}"
    cached = _HIST_CACHE.get(key)
    if cached and cached[0] == today:
        return cached[1]
    start = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    end = date.today().strftime("%Y-%m-%d")
    try:
        df = normalize_ohlcv(_fetch_from_akshare(symbol, start, end))
    except Exception:
        df = pd.DataFrame()
    _HIST_CACHE[key] = (today, df)
    return df



def _fetch_tencent_quote_chunk(chunk: List[str]) -> Dict[str, Dict[str, object]]:
    headers = {"User-Agent": "Mozilla/5.0"}
    out: Dict[str, Dict[str, object]] = {}
    query = ",".join(_market_quote_code(symbol) for symbol in chunk)
    try:
        response = requests.get(f"https://qt.gtimg.cn/q={query}", headers=headers, timeout=8)
        response.encoding = "gbk"
        response.raise_for_status()
    except Exception:
        return out
    for raw_item in response.text.split(";"):
        if "~" not in raw_item:
            continue
        left, _, payload = raw_item.partition('="')
        symbol = left[-6:]
        fields = payload.strip('"').split("~")
        if len(fields) < 39:
            continue
        out[symbol] = {
            "symbol": symbol,
            "name": fields[1].strip() or symbol,
            "price": _safe_float(fields[3], 0),
            "pct_chg": _safe_float(fields[32], 0),
            "amount": _safe_float(fields[37], 0) * 10000 if len(fields) > 37 else 0,
            "turnover_rate": _safe_float(fields[38], 0) if len(fields) > 38 else 0,
            "volume_ratio": _safe_float(fields[49], 0) if len(fields) > 49 else 0,
        }
    return out


def _fetch_tencent_quotes(symbols: List[str]) -> Dict[str, Dict[str, object]]:
    """批量实时行情。全市场约 5000 只需分 ~63 批，并发拉取以压缩耗时。"""
    chunks = [symbols[start:start + 80] for start in range(0, len(symbols), 80)]
    quotes: Dict[str, Dict[str, object]] = {}
    if not chunks:
        return quotes
    with ThreadPoolExecutor(max_workers=min(12, len(chunks))) as executor:
        for partial in executor.map(_fetch_tencent_quote_chunk, chunks):
            quotes.update(partial)
    return quotes


# market_context 的全表窗口扫描冷查询可达 20s+，页面横幅等不起。按快照日期分槽缓存：
# 盘中口径（横幅）与日线口径（池子留痕）各留一份，互不挤掉对方，否则两边会来回互相作废。
_MARKET_CTX_CACHE: Dict[str, Tuple[float, Dict[str, object]]] = {}
_CTX_TTL_INTRADAY = 60.0   # 盘中要跟着刷新走
_CTX_TTL_DAILY = 600.0     # 日线级数据 10 分钟足够


def _day_label(median_pct: float, breadth_up: float) -> str:
    """单日脉冲标签（个股口径）。"""
    if median_pct >= 1.0 and breadth_up >= 0.55:
        return "普涨"
    if median_pct <= -1.0 or breadth_up <= 0.40:
        return "普跌"
    return "企稳" if breadth_up >= 0.50 else "分化"


def _snapshot_stamp(snapshot: Optional[Dict[str, Dict]]) -> Tuple[str, str]:
    """实时快照自报的行情日期与最新时刻 → ("YYYY-MM-DD", "HH:MM")。取不到返回 ("","")。

    必须用快照自己的时间戳，不能用本机日期：节假日/休市时快照停在上一交易日，
    按本机日期算就会把上一交易日的收盘当成「今天盘中」重复计一天。
    时刻用于界面标注（"15:00 实时" vs "10:31 实时"），让用户知道数据新到什么时候。
    """
    import re as _re
    if not snapshot:
        return "", ""
    dates: Dict[str, int] = {}
    times: Dict[str, str] = {}
    for q in list(snapshot.values())[:200]:
        m = _re.match(r"(\d{4})[/-](\d{2})[/-](\d{2})[ T]?(\d{2}:\d{2})?", str((q or {}).get("updated_at") or ""))
        if m:
            key = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            dates[key] = dates.get(key, 0) + 1
            if m.group(4) and m.group(4) > times.get(key, ""):
                times[key] = m.group(4)
    if not dates:
        return "", ""
    day = max(dates, key=dates.get)
    return day, times.get(day, "")


def _snapshot_breadth(snapshot: Dict[str, Dict]) -> Optional[Dict[str, float]]:
    """实时快照 → 当日盘中中位涨幅% / 上涨家数占比。样本不足返回 None。"""
    import statistics
    pcts: List[float] = []
    for q in snapshot.values():
        v = (q or {}).get("change_percent")
        if v is None:
            v = (q or {}).get("pct_chg")
        if v is None:
            continue
        try:
            pcts.append(float(v))
        except (TypeError, ValueError):
            continue
    if len(pcts) < 500:
        return None
    return {
        "median_pct": round(statistics.median(pcts), 2),
        "breadth_up": round(sum(1 for v in pcts if v > 0) / len(pcts), 4),
        "count": len(pcts),
    }


def market_context(snapshot: Optional[Dict[str, Dict]] = None) -> Dict[str, object]:
    """大盘环境：个股口径（逐日中位/广度加权温度）+ 指数口径，背离显式标注。

    两个口径回答的是两个问题，缺一个就会出现「指数大涨但标签偏冷」这种看似矛盾的输出：
    - 指数口径 = 用户口中的「大盘涨没涨」（市值加权，权重股能单独拉出来）；
    - 个股口径 = 「我的票好不好做」（中位数 + 上涨广度，决定短线信号胜率）。
    环境标签 state 取个股口径——选股系统的仓位决策该跟赚钱效应走，不跟指数点位走。

    本地数据不足时返回空 dict，不影响主流程；指数取不到时降级为纯个股口径。
    """
    import time as _time
    from .regime import blend_temp, classify
    now = _time.time()
    snap_date, snap_time = _snapshot_stamp(snapshot)
    cached = _MARKET_CTX_CACHE.get(snap_date)
    if cached:
        ttl = _CTX_TTL_INTRADAY if cached[1].get("intraday") else _CTX_TTL_DAILY
        if now - cached[0] < ttl:
            return cached[1]
    result: Dict[str, object] = {}
    try:
        store = get_local_store()
        daily = store.recent_daily_breadth(days=5)
        if not daily:
            _MARKET_CTX_CACHE[snap_date] = (now, result)
            return result

        # as_of=最后一根真实日线（amount>0）
        as_of = str(daily[0].get("date") or "")
        try:
            as_of = store.latest_real_bar_date() or as_of
        except Exception:
            pass

        # 实时快照只要不比库里旧就优先用：盘中增量同步会把当天的半截 bar 写进 daily_kline
        # （as_of 变成今天但数值停在同步那一刻），只判断 snap_date > as_of 会让整个交易日
        # 都用不上实时数据。同日则替换掉那根半截 bar，跨日则插到最前挤掉最老一天。
        intraday = False
        if snap_date and snap_date >= as_of:
            today = _snapshot_breadth(snapshot or {})
            if today:
                daily = [{"date": snap_date, **today}] + [d for d in daily if d["date"] != snap_date][:4]
                as_of = snap_date
                intraday = True

        temp = blend_temp([(float(d["median_pct"]), float(d["breadth_up"])) for d in daily])
        state = classify(temp)
        latest = daily[0]
        d_median = float(latest["median_pct"])
        d_breadth = float(latest["breadth_up"])

        # 近 5 日累计口径：仍然有用（回答"这波跌了多少"），但不再决定标签
        median_5d, breadth_5d = 0.0, 0.0
        try:
            vals = sorted(store.recent_returns(window=5).values())
            if vals:
                median_5d = vals[len(vals) // 2]
                breadth_5d = sum(1 for v in vals if v > 0) / len(vals)
        except Exception:
            pass

        # 指数口径必须和个股口径同一天，否则会出现「横幅创业板 +7% / 顶部宏观条 -3%」这种
        # 同屏打架：盘中用实时行情，收盘后（日线已同步）用对齐 as_of 的历史。
        index_items: List[Dict[str, object]] = []
        try:
            from .macro_bar import fetch_index_history, fetch_index_quotes
            if intraday:
                hist = {str(i["code"]): i for i in fetch_index_history(as_of="", window=5)}
                for q in fetch_index_quotes():
                    code = str(q.get("code") or "")
                    index_items.append({
                        "code": code,
                        "name": str(q.get("name") or ""),
                        "date": snap_date,
                        "last_pct": round(float(q.get("change_percent") or 0), 2),
                        "window_pct": float((hist.get(code) or {}).get("window_pct") or 0),
                    })
            else:
                index_items = fetch_index_history(as_of=as_of, window=5)
        except Exception:
            index_items = []

        # 背离：指数昨日均值 vs 个股昨日中位。差 1pp 以上才算，避免噪声误报。
        divergence = ""
        idx_last = None
        if index_items:
            idx_last = sum(float(i["last_pct"]) for i in index_items) / len(index_items)
            gap = idx_last - d_median
            if gap >= 1.0:
                divergence = "指数强于个股——权重拉指数，中位股没跟上，赚指数不赚钱"
            elif gap <= -1.0:
                divergence = "个股强于指数——普涨但权重股拖累指数，题材/小盘活跃"

        day_label = _day_label(d_median, d_breadth)
        rebound = state == "偏冷" and d_breadth >= 0.50 and d_median > 0
        if rebound:
            day_label = "反弹"
        when = "今日盘中" if intraday else "最新一日"

        # 建议跟着两个口径一起走：先说个股赚钱效应，再说与指数的背离，最后给动作
        if state == "偏暖":
            advice = "赚钱效应好，短线信号胜率通常较高，可正常参与。"
        elif state == "偏冷":
            advice = "赚钱效应差，短线信号胜率系统性下降，建议降低仓位或观望。"
        else:
            advice = "市场分化，优先选强主线，控制单票仓位。"
        if rebound:
            advice = (f"急跌后{when}企稳反弹（{d_breadth * 100:.0f}% 上涨、中位 "
                      f"{'+' if d_median >= 0 else ''}{d_median:.1f}%），但加权温度仍偏冷——"
                      "反弹持续性待确认，轻仓参与强势方向、不追高。")
        if divergence:
            advice = f"{advice}｜{divergence}。"

        result = {
            "state": state,
            "temp": round(temp, 1),
            # 逐日序列（最新在前）：让用户自己看清标签是被哪几天压住的
            "daily": daily,
            "median_5d_pct": round(median_5d, 2),
            "breadth_up": round(breadth_5d, 4),
            "latest_day": {
                "median_pct": round(d_median, 2),
                "breadth_up": round(d_breadth, 4),
                "label": day_label,
                "rebound": rebound,
            },
            "index": {
                "items": index_items,
                "last_pct": round(idx_last, 2) if idx_last is not None else None,
            },
            "divergence": divergence,
            "as_of": as_of,
            # 实时口径 vs 收盘口径：前端要据此说清数据新到哪一刻，不能只写日期
            "intraday": intraday,
            "as_of_time": snap_time if intraday else "",
            "advice": advice,
        }
        _MARKET_CTX_CACHE[snap_date] = (now, result)
        return result
    except Exception:
        return result


def _pattern_scan_one(payload: Dict[str, object]) -> Optional[Dict[str, object]]:
    """进程池 worker：读本地 K 线 + 形态识别。必须是顶层函数以便 pickle。失败/无命中返回 None。"""
    symbol = str(payload["symbol"])
    min_strength = float(payload["min_strength"])
    min_bars = int(payload["min_bars"])
    quote = payload.get("quote") or {}
    try:
        data = load_local_kline(symbol, days=540)
        if data is None or data.empty or len(data) < min_bars:
            return None
        recognition = recognize_patterns(symbol, data)
        matched = [
            item for item in recognition.patterns
            if item.get("active") and _safe_float(item.get("strength"), 0) >= min_strength
        ]
        if not matched:
            return None
        factors = compute_factor_scores(data)
        quant_score = composite_score(factors)
        risk = risk_metrics(data)
        wyckoff = analyze_wyckoff(data)
        latest = data.iloc[-1]
        price = _safe_float(quote.get("price"), _safe_float(latest.get("close"), 0))
        pct_chg = _safe_float(quote.get("pct_chg"), 0)
        amount = _safe_float(quote.get("amount"), _safe_float(latest.get("amount"), 0))
        pattern_score = max(_safe_float(item.get("strength"), 0) for item in matched)
        realtime_score = max(0.0, min(100.0, 55 + pct_chg * 3.8 + min(amount / 100000000, 10) * 2.0))
        adx_val = latest_adx(data)
        adx_adj = 4.0 if adx_val >= 25 else (-4.0 if 0 < adx_val < 20 else 0.0)
        wyckoff_adj = (float(wyckoff.get("score") or 50.0) - 50.0) * 0.08
        score = round(pattern_score * 0.52 + quant_score * 0.30 + realtime_score * 0.18 + adx_adj + wyckoff_adj, 1)
        reasons = []
        for pattern in matched[:4]:
            reasons.append(f"{pattern.get('name')} {float(pattern.get('strength') or 0):.1f}")
        if pct_chg:
            reasons.append(f"实时涨跌幅 {pct_chg:+.2f}%")
        if amount:
            reasons.append(f"成交额 {amount / 100000000:.2f}亿")
        if adx_val:
            reasons.append(f"ADX {adx_val:.0f}")
        if wyckoff.get("phase"):
            reasons.append(f"Wyckoff {wyckoff.get('phase')} {float(wyckoff.get('score') or 0):.0f}")
        industry = str(payload.get("industry") or "")
        return {
            "symbol": symbol, "code": symbol,
            "name": quote.get("name") or payload.get("name") or symbol,
            "market": payload.get("market") or "A股", "industry": industry, "board": industry,
            "score": score, "quant_score": score, "pattern_score": round(pattern_score, 1),
            "signal": signal_from_score(score), "close": price, "pct_chg": pct_chg, "amount": amount,
            "factors": factors, "risk": risk, "patterns": matched, "matched_patterns": matched,
            "wyckoff": wyckoff,
            "limit_up": is_limit_up(symbol, pct_chg),
            "risk_flags": _risk_flags_safe(symbol, str(quote.get("name") or payload.get("name") or ""), data, quote),
            "trade_plan": trade_plan(price, latest_atr(data)),
            "reasons": list(dict.fromkeys(reasons))[:8],
        }
    except Exception:
        return None


def _risk_flags_safe(symbol: str, name: str, df, quote=None) -> list:
    """七不买体检（worker 内顺手做，失败不影响主结果）。"""
    try:
        from .risk_check import check_risks
        return check_risks(symbol, name, df, quote=quote)["flags"]
    except Exception:
        return []


# 当日板块轮动结果缓存：sector_rotation 计算后写入，strength_pool 读取给个股打「领先/落后板块」标。
# 松耦合——两者各自独立扫描，都跑过当天才有标；缺失时 strength_pool 只是不打标，功能不缺失。
_SECTOR_ROTATION_CACHE: Dict[str, object] = {"date": None, "leaders": set(), "laggards": set()}


class QuantEngine:
    def __init__(self, min_bars: int = 80):
        self.min_bars = min_bars
        self.datalake = AKShareDataLake()
        self.factor_agent = FactorResearchAgent()

    def _scan_pool(self, limit: int) -> tuple[List[Dict[str, object]], str]:
        """Use the synced local universe first; fall back to AKShare only when empty."""
        try:
            local_items = get_local_store().load_meta()
            if local_items:
                return local_items[:limit], "local-store"
        except Exception:
            pass
        return self.datalake.fetch_a_share_pool(limit), "akshare-live"

    def analyze_dataframe(self, symbol: str, df: pd.DataFrame) -> QuantAnalysisResult:
        data = normalize_ohlcv(df)
        warnings: List[str] = []
        if len(data) < self.min_bars:
            warnings.append(f"历史K线不足 {self.min_bars} 根，量化评分可信度下降")
        if data.empty:
            raise ValueError(f"{symbol} 没有可用行情数据")

        factors = compute_factor_scores(data)
        score = composite_score(factors)
        risk = risk_metrics(data)
        patterns = recognize_patterns(symbol, data)
        forecast = kronos_style_forecast(symbol, data, horizon=10)
        wyckoff = analyze_wyckoff(data)
        ml_features = ml_feature_snapshot(data)
        hmm = multi_asset_hmm(symbol)
        latest_row = data.iloc[-1].to_dict()
        prev_close = float(data.iloc[-2]["close"]) if len(data) >= 2 else 0.0
        latest_close = float(latest_row["close"])
        latest = {
            "date": latest_row["date"].strftime("%Y-%m-%d") if hasattr(latest_row.get("date"), "strftime") else str(latest_row.get("date")),
            "open": float(latest_row["open"]),
            "high": float(latest_row["high"]),
            "low": float(latest_row["low"]),
            "close": latest_close,
            "prev_close": prev_close,
            "pct_change": round((latest_close / prev_close - 1) * 100, 2) if prev_close else None,
            "volume": float(latest_row.get("volume", 0) or 0),
            "amount": float(latest_row.get("amount", 0) or 0),
        }
        # Attach ATR / KDJ / ADX / chandelier stop for downstream trade plans & UI.
        try:
            latest.update(indicator_snapshot(data))
        except Exception:
            pass
        # 组装可执行交易计划（买点/止损/止盈/盈亏比），复用上面算好的 ATR。
        try:
            latest["trade_plan"] = trade_plan(latest_close, latest.get("atr") or 0.0)
        except Exception:
            pass
        return QuantAnalysisResult(
            symbol=symbol,
            score=score,
            signal=signal_from_score(score),
            factors=factors,
            risk=risk,
            latest=latest,
            warnings=warnings,
            integrations={
                "pattern_recognition": asdict(patterns),
                "kronos_forecast": asdict(forecast),
                "wyckoff": wyckoff,
                "ml_features": ml_features,
                "multi_asset_hmm": hmm,
            },
        )

    def analyze(self, symbol: str, start_date: Optional[str] = None, end_date: Optional[str] = None) -> QuantAnalysisResult:
        df = fetch_stock_dataframe(symbol, start_date or default_start_date(), end_date)
        return self.analyze_dataframe(symbol, df)

    def screen(
        self,
        symbols: Iterable[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 30,
    ) -> Dict[str, object]:
        picks: List[QuantPick] = []
        errors: Dict[str, str] = {}
        clean_symbols = [str(s).strip() for s in symbols if str(s).strip()]

        def _analyze_one(symbol: str):
            try:
                result = self.analyze(symbol, start_date, end_date)
                return QuantPick(
                    symbol=result.symbol,
                    score=result.score,
                    signal=result.signal,
                    factors=result.factors,
                    risk=result.risk,
                ), None
            except Exception as exc:
                return None, (symbol, str(exc))

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_analyze_one, s): s for s in clean_symbols}
            for future in as_completed(futures):
                pick, err = future.result()
                if pick is not None:
                    picks.append(pick)
                elif err is not None:
                    errors[err[0]] = err[1]

        picks.sort(key=lambda item: item.score, reverse=True)
        return {
            "total": len(picks),
            "items": [asdict(item) for item in picks[:limit]],
            "errors": errors,
        }

    # v3 评分：纯结构因子合成（MACD/布林/趋势/动量/资金流，日线口径）。
    # 依据 2026-07-14 回放 A/B（run 20260714-001726，130 期同轴）：结构因子池可成交口径
    # 平均超额 +1.99pp/期、中位 +0.43、无重叠 t≈3.44、偏冷期仍 +1.97；
    # 旧实时追涨评分 +0.55pp、中位为负、不显著。评分与回放共用同一函数，实盘完全可回放。
    SMART_MIN_AMOUNT = 3e7  # 与回放口径一致：成交额 <3000 万不入候选

    def smart_pool(self, limit: int = 20, universe_limit: int = 300, exclude_fundamental: bool = True) -> Dict[str, object]:
        safe_limit = max(1, min(limit, 50))
        # 全市场最多约 5000 只；结构评分来自本地日线，实时行情用于排除与展示
        safe_universe_limit = max(safe_limit, min(universe_limit, 5000))
        pool, pool_source = self._scan_pool(safe_universe_limit)
        errors: Dict[str, str] = {}
        symbols = [str(meta.get("symbol") or "").strip().zfill(6) for meta in pool]
        quote_map = _fetch_tencent_quotes(symbols)

        _FACTOR_LABELS = {"trend": "趋势", "momentum": "动量", "macd": "MACD",
                          "bollinger": "布林位置", "capital_flow": "资金流",
                          "rsi": "RSI", "risk_control": "风控", "liquidity": "流动性"}

        def _build_item(meta: Dict[str, object], scored: Dict[str, object]) -> Dict[str, object]:
            symbol = str(scored["symbol"])
            quote = quote_map.get(symbol, {})
            rt_amount = _safe_float(quote.get("amount"), 0)
            smart_score = float(scored["score"])
            factors: Dict[str, float] = scored["factors"]
            close_price = _safe_float(quote.get("price"), 0) or float(scored["close_local"] or 0)
            top_factors = sorted(factors.items(), key=lambda kv: -kv[1])[:3]
            reasons = [f"结构因子分 {smart_score:.0f}（回放验证口径）"]
            reasons += [f"{_FACTOR_LABELS.get(k, k)} {v:.0f}" for k, v in top_factors]
            reasons.append(f"成交额 {max(rt_amount, float(scored['amount_local'] or 0)) / 1e8:.2f}亿")
            return {
                "symbol": symbol,
                "code": symbol,
                "name": quote.get("name") or meta.get("name") or symbol,
                "market": meta.get("market") or "A股",
                "score": smart_score,
                "quant_score": smart_score,
                "signal": signal_from_score(smart_score),
                "close": close_price,
                "pct_chg": _safe_float(quote.get("pct_chg"), 0),
                "amount": rt_amount or float(scored["amount_local"] or 0),
                "limit_up": is_limit_up(symbol, _safe_float(quote.get("pct_chg"), 0)),
                "risk_flags": scored.get("risk_flags") or [],
                "factors": factors,
                "risk": {"volatility": 0, "max_drawdown": 0, "sharpe": 0},
                "forecast": {
                    "engine": "factor-composite-v3",
                    "trend_score": factors.get("trend", 50.0),
                    "upside_probability": round(max(0.05, min(0.95, 0.5 + (smart_score - 70) / 100)), 4),
                },
                "patterns": [],
                "reasons": list(dict.fromkeys(reasons))[:8],
            }

        # 基本面利空集（业绩预告亏损/下滑）
        bad_fundamentals: set = set()
        if exclude_fundamental:
            try:
                bad_fundamentals = get_local_store().load_bad_forecast_symbols()
            except Exception:
                bad_fundamentals = set()

        # 第一段：名称+实时行情+基本面第一层排除
        metas = []
        for meta in pool:
            symbol = str(meta.get("symbol") or "").strip().zfill(6)
            if not symbol:
                continue
            q = quote_map.get(symbol, {})
            if exclusion_reason(str(q.get("name") or meta.get("name") or symbol),
                                _safe_float(q.get("price"), 0), _safe_float(q.get("amount"), 0)):
                continue
            if symbol in bad_fundamentals:
                continue
            metas.append(meta)

        # 第二段：结构因子进程池评分（pandas 因子计算持 GIL，线程池实测 3700 只要 4 分钟；
        # 6 进程约 40-60 秒）。不做实时精修与 critic 融合——「实盘评分 = 回放评分」严格同源。
        from .factors import smart_factor_chunk
        meta_by_symbol: Dict[str, Dict[str, object]] = {}
        gated_symbols: List[str] = []
        rt_amounts: Dict[str, float] = {}
        for meta in metas:
            symbol = str(meta.get("symbol") or "").strip().zfill(6)
            rt_amount = _safe_float(quote_map.get(symbol, {}).get("amount"), 0)
            if 0 < rt_amount < self.SMART_MIN_AMOUNT:
                continue  # 实时成交额已明确低于门槛，直接剪枝
            meta_by_symbol[symbol] = meta
            gated_symbols.append(symbol)
            rt_amounts[symbol] = rt_amount
        cutoff = (date.today() - timedelta(days=200)).strftime("%Y-%m-%d")
        db_path = get_local_store().db_path
        workers = min(6, max(1, os.cpu_count() or 4))
        chunk_size = max(1, (len(gated_symbols) + workers - 1) // workers)
        payloads = [{
            "db_path": db_path, "cutoff": cutoff, "min_amount": self.SMART_MIN_AMOUNT,
            "symbols": gated_symbols[i:i + chunk_size],
            "rt_amounts": {s: rt_amounts[s] for s in gated_symbols[i:i + chunk_size]},
        } for i in range(0, len(gated_symbols), chunk_size)]
        scored_rows: List[Dict[str, object]] = []
        try:
            from concurrent.futures import ProcessPoolExecutor
            with ProcessPoolExecutor(max_workers=workers) as executor:
                for part in executor.map(smart_factor_chunk, payloads):
                    scored_rows.extend(part)
        except Exception:
            # 进程池不可用（如受限环境）时串行兜底，功能不缺失只是慢
            for p in payloads:
                scored_rows.extend(smart_factor_chunk(p))
        items = [_build_item(meta_by_symbol.get(str(r["symbol"]), {}), r) for r in scored_rows]
        items.sort(key=lambda item: float(item["score"]), reverse=True)

        # 仅为最终展示的标的附交易计划：优先本地 K 线算 ATR，无本地数据则按比例兜底。
        final_items = items[:safe_limit]
        for it in final_items:
            atr = 0.0
            try:
                kdata = load_local_kline(str(it.get("symbol") or ""), days=120)
                if kdata is not None and not kdata.empty:
                    atr = latest_atr(kdata)
            except Exception:
                atr = 0.0
            it["trade_plan"] = trade_plan(_safe_float(it.get("close"), 0), atr)

        # 留痕当日推荐（首次快照），供复盘页统计真实 T+N 胜率。
        # 池名与评分公式绑定：改公式必须改池名（v2 追涨公式的留痕已迁到 smart_v2），
        # 否则新公式会继承旧公式的战绩，复盘胜率失去意义。
        try:
            get_local_store().record_picks("smart", final_items)
        except Exception:
            pass

        return _json_safe({
            "source": f"quant-engine-smart-pool-v3-factor:{pool_source}",
            "universe_size": len(pool),
            "analyzed": len(items),
            "items": final_items,
            "market_context": market_context(),
            "errors": errors,
        })

    def strength_pool(self, limit: int = 30, universe_limit: int = 5000,
                      dist_min: float = 70.0, adr_min: float = 4.5,
                      require_ema: bool = True, exclude_fundamental: bool = True) -> Dict[str, object]:
        """相对强度筛选器（强势股研究清单）。硬筛：距250日低点≥+70% / ADR≥4.5% /
        站上 EMA8&EMA21；按全市场横截面 RS 评级(1-99)排序。研究清单，非买入清单。"""
        from .relative_strength import assign_rs_rating, strength_chunk
        safe_limit = max(1, min(limit, 60))
        safe_universe_limit = max(safe_limit, min(universe_limit, 5000))
        pool, pool_source = self._scan_pool(safe_universe_limit)
        symbols = [str(m.get("symbol") or "").strip().zfill(6) for m in pool]
        quote_map = _fetch_tencent_quotes(symbols)

        # 第一层排除（停牌/ST/价格/成交额）+ 基本面利空
        bad_fundamentals: set = set()
        if exclude_fundamental:
            try:
                bad_fundamentals = get_local_store().load_bad_forecast_symbols()
            except Exception:
                bad_fundamentals = set()
        meta_by_symbol: Dict[str, Dict[str, object]] = {}
        gated_symbols: List[str] = []
        rt_amounts: Dict[str, float] = {}
        for meta in pool:
            symbol = str(meta.get("symbol") or "").strip().zfill(6)
            if not symbol or not symbol.isdigit() or len(symbol) != 6:
                continue
            q = quote_map.get(symbol, {})
            name = str(q.get("name") or meta.get("name") or symbol)
            if exclusion_reason(name, _safe_float(q.get("price"), 0), _safe_float(q.get("amount"), 0)):
                continue
            if symbol in bad_fundamentals:
                continue
            meta_by_symbol[symbol] = meta
            gated_symbols.append(symbol)
            rt_amounts[symbol] = _safe_float(q.get("amount"), 0)

        # RS 需要长历史（250日低点 + 多周期动量）；回放同源可复算
        cutoff = (date.today() - timedelta(days=400)).strftime("%Y-%m-%d")
        db_path = get_local_store().db_path
        workers = min(6, max(1, os.cpu_count() or 4))
        chunk_size = max(1, (len(gated_symbols) + workers - 1) // workers)
        payloads = [{
            "db_path": db_path, "cutoff": cutoff,
            "symbols": gated_symbols[i:i + chunk_size],
            "rt_amounts": {s: rt_amounts[s] for s in gated_symbols[i:i + chunk_size]},
        } for i in range(0, len(gated_symbols), chunk_size)]
        scored_rows: List[dict] = []
        try:
            from concurrent.futures import ProcessPoolExecutor
            with ProcessPoolExecutor(max_workers=workers) as executor:
                for part in executor.map(strength_chunk, payloads):
                    scored_rows.extend(part)
        except Exception:
            for p in payloads:  # 进程池不可用时串行兜底
                scored_rows.extend(strength_chunk(p))

        # 横截面相对强度评级（对全部有数据的票排名，RS 才具相对意义）
        assign_rs_rating(scored_rows)

        # 硬筛：成交额 + 距低点 + ADR + EMA
        min_amount = self.SMART_MIN_AMOUNT
        passed: List[dict] = []
        for r in scored_rows:
            sym = str(r["symbol"])
            if max(rt_amounts.get(sym, 0.0), r["amount_local"]) < min_amount:
                continue
            if r["dist_from_low"] < dist_min:
                continue
            if r["adr"] < adr_min:
                continue
            if require_ema and not (r["above_ema8"] and r["above_ema21"]):
                continue
            passed.append(r)
        passed.sort(key=lambda r: (r["rs_rating"], r["dist_from_low"]), reverse=True)
        final_rows = passed[:safe_limit]

        items: List[Dict[str, object]] = []
        for r in final_rows:
            sym = str(r["symbol"])
            q = quote_map.get(sym, {})
            meta = meta_by_symbol.get(sym, {})
            close_price = _safe_float(q.get("price"), 0) or r["close_local"]
            rt_amount = _safe_float(q.get("amount"), 0) or r["amount_local"]
            reasons = [
                f"RS 相对强度 {r['rs_rating']}（全市场动量百分位）",
                f"距250日低点 +{r['dist_from_low']:.0f}%",
                f"ADR {r['adr']:.1f}%",
                "站上 EMA8/EMA21" + ("·多头排列" if r["ema_stack"] else ""),
                f"成交额 {rt_amount / 1e8:.2f}亿",
            ]
            atr = 0.0
            try:
                kdata = load_local_kline(sym, days=120)
                if kdata is not None and not kdata.empty:
                    atr = latest_atr(kdata)
            except Exception:
                atr = 0.0
            items.append({
                "symbol": sym, "code": sym,
                "name": q.get("name") or meta.get("name") or sym,
                "market": meta.get("market") or "A股",
                "score": float(r["rs_rating"]),
                "rs_rating": r["rs_rating"],
                "dist_from_low": r["dist_from_low"],
                "adr": r["adr"],
                "ema_stack": r["ema_stack"],
                "signal": signal_from_score(float(r["rs_rating"])),
                "close": close_price,
                "pct_chg": _safe_float(q.get("pct_chg"), 0),
                "amount": rt_amount,
                "limit_up": is_limit_up(sym, _safe_float(q.get("pct_chg"), 0)),
                "reasons": reasons,
                "trade_plan": trade_plan(close_price, atr),
            })

        # 留痕（池名 strength，评分=RS 评级）；供复盘页统计真实 T+N 胜率
        try:
            get_local_store().record_picks("strength", items)
        except Exception:
            pass

        return _json_safe({
            "source": f"quant-engine-strength-screen:{pool_source}",
            "universe_size": len(pool),
            "scored": len(scored_rows),
            "matched": len(passed),
            "items": items,
            "criteria": {"dist_min": dist_min, "adr_min": adr_min, "require_ema": require_ema},
            "market_context": market_context(),
            "note": ("强势股研究清单：距250日低点≥+70%、ADR≥4.5%、站上 EMA8&EMA21 硬筛，"
                     "按全市场相对强度(RS)排名。研究清单不是买入清单——基本面/底部结构/风险位需自行深挖。"),
        })

    def sector_rotation(self, universe_limit: int = 5000,
                        industry_map: Optional[Dict[str, str]] = None) -> Dict[str, object]:
        """板块轮动相对强度排名：行业 4周/12周相对大盘的超额，按 12周 RS 排名，标领先/落后。"""
        from .sector_rotation import rank_sectors, sector_return_chunk
        pool, pool_source = self._scan_pool(max(1, min(universe_limit, 5000)))
        gated_symbols = [str(m.get("symbol") or "").strip().zfill(6) for m in pool
                         if str(m.get("symbol") or "").strip().zfill(6).isdigit()]
        cutoff = (date.today() - timedelta(days=100)).strftime("%Y-%m-%d")
        db_path = get_local_store().db_path
        workers = min(6, max(1, os.cpu_count() or 4))
        chunk_size = max(1, (len(gated_symbols) + workers - 1) // workers)
        payloads = [{"db_path": db_path, "cutoff": cutoff,
                     "symbols": gated_symbols[i:i + chunk_size]}
                    for i in range(0, len(gated_symbols), chunk_size)]
        rows: List[dict] = []
        try:
            from concurrent.futures import ProcessPoolExecutor
            with ProcessPoolExecutor(max_workers=workers) as executor:
                for part in executor.map(sector_return_chunk, payloads):
                    rows.extend(part)
        except Exception:
            for p in payloads:  # 进程池不可用时串行兜底
                rows.extend(sector_return_chunk(p))

        imap = {str(k).zfill(6): v for k, v in (industry_map or {}).items()}
        result = rank_sectors(rows, imap)

        # 缓存当日领先/落后板块，供 strength_pool 给个股打标（松耦合）
        _SECTOR_ROTATION_CACHE["date"] = date.today().isoformat()
        _SECTOR_ROTATION_CACHE["leaders"] = set(result.get("leaders") or [])
        _SECTOR_ROTATION_CACHE["laggards"] = set(result.get("laggards") or [])

        # 结论卡：跟随资金往哪流
        leaders = result.get("leaders") or []
        laggards = result.get("laggards") or []
        verdict = (
            f"资金流向：领先 {' / '.join(leaders) or '—'}；回避 {' / '.join(laggards) or '—'}。"
            "板块决定方向——领先板块里的平庸形态，往往强过落后板块里的完美形态。"
        )
        return _json_safe({
            "source": f"sector-rotation:{pool_source}",
            "universe_size": len(pool),
            "scored": len(rows),
            "mapped": sum(1 for r in rows if imap.get(str(r["symbol"]).zfill(6))),
            "verdict": verdict,
            **result,
            "note": "行业 4周(20日)/12周(60日) 收益中位相对全市场中位的超额(RS)；正=强于大盘。"
                    "研究参考，不构成投资建议。",
        })

    def pattern_pool(self, limit: int = 20, universe_limit: int = 5000, min_strength: float = 70.0,
                     exclude_fundamental: bool = True) -> Dict[str, object]:
        safe_limit = max(1, min(limit, 50))
        safe_universe_limit = max(safe_limit, min(universe_limit, 5000))
        pool, pool_source = self._scan_pool(safe_universe_limit)
        symbols = [str(meta.get("symbol") or "").strip().zfill(6) for meta in pool if meta.get("symbol")]
        local_kline_count = get_local_store().kline_symbol_count()
        local_ready = local_kline_count >= 500
        if local_ready:
            local_snapshot = get_local_store().latest_snapshots()
            symbol_set = set(symbols)
            quote_map = {
                symbol: {**snapshot, "name": ""}
                for symbol, snapshot in local_snapshot.items()
                if symbol in symbol_set
            }
        else:
            quote_map = _fetch_tencent_quotes(symbols)
        errors: Dict[str, str] = {}
        items: List[Dict[str, object]] = []

        # 基本面利空集（业绩预告亏损/下滑），同步后由本地库提供
        bad_fundamentals: set = set()
        if exclude_fundamental:
            try:
                bad_fundamentals = get_local_store().load_bad_forecast_symbols()
            except Exception:
                bad_fundamentals = set()

        # 第一层排除（名称+实时行情 + 基本面利空）
        excluded_reasons: Dict[str, int] = {}
        candidates: List[Dict[str, object]] = []
        for meta in pool:
            symbol = str(meta.get("symbol") or "").strip().zfill(6)
            if not symbol:
                continue
            quote = quote_map.get(symbol, {})
            name = str(quote.get("name") or meta.get("name") or symbol)
            reason = exclusion_reason(name, _safe_float(quote.get("price"), 0), _safe_float(quote.get("amount"), 0))
            if not reason and symbol in bad_fundamentals:
                reason = "fundamental_loss"
            if reason:
                excluded_reasons[reason] = excluded_reasons.get(reason, 0) + 1
                continue
            candidates.append(meta)

        def _board_limit(sym: str) -> float:
            # 日涨跌幅上限：科创板688/689、创业板300/301 = 20%；北交所 8/4/920 = 30%；主板 = 10%
            if sym.startswith(("688", "689", "300", "301")):
                return 20.0
            if sym.startswith(("8", "4", "920")):
                return 30.0
            return 10.0

        def _board(sym: str) -> str:
            if sym.startswith(("688", "689")):
                return "科创板"
            if sym.startswith(("300", "301")):
                return "创业板"
            if sym.startswith(("8", "4", "920")):
                return "北交所"
            return "主板"

        def _activity(meta: Dict[str, object]) -> float:
            sym = str(meta.get("symbol") or "").strip().zfill(6)
            q = quote_map.get(sym, {})
            amount = _safe_float(q.get("amount"), 0) / 1e8
            # 波动按板块涨跌幅上限归一，消除 20%/30% 板（科创/创业/北交）的天然加权
            vol = abs(_safe_float(q.get("pct_chg"), 0)) / _board_limit(sym)
            return amount + vol * 3.0

        # 预筛：放大扫描上限 + 按板块分层 round-robin，保证主板/创业/科创/北交都被分析，
        # 不再被高换手的科创板挤占（旧逻辑写死前 100 只 → 实际只分析了 ~52 只）。
        scan_cap = min(len(candidates), max(safe_limit * 6, 120))
        by_board: Dict[str, List[Dict[str, object]]] = {}
        for meta in sorted(candidates, key=_activity, reverse=True):
            by_board.setdefault(_board(str(meta.get("symbol") or "").strip().zfill(6)), []).append(meta)
        balanced: List[Dict[str, object]] = []
        cursor = {b: 0 for b in by_board}
        while len(balanced) < scan_cap and any(cursor[b] < len(by_board[b]) for b in by_board):
            for b in list(by_board.keys()):
                if cursor[b] < len(by_board[b]):
                    balanced.append(by_board[b][cursor[b]])
                    cursor[b] += 1
                    if len(balanced) >= scan_cap:
                        break
        candidates = balanced
        if local_ready:
            scan_symbols = [str(meta.get("symbol") or "").strip().zfill(6) for meta in candidates if meta.get("symbol")]
            quote_map.update(_fetch_tencent_quotes(scan_symbols))

        def analyze_meta(meta: Dict[str, object]) -> Optional[Dict[str, object]]:
            symbol = str(meta.get("symbol") or "").strip().zfill(6)
            if not symbol:
                return None
            data = load_local_kline(symbol, days=540)
            if data is None or data.empty:
                if local_ready:
                    return None  # 本地缺失（多为退市/停牌/无数据），跳过，避免慢回退拖垮全市场扫描
                data = normalize_ohlcv(_fetch_from_akshare(symbol, default_start_date(540), date.today().strftime("%Y-%m-%d")))
            if len(data) < self.min_bars:
                raise ValueError(f"{symbol} 历史K线不足 {self.min_bars} 根")

            recognition = recognize_patterns(symbol, data)
            matched = [
                item for item in recognition.patterns
                if item.get("active") and _safe_float(item.get("strength"), 0) >= min_strength
            ]
            if not matched:
                return None

            factors = compute_factor_scores(data)
            quant_score = composite_score(factors)
            risk = risk_metrics(data)
            wyckoff = analyze_wyckoff(data)
            quote = quote_map.get(symbol, {})
            latest = data.iloc[-1]
            price = _safe_float(quote.get("price"), _safe_float(latest.get("close"), 0))
            pct_chg = _safe_float(quote.get("pct_chg"), 0)
            amount = _safe_float(quote.get("amount"), _safe_float(latest.get("amount"), 0))
            pattern_score = max(_safe_float(item.get("strength"), 0) for item in matched)
            realtime_score = max(0.0, min(100.0, 55 + pct_chg * 3.8 + min(amount / 100000000, 10) * 2.0))
            adx_val = latest_adx(data)
            adx_adj = 4.0 if adx_val >= 25 else (-4.0 if 0 < adx_val < 20 else 0.0)
            wyckoff_adj = (float(wyckoff.get("score") or 50.0) - 50.0) * 0.08
            score = round(pattern_score * 0.52 + quant_score * 0.30 + realtime_score * 0.18 + adx_adj + wyckoff_adj, 1)
            reasons = []
            for pattern in matched[:4]:
                reasons.append(f"{pattern.get('name')} {float(pattern.get('strength') or 0):.1f}")
            if pct_chg:
                reasons.append(f"实时涨跌幅 {pct_chg:+.2f}%")
            if amount:
                reasons.append(f"成交额 {amount / 100000000:.2f}亿")
            if adx_val:
                reasons.append(f"ADX {adx_val:.0f}")
            if wyckoff.get("phase"):
                reasons.append(f"Wyckoff {wyckoff.get('phase')} {float(wyckoff.get('score') or 0):.0f}")
            industry = str(meta.get("industry") or meta.get("board") or meta.get("sector") or "")
            return {
                "symbol": symbol, "code": symbol,
                "name": quote.get("name") or meta.get("name") or symbol,
                "market": meta.get("market") or "A股", "industry": industry, "board": industry,
                "score": score, "quant_score": score, "pattern_score": round(pattern_score, 1),
                "signal": signal_from_score(score), "close": price, "pct_chg": pct_chg, "amount": amount,
                "factors": factors, "risk": risk, "patterns": matched, "matched_patterns": matched,
                "wyckoff": wyckoff,
                "limit_up": is_limit_up(symbol, pct_chg),
                "trade_plan": trade_plan(price, latest_atr(data)),
                "reasons": list(dict.fromkeys(reasons))[:8],
            }

        if local_ready:
            # Windows 下 ProcessPool 在 uvicorn/交互启动场景容易 BrokenProcessPool；
            # 这里优先保证形态智选稳定刷新。
            payloads = [{
                "symbol": str(meta.get("symbol") or "").strip().zfill(6),
                "name": meta.get("name"),
                "market": meta.get("market"),
                "industry": meta.get("industry") or meta.get("board") or meta.get("sector"),
                "min_strength": min_strength,
                "min_bars": self.min_bars,
                "quote": quote_map.get(str(meta.get("symbol") or "").strip().zfill(6), {}),
            } for meta in candidates]
            workers = max(8, min(24, (os.cpu_count() or 8) * 2))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                for item in executor.map(_pattern_scan_one, payloads):
                    if item:
                        items.append(item)
        else:
            with ThreadPoolExecutor(max_workers=20) as executor:
                futures = {executor.submit(analyze_meta, meta): str(meta.get("symbol") or "") for meta in candidates}
                for future in as_completed(futures):
                    symbol = futures[future]
                    try:
                        item = future.result()
                        if item:
                            items.append(item)
                    except Exception as exc:
                        errors[symbol] = str(exc)

        items.sort(key=lambda item: (float(item.get("pattern_score") or 0), float(item.get("score") or 0)), reverse=True)
        excluded_total = sum(excluded_reasons.values())
        source = "local-full-scan" if local_ready else "live-fallback"
        # 留痕当日推荐（首次快照），供复盘页统计真实 T+N 胜率。
        try:
            get_local_store().record_picks("pattern", items[:safe_limit])
        except Exception:
            pass
        return _json_safe({
            "source": source,
            "pool_source": pool_source,
            "local_kline_symbols": local_kline_count,
            "universe_size": len(pool),
            "excluded": excluded_total,
            "excluded_reasons": {REASON_LABELS.get(k, k): v for k, v in excluded_reasons.items()},
            "excluded_reasons_raw": dict(excluded_reasons),
            "scanned": len(candidates),
            "analyzed": len(candidates) - len(errors),
            "matched": len(items),
            "items": items[:safe_limit],
            "market_context": market_context(),
            "errors": errors,
            "pattern_model": [
                "均线粘合后向上发散", "地量洗盘后放量阳线", "挖坑后快速收复",
                "压力位试盘后突破", "MACD底背离/空中加油", "小步快跑", "压盘不跌后放量突破代理",
            ],
        })

    def swing_pool(self, limit: int = 20, universe_limit: int = 5000, min_score: float = 60.0,
                   exclude_fundamental: bool = True) -> Dict[str, object]:
        """短线波段池（1-3 日持仓）：RSI 超卖 + KDJ/MACD 金叉 + 布林下轨 + 放量 + 资金代理的 6 维共振低吸选股。

        与 smart_pool（实时动量）/ pattern_pool（拉升前形态）互补，偏好"超卖反弹"机会。
        依赖本地 K 线计算指标；本地数据不足时返回提示。每只票附 ATR 买卖点。
        """
        safe_limit = max(1, min(limit, 50))
        safe_universe_limit = max(safe_limit, min(universe_limit, 5000))
        pool, pool_source = self._scan_pool(safe_universe_limit)
        local_kline_count = get_local_store().kline_symbol_count()
        if local_kline_count < 500:
            return _json_safe({
                "source": "local-not-ready", "pool_source": pool_source,
                "local_kline_symbols": local_kline_count, "universe_size": len(pool),
                "matched": 0, "items": [],
                "note": "短线波段档需要本地日线（先在数据中心同步全市场 K 线）。",
            })

        symbols = [str(meta.get("symbol") or "").strip().zfill(6) for meta in pool if meta.get("symbol")]
        snapshot = get_local_store().latest_snapshots()
        quote_map = _fetch_tencent_quotes(symbols)

        bad_fundamentals: set = set()
        if exclude_fundamental:
            try:
                bad_fundamentals = get_local_store().load_bad_forecast_symbols()
            except Exception:
                bad_fundamentals = set()

        # 第一层排除：名称 + 实时行情 + 基本面利空
        excluded_reasons: Dict[str, int] = {}
        candidates: List[Dict[str, object]] = []
        for meta in pool:
            symbol = str(meta.get("symbol") or "").strip().zfill(6)
            if not symbol:
                continue
            quote = quote_map.get(symbol, {})
            name = str(quote.get("name") or meta.get("name") or symbol)
            reason = exclusion_reason(name, _safe_float(quote.get("price"), 0), _safe_float(quote.get("amount"), 0))
            if not reason and symbol in bad_fundamentals:
                reason = "fundamental_loss"
            if reason:
                excluded_reasons[reason] = excluded_reasons.get(reason, 0) + 1
                continue
            candidates.append(meta)

        errors: Dict[str, str] = {}
        items: List[Dict[str, object]] = []

        def _swing_one(meta: Dict[str, object]) -> Optional[Dict[str, object]]:
            symbol = str(meta.get("symbol") or "").strip().zfill(6)
            data = load_local_kline(symbol, days=120)
            if data is None or data.empty or len(data) < self.min_bars:
                return None
            swing = swing_short_score(data)
            score = float(swing.get("score") or 0)
            if score < min_score:
                return None
            quote = quote_map.get(symbol, {})
            latest = data.iloc[-1]
            price = _safe_float(quote.get("price"), _safe_float(latest.get("close"), 0))
            pct_chg = _safe_float(quote.get("pct_chg"), 0)
            amount = _safe_float(quote.get("amount"), _safe_float(latest.get("amount"), 0))
            reasons = list(swing.get("signals") or [])
            if pct_chg:
                reasons.append(f"实时涨跌幅 {pct_chg:+.2f}%")
            if amount:
                reasons.append(f"成交额 {amount / 100000000:.2f}亿")
            industry = str(meta.get("industry") or meta.get("board") or meta.get("sector") or "")
            return {
                "symbol": symbol, "code": symbol,
                "name": quote.get("name") or meta.get("name") or symbol,
                "market": meta.get("market") or "A股", "industry": industry, "board": industry,
                "score": round(score, 1), "quant_score": round(score, 1), "swing_score": round(score, 1),
                "swing_dims": swing.get("dims") or {},
                "signal": signal_from_score(score), "close": price, "pct_chg": pct_chg, "amount": amount,
                "hold_hint": "1-3 日",
                "limit_up": is_limit_up(symbol, pct_chg),
                "trade_plan": trade_plan(price, latest_atr(data)),
                "reasons": list(dict.fromkeys(reasons))[:8],
            }

        with ThreadPoolExecutor(max_workers=max(8, min(24, (os.cpu_count() or 8) * 2))) as executor:
            futures = {executor.submit(_swing_one, meta): str(meta.get("symbol") or "") for meta in candidates}
            for future in as_completed(futures):
                try:
                    item = future.result()
                    if item:
                        items.append(item)
                except Exception as exc:
                    errors[futures[future]] = str(exc)

        items.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
        # 留痕当日推荐（首次快照），供复盘页统计真实 T+N 胜率。
        try:
            get_local_store().record_picks("swing", items[:safe_limit])
        except Exception:
            pass
        return _json_safe({
            "source": "local-swing-scan", "pool_source": pool_source,
            "local_kline_symbols": local_kline_count, "universe_size": len(pool),
            "excluded": sum(excluded_reasons.values()),
            "excluded_reasons": {REASON_LABELS.get(k, k): v for k, v in excluded_reasons.items()},
            "scanned": len(candidates), "analyzed": len(candidates) - len(errors),
            "matched": len(items), "items": items[:safe_limit],
            "market_context": market_context(), "errors": errors,
            "dimensions": ["RSI超卖", "KDJ金叉", "MACD金叉", "布林下轨", "放量上涨", "资金代理"],
        })

    def backtest(
        self,
        symbol: str,
        strategy: str = "ma_volume",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        initial_cash: float = 100000.0,
        engine: str = "vector",
        strategies: Optional[List[str]] = None,
        combine: str = "and",
        stop_loss_pct: float = 0.0,
    ) -> BacktestResult:
        # Resolve single strategy or a composite (and/or/majority) into one signal.
        signal_func, label = resolve_strategy(strategy=strategy, strategies=strategies, combine=combine)

        df = fetch_stock_dataframe(symbol, start_date or default_start_date(900), end_date)
        data = normalize_ohlcv(df)
        if len(data) < self.min_bars:
            raise ValueError(f"{symbol} 历史K线不足 {self.min_bars} 根，无法回测")

        composite = bool(strategies and len([n for n in strategies if n in STRATEGIES]) > 1)
        # Stop-loss + composites are only supported by the vector engine.
        if stop_loss_pct and stop_loss_pct > 0:
            engine = "vector"
        if composite and engine == "akquant":
            engine = "vector"

        if engine == "akquant":
            result_data = run_akquant_backtest_adapter(symbol, data, label, initial_cash)
            return BacktestResult(**result_data)

        if engine == "backtrader":
            try:
                return run_backtrader_backtest(symbol, data, signal_func, label, initial_cash)
            except BacktraderUnavailable:
                result = run_long_only_backtest(symbol, data, signal_func, label, initial_cash, stop_loss_pct)
                result.engine = "vector_fallback_backtrader_missing"
                return result

        result = run_long_only_backtest(symbol, data, signal_func, label, initial_cash, stop_loss_pct)
        result.engine = "vector"
        return result

    async def stock_pool(self, db=None, limit: int = 200) -> Dict[str, object]:
        local_items, source = self._scan_pool(max(1, min(limit, 6000)))
        if source == "local-store":
            return {"source": source, "total": len(local_items), "items": local_items[:limit]}
        return await self.datalake.get_stock_pool(db=db, limit=limit)

    async def sync_stock_pool(self, db=None, limit: int = 5000) -> Dict[str, object]:
        return asdict(await self.datalake.sync_a_share_pool(db=db, limit=limit))

    def research_factors(
        self,
        symbols: Iterable[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        initial_cash: float = 100000.0,
    ) -> Dict[str, object]:
        return asdict(self.factor_agent.research(symbols, start_date, end_date, initial_cash))

    def capabilities(self) -> Dict[str, object]:
        return {
            "strategies": sorted(STRATEGIES.keys()),
            "integrations": integration_capabilities(),
        }

    def forecast(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        horizon: int = 10,
    ) -> ForecastResult:
        df = fetch_stock_dataframe(symbol, start_date or default_start_date(420), end_date)
        data = normalize_ohlcv(df)
        return kronos_style_forecast(symbol, data, horizon=horizon)

    def patterns(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> PatternRecognitionResult:
        df = fetch_stock_dataframe(symbol, start_date or default_start_date(420), end_date)
        data = normalize_ohlcv(df)
        return recognize_patterns(symbol, data)
