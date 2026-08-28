"""地量加分的守卫测试。

这个加分是**产品决定，不是实证结论**（实测 T+10 −0.76 / T+20 −1.58，见
`factors.dryup_bonus` 的 docstring）。正因为它逆着数据走，更要把「什么时候给分」
钉死：一旦门槛悄悄放宽，负 alpha 的影响面就会从每天约 2 只扩散到整张名单。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantcore.quant.factors import DRYUP_BONUS, dryup_bonus


def _df(*, dryup: bool = True, ret60: float = 0.30, above_ma20: bool = True):
    """造一段 120 日日线：前 59 天横盘，最近 61 天涨 ret60（口径是**近 60 日**涨幅），
    最后一天成交额地量或不地量。"""
    n = 120
    close = np.concatenate([np.full(59, 10.0), np.linspace(10.0, 10.0 * (1 + ret60), 61)])
    if not above_ma20:
        close[-1] = close[-20:].mean() * 0.95
    amount = np.full(n, 5e8)
    amount[-1] = 1e7 if dryup else 9e8
    return pd.DataFrame({"close": close, "amount": amount})


def test_bonus_fires_on_allens_exact_criteria():
    assert dryup_bonus(_df(), industry_heat=90.0, amt_rank=0.85) == DRYUP_BONUS


@pytest.mark.parametrize("kwargs, heat, rank, why", [
    (dict(dryup=False), 90.0, 0.85, "当天不是地量"),
    (dict(ret60=0.05), 90.0, 0.85, "60日涨幅不足20%"),
    (dict(above_ma20=False), 90.0, 0.85, "已跌破20日线"),
    (dict(), 50.0, 0.85, "板块不热"),
    (dict(), 90.0, 0.40, "没人气（垃圾股/仙股）"),
])
def test_bonus_stays_silent_when_any_gate_fails(kwargs, heat, rank, why):
    assert dryup_bonus(_df(**kwargs), industry_heat=heat, amt_rank=rank) == 0.0, why


def test_bonus_can_be_switched_off(monkeypatch):
    """LYNX_DRYUP_BONUS=0 必须能整条关掉 —— 它是逆数据的加分，得留一个开关。"""
    monkeypatch.setattr("quantcore.quant.factors.DRYUP_BONUS", 0.0)
    assert dryup_bonus(_df(), industry_heat=90.0, amt_rank=0.85) == 0.0


def test_short_history_does_not_crash():
    assert dryup_bonus(_df().tail(30), industry_heat=90.0, amt_rank=0.85) == 0.0
