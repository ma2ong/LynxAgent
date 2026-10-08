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


def test_breaker_stops_when_source_returns_nothing(tmp_path, monkeypatch):
    """数据源被封时每只都拉空：连续 20 只后必须停手，并把原因写进 last_error。"""
    svc, store, t, y = _svc(tmp_path, monkeypatch, 15, 30)
    universe = [{"symbol": f"{600000 + i:06d}", "name": f"股{i}"} for i in range(100)]
    monkeypatch.setattr(svc, "_fetch_universe", lambda: universe)
    calls = []
    monkeypatch.setattr(svc, "_fetch_kline", lambda sym, start: calls.append(sym) or pd.DataFrame())
    svc.run_sync(full=True, block=True)
    st = svc.status()
    assert len(calls) < 40                      # 没有把 100 只全打一遍
    assert st.get("breaker_tripped") is True
    assert "限流" in st.get("last_error", "")


def test_long_holiday_does_not_mark_every_stock_as_gap(tmp_path, monkeypatch):
    """国庆长假后近 18 天窗口里全市场只有 7 根 bar：数据是完整的，不能把全市场当缺口逐只重拉
    （2026-10-08：5525 只全部回补，回退源线程堆积把事件循环卡死，看门狗反复重启）。
    缺口应以「全市场常态 bar 数」为基准判断，只有明显少于同伴的才回补。"""
    svc, store, t, y = _svc(tmp_path, monkeypatch, 10, 30)
    today = date.today()
    # 7 个交易日都在 8~16 天前（中间是长假），全市场一致
    days = [(today - timedelta(days=k)).isoformat() for k in range(8, 17) if k not in (12, 13)]
    universe = [{"symbol": f"{600000 + i:06d}", "name": f"股{i}"} for i in range(30)]
    rows = [(u["symbol"], d, 10, 10, 10, 10, 1e4, 1e7) for u in universe for d in days]
    rows += [("600099", d, 10, 10, 10, 10, 1e4, 1e7) for d in days[:2]]   # 真缺口：只有 2 根
    universe.append({"symbol": "600099", "name": "缺口股"})
    with store._conn() as conn:
        conn.executemany("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?)", rows)
    monkeypatch.setattr(svc, "_fetch_universe", lambda: universe)
    monkeypatch.setattr(svc, "_fetch_snapshot_bars", lambda syms: ([], t))
    calls = []
    monkeypatch.setattr(svc, "_fetch_kline", lambda sym, start: calls.append(sym) or pd.DataFrame())
    svc.run_sync(full=False, block=True)
    assert calls == ["600099"]
