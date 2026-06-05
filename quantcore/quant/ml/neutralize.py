"""预测分中性化：剔除市值（规模）暴露，避免 Top-K 扎堆小盘/低流动性。

做法：每个交易日内，把模型预测分对市值代理做最小二乘回归，取残差作为
最终排序分。残差 = 模型超越"同规模股票"的部分，规模偏好被消掉。
A 股 Top-K 多头最常见的偏差就是小盘扎堆，市值中性化直击该问题。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .dataset import DATE_COL, SIZE_COL


def neutralize_pred(panel: pd.DataFrame, pred_col: str = "pred",
                    by: str = SIZE_COL, out_col: str = "pred_neutral") -> pd.DataFrame:
    """对每个交易日，用 pred ~ 1 + by 的残差替代 pred。"""
    out = panel.copy()

    def _resid(g: pd.DataFrame) -> pd.Series:
        if len(g) < 3 or g[by].nunique() < 2:
            return g[pred_col] - g[pred_col].mean()
        X = np.column_stack([np.ones(len(g)), g[by].values])
        y = g[pred_col].values
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        return pd.Series(y - X @ beta, index=g.index)

    out[out_col] = (
        panel.groupby(DATE_COL, group_keys=False)[[pred_col, by]].apply(_resid)
    )
    return out
