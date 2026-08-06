"""五方判读有没有预测力：共识分 / 分歧度 → 后续表现。

背景：五方判读是 5 个 LLM 角色（价值/趋势/游资/逆向/量化）对个股各打 0-100 分，聚合成
共识分与分歧度。2026-08-06 把它从推荐表格里撤掉，转入留痕 —— 它的输入全是本地已有指标，
不带来新信息源，而且从未验证过命中率。撤掉不等于删掉：后台每天照常给当日名单打分入库
（board_refresh._run_daily_panel_batch），攒够样本后由本脚本裁决。

**它只在已入选的票之间比较**，因为只有入选票才有评分。所以回答的问题是：
「在选股系统已经挑出来的票里，共识分高的是不是后续更好？」—— 这正是「要不要给它权重」
需要的答案，而不是「它能不能独立选股」。

裁决标准（先写死，避免事后挑数）：
- 主指标：共识分前半 vs 后半的 T+5 超额之差，按**会话日聚类**做 t（票级 t 会被
  同一天票之间的相关性伪造出显著性，2026-08-05 在缩量那轮踩过）；
- 样本不足 30 个交易日不下结论，只打印覆盖情况；
- 前后半切分同号才算数。

    python experiments/panel_eval.py
    python experiments/panel_eval.py --horizon 10 --db runtime/quant_data.sqlite
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(os.path.dirname(HERE), "runtime", "quant_data.sqlite")
MIN_SESSIONS = 30


def load(db: str, horizon: int) -> pd.DataFrame:
    """每条 = 一个 (交易日, 股票) 的评分 + 该日起 horizon 根后的收益 + 当期全市场基准。"""
    con = sqlite3.connect(db)
    panel = pd.read_sql(
        "SELECT date, symbol, consensus, divergence, bull, bear FROM panel_scores", con
    )
    if panel.empty:
        con.close()
        return panel
    since = panel["date"].min()
    bars = pd.read_sql(
        "SELECT symbol, date, close FROM daily_kline WHERE amount>0 AND date>=?",
        con, params=(since,),
    )
    con.close()

    close = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    fwd = close.shift(-horizon) / close - 1.0
    # 基准与组合侧同一统计量：等权持有 → 用均值（口径同 experiments/README.md）
    bench = fwd.mean(axis=1)

    rows = []
    for d, grp in panel.groupby("date"):
        if d not in fwd.index:
            continue
        day, base = fwd.loc[d], bench.get(d)
        if base is None or not np.isfinite(base):
            continue
        for _, r in grp.iterrows():
            ret = day.get(str(r["symbol"]).zfill(6))
            if ret is None or not np.isfinite(ret):
                continue
            rows.append({
                "date": d, "symbol": r["symbol"],
                "consensus": float(r["consensus"]), "divergence": float(r["divergence"]),
                "bull": float(r["bull"] or 0), "bear": float(r["bear"] or 0),
                "excess": (ret - base) * 100,
            })
    return pd.DataFrame(rows)


def split_test(df: pd.DataFrame, column: str, label: str) -> None:
    """每个交易日内按 column 分上下半，取「上半均值 − 下半均值」，再跨日做 t。

    按会话日聚类是硬要求：同一天入选的票高度相关，把 4000 个票级观测当独立样本会把
    t 值放大数倍（缩量那轮实测 t=−2.87 聚类后变 −0.54）。
    """
    diffs, dates = [], []
    for d, g in df.groupby("date"):
        if len(g) < 6:
            continue
        mid = g[column].median()
        hi, lo = g[g[column] > mid]["excess"], g[g[column] <= mid]["excess"]
        if len(hi) < 2 or len(lo) < 2:
            continue
        diffs.append(hi.mean() - lo.mean())
        dates.append(d)
    if len(diffs) < 5:
        print(f"  {label:14s} 可比交易日不足（{len(diffs)}），跳过")
        return
    a = np.array(diffs)
    t = a.mean() / (a.std(ddof=1) / len(a) ** 0.5) if a.std(ddof=1) > 0 else 0.0
    half = len(a) // 2
    print(f"  {label:14s} 交易日{len(a):3d}  高分组−低分组 {a.mean():+6.2f}pp  t={t:+5.2f}  "
          f"占优{100 * (a > 0).mean():3.0f}%  | 前半{a[:half].mean():+6.2f} 后半{a[half:].mean():+6.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--horizon", type=int, default=5)
    a = ap.parse_args()

    df = load(a.db, a.horizon)
    if df.empty:
        print("panel_scores 里还没有可评估的样本。")
        return
    sessions = df["date"].nunique()
    print(f"样本：{len(df)} 条观测 / {sessions} 个交易日 "
          f"({df['date'].min()} ~ {df['date'].max()})  持有 T+{a.horizon}")
    print(f"共识分 中位 {df['consensus'].median():.1f}  分歧度 中位 {df['divergence'].median():.1f}")
    print(f"入选票整体超额 {df['excess'].mean():+.2f}pp（对当期全市场均值）\n")

    print("分组检验（按交易日聚类）：")
    split_test(df, "consensus", "共识分")
    split_test(df, "divergence", "分歧度")
    split_test(df, "bull", "看多人数")

    print("\n共识分分档：")
    bins = pd.cut(df["consensus"], [0, 55, 62, 68, 75, 100])
    for rng, g in df.groupby(bins, observed=True):
        print(f"  {str(rng):14s} n={len(g):4d}  超额 {g['excess'].mean():+6.2f}pp  "
              f"中位 {g['excess'].median():+6.2f}")

    if sessions < MIN_SESSIONS:
        print(f"\n【不下结论】{sessions} 个交易日 < {MIN_SESSIONS}，噪声远大于效应，继续攒。")
    else:
        print(f"\n{sessions} 个交易日已达最低样本量：主指标 t>2 且前后半同号才算有预测力，"
              f"否则从产品里删干净。")

    out = os.path.join(HERE, "results", f"panel_eval_T{a.horizon}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({"sessions": int(sessions), "observations": len(df),
               "horizon": a.horizon,
               "mean_excess_pp": round(float(df["excess"].mean()), 3)},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"-> {out}")


if __name__ == "__main__":
    main()
