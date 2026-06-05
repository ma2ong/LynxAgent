"""截面 Top-K 组合回测：按模型打分每期选前 K 名等权持有。

用非重叠的 horizon 周期换仓（label_fwd 本身是 horizon 日前瞻收益），
避免重叠样本导致的收益高估。同时给出 Top-K、基准（全市场等权）、
多空（Top-K 减 Bottom-K）三条曲线，便于判断模型的真实排序能力。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np
import pandas as pd

from .dataset import DATE_COL, LABEL_COL


@dataclass
class PanelBacktestResult:
    metrics: Dict[str, float]
    benchmark: Dict[str, float]
    long_short: Dict[str, float]
    equity_curve: List[float] = field(default_factory=list)
    benchmark_curve: List[float] = field(default_factory=list)
    long_short_curve: List[float] = field(default_factory=list)
    period_returns: List[float] = field(default_factory=list)
    dates: List[str] = field(default_factory=list)


def _series_metrics(period_returns: np.ndarray, periods_per_year: float) -> Dict[str, float]:
    if len(period_returns) == 0:
        return {"total_return": 0.0, "annual_return": 0.0, "sharpe": 0.0,
                "max_drawdown": 0.0, "win_rate": 0.0, "n_periods": 0}
    equity = np.cumprod(1 + period_returns)
    total = float(equity[-1] - 1)
    annual = float(equity[-1] ** (periods_per_year / len(period_returns)) - 1)
    std = period_returns.std()
    sharpe = float(period_returns.mean() / std * np.sqrt(periods_per_year)) if std else 0.0
    peak = np.maximum.accumulate(equity)
    mdd = float((equity / peak - 1).min())
    win = float((period_returns > 0).mean())
    return {"total_return": round(total, 4), "annual_return": round(annual, 4),
            "sharpe": round(sharpe, 3), "max_drawdown": round(mdd, 4),
            "win_rate": round(win, 3), "n_periods": int(len(period_returns))}


def topk_backtest(
    pred_panel: pd.DataFrame,
    k: int = 50,
    horizon: int = 5,
    pred_col: str = "pred",
) -> PanelBacktestResult:
    """pred_panel 需含 date / pred / label_fwd（label_fwd = horizon 日前瞻收益）。"""
    dates = sorted(pred_panel[DATE_COL].unique())
    rebal_dates = dates[::horizon]  # 非重叠换仓

    topk_rets, bench_rets, ls_rets, used_dates = [], [], [], []
    for d in rebal_dates:
        g = pred_panel[pred_panel[DATE_COL] == d]
        if len(g) < k * 2:
            continue
        ranked = g.sort_values(pred_col, ascending=False)
        top = ranked.head(k)
        bottom = ranked.tail(k)
        topk_rets.append(float(top[LABEL_COL].mean()))
        bench_rets.append(float(g[LABEL_COL].mean()))
        ls_rets.append(float(top[LABEL_COL].mean() - bottom[LABEL_COL].mean()))
        used_dates.append(str(d))

    ppy = 252.0 / horizon
    topk_arr, bench_arr, ls_arr = np.array(topk_rets), np.array(bench_rets), np.array(ls_rets)

    def _curve(arr):
        return [float(round(x, 4)) for x in np.cumprod(1 + arr)] if len(arr) else []

    return PanelBacktestResult(
        metrics=_series_metrics(topk_arr, ppy),
        benchmark=_series_metrics(bench_arr, ppy),
        long_short=_series_metrics(ls_arr, ppy),
        equity_curve=_curve(topk_arr),
        benchmark_curve=_curve(bench_arr),
        long_short_curve=_curve(ls_arr),
        period_returns=[float(round(x, 4)) for x in topk_rets],
        dates=used_dates,
    )
