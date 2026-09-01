"""市场宽度与新鲜度标记的测试。"""
from datetime import date, timedelta

import pandas as pd
import pytest

from quantcore.quant.breadth import build_breadth
from quantcore.quant.freshness import mark
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


def _seed_market(store, n=140, n_up=120, n_down=80):
    """造一个方向明确的市场：n_up 只持续上涨、n_down 只持续下跌。"""
    dates = _trading_dates(n)
    for i in range(n_up + n_down):
        rising = i < n_up
        step = 1.004 if rising else 0.996
        closes = [10.0]
        for _ in range(n - 1):
            closes.append(closes[-1] * step)
        store.upsert_kline(f"{600000 + i}", pd.DataFrame({
            "date": dates, "open": closes, "high": closes, "low": closes,
            "close": closes, "volume": [1e6] * n, "amount": [1e8] * n,
        }))
    return dates


def test_breadth_reflects_actual_direction(store):
    """120 涨 / 80 跌的市场，上涨占比必须落在 0.6 附近，均线占比同向。"""
    _seed_market(store)
    out = build_breadth(store, days=10)
    latest = out["latest"]
    assert latest["total"] == 200
    assert latest["up"] == 120 and latest["down"] == 80
    assert latest["pct_up"] == pytest.approx(0.6, abs=0.01)
    # 持续上涨的票必然站在均线上，持续下跌的必然在下面
    assert latest["above_ma20"] == pytest.approx(0.6, abs=0.01)
    assert latest["above_ma60"] == pytest.approx(0.6, abs=0.01)


def test_new_high_excludes_today(store):
    """新高判定必须比**前** 20 日的极值，否则「今天创今天的新高」恒真。

    持续上涨的票每天都该是新高，持续下跌的每天都该是新低——两者之和等于全市场，
    若把当日算进回看窗口，新高数会被压成 0。
    """
    _seed_market(store)
    latest = build_breadth(store, days=5)["latest"]
    assert latest["new_high20"] == 120
    assert latest["new_low20"] == 80


def test_returns_empty_when_history_too_short(store):
    _seed_market(store, n=30)
    assert build_breadth(store) == {}


def test_freshness_data_behind_beats_recent_compute():
    """数据落后于最新交易日时必须判 stale——哪怕是刚算出来的。

    重算一份旧数据不会让它变新，这里如果按计算时间给 fresh，就会给出误导性的绿灯。
    """
    f = mark("2026-08-25", computed_at=None, latest_bar="2026-08-31")
    assert f["state"] == "stale"
    assert f["data_behind"] is True


def test_freshness_fresh_when_both_axes_current():
    f = mark("2026-08-31", computed_at=None, latest_bar="2026-08-31")
    assert f["state"] == "fresh"
    assert f["data_behind"] is False


def test_freshness_ages_with_compute_time():
    import time
    f = mark("2026-08-31", computed_at=time.time() - 3600, latest_bar="2026-08-31")
    assert f["state"] == "aging"
    f2 = mark("2026-08-31", computed_at=time.time() - 6 * 3600, latest_bar="2026-08-31")
    assert f2["state"] == "stale"
