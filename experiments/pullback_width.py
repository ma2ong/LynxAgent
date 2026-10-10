"""回调优选的挑选范围：前 20 / 30 / 40 / 50（2026-10-10）。

回放（smart_full_lab 复原，每期结构分前 50 经共振/风险排序）里，从前 N 名挑离 20 日高点最远的 2 只，
对「前 10 平均」（用户看到的名单）与对全市场的超额，T+5。线上留痕只记到第 20 名，20 名以外没有线上样本可验。
"""
import pickle
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
_, top = pickle.load(open(ROOT / "experiments/.cache/smart_full_2026-10-09_24m.pkl", "rb"))
days = sorted(top.d.unique())
cut = days[len(days) * 6 // 10]
for n in (10, 20, 30, 40, 50):
    rel, ab, front, back = [], [], [], []
    for d, x in top.groupby("d"):
        x = x[x.risk.fillna(0) < 2]
        ranked = x.assign(k=x.composite + x.bonus.fillna(0)).sort_values("k", ascending=False)
        if len(ranked) < n:
            continue
        pick = ranked.head(n).nsmallest(2, "dist_high20").ex.mean()
        r = pick - ranked.head(10).ex.mean()
        rel.append(r); ab.append(pick); (front if d < cut else back).append(r)
    rel = np.array(rel)
    print(f"前{n:2d}名里挑：对前10平均 {rel.mean()*100:+.2f}pp t={rel.mean()/rel.std(ddof=1)*np.sqrt(len(rel)):+.2f} "
          f"好过的期数 {(rel > 0).mean():.0%} | 对全市场 {np.mean(ab)*100:+.2f}pp | 前60% {np.mean(front)*100:+.2f} 后40% {np.mean(back)*100:+.2f}（{len(rel)}期）")
