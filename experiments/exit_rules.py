"""卖出纪律能不能把一键智选从亏变赚（2026-10-09）。

问题：智选 T+5 胜率 ~34%。散户里赚钱的人靠「亏小赚大」：止损严格、涨对了拿得住。
同一批买入，换几种事先定好的卖法，平均收益和胜率怎么变？

样本：
- replay：最近一次 12 个月回放（52 期 × 前 20），次日开盘买
- live：线上留痕（2026-07-14 起；08-25 前用 picks_history 留痕价，之后用首推表首推价）
对照：同一天从可投资池（20 日均成交额 ≥ 3000 万）随机抽同样多只，用同样卖法 ——
卖法如果对随机票也一样「有效」，那它改变的是分布形状，不是选股能力。

卖法（事先定死）：
- 持有 5 / 10 / 20 / 40 日收盘卖
- 止损8%：盘中触及 −8% 卖（跳空低开按开盘价），否则 20 日卖
- 止损8%+跟踪：−8% 止损；浮盈曾 ≥10% 后，收盘跌破最高收盘 −10% 次日开盘卖；最长 60 日
- 纯跟踪15%：收盘跌破最高收盘 −15% 次日开盘卖；最长 60 日
- 止损5%+止盈15%：触及 +15% 卖（跳空高开按开盘价）；最长 20 日
收益为原始收益减同期全市场等权指数收益（超额），扣双边 0.3%。

用法：python experiments/exit_rules.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "runtime" / "quant_data.sqlite"
COST = 0.003
MAXH = 60
rng = np.random.default_rng(7)


def load():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    run_id = conn.execute("SELECT run_id FROM replay_runs WHERE status='done' ORDER BY created_at DESC LIMIT 1").fetchone()
    run_id = run_id[0] if run_id else conn.execute("SELECT max(run_id) FROM replay_results").fetchone()[0]
    rep = pd.read_sql_query(
        "SELECT as_of AS d, symbol FROM replay_results WHERE pool='smart' AND run_id=? AND rank<=20", conn, params=(run_id,))
    # 线上留痕：首推表之前用 picks_history（留痕时价格），之后用首推价
    live = pd.read_sql_query(
        "SELECT pick_date AS d, symbol, close AS first_price FROM picks_history WHERE pool='smart' AND pick_date < '2026-08-25' "
        "UNION ALL SELECT pick_date, symbol, first_price FROM pick_first_seen WHERE pool='smart'", conn)
    k = pd.read_sql_query(
        "SELECT symbol, date, open, high, low, close, amount FROM daily_kline WHERE date >= '2025-04-01'", conn)
    conn.close()
    k = k[(k["open"] > 0) & (k["close"] > 0)].sort_values(["symbol", "date"])
    k["prev_close"] = k.groupby("symbol")["close"].shift(1)
    k["amt20"] = k.groupby("symbol")["amount"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    mkt = k.assign(dr=(k["close"] / k["prev_close"] - 1).clip(-0.3, 0.3)).groupby("date")["dr"].mean()
    idx = (1 + mkt.fillna(0)).cumprod()
    return run_id, rep, live, k, idx


def simulate(path: pd.DataFrame, entry: float, rule: str) -> tuple[float, int]:
    """path：买入后（不含买入当日已发生部分）的逐日 OHLC。返回 (卖出价, 持有天数)。"""
    o, h, l, c = (path[x].to_numpy() for x in ("open", "high", "low", "close"))
    n = len(c)
    if rule.startswith("持有"):
        H = int(rule[2:-1])
        i = min(H, n) - 1
        return c[i], i + 1
    if rule == "止损8%":
        stop = entry * 0.92
        for i in range(min(20, n)):
            if l[i] <= stop:
                return min(o[i], stop), i + 1
        return c[min(20, n) - 1], min(20, n)
    if rule == "止损5%+止盈15%":
        stop, tp = entry * 0.95, entry * 1.15
        for i in range(min(20, n)):
            if l[i] <= stop:
                return min(o[i], stop), i + 1
            if h[i] >= tp:
                return max(o[i], tp), i + 1
        return c[min(20, n) - 1], min(20, n)
    trail = 0.15 if rule == "纯跟踪15%" else 0.10
    hard = None if rule == "纯跟踪15%" else entry * 0.92
    peak, armed = c[0] if n else entry, rule == "纯跟踪15%"
    for i in range(min(MAXH, n)):
        if hard is not None and l[i] <= hard:
            return min(o[i], hard), i + 1
        peak = max(peak, c[i])
        if peak >= entry * 1.10:
            armed = True
        if armed and c[i] < peak * (1 - trail) and i + 1 < n:
            return o[i + 1], i + 2
    i = min(MAXH, n) - 1
    return c[i], i + 1


RULES = ["持有5日", "持有10日", "持有20日", "持有40日", "止损8%", "止损8%+跟踪", "纯跟踪15%", "止损5%+止盈15%"]


def evaluate(sample: pd.DataFrame, by_sym: dict, idx: pd.Series, days: list, label: str, entry_mode: str):
    last_day = days[-1]
    rows = []
    for d, sym, fp in sample[["d", "symbol", "first_price"]].itertuples(index=False):
        g = by_sym.get(sym)
        if g is None:
            continue
        if entry_mode == "next_open":
            fut = g[g["date"] > d]
            if fut.empty:
                continue
            entry, entry_day, path = fut["open"].iloc[0], fut["date"].iloc[0], fut
        else:
            entry, entry_day, path = fp, d, g[g["date"] > d]
            if not entry or entry <= 0 or path.empty:
                continue
        # 只评估走完 40 个交易日的样本，避免不同卖法样本不一致
        if len(path) < 40:
            continue
        path = path.iloc[:MAXH]
        pre = idx.get(days[days.index(entry_day) - 1]) if entry_mode == "next_open" else idx.get(d)
        for rule in RULES:
            px, held = simulate(path, entry, rule)
            exit_day = path["date"].iloc[min(held, len(path)) - 1]
            raw = px / entry - 1 - COST
            rows.append({"rule": rule, "raw": raw, "ex": raw - (idx[exit_day] / pre - 1), "held": held})
    r = pd.DataFrame(rows)
    if r.empty:
        print(f"{label}: 无样本")
        return r
    s = r.groupby("rule", sort=False).agg(
        笔数=("raw", "size"), 平均收益=("raw", "mean"), 中位收益=("raw", "median"), 胜率=("raw", lambda x: (x > 0).mean()),
        平均盈=("raw", lambda x: x[x > 0].mean()), 平均亏=("raw", lambda x: x[x <= 0].mean()),
        超额均值=("ex", "mean"), 平均持有天=("held", "mean"), 涨超20比例=("raw", lambda x: (x >= 0.2).mean()))
    for c in ("平均收益", "中位收益", "胜率", "平均盈", "平均亏", "超额均值", "涨超20比例"):
        s[c] = s[c] * 100
    print(f"\n=== {label} ===")
    with pd.option_context("display.width", 220):
        print(s.round(2).to_string())
    return r


def main():
    run_id, rep, live, k, idx = load()
    days = list(idx.index)
    by_sym = {s: g.reset_index(drop=True) for s, g in k.groupby("symbol", sort=False)}
    rep["first_price"] = np.nan
    print(f"回放 run {run_id}：{rep['d'].nunique()} 期 {len(rep)} 笔；线上首推 {live['d'].nunique()} 天 {len(live)} 笔")
    evaluate(rep, by_sym, idx, days, "智选回放（12 个月，次日开盘买）", "next_open")
    # 随机对照：同日从可投资池随机抽同样多只
    liquid = k[k["amt20"] >= 3e7]
    pools = {d: g["symbol"].to_numpy() for d, g in liquid[liquid["date"].isin(set(rep["d"]))].groupby("date")}
    rnd = []
    for d, n in rep.groupby("d").size().items():
        if d in pools:
            rnd += [(d, s) for s in rng.choice(pools[d], size=min(n * 3, len(pools[d])), replace=False)]
    rnd = pd.DataFrame(rnd, columns=["d", "symbol"]).assign(first_price=np.nan)
    evaluate(rnd, by_sym, idx, days, "随机对照（同日可投资池，次日开盘买）", "next_open")
    evaluate(live, by_sym, idx, days, "线上留痕（留痕价/首推价买，需走完 40 日）", "first_price")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
