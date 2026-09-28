"""kline_symbol_count 缓存：省掉重复的全表计数，但写入日线后必须立刻反映。"""
import pandas as pd

from quantcore.quant.local_store import LocalQuantStore


def _bar(day):
    return pd.DataFrame([{"date": day, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 1}])


def test_count_is_cached_but_invalidated_on_write(tmp_path):
    store = LocalQuantStore(str(tmp_path / "t.sqlite"))
    store.upsert_kline("600001", _bar("2026-09-01"))
    assert store.kline_symbol_count() == 1
    # 绕过写入接口直接插库：缓存生效时计数不变（证明没有每次全表扫）
    store._conn().execute("INSERT INTO daily_kline VALUES ('600002','2026-09-01',1,1,1,1,1,1)")
    assert store.kline_symbol_count() == 1
    # 给老股票追加日线：股票数不变，缓存不作废（夜间同步 5000 只时不再反复全表计数）
    store.upsert_kline("600001", _bar("2026-09-02"))
    assert store.kline_symbol_count() == 1
    # 新股票第一次落日线：缓存失效，计数立刻更新（绕过接口插入的 600002 也一并算上）
    store.upsert_kline("600003", _bar("2026-09-01"))
    assert store.kline_symbol_count() == 3
