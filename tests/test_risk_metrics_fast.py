"""risk_metrics 不再整套重算指标后，数值必须与原口径（enrich_indicators 的 ret / drawdown）一致。"""
import numpy as np
import pandas as pd

from quantcore.quant.factors import enrich_indicators, risk_metrics


def _reference(df):
    data = enrich_indicators(df)
    r = data["ret"].dropna()
    return {"volatility": round(float(r.std() * 252 ** 0.5), 4),
            "max_drawdown": round(float(data["drawdown"].min()), 4),
            "sharpe": round(float(r.mean() / r.std() * 252 ** 0.5), 4)}


def test_matches_enrich_indicators():
    rng = np.random.default_rng(7)
    close = 10 * np.cumprod(1 + rng.normal(0, 0.03, 300))
    df = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=300), "open": close, "high": close * 1.01,
                       "low": close * 0.99, "close": close, "volume": 1e5, "amount": close * 1e5})
    assert risk_metrics(df) == _reference(df)
