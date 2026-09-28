"""追涨风格失灵提示：上一批已兑现名单 vs 市场（quantcore/quant/style_health.py）。"""
from datetime import date, datetime, timedelta

import pytest

from quantcore.quant.local_store import LocalQuantStore
from quantcore.quant.style_health import smart_style_health

N_MARKET = 520


def _days(n):
    out, d = [], date(2026, 9, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


@pytest.fixture()
def seeded(tmp_path):
    """14 个交易日；市场票每天 +0.1%，5 只推荐票每天 −1%，推荐日在第 1 天。"""
    store = LocalQuantStore(str(tmp_path / "t.sqlite"))
    days = _days(14)
    rows = []
    for i in range(N_MARKET):
        sym = f"{600000 + i:06d}"
        step = -0.01 if i < 5 else 0.001
        for k, d in enumerate(days):
            c = 10 * (1 + step) ** k
            rows.append((sym, d, c, c, c, c, 1e6, 5e7))
    conn = store._conn()
    with conn:
        conn.executemany("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?)", rows)
    with conn:
        conn.executemany(
            "INSERT INTO picks_history(pick_date,pool,symbol,name,score,close,rank,patterns) "
            "VALUES(?,?,?,?,?,?,?,?)",
            [(days[1], "smart", f"{600000 + i:06d}", "", 90, 10, i, "") for i in range(5)])
    return store, days


def test_lagging_batch_is_flagged(seeded):
    store, days = seeded
    # 最后一天收盘后：完整日含最后一天，推荐日 days[1]，持有 12 天 ≥ 10
    now = datetime.fromisoformat(days[-1] + "T16:00:00")
    h = smart_style_health(store, now=now)
    assert h["pick_date"] == days[1]
    assert h["eval_date"] == days[-1]
    assert h["hold_days"] == 12
    assert h["samples"] == 5
    assert h["win_rate"] == 0
    assert h["failing"] is True
    assert h["pick_ret"] == pytest.approx((0.99 ** 12 - 1) * 100, abs=0.01)


def test_today_intraday_bar_is_not_a_complete_day(seeded):
    store, days = seeded
    # 盘中：最后一天的 bar 是占位，评估日必须退到前一天
    now = datetime.fromisoformat(days[-1] + "T10:30:00")
    assert smart_style_health(store, now=now)["eval_date"] == days[-2]


def test_not_enough_history_returns_none(seeded):
    store, days = seeded
    now = datetime.fromisoformat(days[9] + "T16:00:00")
    assert smart_style_health(store, now=now) is None
