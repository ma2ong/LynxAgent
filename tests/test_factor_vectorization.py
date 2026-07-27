"""实验用的整段向量化因子分，必须与生产的 compute_factor_scores 逐点一致。

experiments/ 下的 IC 研究与权重 A/B 都建立在一个前提上：因子指标全是前缀型（rolling /
ewm / cummax / pct_change，只看当前行及之前），所以「整段算一次再取第 i 行」等于
「切到第 i 行重算一次」。这个前提一旦被破坏（比如给某个因子引入了整段归一化、
或者改了公式而没同步 experiments/factor_scores.py），实验结论会静默失真 —— 跑得出
数字，只是数字不再代表线上评分。这个测试就是那道闸门。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments"))

from factor_scores import FACTORS, factor_frame  # noqa: E402
from quantcore.quant.data import normalize_ohlcv  # noqa: E402
from quantcore.quant.factors import composite_score, compute_factor_scores  # noqa: E402


def _synthetic(n: int = 200, seed: int = 7) -> pd.DataFrame:
    """一段有涨有跌、成交额有起伏的走势，避免退化成常数把差异掩盖掉。"""
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.001, 0.02, n)
    close = 20 * np.exp(np.cumsum(steps))
    dates = pd.bdate_range("2024-01-01", periods=n).strftime("%Y-%m-%d")
    return normalize_ohlcv(pd.DataFrame({
        "date": dates,
        "open": close * (1 + rng.normal(0, 0.003, n)),
        "high": close * (1 + abs(rng.normal(0, 0.008, n))),
        "low": close * (1 - abs(rng.normal(0, 0.008, n))),
        "close": close,
        "volume": rng.integers(5e5, 5e6, n).astype(float),
        "amount": rng.uniform(5e7, 8e8, n),
    }))


@pytest.mark.parametrize("cut", [100, 150, 199])
def test_vectorized_matches_pointwise(cut):
    df = _synthetic()
    vectorized = factor_frame(df).reset_index(drop=True)
    pointwise = compute_factor_scores(df.iloc[:cut + 1])

    for name in FACTORS:
        assert float(vectorized[name].iloc[cut]) == pytest.approx(float(pointwise[name]), abs=0.02), name
    assert float(vectorized["composite"].iloc[cut]) == pytest.approx(composite_score(pointwise), abs=0.02)


def test_vectorized_covers_every_production_factor():
    """生产新增因子而 experiments 没跟上时，这里先炸，而不是等实验结论跑偏。"""
    produced = set(compute_factor_scores(_synthetic()))
    assert produced == set(FACTORS), f"因子集合不一致：生产={produced} 实验={set(FACTORS)}"
