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


def test_ex_rights_symbol_gets_full_history_refetch(tmp_path, monkeypatch):
    """快照昨收 ≠ 本地上一根收盘（除权）：该股整段重拉前复权历史，而不是只补近 45 天。"""
    svc, store, t, y = _svc(tmp_path, monkeypatch, 15, 30)
    y2 = (date.today() - timedelta(days=2)).isoformat()
    # 本地已有完整近窗，否则会因「缺口」被纳入回补，测不出除权分支
    rows = [("600001", (date.today() - timedelta(days=k)).isoformat(), 20, 20, 20, 20, 1e4, 2e7)
            for k in range(1, 19)]
    with store._conn() as conn:
        conn.executemany("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?)", rows)
    starts = []

    def fake_snapshot(syms):
        svc._snapshot_prev_close = {"600001": 10.0}      # 10 送 10 后昨收折半
        return [("600001", t, 10, 11, 9, 10.5, 1e4, 1.05e7)], t

    monkeypatch.setattr(svc, "_fetch_snapshot_bars", fake_snapshot)
    monkeypatch.setattr(svc, "_fetch_kline", lambda sym, start: starts.append(start) or pd.DataFrame())
    svc.run_sync(full=False, block=True)
    assert starts, "除权股必须被纳入回补"
    assert starts[0] < (date.today() - timedelta(days=400)).isoformat()   # 用的是全量起点
