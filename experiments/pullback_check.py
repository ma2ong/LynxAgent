"""「线上前 20 里回调最深 2 只」复核（2026-10-10）：剂量反应 + 线上留痕 + 更长持有期。

smart_full_lab 的 11 种挑法里只有它 t>2（+2.02pp，t 2.25），11 选 1 必须复核：
① 回放：线上前 20 按离 20 日高点的距离分 4 档，看是否单调；
② 线上留痕（picks_history，07-14 起，与回放不重叠的真实名单）：同一挑法 T+5/T+10/T+20。
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

_, top = pickle.load(open(ROOT / "experiments/.cache/smart_full_2026-10-09_24m.pkl", "rb"))
rows = []
for d, x in top.groupby("d"):
    o = online20(x).copy()
    o["q"] = pd.qcut(o.dist_high20.rank(method="first"), 4, labels=["回调最深", "较深", "较浅", "贴近高点"])
    rows.append(o)
o = pd.concat(rows)
g = o.groupby(["d", "q"], observed=True).ex.mean().unstack()
print("① 回放 线上前20 按离20日高点分4档（每档5只），T+5 超额 pp：")
print("   " + "  ".join(f"{c} {g[c].mean()*100:+.2f}(t={g[c].mean()/g[c].std()*np.sqrt(len(g)):+.1f})" for c in g.columns))

conn = sqlite3.connect(f"file:{ROOT/'runtime/quant_data.sqlite'}?mode=ro", uri=True)
live = pd.read_sql_query("SELECT pick_date AS d, symbol, rank FROM picks_history WHERE pool='smart' AND rank<=20", conn)
k = pd.read_sql_query("SELECT symbol, date, open, high, close FROM daily_kline WHERE date >= '2026-03-01'", conn)
conn.close()
k = k[k.open > 0]
op, hi, cl = (k.pivot(index="date", columns="symbol", values=c) for c in ("open", "high", "close"))
days = list(op.index); di = {d: i for i, d in enumerate(days)}
idx = (1 + (cl / cl.shift(1) - 1).clip(-0.3, 0.3).mean(axis=1).fillna(0)).cumprod()
dh = cl / hi.shift(1).rolling(20).max() - 1
live["dh"] = [dh.at[d, s] if d in dh.index and s in dh.columns else np.nan for d, s in zip(live.d, live.symbol)]


def fwd(d, s, h):
    i = di.get(d)
    if i is None or i + 1 + h >= len(days) or s not in op.columns:
        return np.nan
    b, e = op.iat[i + 1, op.columns.get_loc(s)], op.iat[i + 1 + h, op.columns.get_loc(s)]
    return e / b - 1 - (idx.iat[i + h] / idx.iat[i] - 1)


print(f"\n② 线上留痕 {live.d.min()} ~ {live.d.max()}（{live.d.nunique()} 天，与回放不同的真实名单）：")
for h in (5, 10, 20):
    out = {}
    for name, fn in (("前20平均", lambda x: x), ("回调最深2", lambda x: x.nsmallest(2, "dh")),
                     ("贴近高点2", lambda x: x.nlargest(2, "dh"))):
        per, hits = [], []
        for d, x in live.dropna(subset=["dh"]).groupby("d"):
            r = [v for v in (fwd(d, s, h) for s in fn(x).symbol) if not np.isnan(v)]
            if r:
                per.append(np.mean(r)); hits += [v > 0 for v in r]
        p = np.array(per)
        out[name] = f"{p.mean()*100:+.2f}pp 胜率{np.mean(hits):.0%} t={p.mean()/p.std()*np.sqrt(len(p)):+.1f}"
    print(f"   T+{h:<2} " + " | ".join(f"{n} {v}" for n, v in out.items()))
