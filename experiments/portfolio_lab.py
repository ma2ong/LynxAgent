"""组合层面的超额：小市值 / 短期反转 / 多因子合成（2026-10-09）。

问题：「挑 20 只短线必涨股」已经试遍了。量化公司的超额来自「一篮子票 + 定期换仓」上
的稳定倾斜。我们用同样的口径，看 A 股教科书级的三类异象在本地数据上还剩多少：
小市值（低成交额代理）、短期反转（近 5/20 日跌得多）、两者合成。

口径（事先定死，看结果后不改）：
- 每周第一个交易日开盘买、下周第一个交易日开盘卖；持仓 N 只等权
- 信号只用买入日之前的收盘数据（shift 1）
- 可投资池：剔除北交所；上市满 250 根日线；前 20 日日均成交额 ≥ 3000 万（买得进卖得出）；
  当日有成交；开盘没封涨停（买不进）
- 成本：按换手扣双边 0.3%
- 基准：同一可投资池等权（= 随便买能得多少）
- 存活偏差：本地库只有现存股票。--delisted 时把 2020 年以来退市股票的日线并进来
  （experiments/.cache/delisted_kline.csv，由 fetch_delisted.py 生成），退市前最后一个收盘价作卖出价

用法：python experiments/fetch_delisted.py && python experiments/portfolio_lab.py --delisted [--top 50] [--min-amt 3e7]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "experiments" / ".snapshot.sqlite"
DELISTED = ROOT / "experiments" / ".cache" / "delisted_kline.csv"
COST = 0.003
MIN_AMT = 3e7
START = "2020-09-01"


def load(with_delisted: bool) -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    k = pd.read_sql_query(
        "SELECT symbol, date, open, high, low, close, amount FROM daily_kline WHERE date >= '2019-06-01'", conn)
    conn.close()
    if with_delisted and DELISTED.exists():
        d = pd.read_csv(DELISTED, dtype={"symbol": str})
        d = d[~d["symbol"].isin(k["symbol"].unique())].drop_duplicates(["symbol", "date"])
        k = pd.concat([k, d[k.columns]], ignore_index=True)
    k = k[~k["symbol"].str.startswith(("8", "4", "92"))]
    k = k[(k["close"] > 0) & (k["open"] > 0)]
    k = k.astype({c: "float32" for c in ("open", "high", "low", "close")} | {"amount": "float64"})
    k = k.sort_values(["symbol", "date"]).reset_index(drop=True)
    g = k.groupby("symbol", sort=False)
    c = g["close"]
    k["prev_close"] = c.shift(1)
    dr = k["close"] / k["prev_close"] - 1
    k["dr"] = dr
    # 以下全部是「截至买入日前一收盘」可见的量（shift 1）
    k["bars"] = g.cumcount()
    k["ret5"] = (c.shift(1) / c.shift(6) - 1)
    k["ret20"] = (c.shift(1) / c.shift(21) - 1)
    k["ret60"] = (c.shift(1) / c.shift(61) - 1)
    k["amt20"] = g["amount"].transform(lambda s: s.shift(1).rolling(20, min_periods=15).mean())
    k["std6"] = g["amount"].transform(lambda s: s.shift(1).rolling(6).std())
    k["vol20"] = k.groupby("symbol", sort=False)["dr"].transform(lambda s: s.shift(1).rolling(20, min_periods=15).std())
    k["max20"] = k.groupby("symbol", sort=False)["dr"].transform(lambda s: s.shift(1).rolling(20, min_periods=15).max())
    k["ma250"] = c.transform(lambda s: s.shift(1).rolling(250, min_periods=200).mean())
    k["above250"] = k["prev_close"] / k["ma250"] - 1
    # 涨跌幅限制：创业板/科创板 20%，其余 10%；历史 ST(5%) 无法还原，按 10% 判开盘封板会漏判一部分
    lim = np.where(k["symbol"].str.startswith(("300", "301", "688", "689")), 0.2, 0.1)
    k["open_limit_up"] = (k["open"] / k["prev_close"] - 1) >= (lim - 0.003)
    return k[k["date"] >= "2020-06-01"]


def _rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True)


# 每个策略：给定当日可投资池，返回按「越该买越靠前」排好的代码
STRATS = {
    "动量前N（=现在智选的取向）": lambda p: p.nlargest(TOP, "ret20").index,
    "反转5日：近5日跌最多": lambda p: p.nsmallest(TOP, "ret5").index,
    "反转20日：近20日跌最多": lambda p: p.nsmallest(TOP, "ret20").index,
    "小市值代理：20日成交额最小": lambda p: p.nsmallest(TOP, "amt20").index,
    "alpha70：6日成交额波动最小": lambda p: p.nsmallest(TOP, "std6").index,
    "低波：20日波动最小": lambda p: p.nsmallest(TOP, "vol20").index,
    "合成：反转20+小市值+非彩票": lambda p: (
        _rank(-p["ret20"]) + _rank(-p["amt20"]) + _rank(-p["max20"])).nlargest(TOP).index,
    "合成+长期趋势在年线上": lambda p: (
        _rank(-p["ret20"]) + _rank(-p["amt20"]) + _rank(-p["max20"]))[p["above250"] > 0].nlargest(TOP).index,
}
TOP = 50


def run(k: pd.DataFrame):
    days = sorted(k["date"].unique())
    weeks = [d for d, prev in zip(days[1:], days[:-1])
             if pd.Timestamp(d).isocalendar().week != pd.Timestamp(prev).isocalendar().week and d >= START]
    by_day = {d: df.set_index("symbol") for d, df in k[k["date"].isin(set(weeks))].groupby("date")}
    # 退市股：持有期内没有下周开盘价，用持有期内最后一个收盘价卖
    last_close = k.groupby("symbol")[["date", "close"]].last()
    rows, holds = [], {name: set() for name in STRATS}
    for d0, d1 in zip(weeks[:-1], weeks[1:]):
        a0, a1 = by_day[d0], by_day[d1]
        pool = a0[(a0["bars"] >= 250) & (a0["amt20"] >= MIN_AMT) & (a0["amount"] > 0)
                  & ~a0["open_limit_up"] & a0[["ret5", "ret20", "amt20", "std6", "vol20", "max20"]].notna().all(axis=1)]

        def exit_px(syms):
            px = a1["open"].reindex(syms)
            gone = px.isna()
            if gone.any():
                lc = last_close.reindex(px.index[gone])
                # 只有在 d0~d1 之间最后交易的才算退市卖出；否则是停牌，按 d0 开盘价视为 0 收益
                px[gone] = np.where((lc["date"] >= d0) & (lc["date"] < d1), lc["close"], a0["open"].reindex(px.index[gone]))
            return px
        base = (exit_px(pool.index) / pool["open"] - 1).clip(-0.6, 1.5)
        row = {"d": d0, "基准": base.mean(), "n_pool": len(pool)}
        for name, fn in STRATS.items():
            picks = list(fn(pool))
            r = (exit_px(picks) / pool.loc[picks, "open"] - 1).clip(-0.6, 1.5)
            turnover = len(set(picks) - holds[name]) / max(len(picks), 1)
            row[name] = r.mean() - COST * turnover
            holds[name] = set(picks)
        rows.append(row)
    return pd.DataFrame(rows)


def report(t: pd.DataFrame):
    t = t[t["n_pool"] > 0].copy()   # 库从 2020-01 起，满 250 根日线要到 2021 年初
    t["yr"] = t["d"].str[:4]
    print(f"{t['d'].iloc[0]} ~ {t['d'].iloc[-1]}，{len(t)} 周，可投资池均 {t['n_pool'].mean():.0f} 只，持仓 {TOP} 只，扣双边 {COST:.1%}")
    names = ["基准"] + list(STRATS)
    out = []
    for n in names:
        nav = (1 + t[n]).cumprod()
        ex = t[n] - t["基准"]
        yrs = t.groupby("yr").apply(lambda x: ((1 + x[n]).prod() - (1 + x["基准"]).prod()) * 100, include_groups=False)
        out.append({
            "策略": n,
            "年化%": (nav.iloc[-1] ** (52 / len(t)) - 1) * 100,
            "最大回撤%": (nav / nav.cummax() - 1).min() * 100,
            "周超额均值pp": ex.mean() * 100,
            "t": ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex))) if n != "基准" else np.nan,
            "周胜率": (ex > 0).mean() * 100,
            "跑赢年数": f"{(yrs > 0).sum()}/{len(yrs)}" if n != "基准" else "",
            **{f"{y}超额": v for y, v in yrs.items()},
        })
    df = pd.DataFrame(out).set_index("策略")
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(df.round(2).to_string())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--delisted", action="store_true")
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--min-amt", type=float, default=3e7, help="流动性门槛：前 20 日日均成交额（元）")
    a = ap.parse_args()
    TOP, MIN_AMT = a.top, a.min_amt
    k = load(a.delisted)
    t = run(k)
    tag = "with-delisted" if a.delisted else "survivors"
    t.to_csv(ROOT / "experiments" / "results" / f"portfolio_lab_{tag}_top{TOP}_amt{int(MIN_AMT / 1e6)}m.csv", index=False)
    print(f"=== {'含退市股' if a.delisted else '仅现存股（有存活偏差）'} ===")
    report(t)
