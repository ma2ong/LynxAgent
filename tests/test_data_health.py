"""数据新鲜度守卫：整天缺失（whole-day gap）检测与健康报告。"""
from datetime import date, timedelta

import pandas as pd
import pytest

from quantcore.quant.local_store import LocalQuantStore


@pytest.fixture()
def store(tmp_path):
    return LocalQuantStore(str(tmp_path / "test.sqlite"))


def _trading_dates(n: int):
    out = []
    d = date.today() - timedelta(days=n * 2 + 5)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


def _seed_market(store: LocalQuantStore, symbols, n_days=10):
    dates = _trading_dates(n_days)
    for sym in symbols:
        df = pd.DataFrame({
            "date": dates, "open": [10.0] * n_days, "high": [10.0] * n_days,
            "low": [10.0] * n_days, "close": [10.0] * n_days,
            "volume": [1e6] * n_days, "amount": [1e8] * n_days,
        })
        store.upsert_kline(sym, df)
    store.upsert_meta([{"symbol": s, "name": s} for s in symbols])
    return dates


def test_whole_day_gap_detected(store):
    # ready 判定要求本地 ≥500 只，按真实规模种 600 只
    syms = [f"6{i:05d}" for i in range(600)]
    dates = _seed_market(store, syms)
    conn = store._conn()
    # 中间一天只剩 2/600 只 → 整天缺失
    conn.execute(
        "DELETE FROM daily_kline WHERE date = ? AND symbol NOT IN ('600000','600001')",
        (dates[5],),
    )
    conn.commit()

    gaps = store.whole_day_gap_dates(days=18)
    assert dates[5] in gaps

    health = store.kline_health()
    assert dates[5] in health["gap_dates"]
    # 有整天缺口时必须触发增量同步（即使当天是盘中/周末）
    assert health["needs_incremental_sync"] is True
    # 近 10 个交易日覆盖曲线对前端可见
    days_map = {d["date"]: d["count"] for d in health["recent_days"]}
    assert days_map[dates[5]] == 2
    assert days_map[dates[4]] == 600


def test_no_gap_when_market_complete(store):
    syms = [f"6001{i:02d}" for i in range(10)]
    _seed_market(store, syms)
    assert store.whole_day_gap_dates(days=18) == []
    health = store.kline_health()
    assert health["gap_dates"] == []


def test_symbols_missing_on_dates(store):
    syms = [f"6002{i:02d}" for i in range(10)]
    dates = _seed_market(store, syms)
    conn = store._conn()
    conn.execute(
        "DELETE FROM daily_kline WHERE date = ? AND symbol NOT IN ('600200','600201')",
        (dates[5],),
    )
    conn.commit()
    missing = store.symbols_missing_on_dates([dates[5]])
    assert len(missing) == 8
    assert "600200" not in missing and "600202" in missing
