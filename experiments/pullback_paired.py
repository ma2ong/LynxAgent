"""「回调最深 2 只」等候选与线上前 20 平均的配对差：回放 141 期 + 线上留痕（2026-10-10）。"""
import sys, pickle, sqlite3; sys.stdout.reconfigure(encoding='utf-8'); sys.path.insert(0,'experiments')
import numpy as np, pandas as pd
from smart_full_lab import online20
_, top = pickle.load(open('experiments/.cache/smart_full_2026-10-09_24m.pkl','rb'))
C = {"回调最深2": lambda x: x.nsmallest(2, "dist_high20"),
     "不贴高点（去掉离高点最近的一半）": lambda x: x.nsmallest(len(x)//2, "dist_high20").pipe(lambda y: y.assign(a=0)),
     "当日涨≤5%": lambda x: x[x.ret_1d <= 0.05],
     "共振且不追高(20日涨<15%)": lambda x: x[x.ret20 < 0.15]}
print("回放 141 期：对「线上前20平均」的配对差（T+5，pp）")
for n, fn in C.items():
    d = []
    for _, x in top.groupby("d"):
        o = online20(x); s = fn(o)
        if len(s): d.append(s.ex.mean() - o.ex.mean())
    d = np.array(d); print(f"  {n:28s} {d.mean()*100:+.2f}pp t={d.mean()/d.std()*np.sqrt(len(d)):+.2f} 好过的期数 {(d>0).mean():.0%}")
# 线上
conn = sqlite3.connect('file:runtime/quant_data.sqlite?mode=ro', uri=True)
live = pd.read_sql_query("SELECT pick_date AS d, symbol FROM picks_history WHERE pool='smart' AND rank<=20", conn)
k = pd.read_sql_query("SELECT symbol, date, open, high, close FROM daily_kline WHERE date >= '2026-03-01'", conn); conn.close()
k = k[k.open > 0]; op, hi, cl = (k.pivot(index='date', columns='symbol', values=c) for c in ('open','high','close'))
days = list(op.index); di = {d: i for i, d in enumerate(days)}
dh = cl / hi.shift(1).rolling(20).max() - 1; r1 = cl / cl.shift(1) - 1; r20 = cl / cl.shift(20) - 1
look = lambda m, d, s: m.at[d, s] if d in m.index and s in m.columns else np.nan
live['dist_high20'] = [look(dh,d,s) for d,s in zip(live.d, live.symbol)]; live['ret_1d'] = [look(r1,d,s) for d,s in zip(live.d, live.symbol)]; live['ret20'] = [look(r20,d,s) for d,s in zip(live.d, live.symbol)]
def fwd(d, s, h=5):
    i = di.get(d)
    if i is None or i+1+h >= len(days) or s not in op.columns: return np.nan
    return op.iat[i+1+h, op.columns.get_loc(s)] / op.iat[i+1, op.columns.get_loc(s)] - 1
live['ex'] = [fwd(d, s) for d, s in zip(live.d, live.symbol)]
live = live.dropna()
print(f"线上留痕 {live.d.nunique()} 天：对「线上前20平均」的配对差（T+5，pp）")
for n, fn in C.items():
    d = []
    for _, x in live.groupby("d"):
        s = fn(x)
        if len(s): d.append(s.ex.mean() - x.ex.mean())
    d = np.array(d); print(f"  {n:28s} {d.mean()*100:+.2f}pp t={d.mean()/d.std()*np.sqrt(len(d)):+.2f} 好过的天数 {(d>0).mean():.0%}")
