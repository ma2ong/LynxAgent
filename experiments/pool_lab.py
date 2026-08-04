"""选股规则裁决台：任意「可交易域 + 排序」组合 → top-N 超额。

与 weight_ab 的分工：weight_ab 只比权重，这里比**规则本身**（硬筛条件、排序依据、
排除项）。两者共用同一条铁律 —— 裁决看 top-N 超额，不看 Rank IC。

口径（2026-08-03 定）：
- 组合侧等权持有 → 收益取**均值**；基准是「随机买 N 只」的期望 → 也取**均值**。
  两边必须同一统计量。个股收益右偏，全市场均值比中位高约 0.8pp/期（T+5），
  拿中位当基准会凭空造出这么多"超额"。`exc_med` 是票级中位口径，两边都用中位，备查。
- 可交易域：非 ST、非涨停收盘（按板块涨跌幅上限判定）、20 日均额 ≥ 5000 万、
  有 250 日以上历史。选股用 d 日收盘数据，收益从 d 日收盘起算。
- 前半 / 后半切分必须同号才算数，t 值只作参考 —— 60 期样本上 t=2 很容易是
  多重检验的产物（教训见 README「龙虎榜方向」一节）。

    from experiments import pool_lab as L
    L.load(); L.build()
    L.judge('我的规则', gate=lambda d: ..., score=lambda d: ...)
"""
from __future__ import annotations

import os
import pickle
import sqlite3

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CACHE_DIR = os.path.join(HERE, ".cache")
DEFAULT_DB = os.path.join(REPO, "runtime", "quant_data.sqlite")

TEST_FROM = "2021-01-04"   # 250 日回看之后（日线自 2020-01 起，见 scripts/backfill_history.py）
SPLIT = "2023-10-01"       # 前 / 后半分界，约为测试区间中点

G: dict = {}


def load(db: str = DEFAULT_DB) -> dict:
    """读日线与股票元信息，铺成 日期×代码 的矩阵。结果缓存到 .cache/。"""
    global G
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, "matrices.pkl")
    if os.path.exists(cache):
        G = pickle.load(open(cache, "rb"))
        return G
    con = sqlite3.connect(db)
    df = pd.read_sql("SELECT symbol,date,open,high,low,close,volume,amount "
                     "FROM daily_kline WHERE amount>0", con)
    meta = pd.read_sql("SELECT symbol,name,list_date FROM stock_meta", con)
    con.close()
    G = {k: df.pivot(index="date", columns="symbol", values=k).sort_index()
         for k in ("open", "high", "low", "close", "volume", "amount")}
    G["dates"] = list(G["close"].index)
    G["meta"] = meta.set_index("symbol")
    pickle.dump(G, open(cache, "wb"))
    return G


def build() -> None:
    """派生因子矩阵。全部只用当日及之前的数据，无前视。"""
    c, h, l, v, a = G["close"], G["high"], G["low"], G["volume"], G["amount"]
    for span in (8, 21, 60):
        G["ema%d" % span] = c.ewm(span=span, adjust=False).mean()
    G["adr"] = (h / l.replace(0, np.nan)).rolling(20).mean().sub(1).mul(100)
    G["dist_low"] = c.div(l.rolling(250, min_periods=120).min()).sub(1).mul(100)
    G["dist_high"] = c.div(h.rolling(250, min_periods=120).max()).sub(1).mul(100)
    for n in (1, 3, 5, 10, 20, 60, 63, 126, 189, 252):
        G["r%d" % n] = c.div(c.shift(n)).sub(1).mul(100)
    # 现行 strength 池的 RS 动量（IBD 式长周期加权）
    G["mom_ibd"] = (0.4 * G["r63"] + 0.2 * G["r126"] + 0.2 * G["r189"] + 0.2 * G["r252"])
    G["ext21"] = c.div(G["ema21"]).sub(1).mul(100)
    G["vr5"] = v.rolling(5).mean().div(v.rolling(60).mean())
    G["amt20"] = a.rolling(20).mean()
    G["vol20"] = G["r1"].rolling(20).std()
    G["dd20"] = c.div(c.rolling(20).max()).sub(1).mul(100)

    # 可交易域
    lim = pd.Series(0.10, index=c.columns)
    lim[[s for s in c.columns if s[:3] in ("300", "301", "688")]] = 0.20
    lim[[s for s in c.columns if s[:2] in ("83", "87", "92", "43")]] = 0.30
    G["not_limit"] = c.div(c.shift(1)).sub(1).lt(lim - 0.005, axis=1)
    names = G["meta"]["name"].reindex(c.columns).fillna("")
    G["not_st"] = pd.Series(~names.str.contains("ST"), index=c.columns)
    G["liquid"] = G["amt20"] > 5e7
    G["alive"] = c.notna() & G["dist_low"].notna()
    G["fwd"] = {hh: c.shift(-hh).div(c).sub(1).mul(100) for hh in (2, 5, 10)}


def base_mask(d):
    """所有规则共用的可交易底线。"""
    return G["not_limit"].loc[d] & G["not_st"] & G["liquid"].loc[d] & G["alive"].loc[d]


def judge(name, gate, score, n=20, step=5, horizons=(2, 5), quiet=False) -> pd.DataFrame:
    """gate(d) -> bool Series；score(d) -> float Series（越大越先选）。返回逐期超额。"""
    dates = G["dates"]
    rows = []
    idx = [i for i, d in enumerate(dates) if d >= TEST_FROM and i + max(horizons) < len(dates)]
    for i in idx[::step]:
        d = dates[i]
        s = score(d)[gate(d) & base_mask(d)].dropna()
        if len(s) < n:
            continue
        top = s.nlargest(n).index
        for hh in horizons:
            f = G["fwd"][hh].loc[d]
            uni = f[base_mask(d) & f.notna()]
            sub = f[top].dropna()
            if len(sub) < n * 0.6 or len(uni) < 100:
                continue
            rows.append({"date": d, "h": hh,
                         "exc": sub.mean() - uni.mean(),
                         "exc_med": sub.median() - uni.median()})
    r = pd.DataFrame(rows)
    if r.empty:
        print(f"{name}: 无有效期数")
        return r
    if not quiet:
        for hh in horizons:
            s = r[r.h == hh]
            t = s.exc.mean() / (s.exc.std(ddof=1) / len(s) ** 0.5) if s.exc.std(ddof=1) > 0 else 0
            print(f"  {name:24s} T+{hh} 期数{len(s):3d} 超额{s.exc.mean():+6.2f}pp t={t:+5.2f} "
                  f"跑赢{100*(s.exc>0).mean():3.0f}% | 前半{s[s.date<SPLIT].exc.mean():+5.2f} "
                  f"后半{s[s.date>=SPLIT].exc.mean():+5.2f}")
    return r


def paired(base: pd.DataFrame, variant: pd.DataFrame, label: str, horizons=(2, 5)) -> None:
    """同期配对：把市场环境的共同波动消掉，只留规则差异。"""
    for hh in horizons:
        p = base[base.h == hh].set_index("date").exc
        q = variant[variant.h == hh].set_index("date").exc
        d = (q - p).dropna()
        if d.empty or d.std(ddof=1) == 0:
            continue
        t = d.mean() / (d.std(ddof=1) / len(d) ** 0.5)
        print(f"  {label:24s} T+{hh}: {d.mean():+.3f}pp t_paired={t:+.2f} | "
              f"前半{d[d.index<SPLIT].mean():+.2f} 后半{d[d.index>=SPLIT].mean():+.2f}")
