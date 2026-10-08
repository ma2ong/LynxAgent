"""自选组合体检：因子分只随日线变化，同一交易日内不该每次请求、每个用户都重算一遍
（44 只约 3.3 秒 CPU，跑在线程里照样抢 GIL 拖慢全站）。"""
import numpy as np
import pandas as pd

from app.routers import favorites


def _df(days=300, last="2026-09-30"):
    close = 10 * np.cumprod(1 + np.random.default_rng(1).normal(0, 0.02, days))
    dates = pd.date_range(end=last, periods=days)
    return pd.DataFrame({"date": dates, "open": close, "high": close, "low": close,
                         "close": close, "volume": 1e5, "amount": close * 1e5})


def test_scores_cached_until_new_bar(monkeypatch):
    calls = []
    frames = {"d": _df()}
    monkeypatch.setattr(favorites, "load_local_kline", lambda s, n=None: frames["d"], raising=False)
    import quantcore.quant.factors as f
    real = f.compute_factor_scores
    monkeypatch.setattr(f, "compute_factor_scores", lambda df: calls.append(1) or real(df))
    favorites._QUANT_CACHE.clear()

    a = favorites._quant_from_kline("600001")
    b = favorites._quant_from_kline("600001")
    assert len(calls) == 1 and a[1] == b[1]
    frames["d"] = _df(last="2026-10-08")        # 新的一根日线 → 重算
    favorites._quant_from_kline("600001")
    assert len(calls) == 2
