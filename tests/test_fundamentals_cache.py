"""财务摘要/概况缓存：同一代码 12 小时内只下载一次；失败（空结果）不缓存。"""
from quantcore.quant import fundamentals as f


def test_second_call_hits_cache(monkeypatch):
    calls = []

    @f._cached
    def fake(symbol):
        calls.append(symbol)
        return {"roe": 10.0}

    f._CACHE.clear()
    assert fake("1") == {"roe": 10.0}
    assert fake("000001") == {"roe": 10.0}      # 代码补零后同一把 key
    assert calls == ["1"]


def test_empty_result_is_not_cached():
    calls = []

    @f._cached
    def flaky(symbol):
        calls.append(symbol)
        return {}

    f._CACHE.clear()
    flaky("600000"); flaky("600000")
    assert len(calls) == 2
