"""单因子 Rank IC：看每个因子单独的全截面排序能力。

**这只是线索，不是决策依据。** 2026-07-25 那轮的教训：IC 排名最难看的 trend / momentum
恰恰是 top-N 超额的主要来源，照 IC 调权重反而把超额砍掉 1.4pp/期。原因是 IC 衡量整个
截面的单调排序，而产品只买评分最高的 20 只 —— 一个中后段乱序的因子，头部照样可以很准。

所以流程是：本脚本挑候选方案 → weight_ab.py 用 top-N 超额裁决。别跳过第二步。

    python experiments/snapshot_db.py
    python experiments/factor_ic.py --anchor 2026-07-25 --months 12 --step 5
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
import pandas as pd

from factor_scores import DEFAULT_DB, FACTORS, HERE, collect

RESULTS = os.path.join(HERE, "results")


def rank_ic(by_session: dict) -> dict:
    cols = FACTORS + ["composite"]
    per_session = {c: [] for c in cols}
    for _s, items in by_session.items():
        if len(items) < 50:
            continue
        rets = np.array([r for _, r in items], dtype=float)
        excess = pd.Series(rets - np.median(rets)).rank()   # 基准：当期全市场中位
        for c in cols:
            fv = pd.Series([v[c] for v, _ in items]).rank()
            if fv.nunique() < 5:
                continue
            per_session[c].append(float(np.corrcoef(fv, excess)[0, 1]))

    out = {}
    for c in cols:
        arr = np.array(per_session[c], dtype=float)
        arr = arr[~np.isnan(arr)]
        if arr.size < 5:
            continue
        mean, sd = float(arr.mean()), float(arr.std(ddof=1))
        out[c] = {
            "rank_ic": round(mean, 4),
            "icir": round(mean / sd, 3) if sd > 0 else None,
            "t_stat": round(mean / sd * np.sqrt(arr.size), 2) if sd > 0 else None,
            "pct_positive": round(float((arr > 0).mean()) * 100, 1),
            "n_sessions": int(arr.size),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--anchor", default="2026-07-25")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--workers", type=int, default=9)
    a = ap.parse_args()

    by_session = collect(a.db, a.anchor, a.months, a.step, a.workers)
    obs = sum(len(v) for v in by_session.values())
    factors = rank_ic(by_session)

    print(f"\nanchor={a.anchor} months={a.months} step={a.step} "
          f"sessions={len(by_session)} observations={obs:,}")
    print(f"{'factor':<14}{'RankIC':>9}{'ICIR':>8}{'t':>7}{'%pos':>7}")
    for k, v in sorted(factors.items(), key=lambda kv: -kv[1]["rank_ic"]):
        print(f"{k:<14}{v['rank_ic']:>9.4f}{v['icir']:>8.2f}{v['t_stat']:>7.2f}{v['pct_positive']:>6.0f}%")

    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, f"factor_ic_{a.anchor}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"anchor": a.anchor, "months": a.months, "step": a.step,
                   "sessions_used": len(by_session), "observations": obs,
                   "factors": factors}, fh, ensure_ascii=False, indent=2)
    print(f"\n-> {path}")


if __name__ == "__main__":
    main()
