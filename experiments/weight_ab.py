"""权重方案 A/B：以 top-N 超额裁决，逐观测同轴配对。

为什么不跑两轮 replay：composite 是 8 个因子分的**线性组合**，所以一次数据扫描就能把
任意多组权重的分数重算出来。所有方案共用同一批 (会话, 股票, 因子分, T+5 收益) 观测 ——
这比两次独立回放更强：不只是"同一根轴"，是逐观测完全相同，因此可以做配对检验，把
市场环境的共同波动全部消掉。

范围声明：本脚本只隔离「权重」一个变量。线上评分之后还有盘中强度混合、形态/强度共振
加成、风控硬闸门、行业分散等环节，都不在这里。所以结论是"权重孰优"，不是收益预测。

样本外：按时间前后各半切分，两段都要赢才算数。

    python experiments/snapshot_db.py
    python experiments/weight_ab.py --anchor 2026-07-25 --top-n 20

要把胜出方案接到线上，用环境变量先验证，再改 quantcore/quant/factors.py 的默认值：
    LYNX_FACTOR_WEIGHTS='{"trend":0.16,...}'
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np

from factor_scores import DEFAULT_DB, FACTORS, HERE, collect

RESULTS = os.path.join(HERE, "results")

BASELINE = {"trend": 0.22, "momentum": 0.22, "rsi": 0.12, "risk_control": 0.15,
            "liquidity": 0.10, "macd": 0.08, "bollinger": 0.06, "capital_flow": 0.05}

# 2026-07-25 那轮的候选，保留下来当例子：全部落败，详见 README。
SCHEMES = {
    "A_baseline": BASELINE,
    # IC 导向：唯一显著正 IC 的 risk_control 加重，显著负 IC 的 rsi 砍到近零
    "B_ic_informed": {"trend": 0.16, "momentum": 0.16, "rsi": 0.04, "risk_control": 0.32,
                      "liquidity": 0.10, "macd": 0.08, "bollinger": 0.06, "capital_flow": 0.08},
    "C_moderate": {"trend": 0.19, "momentum": 0.19, "rsi": 0.08, "risk_control": 0.24,
                   "liquidity": 0.10, "macd": 0.07, "bollinger": 0.06, "capital_flow": 0.07},
    "D_risk_only": {"trend": 0.20, "momentum": 0.20, "rsi": 0.12, "risk_control": 0.25,
                    "liquidity": 0.08, "macd": 0.06, "bollinger": 0.05, "capital_flow": 0.04},
}


def evaluate(by_session: dict, weights: dict, top_n: int) -> np.ndarray:
    """逐期取 top_n，返回每期的平均超额。

    基准必须与组合侧同一统计量。组合侧是等权持有 → 用均值，基准就得是全市场**均值**
    （= 随机买 20 只的期望），不能拿中位当基准：个股收益右偏，全市场均值比中位高
    约 0.8pp/期（T+5，2025-2026 实测），混用会凭空造出这么多"超额"。
    """
    out = []
    for s in sorted(by_session):
        items = by_session[s]
        if len(items) < 200:
            continue
        rets = np.array([r for _, r in items], dtype=float)
        bench = np.mean(rets)
        scores = np.array([min(100.0, max(0.0, sum(v[f] * weights[f] for f in FACTORS)))
                           for v, _ in items], dtype=float)
        idx = np.argsort(-scores)[:top_n]
        out.append(float(np.mean(rets[idx]) - bench))
    return np.array(out, dtype=float)


def stats(arr: np.ndarray) -> dict:
    n = arr.size
    if n < 3:
        return {}
    m, sd = float(arr.mean()), float(arr.std(ddof=1))
    return {"avg_excess_pp": round(m * 100, 3),
            "median_excess_pp": round(float(np.median(arr)) * 100, 3),
            "win_rate": round(float((arr > 0).mean()) * 100, 1),
            "t": round(m / sd * np.sqrt(n), 2) if sd > 0 else None, "n": n}


def paired(base: np.ndarray, variant: np.ndarray) -> dict:
    d = variant - base
    sd = float(d.std(ddof=1))
    m = float(d.mean())
    return {"delta_pp": round(m * 100, 3),
            "t_paired": round(m / sd * np.sqrt(d.size), 2) if sd > 0 else None,
            "variant_better_pct": round(float((d > 0).mean()) * 100, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--anchor", default="2026-07-25")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--workers", type=int, default=9)
    a = ap.parse_args()

    by_session = collect(a.db, a.anchor, a.months, a.step, a.workers)
    keys = sorted(by_session)
    half = len(keys) // 2
    splits = {"ALL": keys, "TRAIN(front half)": keys[:half], "TEST(back half)": keys[half:]}

    out = {"anchor": a.anchor, "months": a.months, "step": a.step, "top_n": a.top_n, "splits": {}}
    for label, ks in splits.items():
        sub = {k: by_session[k] for k in ks}
        series = {name: evaluate(sub, w, a.top_n) for name, w in SCHEMES.items()}
        base = series["A_baseline"]
        block = {}
        for name, arr in series.items():
            e = stats(arr)
            if name != "A_baseline":
                e["vs_baseline"] = paired(base, arr)
            block[name] = e
        out["splits"][label] = block
        print(f"\n===== {label}  ({len(base)} periods) =====")
        for name, e in block.items():
            vs = e.get("vs_baseline")
            tail = (f"   d={vs['delta_pp']:+.3f}pp t_paired={vs['t_paired']:+.2f} "
                    f"better={vs['variant_better_pct']}%") if vs else ""
            print(f"  {name:<15} excess={e['avg_excess_pp']:+.3f}pp median={e['median_excess_pp']:+.3f}pp "
                  f"win={e['win_rate']}% t={e['t']}{tail}")

    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, f"weight_ab_{a.anchor}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2, default=float)
    print(f"\n-> {path}")


if __name__ == "__main__":
    main()
