"""追涨风格失灵时，小市值组合是否更好（2026-09-29）。

问题（事先定好）：智选页「追涨风格正在失灵」红条的判据 = 上一期（已兑现 10 个交易日）结构分前 20
跑输候选均值。红条亮着的周里，小市值组合相对中小板的超额，是否明显好于红条不亮的周？
若是，红条里引导用户看小市值组合才有依据；若不是，不加这个入口。

输入：structure_decay.py --long 与 alpha70_replica.py 落盘的序列（先跑那两个）。
"""
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "experiments" / "results"


def tstat(x):
    x = x.dropna()
    return x.mean() / (x.std(ddof=1) / math.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else float("nan")


def main():
    s10 = pd.read_csv(R / "structure_top20_c10_daily.csv", index_col=0)["top20_c10_excess"]
    s10.index = s10.index.astype(str)
    days = list(s10.index)
    w = pd.read_csv(R / "alpha70_weekly.csv")
    w["sc_ex"] = (w["ret"] - w["pool"]) * 100
    rows = []
    for _, r in w.iterrows():
        d = str(r["d"])
        prior = [x for x in days if x < d]
        if len(prior) < 11:
            continue
        known_day = prior[-11]            # 该期的 10 日持有到 d 前一天已兑现
        now_day = prior[-1] if d not in s10.index else d
        rows.append({"d": d, "known": s10[known_day], "struct_next": s10.get(d), "sc_ex": r["sc_ex"]})
    t = pd.DataFrame(rows).dropna(subset=["known"])
    t["failing"] = t["known"] < 0
    print(f"样本 {len(t)} 周（{t['d'].iloc[0]} ~ {t['d'].iloc[-1]}）")
    for flag, name in ((True, "红条亮（上期跑输）"), (False, "红条不亮")):
        x = t[t["failing"] == flag]
        print(f"  {name:12s} n={len(x):3d}  小市值周超额 {x['sc_ex'].mean():+.2f}pp (t {tstat(x['sc_ex']):+.2f}) "
              f"胜率 {(x['sc_ex'] > 0).mean():.0%} | 同期智选下一期10日超额 {x['struct_next'].mean():+.2f}pp")
    diff = t[t["failing"]]["sc_ex"].mean() - t[~t["failing"]]["sc_ex"].mean()
    print(f"  差（亮−不亮）{diff:+.2f}pp/周")
    t["yr"] = t["d"].str[:4]
    by = t.groupby(["yr", "failing"])["sc_ex"].mean().unstack().rename(columns={True: "亮", False: "不亮"})
    print("逐年小市值周超额\n" + by.round(2).to_string())
    m = t.assign(m=t["d"].str[:7]).groupby("m")[["sc_ex", "struct_next"]].mean()
    print(f"逐月相关（小市值超额 vs 智选超额）{m.corr().iloc[0, 1]:+.2f}（n={len(m)}）")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
