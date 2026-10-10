"""回调优选从前 10 名里挑 vs 前 20 名里挑（2026-10-10，名单改为 10 只后）。

与 pullback_paired.py 同口径：回放 141 期（smart_full_lab 复原）+ 线上留痕；T+5，次日开盘买。
同时报两种对照：对「前 10 平均」（用户现在看到的名单）和对「前 20 平均」。
"""
import pickle
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT / "experiments"))
from smart_full_lab import online20  # noqa: E402


def report(label, groups):
    rows = {"前20里回调最深2": [], "前10里回调最深2": []}
    vs10 = {"前20里回调最深2": [], "前10里回调最深2": []}
    abs_ = {"前10平均": [], "前20里回调最深2": [], "前10里回调最深2": []}
    for x in groups:            # x：按名次排好的一天的名单（≥10 只）
        t20, t10 = x.head(20), x.head(10)
        a = t20.nsmallest(2, "dist_high20").ex.mean()
        b = t10.nsmallest(2, "dist_high20").ex.mean()
        rows["前20里回调最深2"].append(a - t20.ex.mean()); rows["前10里回调最深2"].append(b - t20.ex.mean())
        vs10["前20里回调最深2"].append(a - t10.ex.mean()); vs10["前10里回调最深2"].append(b - t10.ex.mean())
        abs_["前10平均"].append(t10.ex.mean()); abs_["前20里回调最深2"].append(a); abs_["前10里回调最深2"].append(b)
    t = lambda d: f"{np.mean(d)*100:+.2f}pp t={np.mean(d)/np.std(d, ddof=1)*np.sqrt(len(d)):+.2f}"  # noqa: E731
    print(f"\n== {label}（{len(groups)} 天）==")
    print("  绝对超额（对全市场）：" + " | ".join(f"{k} {np.mean(v)*100:+.2f}pp" for k, v in abs_.items()))
    for k in rows:
        print(f"  {k}：对前10平均 {t(vs10[k])}　对前20平均 {t(rows[k])}")


_, top = pickle.load(open(ROOT / "experiments/.cache/smart_full_2026-10-09_24m.pkl", "rb"))
report("回放", [online20(x) for _, x in top.groupby("d") if len(online20(x)) >= 10])

conn = sqlite3.connect(f"file:{ROOT/'runtime/quant_data.sqlite'}?mode=ro", uri=True)
live = pd.read_sql_query("SELECT pick_date AS d, symbol, rank FROM picks_history WHERE pool='smart' AND rank<=20", conn)
k = pd.read_sql_query("SELECT symbol, date, open, high, close FROM daily_kline WHERE date >= '2026-03-01'", conn)
conn.close()
k = k[k.open > 0]
op, hi, cl = (k.pivot(index="date", columns="symbol", values=c) for c in ("open", "high", "close"))
days = list(op.index); di = {d: i for i, d in enumerate(days)}
idx = (1 + (cl / cl.shift(1) - 1).clip(-0.3, 0.3).mean(axis=1).fillna(0)).cumprod()
dh = cl / hi.shift(1).rolling(20).max() - 1
look = lambda m, d, s: m.at[d, s] if d in m.index and s in m.columns else np.nan  # noqa: E731


def fwd(d, s, h=5):
    i = di.get(d)
    if i is None or i + 1 + h >= len(days) or s not in op.columns:
        return np.nan
    return op.iat[i + 1 + h, op.columns.get_loc(s)] / op.iat[i + 1, op.columns.get_loc(s)] - 1 - (idx.iat[i + h] / idx.iat[i] - 1)


live["dist_high20"] = [look(dh, d, s) for d, s in zip(live.d, live.symbol)]
live["ex"] = [fwd(d, s) for d, s in zip(live.d, live.symbol)]
live = live.dropna()
report("线上留痕", [x.sort_values("rank") for _, x in live.groupby("d") if len(x) >= 10])
