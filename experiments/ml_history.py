"""多因子模型的老数据回测：2015–2019，模型从没见过的 5 年（2026-10-10）。

2020–2026 那段已经看过、挑过构造（ml_lab.py），再测多少遍都不是新证据。这里把整套东西冻结原样搬到老数据：
特征、参数、标签、季度滚动重训（隔 10 天）、低换手构造（5 日平滑分前 50、掉出前 20% 才卖）、
扣双边 0.3%、对同池等权。数据 fetch_history.py（含 2013 年后退市股）。
训练从 2014 年起（前一年用来攒 250 根日线），首个样本外季度 2015Q3 —— 与新数据段同样留 1.5 年训练。

事先定的判定（看结果前写下，不改）：
- 老数据单独：净周超额 > 0，且多数年份为正；
- 新老合并（约 450 周）：t ≥ 2 —— 这才算「统计上可靠」。

用法：python experiments/ml_history.py [--drop lprice]
  --drop：去掉某些特征重跑。lprice（前复权价位）会偷看未来：日后送转多的股票早年复权价被压得很低。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "experiments" / ".cache"
sys.path.insert(0, str(ROOT))
from quantcore.quant.ml_factors import PARAMS, ROUNDS, build_features, rank_universe  # noqa: E402
from quantcore.quant.ml_shadow import COST, HOLD, KEEP_PCT, SMOOTH_DAYS  # noqa: E402


def walk_forward(u: pd.DataFrame, names: list[str], first_q: str) -> pd.DataFrame:
    import lightgbm as lgb
    days = sorted(u["date"].unique())
    qstarts = [d for d, p in zip(days[1:], days[:-1]) if d[:7] != p[:7] and d[5:7] in ("01", "04", "07", "10")]
    qstarts = [d for d in qstarts if d >= first_q] + ["9999"]
    preds = []
    for q0, q1 in zip(qstarts[:-1], qstarts[1:]):
        cut = days[max(0, days.index(q0) - 10)]
        tr = u[(u["date"] < cut) & u["y"].notna()]
        te = u[(u["date"] >= q0) & (u["date"] < q1)]
        m = lgb.train(PARAMS, lgb.Dataset(tr[names], tr["y"], free_raw_data=True), ROUNDS)
        preds.append(te[["symbol", "date", "fwd", "next_limit_up"]].assign(score=m.predict(te[names])))
        print(f"  {q0} 训练 {len(tr)} 行 → 预测 {len(te)} 行", flush=True)
    return pd.concat(preds)


def low_turnover(p: pd.DataFrame) -> pd.DataFrame:
    """与 ml_shadow.run_weekly 同一套构造：周最后一个交易日出信号，下一开盘买、持有 5 天。
    开盘即涨停的买不进（不算收益，但仍算持有，下周可以留）。"""
    p = p.copy()
    p["pct"] = p.groupby("date")["score"].rank(pct=True)
    days = sorted(p["date"].unique())
    fri = [d for d, n in zip(days, days[1:]) if pd.Timestamp(d).isocalendar()[:2] != pd.Timestamp(n).isocalendar()[:2]]
    by_day = {d: x.set_index("symbol") for d, x in p.groupby("date")}
    rows, hold = [], set()
    for d in fri:
        i = days.index(d)
        today = by_day[d]
        recent = pd.concat([by_day[x]["pct"] for x in days[max(0, i - SMOOTH_DAYS + 1): i + 1]], axis=1)
        score = recent.loc[today.index].mean(axis=1).rank(pct=True)
        kept = [s for s in hold if s in score.index and score[s] >= KEEP_PCT]
        fill = [s for s in score.sort_values(ascending=False).index if s not in kept][: HOLD - len(kept)]
        picks = kept + fill
        x = today.loc[picks]
        x = x[~x["next_limit_up"] & x["fwd"].notna()]
        base = today[~today["next_limit_up"] & today["fwd"].notna()]["fwd"].mean()
        turn = len(set(picks) - hold) / HOLD
        hold = set(picks)
        ret = x["fwd"].mean() - COST * turn
        rows.append({"d": d, "ret": ret, "base": base, "excess": ret - base, "turn": turn})
    return pd.DataFrame(rows).dropna()


def report(t: pd.DataFrame, label: str) -> None:
    ex = t["excess"]
    nav, bnav = (1 + t["ret"]).cumprod(), (1 + t["base"]).cumprod()
    tt = ex.mean() / (ex.std() / np.sqrt(len(ex)))
    yrs = t.groupby(t["d"].str[:4]).apply(lambda x: ((1 + x["ret"]).prod() - (1 + x["base"]).prod()) * 100,
                                          include_groups=False)
    print(f"{label}: {t['d'].iloc[0]}~{t['d'].iloc[-1]} {len(t)} 周 | 年化 {(nav.iloc[-1] ** (52 / len(t)) - 1) * 100:+.1f}% "
          f"(基准 {(bnav.iloc[-1] ** (52 / len(t)) - 1) * 100:+.1f}%) | 回撤 {(nav / nav.cummax() - 1).min() * 100:.0f}% "
          f"(基准 {(bnav / bnav.cummax() - 1).min() * 100:.0f}%) | 净周超额 {ex.mean() * 100:+.2f}pp t={tt:.2f} "
          f"周胜率 {(ex > 0).mean():.0%} 换手 {t['turn'].mean():.0%}\n    逐年超额 pp: "
          + "  ".join(f"{y}:{v:+.1f}" for y, v in yrs.items()), flush=True)


def main():
    import argparse
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop", nargs="*", default=[])
    a = ap.parse_args()
    # ① 先用同一函数重算新数据段，确认构造能复现 ml_lab 记下的 +0.14pp / t 1.17
    new = low_turnover(pd.read_pickle(CACHE / "ml_preds_noev.pkl"))
    report(new, "新数据 2022-2026（复核）")

    k = pd.read_parquet(CACHE / "kline_2013_2019.parquet")
    k = k[(k["open"] > 0) & (k["close"] > 0) & (k["amount"] > 0)].drop_duplicates(["symbol", "date"])
    k = k.astype({c: "float32" for c in ("open", "high", "low", "close")} | {"amount": "float64"})
    k = k.sort_values(["symbol", "date"]).reset_index(drop=True)
    print(f"老数据日线 {len(k)} 行，{k['symbol'].nunique()} 只", flush=True)
    k, names = build_features(k)
    names = [n for n in names if n not in a.drop]
    u = rank_universe(k, names, "2014-01-01")
    del k
    p = walk_forward(u, names, "2015-07-01")
    tag = "_drop-" + "-".join(a.drop) if a.drop else ""
    p.to_pickle(CACHE / f"ml_preds_2015_2019{tag}.pkl")
    old = low_turnover(p)
    report(old, "老数据 2015-2019（从没见过）")
    both = pd.concat([old, new], ignore_index=True)
    report(both, "新老合并")
    both.to_csv(ROOT / "experiments" / "results" / f"ml_history_weekly{tag}.csv", index=False)


if __name__ == "__main__":
    main()
