"""多因子模型的影子留痕（2026-10-09 Allen 定：记半年，只记录不给用户看）。

回测（experiments/ml_lab.py）里低换手构造 5 年全正、年化约 +17%，但 t≈1.3 不够显著，
而且是 4 种构造里挑的。唯一可靠的检验是没见过的真实行情：每周记一份名单，半年后看。

每周最后一个交易日收盘后（计划任务周一到周五 17:30 跑，周末电脑关机；
周五没跑成，下周一补记上周五的名单，只用上周五及以前的数据）：
1. 用全部历史重训一次（参数固定，与回测相同）；
2. 对最近 5 个交易日打分取平均（平滑），按当周最后一天横截面分位排序；
3. 剔除 ST/退市股（按记录时的名称）；上周持仓分位仍 ≥0.8 的留下，其余从高分往下补满 50 只；
4. 写进 ml_shadow_picks。下周一开盘买、再下周一开盘卖，由 evaluate() 事后计算。

单独进程跑，不进后端：训练要几 GB 内存、几分钟 CPU。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .universe import is_blocked_name
from .ml_factors import MIN_AMT, MIN_BARS, PARAMS, ROUNDS, build_features, load_kline, rank_universe

HOLD = 50
KEEP_PCT = 0.8
SMOOTH_DAYS = 5
COST = 0.003          # 双边，按换手扣
MIN_WEEKS = 26        # 半年；不到就只报进度不下结论

SCHEMA = """
CREATE TABLE IF NOT EXISTS ml_shadow_picks (
    signal_date TEXT,      -- 当周最后一个交易日（信号收盘后产生，下一交易日开盘买）
    symbol TEXT,
    rank INTEGER,
    score REAL,            -- 5 日平滑后的横截面分位
    kept INTEGER,          -- 1 = 上周持仓留下的
    close REAL,
    created_at TEXT,
    PRIMARY KEY (signal_date, symbol)
);
"""


def _conn(db: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db), timeout=60)
    conn.executescript(SCHEMA)
    return conn


def signal_date(db: str | Path, now: datetime) -> str | None:
    """该记哪一周：最近一个已经收完的周的最后一个交易日。
    周五 17:00 后本周算收完，但要求今天的日线已同步（否则留给下周一补记）。"""
    monday = (now - timedelta(days=now.weekday())).date()
    week_done = now.weekday() >= 5 or (now.weekday() == 4 and now.hour >= 17)
    cutoff = monday + timedelta(days=7) if week_done else monday
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    last = conn.execute("SELECT max(date) FROM daily_kline WHERE date < ?", (cutoff.isoformat(),)).fetchone()[0]
    conn.close()
    if now.weekday() == 4 and week_done and last != now.date().isoformat():
        return None
    return last


def run_weekly(db: str | Path, now: datetime | None = None) -> dict:
    import lightgbm as lgb

    last = signal_date(db, now or datetime.now())
    if not last:
        return {"status": "skip", "reason": "今天日线还没同步，下个交易日再补记"}
    with _conn(db) as conn:
        done = conn.execute("SELECT 1 FROM ml_shadow_picks WHERE signal_date = ? LIMIT 1", (last,)).fetchone()
        prev_date = conn.execute(
            "SELECT max(signal_date) FROM ml_shadow_picks WHERE signal_date < ?", (last,)).fetchone()[0]
        prev = {r[0] for r in conn.execute(
            "SELECT symbol FROM ml_shadow_picks WHERE signal_date = ?", (prev_date,))} if prev_date else set()
        names_now = dict(conn.execute("SELECT symbol, name FROM stock_meta"))
    if done:
        return {"status": "skip", "reason": f"{last} 已记过"}

    k = load_kline(db, since="2020-01-01")
    k = k[k["date"] <= last]
    k, names = build_features(k)
    u = rank_universe(k, names, "2021-01-01")
    del k
    train = u[u["y"].notna()]
    model = lgb.train(PARAMS, lgb.Dataset(train[names], train["y"], free_raw_data=True), ROUNDS)
    recent_days = sorted(u["date"].unique())[-SMOOTH_DAYS:]
    rec = u[u["date"].isin(recent_days)][["symbol", "date", "close"] + names].copy()
    rec["pct"] = model.predict(rec[names])
    rec["pct"] = rec.groupby("date")["pct"].rank(pct=True)
    today = rec[rec["date"] == last].set_index("symbol")
    smooth = rec[rec["symbol"].isin(today.index)].groupby("symbol")["pct"].mean()
    score = smooth.rank(pct=True)
    score = score[[not is_blocked_name(names_now.get(s)) for s in score.index]]

    kept = [s for s in prev if s in score.index and score[s] >= KEEP_PCT]
    fill = [s for s in score.sort_values(ascending=False).index if s not in kept][: max(0, HOLD - len(kept))]
    picks = kept + fill
    order = score[picks].sort_values(ascending=False)
    now = datetime.now().isoformat(timespec="seconds")
    rows = [(last, s, i + 1, float(order[s]), int(s in kept), float(today.at[s, "close"]), now)
            for i, s in enumerate(order.index)]
    with _conn(db) as conn:
        conn.executemany("INSERT OR REPLACE INTO ml_shadow_picks VALUES (?,?,?,?,?,?,?)", rows)
    return {"status": "ok", "signal_date": last, "picks": len(rows), "kept": len(kept),
            "train_rows": int(len(train))}


def evaluate(db: str | Path) -> dict:
    """每周：信号日次日开盘买、下一个信号日次日开盘卖；基准 = 同日可投资池等权（同口径）。
    开盘即涨停的买不进，按没买算（从组合里剔掉）。"""
    with _conn(db) as conn:
        picks = pd.read_sql_query("SELECT signal_date, symbol, kept FROM ml_shadow_picks", conn)
    if picks.empty:
        return {"weeks": 0, "rows": []}
    dates = sorted(picks["signal_date"].unique())
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    k = pd.read_sql_query("SELECT symbol, date, open, close, amount FROM daily_kline WHERE date >= ?",
                          conn, params=(str(pd.Timestamp(dates[0]) - pd.Timedelta(days=420))[:10],))   # 够数满 250 根
    conn.close()
    k = k[~k["symbol"].str.startswith(("8", "4", "92")) & (k["open"] > 0)].sort_values(["symbol", "date"])
    g = k.groupby("symbol", sort=False)
    k["amt20"] = g["amount"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    k["bars"] = g.cumcount()
    days = sorted(k["date"].unique())
    nxt = {d: days[i + 1] for i, d in enumerate(days[:-1])}
    by_day = {d: x.set_index("symbol") for d, x in k.groupby("date")}
    lim = lambda s: 0.195 if s.startswith(("300", "301", "688", "689")) else 0.095  # noqa: E731
    rows, hold = [], set()
    for d0, d1 in zip(dates, dates[1:] + [None]):
        buy_day = nxt.get(d0)
        sell_day = nxt.get(d1) if d1 else None
        if not buy_day or not sell_day:
            break                         # 本周还没兑现
        sig, buy, sell = by_day[d0], by_day[buy_day], by_day[sell_day]
        syms = list(picks.loc[picks["signal_date"] == d0, "symbol"])
        ok = [s for s in syms if s in buy.index and s in sell.index and s in sig.index
              and buy.at[s, "open"] / sig.at[s, "close"] - 1 < lim(s) - 0.002]
        r = (sell.loc[ok, "open"] / buy.loc[ok, "open"] - 1).clip(-0.6, 1.5)
        pool = sig[(sig["amt20"] >= MIN_AMT) & (sig["bars"] >= MIN_BARS)].index
        both = pool.intersection(buy.index).intersection(sell.index)
        base = (sell.loc[both, "open"] / buy.loc[both, "open"] - 1).clip(-0.6, 1.5)
        turnover = len(set(syms) - hold) / max(len(syms), 1)
        hold = set(syms)
        ret = float(r.mean()) - COST * turnover
        rows.append({"signal_date": d0, "ret": ret, "base": float(base.mean()), "excess": ret - float(base.mean()),
                     "bought": len(ok), "turnover": turnover})
    if not rows:
        return {"weeks": 0, "rows": []}
    t = pd.DataFrame(rows)
    ex = t["excess"]
    out = {
        "weeks": len(t), "rows": rows,
        "cum_ret": float((1 + t["ret"]).prod() - 1), "cum_base": float((1 + t["base"]).prod() - 1),
        "mean_excess": float(ex.mean()), "win_rate": float((ex > 0).mean()),
        "t": float(ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex)))) if len(ex) > 2 and ex.std() > 0 else None,
    }
    out["verdict"] = ("样本不足：至少记满 %d 周再下结论" % MIN_WEEKS if len(t) < MIN_WEEKS else
                      "有效" if out["t"] is not None and out["t"] >= 2 and out["mean_excess"] > 0 else "未证明有效")
    return out


def latest_list(db: str | Path) -> dict:
    """「模型选股」页用：最新一期名单 + 相对上一期的进出 + 买入日开盘价（还没到买入日则为空）。"""
    import json
    from .ml_factors import INDUSTRY_MAP

    with _conn(db) as conn:
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT signal_date FROM ml_shadow_picks ORDER BY signal_date DESC LIMIT 2")]
        if not dates:
            return {"signal_date": None, "items": [], "sold": []}
        cur = dates[0]
        rows = conn.execute("SELECT symbol, rank, score, kept, close FROM ml_shadow_picks "
                            "WHERE signal_date = ? ORDER BY rank", (cur,)).fetchall()
        prev = {r[0] for r in conn.execute(
            "SELECT symbol FROM ml_shadow_picks WHERE signal_date = ?", (dates[1],))} if len(dates) > 1 else set()
        names = dict(conn.execute("SELECT symbol, name FROM stock_meta"))
        buy_date = conn.execute("SELECT min(date) FROM daily_kline WHERE date > ?", (cur,)).fetchone()[0]
        buy_open = dict(conn.execute("SELECT symbol, open FROM daily_kline WHERE date = ?", (buy_date,))) if buy_date else {}
    try:
        ind = json.load(open(INDUSTRY_MAP, encoding="utf-8"))
    except OSError:
        ind = {}
    now = {r[0] for r in rows}
    items = [{"symbol": s, "name": names.get(s, ""), "industry": ind.get(s, ""), "rank": rk,
              "score": round(sc, 4), "kept": bool(kp), "signal_close": c, "buy_open": buy_open.get(s)}
             for s, rk, sc, kp, c in rows]
    sold = [{"symbol": s, "name": names.get(s, "")} for s in sorted(prev - now)]
    return {"signal_date": cur, "prev_date": dates[1] if len(dates) > 1 else None, "buy_date": buy_date,
            "items": items, "sold": sold}
