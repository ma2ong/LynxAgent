from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
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
from .factors import composite_score, compute_factor_scores, indicator_snapshot, latest_adx, risk_metrics, signal_from_score
from .integrations import integration_capabilities, kronos_style_forecast, recognize_patterns, run_akquant_backtest_adapter
from .models import BacktestResult, ForecastResult, PatternRecognitionResult, QuantAnalysisResult, QuantPick
from .strategies import STRATEGIES


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


# MA20 仅用于评分里 ±6 的微调，按 (symbol, 当日) 缓存，避免全市场扫描重复抓取
_MA20_CACHE: Dict[str, Tuple[str, Optional[float]]] = {}

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


def _get_ma20_for_symbol(symbol: str) -> Optional[float]:
    """Fetch 20-day moving average. Returns None on failure."""
    today = date.today().isoformat()
    cached = _MA20_CACHE.get(symbol)
    if cached and cached[0] == today:
        return cached[1]
    value: Optional[float] = None
    try:
        # 只取约 40 天即可覆盖 20 个交易日；直连 akshare，失败即跳过，避免落入缓慢的 yfinance 回退
        start = (date.today() - timedelta(days=40)).strftime("%Y-%m-%d")
        end = date.today().strftime("%Y-%m-%d")
        df = load_local_kline(symbol, days=40)
        if df is None or df.empty:
            df = normalize_ohlcv(_fetch_from_akshare(symbol, start, end))
        if df is not None and len(df) >= 20:
            value = float(df["close"].tail(20).mean())
    except Exception:
        value = None
    _MA20_CACHE[symbol] = (today, value)
    return value


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
        latest = data.iloc[-1]
        price = _safe_float(quote.get("price"), _safe_float(latest.get("close"), 0))
        pct_chg = _safe_float(quote.get("pct_chg"), 0)
        amount = _safe_float(quote.get("amount"), _safe_float(latest.get("amount"), 0))
        pattern_score = max(_safe_float(item.get("strength"), 0) for item in matched)
        realtime_score = max(0.0, min(100.0, 55 + pct_chg * 3.8 + min(amount / 100000000, 10) * 2.0))
        adx_val = latest_adx(data)
        adx_adj = 4.0 if adx_val >= 25 else (-4.0 if 0 < adx_val < 20 else 0.0)
        score = round(pattern_score * 0.52 + quant_score * 0.30 + realtime_score * 0.18 + adx_adj, 1)
        reasons = []
        for pattern in matched[:4]:
            reasons.append(f"{pattern.get('name')} {float(pattern.get('strength') or 0):.1f}")
        if pct_chg:
            reasons.append(f"实时涨跌幅 {pct_chg:+.2f}%")
        if amount:
            reasons.append(f"成交额 {amount / 100000000:.2f}亿")
        if adx_val:
            reasons.append(f"ADX {adx_val:.0f}")
        industry = str(payload.get("industry") or "")
        return {
            "symbol": symbol, "code": symbol,
            "name": quote.get("name") or payload.get("name") or symbol,
            "market": payload.get("market") or "A股", "industry": industry, "board": industry,
            "score": score, "quant_score": score, "pattern_score": round(pattern_score, 1),
            "signal": signal_from_score(score), "close": price, "pct_chg": pct_chg, "amount": amount,
            "factors": factors, "risk": risk, "patterns": matched, "matched_patterns": matched,
            "reasons": list(dict.fromkeys(reasons))[:8],
        }
    except Exception:
        return None


class QuantEngine:
    def __init__(self, min_bars: int = 80):
        self.min_bars = min_bars
        self.datalake = AKShareDataLake()
        self.factor_agent = FactorResearchAgent()

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

    def smart_pool(self, limit: int = 20, universe_limit: int = 300, exclude_fundamental: bool = True) -> Dict[str, object]:
        safe_limit = max(1, min(limit, 50))
        # 全市场最多约 5000 只；评分主要来自批量实时行情，可覆盖全市场
        safe_universe_limit = max(safe_limit, min(universe_limit, 5000))
        pool = self.datalake.fetch_a_share_pool(safe_universe_limit)
        errors: Dict[str, str] = {}
        symbols = [str(meta.get("symbol") or "").strip().zfill(6) for meta in pool]
        quote_map = _fetch_tencent_quotes(symbols)

        def _score(meta: Dict[str, object], ma20: Optional[float]) -> Dict[str, object]:
            symbol = str(meta.get("symbol") or "").strip().zfill(6)
            quote = quote_map.get(symbol, {})
            pct_chg = _safe_float(quote.get("pct_chg"), 0)
            amount = _safe_float(quote.get("amount"), 0)
            turnover = _safe_float(quote.get("turnover_rate"), 0)
            volume_ratio = _safe_float(quote.get("volume_ratio"), 0)
            close_price = _safe_float(quote.get("price"), 0)
            ma20_adj = 0.0
            if ma20 is not None and close_price > 0:
                ma20_adj = 6.0 if close_price > ma20 else -6.0
            trend_score = max(0.0, min(100.0, 55 + pct_chg * 5.2 + min(amount / 100000000, 8) * 2.6 + ma20_adj))
            momentum_score = max(0.0, min(100.0, 50 + pct_chg * 6.0 + min(volume_ratio, 3) * 9.0))
            liquidity_score = max(0.0, min(100.0, 45 + min(amount / 100000000, 12) * 4.0))
            rsi_score = max(0.0, min(100.0, 50 + pct_chg * 3.0))
            risk_score = max(0.0, min(100.0, 78 - abs(pct_chg) * 2.8 + min(turnover, 8)))
            smart_score = round(
                trend_score * 0.30
                + momentum_score * 0.28
                + liquidity_score * 0.18
                + rsi_score * 0.12
                + risk_score * 0.12,
                1,
            )
            factors = {
                "trend": round(trend_score, 1),
                "momentum": round(momentum_score, 1),
                "rsi": round(rsi_score, 1),
                "risk_control": round(risk_score, 1),
                "liquidity": round(liquidity_score, 1),
            }
            patterns = []
            if trend_score >= 75:
                patterns.append({"key": "realtime_trend", "name": "实时趋势强", "active": True, "strength": round(trend_score, 1), "reason": "涨幅、成交额和流动性共同抬升"})
            if momentum_score >= 75:
                patterns.append({"key": "realtime_momentum", "name": "短线动量强", "active": True, "strength": round(momentum_score, 1), "reason": "实时涨跌幅和量比显示短线动量"})
            reasons = [
                f"实时涨跌幅 {pct_chg:+.2f}%",
                f"成交额 {amount / 100000000:.2f}亿",
                f"趋势 {trend_score:.0f}",
                f"动量 {momentum_score:.0f}",
                f"流动性 {liquidity_score:.0f}",
            ]
            reasons.extend([str(item.get("name")) for item in patterns[:3] if item.get("name")])
            return {
                "symbol": symbol,
                "code": symbol,
                "name": quote.get("name") or meta.get("name") or symbol,
                "market": meta.get("market") or "A股",
                "score": smart_score,
                "quant_score": smart_score,
                "signal": signal_from_score(smart_score),
                "close": close_price,
                "pct_chg": pct_chg,
                "amount": amount,
                "factors": factors,
                "risk": {"volatility": 0, "max_drawdown": 0, "sharpe": 0},
                "forecast": {
                    "engine": "realtime-fast-proxy",
                    "trend_score": round(trend_score, 1),
                    "upside_probability": round(max(0.05, min(0.95, 0.5 + (smart_score - 70) / 100)), 4),
                },
                "patterns": patterns,
                "reasons": list(dict.fromkeys(reasons))[:8],
            }

        # 基本面利空集（业绩预告亏损/下滑）
        bad_fundamentals: set = set()
        if exclude_fundamental:
            try:
                bad_fundamentals = get_local_store().load_bad_forecast_symbols()
            except Exception:
                bad_fundamentals = set()

        # 第一段：先按名称+实时行情+基本面做第一层排除，再用批量行情给剩余股打分
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
        items = [_score(meta, None) for meta in metas]

        # 第二段：只对预筛 top 候选抓 MA20 精修（MA20 仅 ±6 微调，不会让落榜股逆袭进前列）
        order = sorted(range(len(items)), key=lambda i: float(items[i]["score"]), reverse=True)
        refine_n = min(len(order), max(safe_limit * 4, 100))
        top_idx = order[:refine_n]
        with ThreadPoolExecutor(max_workers=24) as executor:
            ma20_results = list(executor.map(lambda i: (i, _get_ma20_for_symbol(items[i]["symbol"])), top_idx))
        for i, ma20 in ma20_results:
            items[i] = _score(metas[i], ma20)

        items.sort(key=lambda item: float(item["score"]), reverse=True)
        return _json_safe({
            "source": "quant-engine-smart-pool",
            "universe_size": len(pool),
            "analyzed": len(items),
            "items": items[:safe_limit],
            "errors": errors,
        })

    def pattern_pool(self, limit: int = 20, universe_limit: int = 5000, min_strength: float = 70.0,
                     exclude_fundamental: bool = True) -> Dict[str, object]:
        safe_limit = max(1, min(limit, 50))
        safe_universe_limit = max(safe_limit, min(universe_limit, 5000))
        pool = self.datalake.fetch_a_share_pool(safe_universe_limit)
        symbols = [str(meta.get("symbol") or "").strip().zfill(6) for meta in pool if meta.get("symbol")]
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

        # 本地数据是否就绪：就绪则纯本地扫描（缺失即跳过，不做慢回退）；未就绪则 live 回退并限量，避免数千次实时抓取超时
        local_ready = get_local_store().kline_symbol_count() >= 500
        if not local_ready:
            def _activity(meta: Dict[str, object]) -> float:
                q = quote_map.get(str(meta.get("symbol") or "").strip().zfill(6), {})
                return _safe_float(q.get("amount"), 0) / 1e8 + abs(_safe_float(q.get("pct_chg"), 0)) * 0.5
            candidates = sorted(candidates, key=_activity, reverse=True)[:min(len(candidates), 800)]

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
            quote = quote_map.get(symbol, {})
            latest = data.iloc[-1]
            price = _safe_float(quote.get("price"), _safe_float(latest.get("close"), 0))
            pct_chg = _safe_float(quote.get("pct_chg"), 0)
            amount = _safe_float(quote.get("amount"), _safe_float(latest.get("amount"), 0))
            pattern_score = max(_safe_float(item.get("strength"), 0) for item in matched)
            realtime_score = max(0.0, min(100.0, 55 + pct_chg * 3.8 + min(amount / 100000000, 10) * 2.0))
            adx_val = latest_adx(data)
            adx_adj = 4.0 if adx_val >= 25 else (-4.0 if 0 < adx_val < 20 else 0.0)
            score = round(pattern_score * 0.52 + quant_score * 0.30 + realtime_score * 0.18 + adx_adj, 1)
            reasons = []
            for pattern in matched[:4]:
                reasons.append(f"{pattern.get('name')} {float(pattern.get('strength') or 0):.1f}")
            if pct_chg:
                reasons.append(f"实时涨跌幅 {pct_chg:+.2f}%")
            if amount:
                reasons.append(f"成交额 {amount / 100000000:.2f}亿")
            if adx_val:
                reasons.append(f"ADX {adx_val:.0f}")
            industry = str(meta.get("industry") or meta.get("board") or meta.get("sector") or "")
            return {
                "symbol": symbol, "code": symbol,
                "name": quote.get("name") or meta.get("name") or symbol,
                "market": meta.get("market") or "A股", "industry": industry, "board": industry,
                "score": score, "quant_score": score, "pattern_score": round(pattern_score, 1),
                "signal": signal_from_score(score), "close": price, "pct_chg": pct_chg, "amount": amount,
                "factors": factors, "risk": risk, "patterns": matched, "matched_patterns": matched,
                "reasons": list(dict.fromkeys(reasons))[:8],
            }

        if local_ready:
            # 本地数据就绪：形态识别是 CPU 密集（GIL 下线程无法并行），用进程池真并行
            payloads = [{
                "symbol": str(meta.get("symbol") or "").strip().zfill(6),
                "name": meta.get("name"),
                "market": meta.get("market"),
                "industry": meta.get("industry") or meta.get("board") or meta.get("sector"),
                "min_strength": min_strength,
                "min_bars": self.min_bars,
                "quote": quote_map.get(str(meta.get("symbol") or "").strip().zfill(6), {}),
            } for meta in candidates]
            workers = max(2, min(8, (os.cpu_count() or 4)))
            with ProcessPoolExecutor(max_workers=workers) as executor:
                for item in executor.map(_pattern_scan_one, payloads, chunksize=16):
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
        return _json_safe({
            "source": source,
            "universe_size": len(pool),
            "excluded": excluded_total,
            "excluded_reasons": {REASON_LABELS.get(k, k): v for k, v in excluded_reasons.items()},
            "excluded_reasons_raw": dict(excluded_reasons),
            "scanned": len(candidates),
            "analyzed": len(candidates) - len(errors),
            "matched": len(items),
            "items": items[:safe_limit],
            "errors": errors,
            "pattern_model": [
                "均线粘合后向上发散", "地量洗盘后放量阳线", "挖坑后快速收复",
                "压力位试盘后突破", "MACD底背离/空中加油", "小步快跑", "压盘不跌后放量突破代理",
            ],
        })

    def backtest(
        self,
        symbol: str,
        strategy: str = "ma_volume",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        initial_cash: float = 100000.0,
        engine: str = "vector",
    ) -> BacktestResult:
        if strategy not in STRATEGIES:
            supported = ", ".join(sorted(STRATEGIES))
            raise ValueError(f"不支持的策略: {strategy}. 支持: {supported}")

        df = fetch_stock_dataframe(symbol, start_date or default_start_date(900), end_date)
        data = normalize_ohlcv(df)
        if len(data) < self.min_bars:
            raise ValueError(f"{symbol} 历史K线不足 {self.min_bars} 根，无法回测")

        if engine == "akquant":
            result_data = run_akquant_backtest_adapter(symbol, data, strategy, initial_cash)
            return BacktestResult(**result_data)

        if engine == "backtrader":
            try:
                return run_backtrader_backtest(symbol, data, STRATEGIES[strategy], strategy, initial_cash)
            except BacktraderUnavailable:
                result = run_long_only_backtest(symbol, data, STRATEGIES[strategy], strategy, initial_cash)
                result.engine = "vector_fallback_backtrader_missing"
                return result

        result = run_long_only_backtest(symbol, data, STRATEGIES[strategy], strategy, initial_cash)
        result.engine = "vector"
        return result

    async def stock_pool(self, db=None, limit: int = 200) -> Dict[str, object]:
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
