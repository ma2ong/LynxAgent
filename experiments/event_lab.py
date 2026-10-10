"""只买一两只：事件驱动规则的集中持仓回测（2026-10-10）。

Allen 资金小，只想同时拿 1~2 只，持有一两周到一两个月。前面证明了价量排名的头部是平的，
只买两三只等于抽签。券商金工与学术界在 1~3 个月尺度上反复报告的是「事件」：业绩超预期后的漂移（PEAD）、
业绩预告、机构调研、回购……这里用同一个模拟器检验哪条事件规则在「只持 2 只」时也稳。

模拟器（事先定死）：
- 事件日 R = 公告日当天或之后的第一个交易日（交易所公告多在前一晚或盘前发布，R 当天就有反应）。
  为避开盘中公告的偷看，一律在 R 收盘买入；R 收盘涨停的买不到，跳过。
- 2 个仓位，各一半资金；空出来就从近 5 个交易日出的事件里挑优先级最高的（同一事件只买一次）；持有 N 个交易日后的开盘卖。
  停牌顺延到复牌开盘，退市按最后收盘。双边成本 0.3%。空仓拿现金（0 收益），所以对规则不利。
- 基准：可投资池（非北交所、满 250 根、20 日均额 ≥3000 万）每日等权，满仓。
- 运气有多大：① 同日多个事件时随机挑（300 次），看每年跑赢基准的比例；
  ② 「猴子」：每天从可投资池随机挑股票、同样持有 N 天（300 次）—— 不会选股能拿多少。
- 含 2020 年以来退市股（fetch_delisted.py），2021-01 ~ 2026-09。

判定「稳定跑赢」：随机挑选的 300 次里，每一年都有 ≥70% 的次数跑赢基准，且整体年化超额 > 猴子的 90 分位。

用法：python experiments/event_lab.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "experiments" / ".cache"
DB = ROOT / "runtime" / "quant_data.sqlite"
COST = 0.003
START, END = "2021-01-01", "2026-09-30"
RUNS = 300


def load_prices():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    k = pd.read_sql_query("SELECT symbol, date, open, close, amount FROM daily_kline WHERE date >= '2019-06-01'", conn)
    conn.close()
    dl = pd.read_csv(CACHE / "delisted_kline.csv", dtype={"symbol": str})
    dl = dl[~dl["symbol"].isin(k["symbol"].unique())][k.columns]
    k = pd.concat([k, dl], ignore_index=True).drop_duplicates(["symbol", "date"])
    k = k[~k["symbol"].str.startswith(("8", "4", "92")) & (k["open"] > 0) & (k["close"] > 0)]
    op = k.pivot(index="date", columns="symbol", values="open").sort_index()
    cl = k.pivot(index="date", columns="symbol", values="close").reindex(op.index)
    amt = k.pivot(index="date", columns="symbol", values="amount").reindex(op.index)
    return op, cl, amt


class Market:
    def __init__(self):
        op, cl, amt = load_prices()
        self.days = list(op.index)
        self.di = {d: i for i, d in enumerate(self.days)}
        self.syms = list(op.columns)
        self.si = {s: i for i, s in enumerate(self.syms)}
        self.op, self.cl = op.to_numpy(), cl.to_numpy()
        bars = np.cumsum(~np.isnan(self.cl), axis=0)
        amt20 = amt.rolling(20, min_periods=15).mean().to_numpy()
        self.pool = (bars >= 250) & (amt20 >= 3e7) & ~np.isnan(self.cl)
        prev = np.vstack([np.full(self.cl.shape[1], np.nan), self.cl[:-1]])
        self.dr = self.cl / prev - 1
        limit = np.where([s.startswith(("300", "301", "688", "689")) for s in self.syms], 0.195, 0.095)
        self.limit_up = self.dr >= limit - 0.002
        self.bench = pd.Series(np.nanmean(np.where(np.vstack([self.pool[:1], self.pool[:-1]]), self.dr, np.nan), axis=1),
                               index=self.days).fillna(0)
        self.ret20 = cl.pct_change(20).to_numpy()

    def first_day_on_or_after(self, d: str) -> int | None:
        i = int(np.searchsorted(self.days, d))
        return i if i < len(self.days) else None

    def exit_price(self, j: int, i: int) -> tuple[float, int]:
        """第 i 天开盘卖；停牌顺延，退市按最后收盘。"""
        col = self.op[:, j]
        for t in range(i, min(i + 120, len(self.days))):
            if not np.isnan(col[t]):
                return col[t], t
        last = np.where(~np.isnan(self.cl[:i, j]))[0]
        return self.cl[last[-1], j], int(last[-1])


WINDOW = 5


def simulate(m: Market, ev: pd.DataFrame, hold: int, slots: int = 2, rng: np.random.Generator | None = None):
    """ev: 列 day_i（R 的下标）、j（股票列下标）、prio。返回 (年度收益 Series, 交易明细 DataFrame)。"""
    lo, hi = m.first_day_on_or_after(START), m.first_day_on_or_after(END)
    by_day: dict[int, list] = {}
    for k, (d, j, p) in enumerate(ev[["day_i", "j", "prio"]].itertuples(index=False)):
        p = p if rng is None else rng.random()
        for dd in range(d, d + WINDOW):
            by_day.setdefault(dd, []).append((p, j, k))
    used = set()
    nav = np.ones(slots) / slots
    busy = [None] * slots              # (j, entry_price, exit_day)
    daily = np.zeros(hi - lo)
    trades = []
    for t in range(lo, hi):
        # 先结算到期的（开盘卖）
        for s in range(slots):
            if busy[s] and busy[s][2] == t:
                j, px, _ = busy[s]
                out, _ = m.exit_price(j, t)
                r = min(max(out / px - 1, -0.6), 2.0) - COST
                nav[s] *= 1 + r
                trades.append((m.days[t], m.syms[j], r))
                busy[s] = None
        # 再按今日事件买（收盘买）
        cands = sorted(by_day.get(t, []), reverse=True)
        held = {b[0] for b in busy if b}
        for _, j, k in cands:
            free = [s for s in range(slots) if busy[s] is None]
            if not free:
                break
            if k in used or j in held or np.isnan(m.cl[t, j]) or m.limit_up[t, j] or not m.pool[t, j]:
                continue
            busy[free[0]] = (j, m.cl[t, j], t + hold + 1)
            held.add(j)
            used.add(k)
        daily[t - lo] = nav.sum()
    # 按年：期末仍持仓的按收盘估值（只影响最后一年一点点）
    for s in range(slots):
        if busy[s]:
            j, px, _ = busy[s]
            last = m.cl[hi - 1, j]
            if not np.isnan(last):
                nav[s] *= last / px
    daily[-1] = nav.sum()
    eq = pd.Series(daily, index=m.days[lo:hi])
    return eq, pd.DataFrame(trades, columns=["exit", "symbol", "r"])


def yearly(eq: pd.Series) -> pd.Series:
    y = eq.groupby(eq.index.str[:4]).last()
    start = pd.concat([pd.Series([1.0], index=["0"]), y.iloc[:-1]])
    return pd.Series(y.values / start.values - 1, index=y.index)


def events(m: Market) -> dict[str, pd.DataFrame]:
    """各条规则的事件表（R 日下标、股票下标、优先级）。规则清单看结果前定死。"""
    out = {}

    def to_ev(df: pd.DataFrame, date_col: str, prio_col: str) -> pd.DataFrame:
        df = df[df["symbol"].isin(m.si)].copy()
        df["day_i"] = [m.first_day_on_or_after(str(d)[:10]) for d in df[date_col]]
        df = df.dropna(subset=["day_i"])
        df["day_i"] = df["day_i"].astype(int)
        df["j"] = df["symbol"].map(m.si)
        df["prio"] = df[prio_col]
        return df[["day_i", "j", "prio"]]

    # ① 定期报告：单季度净利润超预期（SUE = 同比增量 / 过去 8 个季度同比增量的标准差）
    rp = pd.read_csv(CACHE / "reports.csv", dtype={"SECURITY_CODE": str})
    rp = rp.rename(columns={"SECURITY_CODE": "symbol"}).sort_values(["symbol", "REPORTDATE"])
    rp["q"] = rp["REPORTDATE"].str[5:7].map({"03": 1, "06": 2, "09": 3, "12": 4})
    rp["y"] = rp["REPORTDATE"].str[:4].astype(int)
    prev_cum = rp.groupby(["symbol", "y"])["PARENT_NETPROFIT"].shift(1)
    rp["sq"] = np.where(rp["q"] == 1, rp["PARENT_NETPROFIT"], rp["PARENT_NETPROFIT"] - prev_cum)
    rp["sq_ly"] = rp.groupby(["symbol", "q"])["sq"].shift(1)
    rp["d"] = rp["sq"] - rp["sq_ly"]
    rp["sd"] = rp.groupby("symbol")["d"].transform(lambda s: s.shift(1).rolling(8, min_periods=4).std())
    rp["sue"] = rp["d"] / rp["sd"]
    good = rp[(rp["sue"] >= 2) & (rp["sq"] > 0) & (rp["YSTZ"] > 0)].dropna(subset=["NOTICE_DATE"])
    out["pead_sue"] = to_ev(good, "NOTICE_DATE", "sue")

    # ② 业绩预告：预增/扭亏、净利润变动 ≥50%（之前过七闸的「强预增」）
    fc = pd.read_csv(CACHE / "forecasts.csv", dtype={"股票代码": str})
    fc = fc[fc["预测指标"] == "归属于上市公司股东的净利润"]
    fc = fc.assign(symbol=fc["股票代码"].str.zfill(6), chg=pd.to_numeric(fc["业绩变动幅度"], errors="coerce"))
    fc = fc[fc["预告类型"].isin(["预增", "扭亏"]) & (fc["chg"] >= 50)].drop_duplicates(["symbol", "公告日期"])
    out["forecast_up"] = to_ev(fc, "公告日期", "chg")

    # ③ 机构调研：参与机构 ≥20 家
    sv = pd.read_csv(CACHE / "survey.csv", dtype={"SECURITY_CODE": str})
    sv = sv.assign(symbol=sv["SECURITY_CODE"].str.zfill(6))
    sv = sv[sv["SUM"] >= 20].drop_duplicates(["symbol", "NOTICE_DATE"])
    out["survey_hot"] = to_ev(sv, "NOTICE_DATE", "SUM")

    # ④ 回购预案：计划金额上限 ≥1 亿
    rb = pd.read_csv(CACHE / "repurchase.csv", dtype=str)
    rb = pd.DataFrame({"symbol": rb.iloc[:, 1].str.zfill(6), "ann": rb.iloc[:, 11],
                       "amt": pd.to_numeric(rb.iloc[:, 10], errors="coerce")})
    # 「回购起始时间」是董事会日，公告在当晚或次日（实测 10-08 起始、10-09 公告）——R 顺延一个交易日，否则是偷看
    bb = to_ev(rb[rb["amt"] >= 1e8].dropna(subset=["ann"]), "ann", "amt")
    out["buyback_big"] = bb.assign(day_i=bb["day_i"] + 1)

    # ⑤⑥ 在 ①② 上加两条市场确认（研报里的「预期差」）：R 日当天涨 ≥2%（市场认可）且之前 20 日没涨超 20%（没被提前炒）
    for base in ("pead_sue", "forecast_up"):
        e = out[base]
        dr = m.dr[e["day_i"].to_numpy(), e["j"].to_numpy()]
        r20 = m.ret20[np.maximum(e["day_i"].to_numpy() - 1, 0), e["j"].to_numpy()]
        out[base + "+confirm"] = e[(dr >= 0.02) & (r20 < 0.20)]
    return out


def report(m: Market, name: str, ev: pd.DataFrame, hold: int, monkey90: float | None) -> dict:
    bench = (1 + m.bench.loc[m.days[m.first_day_on_or_after(START)]:m.days[m.first_day_on_or_after(END) - 1]]).cumprod()
    by = yearly(bench)
    eq, tr = simulate(m, ev, hold)
    yrs = yearly(eq)
    n_years = len(eq) / 244
    cagr = eq.iloc[-1] ** (1 / n_years) - 1
    bcagr = bench.iloc[-1] ** (1 / n_years) - 1
    beat = np.zeros(len(by))
    cagrs = []
    for i in range(RUNS):
        e2, _ = simulate(m, ev, hold, rng=np.random.default_rng(i))
        beat += (yearly(e2).values > by.values)
        cagrs.append(e2.iloc[-1] ** (1 / n_years) - 1 - bcagr)
    cagrs = np.array(cagrs)
    row = {"规则": name, "持有": hold, "交易数": len(tr), "年化": cagr, "基准": bcagr, "超额": cagr - bcagr,
           "随机挑 超额中位": np.median(cagrs), "随机挑 超额10分位": np.percentile(cagrs, 10),
           "单笔胜率": (tr["r"] > 0).mean(), "单笔均值": tr["r"].mean(),
           "逐年(规则-基准)": " ".join(f"{y}:{(a - b) * 100:+.0f}" for y, a, b in zip(yrs.index, yrs.values, by.values)),
           "每年跑赢比例": " ".join(f"{y}:{v / RUNS:.0%}" for y, v in zip(by.index, beat))}
    stable = (beat / RUNS >= 0.7).all() and monkey90 is not None and np.percentile(cagrs, 10) > monkey90
    row["稳定"] = "是" if stable else "否"
    print(f"\n[{name} 持 {hold} 天] 交易 {len(tr)} 笔 | 年化 {cagr:+.1%}（基准 {bcagr:+.1%}）| 随机挑超额 中位 {np.median(cagrs):+.1%} "
          f"10分位 {np.percentile(cagrs, 10):+.1%} | 单笔胜率 {row['单笔胜率']:.0%} 均值 {row['单笔均值']:+.2%}\n"
          f"   逐年超额 {row['逐年(规则-基准)']}\n   随机挑每年跑赢 {row['每年跑赢比例']} → 稳定：{row['稳定']}", flush=True)
    return row


def monkey(m: Market, hold: int) -> float:
    """每天从可投资池随机挑：不会选股、同样只持 2 只能拿多少超额（90 分位）。"""
    lo, hi = m.first_day_on_or_after(START), m.first_day_on_or_after(END)
    rng = np.random.default_rng(99)
    rows = []
    for t in range(lo, hi):
        js = np.where(m.pool[t])[0]
        for j in rng.choice(js, size=min(3, len(js)), replace=False):
            rows.append((t, j, 0.0))
    ev = pd.DataFrame(rows, columns=["day_i", "j", "prio"])
    bench = (1 + m.bench.iloc[lo:hi]).cumprod()
    n_years = (hi - lo) / 244
    bc = bench.iloc[-1] ** (1 / n_years) - 1
    ex = [simulate(m, ev, hold, rng=np.random.default_rng(i))[0].iloc[-1] ** (1 / n_years) - 1 - bc for i in range(RUNS)]
    print(f"猴子（持 {hold} 天）超额：中位 {np.median(ex):+.1%}，90 分位 {np.percentile(ex, 90):+.1%}", flush=True)
    return float(np.percentile(ex, 90))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import pickle
    pk = CACHE / "event_market.pkl"     # 读库+透视要 2 分钟，缓存一份
    m = pickle.load(open(pk, "rb")) if pk.exists() else Market()
    if not pk.exists():
        pickle.dump(m, open(pk, "wb"))
    evs = events(m)
    for k, v in evs.items():
        print(f"{k}: {len(v)} 个事件", flush=True)
    rows = []
    for hold in (10, 20, 40):
        m90 = monkey(m, hold)
        for name, ev in evs.items():
            rows.append(report(m, name, ev, hold, m90))
    pd.DataFrame(rows).to_csv(ROOT / "experiments" / "results" / "event_lab.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
