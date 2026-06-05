"""LightGBM 因子模型：训练、预测、IC/RankIC 评估。

LightGBM 是 qlib 基线里的头号主力，适合这种低信噪比的截面收益预测。
评估用 IC（每日 pred 与 label 的相关系数）而非 RMSE——量化关心排序能力，不关心绝对误差。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .dataset import DATE_COL, FEATURE_COLS, LABEL_COL

# 针对低信噪比金融截面回归的稳健默认参数：小学习率、浅树、强正则。
DEFAULT_PARAMS: Dict[str, object] = {
    "objective": "regression",
    "metric": "l2",
    "learning_rate": 0.02,
    "num_leaves": 31,
    "max_depth": 6,
    "min_child_samples": 100,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "lambda_l1": 1.0,
    "lambda_l2": 1.0,
    "verbose": -1,
    "num_threads": 0,
}


@dataclass
class ModelResult:
    model: object
    best_iteration: int
    feature_importance: Dict[str, float]
    valid_ic: float = 0.0
    valid_rank_ic: float = 0.0


def train_lgb(
    train: pd.DataFrame,
    valid: Optional[pd.DataFrame] = None,
    params: Optional[Dict[str, object]] = None,
    num_boost_round: int = 1000,
    early_stopping: int = 50,
) -> ModelResult:
    import lightgbm as lgb

    p = {**DEFAULT_PARAMS, **(params or {})}
    dtrain = lgb.Dataset(train[FEATURE_COLS], label=train[LABEL_COL])
    valid_sets, valid_names, callbacks = [dtrain], ["train"], [lgb.log_evaluation(0)]
    if valid is not None and len(valid):
        dvalid = lgb.Dataset(valid[FEATURE_COLS], label=valid[LABEL_COL], reference=dtrain)
        valid_sets.append(dvalid)
        valid_names.append("valid")
        callbacks.append(lgb.early_stopping(early_stopping, verbose=False))

    booster = lgb.train(
        p, dtrain,
        num_boost_round=num_boost_round,
        valid_sets=valid_sets,
        valid_names=valid_names,
        callbacks=callbacks,
    )
    imp = dict(zip(FEATURE_COLS, booster.feature_importance(importance_type="gain")))
    imp = {k: float(v) for k, v in sorted(imp.items(), key=lambda x: -x[1])}

    res = ModelResult(model=booster, best_iteration=booster.best_iteration or num_boost_round,
                      feature_importance=imp)
    if valid is not None and len(valid):
        pred = predict(booster, valid)
        ic = evaluate_ic(valid.assign(pred=pred))
        res.valid_ic = ic["ic_mean"]
        res.valid_rank_ic = ic["rank_ic_mean"]
    return res


def predict(booster, df: pd.DataFrame) -> np.ndarray:
    n = getattr(booster, "best_iteration", None) or 0
    return booster.predict(df[FEATURE_COLS], num_iteration=n if n > 0 else None)


def evaluate_ic(df: pd.DataFrame, pred_col: str = "pred") -> Dict[str, float]:
    """逐交易日计算 IC（Pearson）与 RankIC（Spearman），汇总均值/标准差/ICIR。"""
    ics: List[float] = []
    rank_ics: List[float] = []
    for _, g in df.groupby(DATE_COL):
        if len(g) < 5:
            continue
        a, b = g[pred_col], g[LABEL_COL]
        ic = a.corr(b)
        ric = a.corr(b, method="spearman")
        if pd.notna(ic):
            ics.append(ic)
        if pd.notna(ric):
            rank_ics.append(ric)

    def _stats(xs: List[float]):
        if not xs:
            return 0.0, 0.0, 0.0
        arr = np.array(xs)
        mean, std = float(arr.mean()), float(arr.std())
        icir = mean / std * np.sqrt(252) if std else 0.0
        return mean, std, float(icir)

    ic_mean, ic_std, icir = _stats(ics)
    ric_mean, ric_std, ricir = _stats(rank_ics)
    return {
        "ic_mean": round(ic_mean, 4), "ic_std": round(ic_std, 4), "icir": round(icir, 3),
        "rank_ic_mean": round(ric_mean, 4), "rank_ic_std": round(ric_std, 4), "rank_icir": round(ricir, 3),
        "n_days": len(ics),
    }
