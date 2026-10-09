"""多因子统计模型：量化公司的标准做法在本地数据上能拿多少（2026-10-09）。

Allen 同意为研究恢复机器学习（生产里的 ML 仍停用）。思路：不再人工挑一两条规则，
把几十个弱信号交给 LightGBM，学「下周谁比同侪涨得多」，再用前面同一把尺子
（portfolio_lab 的周换仓组合 + 退市股）评估。

防自欺的口径（事先定死，看结果后不改）：
- 特征全部只用 T 日收盘及以前的数据；每天横截面转成分位（0~1），模型学不到「整体行情」
- 标签：T+1 开盘买、T+6 开盘卖的收益，在当天横截面上的分位 —— 只学相对强弱
- 滚动训练（walk-forward）：每季度重训一次，只用该季度开始前 10 个交易日以前的数据
  （标签要 6 天才兑现，留空隔离），首个样本外季度 2022Q3
- 可投资池与 portfolio_lab 相同：非北交所、满 250 根、20 日均成交额 ≥3000 万、开盘未封涨停；
  并入 2020 年以来的退市股（先跑 fetch_delisted.py）
- 评估：① 周一开盘买前 50/前 20、下周一开盘卖，扣双边 0.3%，对同池等权；
  ② 逐日 rank IC；③ 分年
- 超参数固定，不调：调参在 4 年样本上等于又一次挑最好看的

用法：python experiments/ml_lab.py [--no-events]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "experiments" / ".snapshot.sqlite"
CACHE = ROOT / "experiments" / ".cache"
COST = 0.003
MIN_AMT = 3e7
PARAMS = dict(objective="regression", learning_rate=0.05, num_leaves=63, min_data_in_leaf=2000,
              feature_fraction=0.8, bagging_fraction=0.7, bagging_freq=1, lambda_l2=10.0,
              verbose=-1, num_threads=6, seed=7)
ROUNDS = 300


def load() -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    k = pd.read_sql_query("SELECT symbol, date, open, high, low, close, amount FROM daily_kline", conn)
    conn.close()
    dl = CACHE / "delisted_kline.csv"
    if dl.exists():
        d = pd.read_csv(dl, dtype={"symbol": str})
        d = d[~d["symbol"].isin(k["symbol"].unique())].drop_duplicates(["symbol", "date"])
        k = pd.concat([k, d[k.columns]], ignore_index=True)
    k = k[~k["symbol"].str.startswith(("8", "4", "92")) & (k["open"] > 0) & (k["close"] > 0) & (k["amount"] > 0)]
    k = k.astype({c: "float32" for c in ("open", "high", "low", "close")} | {"amount": "float64"})
    return k.sort_values(["symbol", "date"]).reset_index(drop=True)


def _roll(g, col, n, fn):
    return g[col].transform(lambda s: getattr(s.rolling(n, min_periods=max(2, n * 3 // 4)), fn)())


def features(k: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    g = k.groupby("symbol", sort=False)
    c = g["close"]
    k["prev_close"] = c.shift(1)
    k["dr"] = (k["close"] / k["prev_close"] - 1).astype("float32")
    g = k.groupby("symbol", sort=False)
    f = {}
    for n in (1, 3, 5, 10, 20, 60, 120, 250):
        f[f"ret{n}"] = k["close"] / c.shift(n) - 1
    for n in (5, 10, 20, 60, 250):
        f[f"dma{n}"] = k["close"] / _roll(g, "close", n, "mean") - 1
    for n in (5, 20, 60):
        f[f"vol{n}"] = _roll(g, "dr", n, "std")
    f["max20"] = _roll(g, "dr", 20, "max")
    f["min20"] = _roll(g, "dr", 20, "min")
    f["skew20"] = _roll(g, "dr", 20, "skew")
    for n in (20, 60, 250):
        f[f"dhi{n}"] = k["close"] / _roll(g, "high", n, "max") - 1
        f[f"dlo{n}"] = k["close"] / _roll(g, "low", n, "min") - 1
    amt5, amt20, amt60 = (_roll(g, "amount", n, "mean") for n in (5, 20, 60))
    f["lamt20"] = np.log(amt20)
    f["amt_r1"] = k["amount"] / amt20
    f["amt_r5"] = amt5 / amt20
    f["amt_r20"] = amt20 / amt60
    f["amt_cv20"] = _roll(g, "amount", 20, "std") / amt20
    f["illiq20"] = (k["dr"].abs() / k["amount"] * 1e8).groupby(k["symbol"], sort=False).transform(
        lambda s: s.rolling(20, min_periods=15).mean())
    span = (k["high"] - k["low"]).replace(0, np.nan)
    f["clv"] = (k["close"] - k["low"]) / span
    f["upper_sh"] = (k["high"] - np.maximum(k["open"], k["close"])) / k["prev_close"]
    f["intraday"] = k["close"] / k["open"] - 1
    f["gap"] = k["open"] / k["prev_close"] - 1
    lim = np.where(k["symbol"].str.startswith(("300", "301", "688", "689")), 0.195, 0.095)
    f["lu20"] = (k["dr"] >= lim).astype("float32").groupby(k["symbol"], sort=False).transform(
        lambda s: s.rolling(20, min_periods=1).sum())
    f["lprice"] = np.log(k["close"])
    feat = pd.DataFrame(f).astype("float32")
    k = pd.concat([k, feat], axis=1)
    names = list(feat.columns)

    # 行业：个股相对行业、行业自身动量（唯一过闸的价量规则就是 20 日板块动量）
    ind = json.load(open(ROOT / "runtime" / "industry_map.json", encoding="utf-8"))
    k["industry"] = k["symbol"].map(ind).fillna("未知")
    for col in ("ret5", "ret20", "ret60"):
        m = k.groupby(["date", "industry"])[col].transform("mean")
        k[f"ind_{col}"] = m.astype("float32")
        k[f"rel_{col}"] = (k[col] - m).astype("float32")
        names += [f"ind_{col}", f"rel_{col}"]

    # 可投资池与标签
    k["bars"] = k.groupby("symbol", sort=False).cumcount()
    nxt_open = k.groupby("symbol", sort=False)["open"].shift(-1)
    exit_open = k.groupby("symbol", sort=False)["open"].shift(-6)
    # 持有期内退市的：按最后一个收盘价卖（否则这些票的标签是空的，又回到只看幸存者）
    last_date = k.groupby("symbol", sort=False)["date"].transform("last")
    last_close = k.groupby("symbol", sort=False)["close"].transform("last")
    delisted = (last_date < k["date"].max()) & exit_open.isna() & nxt_open.notna()
    exit_open = exit_open.where(~delisted, last_close)
    k["fwd"] = (exit_open / nxt_open - 1).clip(-0.6, 1.5)
    k["next_limit_up"] = (nxt_open / k["close"] - 1) >= (lim - 0.002)
    k["amt20"] = amt20
    return k, names


def attach_events(k: pd.DataFrame, names: list[str]) -> None:
    """价外事件：距最近一次事件过了几个交易日（无事件 = 999），由横截面分位再处理。"""
    days = pd.DatetimeIndex(sorted(pd.to_datetime(k["date"].unique())))
    pos = k.groupby("symbol", sort=False).cumcount()

    def age(ev: pd.DataFrame, name: str):
        ev = ev.dropna(subset=["ann"])
        p = days.searchsorted(ev["ann"].values, side="right") - 1
        ev = ev[p >= 0].assign(date=days[p[p >= 0]].strftime("%Y-%m-%d"))
        hit = pd.Series(1.0, index=pd.MultiIndex.from_frame(ev[["symbol", "date"]].drop_duplicates()))
        flag = hit.reindex(pd.MultiIndex.from_arrays([k["symbol"], k["date"]])).notna().to_numpy()
        last = pos.where(flag).groupby(k["symbol"], sort=False).ffill()
        k[name] = (pos - last).fillna(999).clip(upper=999).astype("float32")
        names.append(name)
        print(f"  事件 {name}: {flag.sum()} 次命中")

    fc = CACHE / "forecasts.csv"
    if fc.exists():
        x = pd.read_csv(fc, dtype={"股票代码": str})
        x = x[x["预测指标"] == "归属于上市公司股东的净利润"]
        x = x.assign(symbol=x["股票代码"].str.zfill(6), ann=pd.to_datetime(x["公告日期"], errors="coerce"),
                     chg=pd.to_numeric(x["业绩变动幅度"], errors="coerce"))
        age(x[x["预告类型"].isin(["预增", "扭亏"]) & (x["chg"] >= 50)], "ev_fc_beat")
        age(x[x["预告类型"].isin(["略增", "续盈"])], "ev_fc_mild")
        age(x[x["预告类型"].isin(["预减", "首亏", "续亏", "增亏", "略减"])], "ev_fc_bad")
    for fname, name, code_col, date_col in (
            ("repurchase.csv", "ev_buyback", 1, 11), ("holder_up.csv", "ev_holder_up", 0, 15),
            ("holder_down.csv", "ev_holder_down", 0, 15)):
        p = CACHE / fname
        if p.exists():
            x = pd.read_csv(p, dtype=str)
            age(pd.DataFrame({"symbol": x.iloc[:, code_col].str.zfill(6),
                              "ann": pd.to_datetime(x.iloc[:, date_col], errors="coerce")}), name)
    p = CACHE / "survey.csv"
    if p.exists():
        x = pd.read_csv(p, dtype={"SECURITY_CODE": str})
        age(pd.DataFrame({"symbol": x["SECURITY_CODE"].str.zfill(6),
                          "ann": pd.to_datetime(x["NOTICE_DATE"], errors="coerce")}), "ev_survey")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-events", action="store_true")
    a = ap.parse_args()
    import lightgbm as lgb

    k = load()
    print(f"日线 {len(k)} 行，{k['symbol'].nunique()} 只（含退市）", flush=True)
    k, names = features(k)
    if not a.no_events:
        attach_events(k, names)
    u = k[(k["bars"] >= 250) & (k["amt20"] >= MIN_AMT) & (k["date"] >= "2021-01-01")].copy()
    del k
    # 横截面分位
    for n in names:
        u[n] = u.groupby("date")[n].rank(pct=True).astype("float32")
    u["y"] = u.groupby("date")["fwd"].rank(pct=True).astype("float32")
    print(f"可投资样本 {len(u)} 行，{len(names)} 个特征", flush=True)

    days = sorted(u["date"].unique())
    qstarts = [d for d, p in zip(days[1:], days[:-1]) if d[:7] != p[:7] and d[5:7] in ("01", "04", "07", "10")]
    qstarts = [d for d in qstarts if d >= "2022-07-01"] + ["9999"]
    preds, imps = [], []
    for q0, q1 in zip(qstarts[:-1], qstarts[1:]):
        cut = days[max(0, days.index(q0) - 10)]
        tr = u[(u["date"] < cut) & u["y"].notna()]
        te = u[(u["date"] >= q0) & (u["date"] < q1)]
        m = lgb.train(PARAMS, lgb.Dataset(tr[names], tr["y"], free_raw_data=True), ROUNDS)
        preds.append(te[["symbol", "date", "fwd", "open", "next_limit_up"]].assign(score=m.predict(te[names])))
        imps.append(pd.Series(m.feature_importance("gain"), index=names))
        print(f"  {q0} 训练 {len(tr)} 行 → 预测 {len(te)} 行", flush=True)
    p = pd.concat(preds)
    tag = "noev" if a.no_events else "ev"
    p.to_pickle(ROOT / "experiments" / ".cache" / f"ml_preds_{tag}.pkl")

    # ① 逐日 rank IC
    ic = p.dropna(subset=["fwd"]).groupby("date").apply(
        lambda x: x["score"].corr(x["fwd"], method="spearman"), include_groups=False)
    print(f"\n逐日 rank IC 均值 {ic.mean():.4f}，IC>0 天数占比 {(ic > 0).mean():.1%}，"
          f"ICIR {ic.mean() / ic.std():.2f}（{len(ic)} 天，含重叠）")
    # ② 周换仓组合
    weeks = [d for d, pr in zip(days[1:], days[:-1])
             if pd.Timestamp(d).isocalendar().week != pd.Timestamp(pr).isocalendar().week]
    # 信号在每周最后一个交易日收盘后产生，下周一开盘买
    fri = {days[days.index(w) - 1] for w in weeks}
    pf = p[p["date"].isin(fri) & p["fwd"].notna()]
    rows, hold = [], {20: set(), 50: set()}
    for d, x in pf.groupby("date"):
        x = x[~x["next_limit_up"]]
        row = {"d": d, "基准": x["fwd"].mean()}
        for n in (20, 50):
            top = x.nlargest(n, "score")
            turn = len(set(top["symbol"]) - hold[n]) / n
            row[f"前{n}"] = top["fwd"].mean() - COST * turn
            hold[n] = set(top["symbol"])
        rows.append(row)
    t = pd.DataFrame(rows)
    t["yr"] = t["d"].str[:4]
    print(f"\n=== 周换仓（样本外 {t['d'].iloc[0]} ~ {t['d'].iloc[-1]}，{len(t)} 周，扣双边 {COST:.1%}，含退市股） ===")
    for col in ("基准", "前20", "前50"):
        nav = (1 + t[col]).cumprod()
        ex = t[col] - t["基准"]
        yrs = t.groupby("yr").apply(lambda x: ((1 + x[col]).prod() - (1 + x["基准"]).prod()) * 100, include_groups=False)
        tt = ex.mean() / (ex.std() / np.sqrt(len(ex))) if col != "基准" else float("nan")
        print(f"{col:4s} 年化 {(nav.iloc[-1] ** (52 / len(t)) - 1) * 100:+6.1f}%  最大回撤 {(nav / nav.cummax() - 1).min() * 100:6.1f}%  "
              f"周超额 {ex.mean() * 100:+.2f}pp t={tt:.2f} 周胜率 {(ex > 0).mean():.0%}  "
              + "  ".join(f"{y}:{v:+.1f}" for y, v in yrs.items()))
    t.to_csv(ROOT / "experiments" / "results" / f"ml_lab_weekly_{tag}.csv", index=False)
    imp = pd.concat(imps, axis=1).mean(axis=1).sort_values(ascending=False)
    print("\n特征重要性前 15：", ", ".join(f"{n}" for n in imp.index[:15]))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
