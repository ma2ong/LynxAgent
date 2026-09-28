"""结构分最近两个月为什么失效：某个因子坏了，还是追涨风格整体不灵（2026-09-28）。

起因
----
9/24 实测：纯结构前 20 在 8/4~9/16 样本外 T+20 −5.8pp，线上 smart_structure T+10 −2.88。
去掉盘中追涨只是止血，底座本身在这段行情里失效。本脚本回答一个事先定好的问题：

  失效是**单个因子**的问题（去掉它就恢复、只有它的因子收益翻负、或它的分布出了技术故障），
  还是**整类风格**的问题（动量/趋势/贴高点这些同向因子在全市场一起翻负）？

做法（全部在同一批观测上，逐日、全候选）
----------------------------------------
1. 技术体检：各因子逐月均值 / 饱和率（=0 或 =100 的比例）/ 缺失——排除「算错了」。
2. 单因子 Rank IC 与 单因子前 20 超额，旧窗 vs 新窗。
3. 留一法：去掉某因子（其余按原权重重归一）后的前 20 超额。某因子坏了 ⇒ 去掉它新窗明显回血。
4. 归因：前 20 的因子暴露（横截面 z）× 各因子的横截面收益（逐日回归斜率），拆出
   「新窗比旧窗少赚的那几个点」来自哪些因子。
5. 风格对照：与结构分无关的纯风格代理（20 日涨幅、5 日涨幅、距 60 日高点、波动率）在
   全市场的 IC 与五分位价差。它们一起翻负 ⇒ 是行情，不是哪个因子坏了。

口径
----
- 候选：当日成交额 ≥3000 万、历史 ≥80 根、exclusion_reason 过滤（与 replay smart 分支同）
- 超额：个股收益 − 当日候选全体均值（组合口径）；入场 close = T 日收盘，open = T+1 开盘
- industry_heat：生产用板块阶段分（mom20 主导）+ 当日主题；这里用「行业 20 日平均涨幅的
  全行业分位」近似，并同时给出按中性 50 计的版本（= 回放口径）
- t 统计：只取间隔 ≥H 的不重叠会话算，避免重叠窗口把 t 吹大

用法
----
    python experiments/snapshot_db.py
    python experiments/structure_decay.py --long         # 2021 起 5 年：年度表现 + 延续性是否跨年成立
    python experiments/structure_decay.py                # 旧窗 2025-09-01~2026-06-30，新窗 2026-07-01 起
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "experiments" / ".snapshot.sqlite"
for _p in (str(ROOT), str(ROOT / "experiments")):   # 模块级：spawn 出的子进程也要能 import
    if _p not in sys.path:
        sys.path.insert(0, _p)
LOAD_SINCE = "2025-03-01"
# 新窗起点取 7/1：逐月结果显示回撤从 7 月开始（9/24 按 8/4 切是沿用样本外起点，偏晚）。
# 这是看过数据后定的切点，所以本脚本只做诊断归因，不做显著性裁决。
OLD = ("2025-09-01", "2026-06-30")
NEW = ("2026-07-01", "2099-01-01")
TOP = 20
HS = (1, 5, 10, 20)
FACTORS = ["trend", "momentum", "rsi", "risk_control", "liquidity", "macd", "bollinger",
           "capital_flow", "industry_heat"]
STYLE = ["ret20", "ret5", "ret1", "dist_high60", "vol20"]


def _worker(payload):
    from factor_scores import factor_frame
    from quantcore.quant.data import normalize_ohlcv
    from quantcore.quant.screening import exclusion_reason
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out = []
    for sym, name in payload["syms"]:
        raw = pd.read_sql_query(
            "SELECT date, open, high, low, close, volume, amount FROM daily_kline "
            "WHERE symbol=? AND date>=? AND amount>0 ORDER BY date", conn, params=(sym, payload["since"]))
        if len(raw) < 100:
            continue
        try:
            nd = normalize_ohlcv(raw).reset_index(drop=True)
            ff = factor_frame(nd).reset_index(drop=True)
        except Exception:
            continue
        close = ff["close"].astype(float)
        high = pd.to_numeric(nd["high"], errors="coerce")
        opn = pd.to_numeric(nd["open"], errors="coerce")
        df = ff[["date"] + FACTORS[:-1]].copy()
        df["symbol"] = sym
        df["close"] = close
        df["amount"] = ff["amount"].astype(float)
        df["ret1"] = close.pct_change(1)
        df["ret5"] = close.pct_change(5)
        df["ret20"] = close.pct_change(20)
        df["dist_high60"] = close / high.rolling(60).max() - 1
        df["vol20"] = close.pct_change().rolling(20).std()
        o1 = opn.shift(-1)
        for h in HS:
            ch = close.shift(-h)
            df[f"c{h}"] = ch / close - 1
            df[f"o{h}"] = ch / o1 - 1
        df["bar"] = np.arange(len(df))
        df = df[(df["bar"] >= 79) & (df["date"] >= payload["start"]) & (df["amount"] >= 3e7)]
        if df.empty:
            continue
        df = df[np.array([not exclusion_reason(name, c, a) for c, a in zip(df["close"], df["amount"])])]
        if payload["lite"]:   # 长样本只留排序与择时要用的列，16GB 机器上全列会被杀
            df = df[["date", "symbol", "bar"] + FACTORS[:-1] + ["amount", "ret20", "c10"]]
        out.append(df.drop(columns=["bar"]).astype({c: "float32" for c in df.columns
                                                     if c not in ("date", "symbol", "bar")}))
    conn.close()
    return pd.concat(out) if out else None


def load_panel(since=LOAD_SINCE, start=OLD[0], lite=False):
    from concurrent.futures import ProcessPoolExecutor
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    names = dict(conn.execute("SELECT symbol, name FROM stock_meta"))
    syms = [r[0] for r in conn.execute(
        "SELECT symbol FROM daily_kline WHERE date>=? GROUP BY symbol HAVING COUNT(*)>=100", (since,))]
    # 行业热度要用全市场（不只候选）算，单独拉一份 20 日涨幅；长样本按中性 50（= 回放口径）
    k = None if lite else pd.read_sql_query(
        "SELECT symbol, date, close FROM daily_kline WHERE date>=? AND amount>0", conn, params=(since,))
    conn.close()
    chunks = [syms[i::48] for i in range(48)]
    payloads = [{"syms": [(s, str(names.get(s) or s)) for s in c], "since": since, "start": start, "lite": lite}
                for c in chunks]
    parts = []
    with ProcessPoolExecutor(max_workers=6) as ex:
        for i, p in enumerate(ex.map(_worker, payloads), 1):
            if p is not None:
                parts.append(p)
            print(f"  chunk {i}/{len(payloads)}", end="\r", flush=True)
    panel = pd.concat(parts, ignore_index=True)

    if lite:
        panel["industry_heat"] = np.float32(50.0)
        print(f"\n面板 {len(panel):,} 行 · {panel['date'].nunique()} 个交易日（industry_heat 按中性 50）")
        return panel
    ind = json.loads((ROOT / "runtime" / "industry_map.json").read_text(encoding="utf-8"))
    k = k.sort_values(["symbol", "date"])
    k["r20"] = k.groupby("symbol")["close"].pct_change(20)
    k["ind"] = k["symbol"].map(ind)
    heat = (k.dropna(subset=["ind", "r20"]).groupby(["date", "ind"])["r20"].mean()
            .groupby(level=0).rank(pct=True) * 100).rename("heat").reset_index()
    panel["ind"] = panel["symbol"].map(ind)
    panel = panel.merge(heat, left_on=["date", "ind"], right_on=["date", "ind"], how="left")
    panel["industry_heat"] = panel["heat"].fillna(50.0).astype("float32")
    panel = panel.drop(columns=["heat"])
    print(f"\n面板 {len(panel):,} 行 · {panel['date'].nunique()} 个交易日 · 行业覆盖 {panel['ind'].notna().mean():.0%}")
    return panel


def weights():
    from quantcore.quant.factors import _WEIGHTS
    return dict(_WEIGHTS)


def composite(panel, w, drop=None):
    ws = {k: v for k, v in w.items() if k != drop}
    tot = sum(ws.values())
    return sum(panel[k] * (v / tot) for k, v in ws.items())


def window(d, win):
    return (d >= win[0]) & (d <= win[1])


def tstat(s, step):
    s = s.dropna().iloc[::max(1, step)]
    if len(s) < 3 or s.std() == 0:
        return float("nan"), len(s)
    return s.mean() / (s.std(ddof=1) / math.sqrt(len(s))), len(s)


def top_excess(panel, score_col, ret_col):
    """每日：前 20 均值 − 候选全体均值。返回按日 Series（pp）。"""
    def f(g):
        g = g.dropna(subset=[ret_col])
        if len(g) < 500:
            return np.nan
        return (g.nlargest(TOP, score_col)[ret_col].mean() - g[ret_col].mean()) * 100
    return panel.groupby("date").apply(f, include_groups=False)


def daily_ic(panel, col, ret_col):
    def f(g):
        g = g[[col, ret_col]].dropna()
        return g[col].rank().corr(g[ret_col].rank()) if len(g) > 500 else np.nan
    return panel.groupby("date").apply(f, include_groups=False)


def quint_spread(panel, col, ret_col):
    def f(g):
        g = g[[col, ret_col]].dropna()
        if len(g) < 500:
            return np.nan
        q = pd.qcut(g[col].rank(method="first"), 5, labels=False)
        return (g[ret_col][q == 4].mean() - g[ret_col][q == 0].mean()) * 100
    return panel.groupby("date").apply(f, include_groups=False)


def summarize(s, h):
    d = s.index.to_series()
    row = {}
    for tag, win in (("old", OLD), ("new", NEW)):
        x = s[window(d, win)]
        t, n = tstat(x, h)
        row[tag] = (round(float(x.mean()), 3), round(t, 2), n)
    return row


def main_long():
    """5 年长样本：只看结构前20 的年度表现，以及「上一期失效 → 下一期失效」是否跨年成立。"""
    panel = load_panel(since="2020-06-01", start="2021-01-01", lite=True)
    panel["S"] = composite(panel, weights())
    s10 = top_excess(panel, "S", "c10").dropna()
    # 逐日序列落盘，供与其他策略对齐（如小市值组合的「此消彼长」检验）
    s10.rename("top20_c10_excess").to_csv(ROOT / "experiments" / "results" / "structure_top20_c10_daily.csv")
    sp = quint_spread(panel, "ret20", "c10")
    yr = pd.DataFrame({"top20": s10, "ret20_spread": sp})
    print("\n=== 长样本：结构前20 超额 / 全市场20日涨幅五分位价差（T+10，pp，年度均值）===")
    print(yr.groupby(yr.index.str[:4]).mean().round(2).to_string())
    mon = yr.groupby(yr.index.str[:7]).mean()
    print(f"逐月相关 {mon.corr().iloc[0, 1]:+.2f}（n={len(mon)}）")
    res = {}
    for off in range(10):          # 10 个起点各取一遍不重叠序列，看结论是否依赖起点
        lagged = pd.DataFrame({"now": s10, "known": s10.shift(10)}).dropna().iloc[off::10]
        neg, pos = lagged[lagged["known"] < 0]["now"], lagged[lagged["known"] >= 0]["now"]
        res[off] = (lagged["now"].corr(lagged["known"]), neg.mean(), pos.mean(), len(neg), len(pos),
                    (neg > 0).mean(), (pos > 0).mean())
    r = pd.DataFrame(res, index=["相关", "上期负→本期", "上期正→本期", "n负", "n正", "胜率|负", "胜率|正"]).T
    print("\n=== 长样本：上一期已兑现超额 → 本期（10 个不重叠起点）===")
    print(r.round(2).to_string())
    print("均值\n" + r.mean().round(2).to_string())
    lag = pd.DataFrame({"now": s10, "known": s10.shift(10)}).dropna()
    by_year = lag.groupby([lag.index.str[:4], lag["known"] < 0])["now"].mean().unstack()
    by_year.columns = ["上期正→本期", "上期负→本期"]
    print("\n逐年（全部会话，重叠）\n" + by_year.round(2).to_string())
    out = ROOT / "experiments" / "results" / "structure_decay_long_2026-09-28.json"
    out.write_text(json.dumps({"yearly": yr.groupby(yr.index.str[:4]).mean().round(3).to_dict(),
                               "lag": r.round(3).to_dict(), "lag_by_year": by_year.round(3).to_dict()},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ {out.relative_to(ROOT)}")


def main():
    panel = load_panel()
    w = weights()
    panel["S"] = composite(panel, w)
    w50 = dict(w)
    panel["S50"] = composite(panel.assign(industry_heat=50.0), w50)
    months = panel["date"].str[:7]
    res = {}

    # 0. 基线：结构前 20 逐月
    print("\n=== 0. 结构前20 超额（pp，vs 候选均值）逐月 ===")
    base = {}
    for h in (5, 10, 20):
        for e in ("c", "o"):
            base[f"{e}{h}"] = top_excess(panel, "S", f"{e}{h}")
    bm = pd.DataFrame(base)
    bm["S50_c10"] = top_excess(panel, "S50", "c10")
    print(bm.groupby(bm.index.str[:7]).mean().round(2).to_string())
    res["baseline"] = {c: summarize(bm[c], int(c[-2:]) if c[-2:].isdigit() else 5) for c in bm}
    for c, v in res["baseline"].items():
        print(f"  {c:8s} 旧窗 {v['old'][0]:+.2f} (t {v['old'][1]:+.2f}, n{v['old'][2]})   新窗 {v['new'][0]:+.2f} (t {v['new'][1]:+.2f}, n{v['new'][2]})")

    # 1. 技术体检
    print("\n=== 1. 因子技术体检：逐月均值 / 饱和率(0或100) / 缺失率（全候选） ===")
    g = panel.groupby(months)
    mean_t = g[FACTORS].mean().round(1)
    sat_t = g[FACTORS].agg(lambda s: ((s <= 0.01) | (s >= 99.99)).mean() * 100).round(0)
    na_t = g[FACTORS].agg(lambda s: s.isna().mean() * 100).round(1)
    print("均值\n" + mean_t.to_string())
    print("饱和率%\n" + sat_t.to_string())
    if na_t.values.max() > 0:
        print("缺失率%\n" + na_t.to_string())
    else:
        print("缺失率：全部 0")
    # 前 20 的因子画像
    top = panel.loc[panel.groupby("date")["S"].nlargest(TOP).index.get_level_values(1)]
    prof = top.groupby(top["date"].str[:7])[FACTORS + STYLE].mean()
    print("结构前20 的平均因子分与风格画像\n" + prof.round(3).to_string())
    res["top20_profile"] = prof.round(3).to_dict()

    # 2. 单因子 IC / 前20 超额 / 留一法
    print("\n=== 2. 单因子：Rank IC(T+10)、单因子前20超额(T+10)、留一法前20超额(T+10) ===")
    rows = []
    loo_base = bm["c10"]
    for f in FACTORS:
        ic = summarize(daily_ic(panel, f, "c10"), 10)
        solo = summarize(top_excess(panel, f, "c10"), 10)
        panel["_loo"] = composite(panel, w, drop=f)
        loo_s = top_excess(panel, "_loo", "c10")
        loo = summarize(loo_s - loo_base, 10)
        rows.append({"factor": f, "w": w[f],
                     "IC旧": ic["old"][0], "IC新": ic["new"][0], "ICt新": ic["new"][1],
                     "单因子前20旧": solo["old"][0], "单因子前20新": solo["new"][0],
                     "去掉它Δ旧": loo["old"][0], "去掉它Δ新": loo["new"][0], "Δt新": loo["new"][1]})
    t2 = pd.DataFrame(rows).set_index("factor")
    print(t2.to_string())
    res["factors"] = t2.to_dict(orient="index")

    # 3. 归因：Fama-MacBeth 因子收益 × 前20 暴露
    print("\n=== 3. 归因（T+10）：前20 暴露(z) × 因子横截面收益(pp/1σ)，旧窗→新窗 ===")
    fr, ex = [], []
    for d, gd in panel.groupby("date"):
        gd = gd.dropna(subset=["c10"])
        if len(gd) < 500:
            continue
        Z = (gd[FACTORS] - gd[FACTORS].mean()) / gd[FACTORS].std().replace(0, np.nan)
        Z = Z.fillna(0.0)
        y = (gd["c10"] - gd["c10"].mean()).to_numpy() * 100
        beta, *_ = np.linalg.lstsq(Z.to_numpy(), y, rcond=None)
        fr.append(pd.Series(beta, index=FACTORS, name=d))
        ex.append(Z.loc[gd["S"].nlargest(TOP).index].mean().rename(d))
    fr, ex = pd.DataFrame(fr), pd.DataFrame(ex)
    contrib = fr * ex
    att = pd.DataFrame({
        "暴露旧": ex[window(ex.index.to_series(), OLD)].mean(),
        "暴露新": ex[window(ex.index.to_series(), NEW)].mean(),
        "因子收益旧": fr[window(fr.index.to_series(), OLD)].mean(),
        "因子收益新": fr[window(fr.index.to_series(), NEW)].mean(),
        "贡献旧": contrib[window(contrib.index.to_series(), OLD)].mean(),
        "贡献新": contrib[window(contrib.index.to_series(), NEW)].mean(),
    })
    att["贡献变化"] = att["贡献新"] - att["贡献旧"]
    print(att.round(3).to_string())
    print(f"  合计贡献 旧 {att['贡献旧'].sum():+.2f}  新 {att['贡献新'].sum():+.2f}  变化 {att['贡献变化'].sum():+.2f}")
    print("逐月因子收益（pp/1σ，T+10）\n" + fr.groupby(fr.index.str[:7]).mean().round(2).to_string())
    res["attribution"] = att.round(3).to_dict(orient="index")

    # 4. 纯风格代理（与结构分无关）
    print("\n=== 4. 风格代理：全市场 Rank IC 与 五分位价差 Q5−Q1（T+10，pp） ===")
    srows = []
    for s in STYLE:
        ic_s = daily_ic(panel, s, "c10")
        qs = quint_spread(panel, s, "c10")
        a, b = summarize(ic_s, 10), summarize(qs, 10)
        srows.append({"style": s, "IC旧": a["old"][0], "IC新": a["new"][0], "ICt新": a["new"][1],
                      "Q5-Q1旧": b["old"][0], "Q5-Q1新": b["new"][0], "价差t新": b["new"][1]})
        res.setdefault("style_monthly", {})[s] = qs.groupby(qs.index.str[:7]).mean().round(2).to_dict()
    t4 = pd.DataFrame(srows).set_index("style")
    print(t4.to_string())
    print("逐月五分位价差\n" + pd.DataFrame(res["style_monthly"]).to_string())
    res["style"] = t4.to_dict(orient="index")

    # 5. 能不能提前看出来：已兑现的上一期结构前20超额 → 下一期（间隔 10 个交易日，不重叠）
    print("\n=== 5. 风格是否可提前识别（T+10）===")
    s10 = bm["c10"].dropna()
    lagged = pd.DataFrame({"now": s10, "known": s10.shift(10)}).dropna().iloc[::10]
    r = lagged["now"].corr(lagged["known"])
    print(f"  上一期已兑现超额 vs 本期超额：相关 {r:+.2f}（n={len(lagged)}）")
    for cond, name in ((lagged["known"] < 0, "上一期为负"), (lagged["known"] >= 0, "上一期为正")):
        x = lagged.loc[cond, "now"]
        print(f"  {name}：本期均值 {x.mean():+.2f}pp  胜率 {(x > 0).mean():.0%}  n={len(x)}")
    sp = quint_spread(panel, "ret20", "c10")
    mon = pd.DataFrame({"top20": bm["c10"], "ret20_spread": sp}).groupby(bm.index.str[:7]).mean()
    print(f"  逐月：结构前20超额 与 全市场20日涨幅五分位价差 相关 {mon.corr().iloc[0, 1]:+.2f}（n={len(mon)}）")
    res["predictability"] = {"lag_corr": round(r, 3), "n": len(lagged),
                             "monthly_corr_with_ret20_spread": round(float(mon.corr().iloc[0, 1]), 3)}

    out = ROOT / "experiments" / "results" / "structure_decay_2026-09-28.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\n→ {out.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main_long() if "--long" in sys.argv else main()
