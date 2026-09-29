"""盘中同步不能把当天未收盘的 bar 当完整日线写进库（2026-09-28 10:30 事故）。"""
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from quantcore.quant import sync_service
from quantcore.quant.local_store import LocalQuantStore
from quantcore.quant.sync_service import MarketSyncService


def _svc(tmp_path, monkeypatch, hour, minute):
    today = date.today()
    fixed = datetime.combine(today, datetime.min.time()).replace(hour=hour, minute=minute)

    class FixedDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(sync_service, "datetime", FixedDT)
    store = LocalQuantStore(str(tmp_path / "t.sqlite"))
    svc = MarketSyncService(store=store, max_workers=1)
    t, y = today.isoformat(), (today - timedelta(days=1)).isoformat()
    monkeypatch.setattr(svc, "_fetch_universe", lambda: [{"symbol": "600001", "name": "测试"}])
    monkeypatch.setattr(svc, "_fetch_snapshot_bars",
                        lambda syms: ([("600001", t, 10, 11, 9, 10.5, 1e4, 1.05e7)], t))
    monkeypatch.setattr(svc, "_fetch_kline", lambda sym, start: pd.DataFrame([
        {"date": y, "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1e4, "amount": 1e7},
        {"date": t, "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1e4, "amount": 1.05e7},
    ]))
    monkeypatch.setattr(svc, "_fetch_fundamental_flags", lambda: [])
    return svc, store, t, y


def _dates(store):
    return [r[0] for r in store._conn().execute("SELECT date FROM daily_kline ORDER BY date")]


def test_intraday_sync_does_not_write_todays_bar(tmp_path, monkeypatch):
    svc, store, t, y = _svc(tmp_path, monkeypatch, 10, 30)
    svc.run_sync(full=False, block=True)
    assert t not in _dates(store)       # 当天半截 bar 不落库
    assert y in _dates(store)           # 历史缺口照常回补


def test_after_close_sync_writes_todays_bar(tmp_path, monkeypatch):
    svc, store, t, y = _svc(tmp_path, monkeypatch, 15, 30)
    svc.run_sync(full=False, block=True)
    assert t in _dates(store)
