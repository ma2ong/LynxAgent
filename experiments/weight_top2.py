"""改智选综合分的权重 / 规则，能不能让「只买前 2 名」更准（2026-10-10）。

7 月做过 top-20 的权重搜索（weight_ab / median_ab）：没有一组权重在样本外稳定好过现行。
之后风格翻转（7 月起强者恒强失效），Allen 要求按「只挑一两只」的口径重新搜。

口径（看结果前定死）：
- 样本：anchor 2026-10-09 往前 24 个月，每 3 个交易日一期（约 160 期，含 7 月后的新风格）；
  候选与因子分同 factor_scores（生产 8 因子的逐点复刻）。
- 每期按 Σ 权重×因子分 取前 2 名，可选「当日涨幅上限」闸门（无 / 9% / 5% / 0%）；
  次日开盘买 → T+5 收盘（可成交口径），对当期全部候选均值。
- 搜索：Dirichlet 随机权重 × 闸门，每次 3000 组。在训练段挑「前 2 名平均超额」最好的一组，到测试段看。
  两种切分（前 60% 训练→后 40% 测试；反过来）× 5 个种子 = 10 次。
- 判定：10 次里样本外前 2 名超额都高于现行权重、且配对 t 的均值 ≥2，才算「改权重有用」。
- 另有 8 套人工设计的方案（SCHEMES，看结果前写定）逐套对比：全段、前 60%、后 40%（7 月后新风格）分开报，
  前 2 名与前 20 名都报。人工方案也是 8 选 1，最好那套要两段都赢现行才算数。

用法：python experiments/snapshot_db.py && python experiments/weight_top2.py
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

from factor_scores import DEFAULT_DB, FACTORS, collect
from median_ab import BASELINE

GATES = (None, 0.09, 0.05, 0.0)
_F = lambda **kw: np.array([kw.get(f, 0.0) for f in FACTORS])  # noqa: E731
# 人工方案：(权重, 当日涨幅上限)。思路各不相同，看结果前写定
SCHEMES = {
    "A 现行": (None, None),
    "B 现行+不追涨(当日≤5%)": (None, 0.05),
    "C 等权": (_F(**{f: 1 for f in FACTORS}), None),
    "D 趋势动量(追强)": (_F(trend=0.5, momentum=0.5), None),
    "E 稳健风控": (_F(risk_control=0.4, trend=0.2, liquidity=0.2, momentum=0.1, rsi=0.1), 0.0),
    "F 资金驱动": (_F(capital_flow=0.4, liquidity=0.3, momentum=0.15, trend=0.15), None),
    "G 技术形态": (_F(macd=0.35, bollinger=0.35, trend=0.3), None),
    "H 低位反转(不追)": (_F(rsi=0.35, risk_control=0.35, bollinger=0.3), 0.0),
}
TOP = 2


def pack(by_session: dict) -> list:
    out = []
    for s in sorted(by_session):
        items = by_session[s]
        if len(items) < 200:
            continue
        F = np.array([[v[f] for f in FACTORS] for v, _ in items], dtype=float)
        r = np.array([v["ret_open"] for v, _ in items], dtype=float)
        r1 = np.array([v["ret_1d"] for v, _ in items], dtype=float)
        out.append((s, F, r - r.mean(), r1))
    return out


def run(packed: list, w: np.ndarray, gate) -> np.ndarray:
    """每期前 2 名的平均超额（期级序列）。"""
    res = []
    for _, F, ex, r1 in packed:
        sc = np.clip(F @ w, 0, 100)
        if gate is not None:
            sc = np.where(r1 <= gate, sc, -1)
        top = np.argsort(-sc)[:TOP]
        res.append(ex[top].mean())
    return np.array(res)


def tstat(x: np.ndarray) -> float:
    return float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else float("nan")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--anchor", default="2026-10-09")
    ap.add_argument("--months", type=int, default=24)
    ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--n", type=int, default=3000)
    a = ap.parse_args()
    import os
    import pickle
    pk = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", f"top2_{a.anchor}_{a.months}m.pkl")
    if os.path.exists(pk):
        packed = pickle.load(open(pk, "rb"))
    else:
        packed = pack(collect(a.db, a.anchor, a.months, a.step, a.workers))
        pickle.dump(packed, open(pk, "wb"))
    base_w = np.array([BASELINE[f] for f in FACTORS])
    print(f"\n{len(packed)} 期 {packed[0][0]} ~ {packed[-1][0]}")

    base = run(packed, base_w, None)
    hit = lambda x: f"{x.mean()*100:+.2f}pp 期胜率 {(x > 0).mean():.0%} t={tstat(x):+.2f}"  # noqa: E731
    print(f"现行权重 前2名：{hit(base)}")
    half = len(packed) * 6 // 10
    print(f"  前 60%：{hit(base[:half])}  |  后 40%：{hit(base[half:])}")

    global TOP
    print("\n人工方案对比（次日开盘买→T+5 收盘，对全部候选均值；期胜率 = 这一期挑的票平均跑赢的比例）")
    for top in (2, 20):
        TOP = top
        print(f"-- 前 {top} 名 --")
        for name, (w, gate) in SCHEMES.items():
            x = run(packed, base_w if w is None else w / w.sum(), gate)
            print(f"{name:16s} 全段 {hit(x)} | 前60% {x[:half].mean()*100:+.2f} | 后40% {x[half:].mean()*100:+.2f}")
    TOP = 2

    rows = []
    for seed in range(5):
        rng = np.random.default_rng(seed)
        cands = [(rng.dirichlet(np.ones(len(FACTORS))), GATES[rng.integers(len(GATES))]) for _ in range(a.n)]
        for split in ("前→后", "后→前"):
            tr, te = (packed[:half], packed[half:]) if split == "前→后" else (packed[half:], packed[:half])
            best = max(cands, key=lambda c: run(tr, *c).mean())
            out, ref = run(te, *best), run(te, base_w, None)
            d = out - ref
            rows.append((seed, split, best, out.mean(), ref.mean(), tstat(d)))
            print(f"种子{seed} {split}：训练段最优 → 测试段前2名 {out.mean()*100:+.2f}pp vs 现行 {ref.mean()*100:+.2f}pp "
                  f"（配对差 t={tstat(d):+.2f}）闸门={best[1]} 权重="
                  + ",".join(f"{f}:{x:.2f}" for f, x in zip(FACTORS, best[0])), flush=True)
    better = sum(r[3] > r[4] for r in rows)
    print(f"\n样本外好过现行：{better}/{len(rows)} 次；配对 t 均值 {np.nanmean([r[5] for r in rows]):+.2f}"
          f" → {'改权重有用' if better == len(rows) and np.nanmean([r[5] for r in rows]) >= 2 else '改权重没用（搜出来的是过拟合）'}")


if __name__ == "__main__":
    main()
