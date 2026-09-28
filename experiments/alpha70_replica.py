"""PTrade「小市值 Alpha70」策略在本地 5 年数据上的复刻（2026-09-28）。

原策略（alpha70_源码/user_strategy.py）：
- 池：中小板综（深市 002/003），剔除 ST / 停牌 / 退市
- 因子：近 6 日成交额标准差 STD(AMOUNT, 6)，升序（注释写「流通市值最小」，代码实际按这个排）
- 每周一 09:31 换仓，持有最小的 5 只；持仓中涨停的不卖
- 截图成绩：2025-01~2026-09 +62.5%（沪深300 +12.8%），最大回撤 29.7%

问题：这是 2025 年以后微盘股大牛市里的一段。跨 5 年还成立吗？回撤在哪？

复刻口径（尽量贴原策略，做不到的写明）：
- 周一开盘买、下周一开盘卖（原策略 09:31 市价）；双边成本 0.13%（与 rule_audit 同）
- 「持仓涨停不卖」近似为：上周持仓若周五收盘涨停，则本周继续持有
- 停牌：当日 amount=0 的不入选；ST 用当前名称判断（历史 ST 无法还原，偏乐观）
- 基准：同池等权均值（= 随便买这个池的收益），另给全市场等权
- 对照：同样规则但换成全市场（不限中小板）
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "experiments" / ".snapshot.sqlite"
sys.path.insert(0, str(ROOT))
TOP = 5
COST = 0.0013


def load():
    from quantcore.quant.screening import exclusion_reason
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    names = dict(conn.execute("SELECT symbol, name FROM stock_meta"))
    k = pd.read_sql_query(
        "SELECT symbol, date, open, close, amount FROM daily_kline WHERE date >= '2020-09-01'", conn)
    conn.close()
    k = k.astype({"open": "float32", "close": "float32", "amount": "float64"})
    k["st"] = k["symbol"].map(lambda s: bool(exclusion_reason(str(names.get(s) or s), 10.0, 1e9)))
    k = k.sort_values(["symbol", "date"])
    g = k.groupby("symbol", sort=False)
    k["std6"] = g["amount"].transform(lambda s: s.shift(1).rolling(6).std())   # 盘前只能看到上一交易日为止
    k["mean6"] = g["amount"].transform(lambda s: s.shift(1).rolling(6).mean())
    k["prev_close"] = g["close"].shift(1)
    return k


def run(k, universe_mask, label, factor="std6", top=TOP, quiet=False):
    days = sorted(k["date"].unique())
    dts = pd.to_datetime(pd.Series(days))
    # 每周第一个交易日
    mondays = [d for d, prev in zip(days[1:], days[:-1])
               if pd.Timestamp(d).isocalendar().week != pd.Timestamp(prev).isocalendar().week]
    by_day = {d: df.set_index("symbol") for d, df in k[universe_mask].groupby("date")}
    all_by_day = {d: df.set_index("symbol") for d, df in k.groupby("date")}
    rows, hold = [], []
    for d0, d1 in zip(mondays[:-1], mondays[1:]):
        pool = by_day.get(d0)
        a0, a1 = all_by_day.get(d0), all_by_day.get(d1)
        if pool is None or a1 is None:
            continue
        cand = pool[(~pool["st"]) & (pool["amount"] > 0) & pool["std6"].notna() & (pool["std6"] > 0)]
        # 上周持仓里周五涨停（上周最后一个交易日收盘 ≥ 前收×1.095）的继续持有
        keep = []
        if hold:
            prev_day = days[days.index(d0) - 1]
            last = all_by_day.get(prev_day)
            if last is not None:
                keep = [s for s in hold if s in last.index
                        and last.at[s, "close"] >= last.at[s, "prev_close"] * 1.095]
        picks = keep + [s for s in cand.nsmallest(top * 3, factor).index if s not in keep][: top - len(keep)]
        r = [(a1.at[s, "open"] / a0.at[s, "open"] - 1) for s in picks
             if s in a1.index and s in a0.index and a0.at[s, "open"] > 0 and a1.at[s, "open"] > 0]
        if not r:
            continue
        turnover = len(set(picks) - set(hold)) / top
        ret = float(np.mean(r)) - COST * turnover
        both = pool.index.intersection(a1.index)
        base = (a1.loc[both, "open"] / pool.loc[both, "open"] - 1).replace([np.inf, -np.inf], np.nan).dropna()
        both_all = a0.index.intersection(a1.index)
        mkt = (a1.loc[both_all, "open"] / a0.loc[both_all, "open"] - 1).replace([np.inf, -np.inf], np.nan).dropna()
        rows.append({"d": d0, "ret": ret, "pool": base.clip(-0.5, 1).mean(), "mkt": mkt.clip(-0.5, 1).mean(),
                     "amt": float(a0.loc[[p for p in picks if p in a0.index], "amount"].mean())})
        hold = picks
    t = pd.DataFrame(rows)
    t["yr"] = t["d"].str[:4]
    nav = (1 + t["ret"]).cumprod()
    dd = (nav / nav.cummax() - 1).min()
    if quiet:
        return t, (nav.iloc[-1] - 1) * 100, dd * 100
    print(f"\n=== {label}：{t['d'].iloc[0]} ~ {t['d'].iloc[-1]}，{len(t)} 周 ===")
    yr = t.groupby("yr").apply(lambda x: pd.Series({
        "策略": (1 + x["ret"]).prod() - 1, "同池等权": (1 + x["pool"]).prod() - 1,
        "全市场等权": (1 + x["mkt"]).prod() - 1, "周胜率vs池": (x["ret"] > x["pool"]).mean(),
        "持仓日均成交额(万)": x["amt"].mean() / 1e4}), include_groups=False)
    print((yr * [100, 100, 100, 100, 1]).round(1).to_string())
    worst = t.nsmallest(3, "ret")[["d", "ret"]]
    print(f"累计 {(nav.iloc[-1] - 1) * 100:+.1f}%  最大回撤 {dd * 100:.1f}%  "
          f"最差三周 {', '.join(f'{d} {r * 100:+.1f}%' for d, r in worst.values)}")
    return t


def main():
    k = load()
    sme = k["symbol"].str.startswith(("002", "003"))
    run(k, sme, "原策略：中小板综 × 6日成交额标准差最小 5 只 × 周频")
    main_board = ~k["symbol"].str.startswith(("8", "4", "92"))
    run(k, main_board, "对照：全市场（剔除北交所）同规则")
    print("\n=== 稳健性：中小板池，不同只数 / 因子（累计收益%、最大回撤%、逐年超额同池 pp） ===")
    for factor, top in (("std6", 5), ("std6", 10), ("std6", 20), ("mean6", 5), ("mean6", 20)):
        t, cum, dd = run(k, sme, "", factor=factor, top=top, quiet=True)
        ex = t.groupby("yr").apply(lambda x: ((1 + x["ret"]).prod() - (1 + x["pool"]).prod()) * 100, include_groups=False)
        print(f"  {factor} top{top:<3d} 累计 {cum:+7.1f}  回撤 {dd:6.1f}  " + "  ".join(f"{y}:{v:+.0f}" for y, v in ex.items()))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
