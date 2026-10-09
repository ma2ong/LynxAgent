"""多因子模型影子留痕（2026-10-09）：只记录不展示，半年后看样本外成绩。"""
import sqlite3
from datetime import date, timedelta

import numpy as np
import pytest

from quantcore.quant import ml_shadow


def _weekdays(n: int, end: date) -> list[str]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return sorted(out)


def _db(tmp_path, n_sym=30, n_days=320, end=date(2026, 10, 9)):
    db = tmp_path / "q.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE daily_kline (symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, "
                 "volume REAL, amount REAL)")
    rng = np.random.default_rng(1)
    days = _weekdays(n_days, end)
    for i in range(n_sym):
        px = 10.0
        for d in days:
            px *= 1 + rng.normal(0, 0.02)
            conn.execute("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?)",
                         (f"600{i:03d}", d, px, px * 1.01, px * 0.99, px, 1e6, 5e7))
    conn.commit()
    conn.close()
    return db, days


def test_run_weekly_records_once_and_only_on_weekends(tmp_path, monkeypatch):
    pytest.importorskip("lightgbm")
    monkeypatch.setitem(ml_shadow.PARAMS, "min_data_in_leaf", 20)
    monkeypatch.setattr(ml_shadow, "ROUNDS", 5)
    db, days = _db(tmp_path)

    class Weekday:
        @staticmethod
        def now():
            from datetime import datetime
            return datetime(2026, 10, 8, 20)   # 周四

    monkeypatch.setattr(ml_shadow, "datetime", Weekday)
    assert ml_shadow.run_weekly(db)["status"] == "skip"          # 周中不记

    r = ml_shadow.run_weekly(db, force=True)
    assert r["status"] == "ok" and r["signal_date"] == days[-1]
    assert r["picks"] == 30                                        # 池子只有 30 只，全选
    assert ml_shadow.run_weekly(db, force=True)["status"] == "skip"   # 同一周不重复记
    with sqlite3.connect(db) as conn:
        ranks = [r[0] for r in conn.execute("SELECT rank FROM ml_shadow_picks ORDER BY rank")]
    assert ranks == list(range(1, 31))


def test_evaluate_uses_next_open_and_pool_baseline(tmp_path):
    db, days = _db(tmp_path, n_sym=3, n_days=300)
    conn = sqlite3.connect(db)
    conn.executescript(ml_shadow.SCHEMA)
    d0, d1 = days[-12], days[-6]          # 两个信号日
    for s in ("600000",):
        conn.execute("INSERT INTO ml_shadow_picks VALUES (?,?,?,?,?,?,?)", (d0, s, 1, 0.9, 0, 10, "x"))
        conn.execute("INSERT INTO ml_shadow_picks VALUES (?,?,?,?,?,?,?)", (d1, s, 1, 0.9, 1, 10, "x"))
    # 把 600000 在买入日、卖出日的开盘价钉死：+10%
    buy, sell = days[days.index(d0) + 1], days[days.index(d1) + 1]
    sig_close = conn.execute("SELECT close FROM daily_kline WHERE symbol='600000' AND date=?", (d0,)).fetchone()[0]
    conn.execute("UPDATE daily_kline SET open=? WHERE symbol='600000' AND date=?", (sig_close, buy))
    conn.execute("UPDATE daily_kline SET open=? WHERE symbol='600000' AND date=?", (sig_close * 1.1, sell))
    conn.commit()
    conn.close()
    r = ml_shadow.evaluate(db)
    assert r["weeks"] == 1                      # 第二周还没兑现
    row = r["rows"][0]
    assert row["ret"] == pytest.approx(0.10 - ml_shadow.COST, abs=1e-6)   # 首周全是新买，换手 100%
    assert row["excess"] == pytest.approx(row["ret"] - row["base"])
    assert r["verdict"].startswith("样本不足")
