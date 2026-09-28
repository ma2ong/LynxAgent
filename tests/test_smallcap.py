"""小市值周频组合：选股规则、涨停续持、盘中占位 bar 不参与因子。"""
from datetime import date, datetime, timedelta

import pytest

from quantcore.quant.local_store import LocalQuantStore
from quantcore.quant.smallcap import compute


def _days(start, n):
    out, d = [], date.fromisoformat(start)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


@pytest.fixture()
def store(tmp_path):
    """20 只中小板票 × 30 个交易日；002000~002004 成交额最平稳（标准差最小）。"""
    s = LocalQuantStore(str(tmp_path / "t.sqlite"))
    days = _days("2026-08-03", 30)
    rows = []
    for i in range(20):
        sym = f"{2000 + i:06d}"
        for j, d in enumerate(days):
            amt = 5e7 + ((i + 1) * 1e6 * (1 if j % 2 else -1))   # i 越大波动越大；波动为 0 的按原策略剔除
            rows.append((sym, d, 10.0, 10.0, 10.0, 10.0, 1e6, amt))
    conn = s._conn()
    with conn:
        conn.executemany("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?)", rows)
        conn.executemany("INSERT OR REPLACE INTO stock_meta(symbol, name) VALUES (?, ?)",
                         [(f"{2000 + i:06d}", f"股{i}") for i in range(20)])
    return s, days


def _run(s, days):
    return compute(s, weeks=2, now=datetime.fromisoformat(days[-1] + "T16:00:00"))


def test_picks_the_five_steadiest_turnover(store):
    s, days = store
    r = _run(s, days)
    steadiest = ["002000", "002001", "002002", "002003", "002004"]
    assert r["history"] and all(h["symbols"] == steadiest for h in r["history"])
    assert [it["symbol"] for it in r["items"]] == steadiest


def test_st_names_are_excluded(store):
    s, days = store
    with s._conn() as conn:
        conn.execute("UPDATE stock_meta SET name='*ST股0' WHERE symbol='002000'")
    r = _run(s, days)
    assert all("002000" not in h["symbols"] for h in r["history"])
    assert "002000" not in [it["symbol"] for it in r["items"]]


def test_limit_up_holding_is_kept_next_week(store):
    """上周持仓在换仓前一日涨停：本周续持，即使它已不在最平稳的前 5。"""
    s, days = store
    r0 = _run(s, days)
    last_week = r0["history"][0]
    held = last_week["symbols"][0]
    ws = r0["week_start"]
    i = days.index(ws)
    with s._conn() as conn:
        # 换仓前一日涨停；并把它这几天的成交额打乱，让它按因子本该被换掉
        conn.execute("UPDATE daily_kline SET close=11.0 WHERE symbol=? AND date=?", (held, days[i - 1]))
        for j, d in enumerate(days[i - 5:i]):   # 只动本周选股窗口，别碰上周的
            conn.execute("UPDATE daily_kline SET amount=? WHERE symbol=? AND date=?",
                         (1e7 if j % 2 else 9e7, held, d))
    r = _run(s, days)
    item = next(it for it in r["items"] if it["symbol"] == held)
    assert item["kept_limit_up"] is True


def test_endpoint_requires_login_and_survives_empty_db():
    import uuid

    from fastapi.testclient import TestClient

    import app.lite_main as lite_main
    from app.lite_auth import issue_tokens, store as auth_store

    client = TestClient(lite_main.app)
    assert client.get("/api/quant/smallcap").status_code == 401
    name = f"u{uuid.uuid4().hex[:8]}"
    auth_store.create_user(name, f"{name}@example.com", "Passw0rd!x")
    r = client.get("/api/quant/smallcap",
                   headers={"Authorization": f"Bearer {issue_tokens(name)['access_token']}"})
    assert r.status_code == 200 and r.json()["data"]["items"] == []
