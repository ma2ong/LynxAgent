"""一键智选里只挑一两只：换「挑法」能不能比智选平均更准（2026-10-10）。

线上留痕显示综合分第 1~2 名最差（刚拉完、贴高点）。Allen 要求不改智选本身，另做小板块推 1~2 只，
挑法不一定取最高分。这里在智选前 20 名里按六种挑法各取 2 只（看结果前定死）：
  最不追高  近 5 日涨幅最小        回踩均线  60 日趋势向上里离 20 日线最近
  模型分    多因子模型分最高        板块最强  所在行业 20 日动量最高（唯一过七闸的价量规则）
  模型+板块 两者排名之和最好        低波动    20 日波动最小
信号日 d 收盘后（线上是 d 盘中）→ d+1 开盘买，T+H 开盘卖，对全市场等权；t 按日聚类（每天 2 只取均值）。
两份样本：历史回放（run 内 52 天）与线上留痕（07-14 起）。两份都显著且同向才算。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "runtime" / "quant_data.sqlite"
H = (1, 5, 10, 20)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    run = conn.execute("SELECT run_id FROM replay_runs WHERE status='done' ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    rep = pd.read_sql_query("SELECT as_of AS d, symbol, rank FROM replay_results WHERE pool='smart' AND run_id=? AND rank<=20",
                            conn, params=(run,))
    live = pd.read_sql_query("SELECT pick_date AS d, symbol, rank FROM picks_history WHERE pool='smart' AND rank<=20", conn)
    k = pd.read_sql_query("SELECT symbol, date, open, close FROM daily_kline WHERE date >= '2024-06-01'", conn)
    conn.close()
    k = k[k.open > 0].sort_values(["symbol", "date"])
    op = k.pivot(index="date", columns="symbol", values="open")
    cl = k.pivot(index="date", columns="symbol", values="close")
    days = list(op.index)
    di = {d: i for i, d in enumerate(days)}
    dr = cl / cl.shift(1) - 1
    idx = (1 + dr.clip(-0.3, 0.3).mean(axis=1).fillna(0)).cumprod()
    ret5, vol20 = cl / cl.shift(5) - 1, dr.rolling(20).std()
    dma20, dma60 = cl / cl.rolling(20).mean() - 1, cl / cl.rolling(60).mean() - 1
    ind = json.load(open(ROOT / "runtime" / "industry_map.json", encoding="utf-8"))
    r20 = cl / cl.shift(20) - 1
    ind_s = pd.Series(ind).reindex(r20.columns)
    ind_mom = r20.T.groupby(ind_s).transform("mean").T          # 每只票所在行业的 20 日平均涨幅
    ml = pd.read_pickle(ROOT / "experiments" / ".cache" / "ml_preds_noev_drop-lprice.pkl")
    ml["mlp"] = ml.groupby("date")["score"].rank(pct=True)
    mlp = ml.pivot(index="date", columns="symbol", values="mlp")

    def feat(df):
        out = df.copy()
        for name, mat in (("ret5", ret5), ("vol20", vol20), ("dma20", dma20), ("dma60", dma60), ("ind", ind_mom), ("ml", mlp)):
            out[name] = [mat.at[d, s] if d in mat.index and s in mat.columns else np.nan for d, s in zip(df.d, df.symbol)]
        return out

    rules = {
        "智选前20平均": lambda x: x,
        "综合分第1-2名": lambda x: x.nsmallest(2, "rank"),
        "最不追高": lambda x: x.nsmallest(2, "ret5"),
        "回踩均线": lambda x: x[x.dma60 > 0].assign(a=lambda y: y.dma20.abs()).nsmallest(2, "a"),
        "模型分": lambda x: x.dropna(subset=["ml"]).nlargest(2, "ml"),
        "板块最强": lambda x: x.nlargest(2, "ind"),
        "模型+板块": lambda x: x.dropna(subset=["ml"]).assign(a=lambda y: y.ml.rank() + y.ind.rank()).nlargest(2, "a"),
        "低波动": lambda x: x.nsmallest(2, "vol20"),
    }

    def fwd(d, s, h):
        i = di.get(d)
        if i is None or i + 1 + h >= len(days) or s not in op.columns:
            return np.nan
        b, e = op.iat[i + 1, op.columns.get_loc(s)], op.iat[i + 1 + h, op.columns.get_loc(s)]
        return e / b - 1 - (idx.iat[i + h] / idx.iat[i] - 1)

    for label, df in (("历史回放", rep), ("线上留痕", live)):
        df = feat(df)
        print(f"\n== {label}：{df.d.min()} ~ {df.d.max()}，{df.d.nunique()} 天 ==")
        print("挑法".ljust(10) + "".join(f"  T+{h}: 超额/胜率/t".ljust(24) for h in H))
        for name, fn in rules.items():
            line = name.ljust(10)
            for h in H:
                per_day, hits = [], []
                for d, x in df.groupby("d"):
                    sel = fn(x)
                    r = [fwd(d, s, h) for s in sel.symbol]
                    r = [v for v in r if not np.isnan(v)]
                    if r:
                        per_day.append(np.mean(r)); hits += [v > 0 for v in r]
                p = np.array(per_day)
                t = p.mean() / p.std() * np.sqrt(len(p)) if len(p) > 2 and p.std() > 0 else np.nan
                line += f"  {p.mean()*100:+6.2f} {np.mean(hits):4.0%} {t:+5.2f}".ljust(24) if len(p) else "  -".ljust(24)
            print(line)


if __name__ == "__main__":
    main()
