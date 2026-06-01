from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from .backtest import run_long_only_backtest
from .data import default_start_date, fetch_stock_dataframe, normalize_ohlcv
from .factors import enrich_indicators
from .models import FactorResearchResult


def _trend_pullback(data: pd.DataFrame) -> pd.Series:
    enriched = enrich_indicators(data)
    return (
        (enriched["ma20"] > enriched["ma60"])
        & (enriched["close"] > enriched["ma60"])
        & (enriched["rsi14"] < 58)
        & (enriched["momentum_20"] > 0)
    )


def _low_vol_momentum(data: pd.DataFrame) -> pd.Series:
    enriched = enrich_indicators(data)
    vol_rank = enriched["volatility20"].rolling(120).rank(pct=True)
    return (enriched["momentum_60"] > 0.12) & (vol_rank < 0.55)


def _volume_breakout(data: pd.DataFrame) -> pd.Series:
    enriched = enrich_indicators(data)
    prior_high = enriched["high"].rolling(60).max().shift(1)
    return (enriched["close"] > prior_high) & (enriched["volume"] > enriched["volume_ma20"] * 1.5)


FACTOR_CANDIDATES = {
    "trend_pullback": {
        "hypothesis": "中期趋势向上且短线不过热，回踩后更适合继续观察。",
        "signal": _trend_pullback,
    },
    "low_vol_momentum": {
        "hypothesis": "动量有效但波动未显著放大的股票，风险收益更稳定。",
        "signal": _low_vol_momentum,
    },
    "volume_breakout": {
        "hypothesis": "放量突破 60 日高点代表资金确认，适合趋势跟踪。",
        "signal": _volume_breakout,
    },
}


class FactorResearchAgent:
    """QuantGPT-style deterministic factor researcher.

    This is intentionally deterministic for production safety: LLMs can propose
    hypotheses later, but execution only runs approved templates.
    """

    def research(
        self,
        symbols: Iterable[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        initial_cash: float = 100000.0,
    ) -> FactorResearchResult:
        symbol_list = [str(symbol).strip() for symbol in symbols if str(symbol).strip()]
        errors: Dict[str, str] = {}
        candidate_scores: Dict[str, List[Dict[str, Any]]] = {name: [] for name in FACTOR_CANDIDATES}

        for symbol in symbol_list:
            try:
                data = normalize_ohlcv(fetch_stock_dataframe(symbol, start_date or default_start_date(900), end_date))
                if len(data) < 120:
                    raise ValueError("not enough bars for factor research")
                for name, candidate in FACTOR_CANDIDATES.items():
                    result = run_long_only_backtest(symbol, data, candidate["signal"], name, initial_cash)
                    candidate_scores[name].append(asdict(result))
            except Exception as exc:
                errors[symbol] = str(exc)

        candidates: List[Dict[str, Any]] = []
        for name, runs in candidate_scores.items():
            if not runs:
                continue
            avg_return = sum(item["total_return"] for item in runs) / len(runs)
            avg_drawdown = sum(item["max_drawdown"] for item in runs) / len(runs)
            avg_win_rate = sum(item["win_rate"] for item in runs) / len(runs)
            score = avg_return * 100 + avg_drawdown * 35 + avg_win_rate * 20
            candidates.append({
                "name": name,
                "hypothesis": FACTOR_CANDIDATES[name]["hypothesis"],
                "score": round(score, 2),
                "avg_return": round(avg_return, 4),
                "avg_max_drawdown": round(avg_drawdown, 4),
                "avg_win_rate": round(avg_win_rate, 4),
                "sample_size": len(runs),
                "runs": runs,
            })

        candidates.sort(key=lambda item: item["score"], reverse=True)
        return FactorResearchResult(
            universe_size=len(symbol_list),
            candidates=candidates,
            errors=errors,
        )
