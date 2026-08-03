"""复现 2026-08-03 那轮的两个决定性结论。

    python experiments/pool_rules_ab.py

① strength 池的四条硬筛全部非约束，规则实际只剩「买动量最极端的 20 只」，无 alpha。
② 「排除近 10 日上过龙虎榜」看似有效（配对 t=2.0），但本地精确复刻交易所口径后效果
   归零 —— 判定为多重检验产物，不接这个数据源。

②需要龙虎榜数据（akshare `stock_lhb_detail_em`）。没装 akshare 或拉不到就只跑①。
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pool_lab as L


def strength_ablation():
    g = L.G
    everything = lambda d: pd.Series(True, index=g["close"].columns)
    dist = lambda d: g["dist_low"].loc[d] >= 70
    adr = lambda d: g["adr"].loc[d] >= 4.5
    ema = lambda d: (g["close"].loc[d] > g["ema8"].loc[d]) & (g["close"].loc[d] > g["ema21"].loc[d])
    mom = lambda d: g["mom_ibd"].loc[d]

    print("\n=== ① strength 规则消融（排序均为 IBD 动量）===")
    L.judge("现行完整规则", lambda d: dist(d) & adr(d) & ema(d), mom)
    L.judge("去掉 距低点+70%", lambda d: adr(d) & ema(d), mom)
    L.judge("去掉 ADR>=4.5", lambda d: dist(d) & ema(d), mom)
    L.judge("去掉 站上EMA", lambda d: dist(d) & adr(d), mom)
    L.judge("完全不筛(全市场)", everything, mom)
    print("  ↑ 前两行逐位相同：按动量排出的 top-20 天然满足『距低点+70%』，该条从未淘汰过任何票。")


def lhb_replication():
    """外部数据源接入前的必答题：本地能不能复刻出同样的效果。"""
    g = L.G
    dates, cols = g["dates"], g["close"].columns
    try:
        import akshare as ak
    except ImportError:
        print("\n=== ② 跳过：未安装 akshare ===")
        return
    cache = os.path.join(L.CACHE_DIR, "lhb.pkl")
    if os.path.exists(cache):
        raw = pd.read_pickle(cache)
    else:
        frames = []
        for y in (2025, 2026):
            for m in range(1, 13):
                if (y, m) < (2025, 4) or (y, m) > (2026, 7):
                    continue
                days = pd.Period(f"{y}-{m:02d}").days_in_month
                try:
                    frames.append(ak.stock_lhb_detail_em(
                        start_date=f"{y}{m:02d}01", end_date=f"{y}{m:02d}{days}"))
                except (OSError, ValueError, KeyError) as ex:
                    print(f"  {y}-{m:02d} 拉取失败 {type(ex).__name__}: {ex}")
        if not frames:
            print("\n=== ② 跳过：龙虎榜一条都没拉到 ===")
            return
        raw = pd.concat(frames, ignore_index=True)
        raw.to_pickle(cache)

    raw = raw.assign(date=pd.to_datetime(raw["上榜日"]).dt.strftime("%Y-%m-%d"),
                     sym=raw["代码"].astype(str).str.zfill(6))
    raw = raw[raw.sym.isin(set(cols))]
    # 榜单收盘后公布，d 日选股只能用 d-1 及更早 → shift(1)
    on = (raw.assign(v=1).pivot_table(index="date", columns="sym", values="v", aggfunc="max")
          .reindex(index=dates, columns=cols).fillna(0).shift(1).fillna(0))
    lhb10 = on.rolling(10, min_periods=1).max() > 0

    # 本地复刻交易所口径：涨/跌幅偏离前5、振幅前5、三日累计偏离20%
    c, h, l = g["close"], g["high"], g["low"]
    dev = g["r1"].sub(g["r1"].median(axis=1), axis=0)
    amp = (h - l).div(c.shift(1)).mul(100)
    topk = lambda df, k, asc=False: (df.rank(axis=1, ascending=asc, method="first") <= k) & df.notna()
    hit = topk(dev, 5) | topk(dev, 5, asc=True) | topk(amp, 5) | (dev.rolling(3).sum().abs() >= 20)
    abn10 = hit.rolling(10, min_periods=1).max().astype(bool).shift(1).fillna(False)

    w = dates[-300:]
    inter = (abn10 & lhb10).loc[w].sum().sum()
    print(f"\n=== ② 龙虎榜 vs 本地复刻（真榜每日 {int(lhb10.loc[w].sum(axis=1).mean())} 只，"
          f"本地 {int(abn10.loc[w].sum(axis=1).mean())} 只，重合 "
          f"{100*inter/max(1, lhb10.loc[w].sum().sum()):.0f}%）===")

    comp = composite()
    if comp is None:
        return
    g["composite"] = comp
    everything = lambda d: pd.Series(True, index=cols)
    score = lambda d: g["composite"].loc[d]
    base = L.judge("composite 原样", everything, score)
    ext = L.judge("排除真龙虎榜(需外部)", lambda d: ~lhb10.loc[d], score)
    loc = L.judge("排除本地异动(零依赖)", lambda d: ~abn10.loc[d], score)
    print("\n  配对 vs 原样：")
    L.paired(base, ext, "真龙虎榜")
    L.paired(base, loc, "本地复刻")
    print("  ↑ 复刻下效果归零 → 判定为多重检验产物，不接这个数据源。")


def composite():
    """线上 smart 池的 composite 分，整段向量化（等价性由 test_factor_vectorization 钉死）。"""
    import pickle
    import sqlite3
    cache = os.path.join(L.CACHE_DIR, "composite.pkl")
    if os.path.exists(cache):
        comp = pickle.load(open(cache, "rb"))
    else:
        from factor_scores import factor_frame
        con = sqlite3.connect(L.DEFAULT_DB)
        out = {}
        for sym in [r[0] for r in con.execute("SELECT DISTINCT symbol FROM daily_kline")]:
            df = pd.read_sql("SELECT date,open,high,low,close,volume,amount FROM daily_kline "
                             "WHERE symbol=? AND amount>0 ORDER BY date", con, params=(sym,))
            if len(df) < 120:
                continue
            out[sym] = pd.Series(factor_frame(df)["composite"].to_numpy(),
                                 index=df["date"].astype(str))
        con.close()
        comp = pd.DataFrame(out).sort_index()
        pickle.dump(comp, open(cache, "wb"))
    return comp.reindex(index=L.G["close"].index, columns=L.G["close"].columns)


if __name__ == "__main__":
    L.load()
    L.build()
    strength_ablation()
    lhb_replication()
