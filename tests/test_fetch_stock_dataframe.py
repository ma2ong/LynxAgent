"""个股日线取数：A 股必须走本地库，且并发下不得串股票。

回归背景（2026-07-27）：三个网络源在本机全部不可用（akshare 走东财被代理挡、
efinance 签名对不上、baostock 超时），fetch_stock_dataframe 每次都掉到 yfinance 兜底。
而 yfinance 并发不安全——两只股票同时取，第二只会拿到第一只的 K 线。凡是并发调
analyze() 的路径（组合诊断的 gather、engine.screen() 的 8 线程池）都会张冠李戴。
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd
import pytest

from quantcore.quant import data as qdata


def _trading_dates(n: int):
    out = []
    d = date.today() - timedelta(days=n * 2 + 5)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


@pytest.fixture()
def local_store(tmp_path, monkeypatch):
    """给每只股票种一条可区分的价格曲线（close 以股票代码为基数）。"""
    from quantcore.quant.local_store import LocalQuantStore

    store = LocalQuantStore(str(tmp_path / "kline.sqlite"))
    dates = _trading_dates(120)
    for sym, base in (("600001", 100.0), ("600002", 200.0), ("600003", 300.0)):
        store.upsert_kline(sym, pd.DataFrame({
            "date": dates, "open": [base] * 120, "high": [base] * 120,
            "low": [base] * 120, "close": [base] * 120,
            "volume": [1e6] * 120, "amount": [1e8] * 120,
        }))
    monkeypatch.setattr("quantcore.quant.local_store.get_local_store", lambda: store)
    return store


def _boom(*_a, **_kw):
    raise AssertionError("A 股不应触网：本地库已有数据")


def test_a_share_reads_local_and_never_hits_network(local_store, monkeypatch):
    monkeypatch.setattr(qdata, "_fetch_from_yfinance", _boom)
    monkeypatch.setattr("quantcore.quant.data_sources.fetch_history", _boom)

    df = qdata.fetch_stock_dataframe("600002", None, None)
    assert not df.empty
    assert float(df["close"].iloc[-1]) == 200.0


def test_concurrent_fetches_do_not_cross_symbols(local_store, monkeypatch):
    """核心回归：并发取三只，各自必须拿到自己的价格，不能串。"""
    monkeypatch.setattr(qdata, "_fetch_from_yfinance", _boom)
    monkeypatch.setattr("quantcore.quant.data_sources.fetch_history", _boom)

    symbols = ["600001", "600002", "600003"]
    expected = {"600001": 100.0, "600002": 200.0, "600003": 300.0}
    with ThreadPoolExecutor(max_workers=3) as pool:
        frames = list(pool.map(lambda s: (s, qdata.fetch_stock_dataframe(s, None, None)), symbols))
    got = {s: float(df["close"].iloc[-1]) for s, df in frames}
    assert got == expected


def test_non_a_share_still_uses_network(local_store, monkeypatch):
    """港美股等非 6 位数字代码本地没有，必须保持原来的网络取数路径。"""
    called = {}

    def fake_fetch_history(symbol, start, end):
        called["symbol"] = symbol
        return pd.DataFrame({"date": _trading_dates(5), "open": [1.0] * 5, "high": [1.0] * 5,
                             "low": [1.0] * 5, "close": [1.0] * 5}), "stub", {}

    monkeypatch.setattr("quantcore.quant.data_sources.fetch_history", fake_fetch_history)
    df = qdata.fetch_stock_dataframe("AAPL", None, None)
    assert called["symbol"] == "AAPL"
    assert not df.empty
