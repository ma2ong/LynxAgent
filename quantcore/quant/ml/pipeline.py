"""ML 因子链编排：单次训练评估 + 滚动再训练（walk-forward）+ 今日选股。

run_once   —— 一次性 train/valid/test，快速看模型有没有信号。
run_rolling —— 滚动再训练：每隔 retrain_every 期用最新数据重训，
              预测下一段，拼成完整的样本外预测序列，再回测。
              这是应对 A 股风格漂移的关键（对齐 qlib rolling / DDG-DA 思路）。

两者都支持市值中性化（neutralize），并返回最新交易日的 Top-K 选股名单。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import pandas as pd

from ..local_store import get_local_store
from .backtest_panel import topk_backtest
from .dataset import DATE_COL, SYMBOL_COL, build_panel, split_by_time
from .model import evaluate_ic, predict, train_lgb
from .neutralize import neutralize_pred


def _date_at(dates: List[str], frac: float) -> str:
    idx = max(0, min(len(dates) - 1, int(len(dates) * frac)))
    return dates[idx]


def _name_map() -> Dict[str, str]:
    try:
        return {r.get("symbol"): r.get("name", "") for r in get_local_store().load_meta()}
    except Exception:
        return {}


def _finalize(oos: pd.DataFrame, k: int, horizon: int, neutralize: bool) -> Dict[str, object]:
    """对样本外预测做中性化、IC 评估、回测、并抽取最新交易日选股。"""
    rank_col = "pred"
    if neutralize:
        oos = neutralize_pred(oos, pred_col="pred")
        rank_col = "pred_neutral"

    ic = evaluate_ic(oos, pred_col=rank_col)
    bt = topk_backtest(oos, k=k, horizon=horizon, pred_col=rank_col)

    # 最新交易日 Top-K 选股
    last_date = oos[DATE_COL].max()
    today = oos[oos[DATE_COL] == last_date].sort_values(rank_col, ascending=False).head(k)
    names = _name_map()
    picks = [
        {"symbol": s, "name": names.get(s, ""), "score": round(float(v), 4)}
        for s, v in zip(today[SYMBOL_COL], today[rank_col])
    ]

    return {
        "test_ic": ic,
        "backtest": bt.metrics,
        "benchmark": bt.benchmark,
        "long_short": bt.long_short,
        "neutralized": neutralize,
        "pick_date": str(last_date),
        "picks": picks,
        "_bt": bt,
    }


def run_once(
    panel: Optional[pd.DataFrame] = None,
    symbols: Optional[Sequence[str]] = None,
    horizon: int = 5,
    k: int = 50,
    train_frac: float = 0.7,
    valid_frac: float = 0.15,
    neutralize: bool = True,
) -> Dict[str, object]:
    if panel is None:
        panel = build_panel(symbols=symbols, horizon=horizon)
    if panel.empty:
        return {"error": "empty panel"}

    dates = sorted(panel[DATE_COL].unique())
    train_end = _date_at(dates, train_frac)
    valid_end = _date_at(dates, train_frac + valid_frac)
    train, valid, test = split_by_time(panel, train_end, valid_end)

    res = train_lgb(train, valid)
    test = test.assign(pred=predict(res.model, test))

    out = {
        "mode": "once",
        "rows": {"train": len(train), "valid": len(valid), "test": len(test)},
        "split": {"train_end": train_end, "valid_end": valid_end},
        "best_iteration": res.best_iteration,
        "valid_ic": res.valid_ic,
        "top_features": dict(list(res.feature_importance.items())[:10]),
    }
    out.update(_finalize(test, k=k, horizon=horizon, neutralize=neutralize))
    return out


def run_rolling(
    panel: Optional[pd.DataFrame] = None,
    symbols: Optional[Sequence[str]] = None,
    horizon: int = 5,
    k: int = 50,
    init_train_frac: float = 0.5,
    retrain_every: int = 20,
    valid_tail_frac: float = 0.15,
    neutralize: bool = True,
) -> Dict[str, object]:
    """滚动再训练：从 init_train_frac 处起步，每 retrain_every 个交易日重训一次。"""
    if panel is None:
        panel = build_panel(symbols=symbols, horizon=horizon)
    if panel.empty:
        return {"error": "empty panel"}

    dates = sorted(panel[DATE_COL].unique())
    n = len(dates)
    cutoff = int(n * init_train_frac)
    oos_frames: List[pd.DataFrame] = []
    n_models = 0

    while cutoff < n - 1:
        train_end = dates[cutoff]
        test_start = dates[cutoff + 1]
        test_end = dates[min(cutoff + retrain_every, n - 1)]

        train_full = panel[panel[DATE_COL] <= train_end]
        tr_dates = sorted(train_full[DATE_COL].unique())
        valid_start = _date_at(tr_dates, 1 - valid_tail_frac)
        train = train_full[train_full[DATE_COL] < valid_start]
        valid = train_full[train_full[DATE_COL] >= valid_start]
        test = panel[(panel[DATE_COL] >= test_start) & (panel[DATE_COL] <= test_end)]
        if train.empty or test.empty:
            cutoff += retrain_every
            continue

        res = train_lgb(train, valid if len(valid) else None)
        oos_frames.append(test.assign(pred=predict(res.model, test)))
        n_models += 1
        cutoff += retrain_every

    if not oos_frames:
        return {"error": "no out-of-sample windows produced"}

    oos = pd.concat(oos_frames, ignore_index=True)
    out = {
        "mode": "rolling",
        "n_models": n_models,
        "retrain_every": retrain_every,
        "oos_rows": len(oos),
        "oos_dates": [str(oos[DATE_COL].min()), str(oos[DATE_COL].max())],
    }
    out.update(_finalize(oos, k=k, horizon=horizon, neutralize=neutralize))
    return out
