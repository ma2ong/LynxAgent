"""规则审计尺子：一条选股规则到底有没有独立 alpha。

为什么要有这个文件
------------------
过去每次问「这条规则行不行」，都是临时写一段脚本、算个平均超额和胜率，然后凭
t 值拍板。这套做法已经连续把我们坑过几次：

- 权重 A/B、强势池、龙虎榜、缩量埋伏、主升浪，全是「看着不错」但复查后为负；
- 「超额 100% 来自右尾」——平均值为正只是被少数几只大涨股拉起来的；
- 「+0.823pp 只是唯一为正的那个 12 个月窗口」——换个跨度就翻号；
- 「t=2.0 是多重检验产物」——横竖切十几刀总能切出一个显著。

这些坑各自对应一项本文件固化下来的检验。**任何新规则上线前必须过这七关**，
过不了就不进排序，最多当观察标记。

七关（全部同时满足才算通过）
----------------------------
1. 样本量        ≥ MIN_SAMPLES 笔，且 ≥ MIN_CLUSTERS 个交易日
2. 超额为正      平均超额 > 0（相对全市场中位）
3. 匹配对照增量  > 0，且按交易日聚类的 95% 置信区间下沿 > 0
4. 右尾稳健      去掉最好的 5% 之后平均超额仍 > 0
5. 时间稳定      ≥ MIN_STABLE_YEARS 个年份方向一致
6. 多重检验      Holm 校正后 p < 0.05
7. 扣摩擦为正    平均超额 − FRICTION_PP > 0

第 3 关是整套东西的核心，也是我们一直缺的那把尺子
------------------------------------------------
以前的基准是「全市场中位」。问题在于它没有控制个股自身的状态：深跌 30% 的票本来
就比全市场反弹得多，拿全市场中位当基准，会把这份 beta 记成规则的 alpha。匹配对照
把每一笔信号跟「同一天、同样跌幅档、同样流动性档、但没触发信号」的股票比，答的是
「在同样处境的票里，这条规则挑出来的那些是不是更好」——这才是规则自己的贡献。

用法
----
    python experiments/snapshot_db.py            # 先做快照，实验绝不碰生产库
    python experiments/rule_audit.py --list
    python experiments/rule_audit.py --rule chase20 --rule deep_drop --horizon 5
    python experiments/rule_audit.py --pool smart --pool strength --horizon 5

一次跑多条规则时，Holm 校正按本次提交的全部规则一起算——这正是它存在的意义，
分多次跑再挑好看的那条，等于自己把校正绕过去了。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

DEFAULT_DB = os.path.join(HERE, ".snapshot.sqlite")
RESULTS = os.path.join(HERE, "results")

# 通关阈值。放在最上面且不许在看到结果之后再改 —— 先定规则再统计，
# 否则「调到显著为止」是必然结局。
MIN_SAMPLES = 200
MIN_CLUSTERS = 30          # 交易日数：200 笔全挤在 3 天里，等于只有 3 个独立观测
MIN_STABLE_YEARS = 3
TAIL_DROP = 0.05           # 去掉最好的 5%
ALPHA = 0.05
# 单次往返摩擦：佣金 0.06% + 过户 0.002% + 印花税 0.05% + 买卖价差 0.20%
FRICTION_PP = 0.312

# 候选过滤：太小/太贵/停牌的票进来只会制造无法成交的漂亮数字
MIN_AMOUNT = 3e7
MIN_PRICE = 2.0

# 匹配对照的分层维度。只用「进场前就能知道」的量，否则就是未来函数。
PRIOR_BUCKETS = [-100, -20, -10, -3, 3, 10, 20, 100]   # 近20日涨幅分档(%)
AMOUNT_BUCKETS = [0, 1e8, 3e8, 1e9, 1e18]              # 当日成交额分档(元)


# --------------------------------------------------------------------------
# 面板：一次性把全市场日线摊成一张长表，规则写成向量化谓词
# --------------------------------------------------------------------------
def build_panel(db: str, since: str, horizon: int) -> pd.DataFrame:
    """长表：每行 = 一只票在某个交易日的状态 + 它未来 horizon 日的超额。

    只保留 amount>0 的真实 bar（跳过盘中占位 bar）。所有 prior_* 特征都严格用
    截至当日的数据，fwd_* 才用未来 —— 面板里同时存在这两类列，写规则时**只能**
    读 prior_*，读到 fwd_* 就是未来函数。
    """
    conn = sqlite3.connect(db, timeout=60)
    try:
        df = pd.read_sql_query(
            "SELECT symbol, date, open, high, low, close, amount FROM daily_kline "
            "WHERE amount > 0 AND date >= ? ORDER BY symbol, date",
            conn, params=(since,))
    finally:
        conn.close()
    if df.empty:
        raise SystemExit("快照库里没有日线数据，先跑 experiments/snapshot_db.py")

    g = df.groupby("symbol", sort=False)["close"]
    df["prior_ret20"] = (df["close"] / g.shift(20) - 1) * 100
    df["prior_ret10"] = (df["close"] / g.shift(10) - 1) * 100
    df["fwd_ret"] = (g.shift(-horizon) / df["close"] - 1) * 100
    # 距近 20 日最高价（0 = 就在高点上，负数 = 低于高点）
    hi20 = g.transform(lambda s: s.rolling(20, min_periods=20).max())
    df["dist_high20"] = (df["close"] / hi20 - 1) * 100
    vma20 = df.groupby("symbol", sort=False)["amount"].transform(
        lambda s: s.rolling(20, min_periods=20).mean())
    df["vol_ratio"] = df["amount"] / vma20
    # 当日 K 线收盘位置：1=收在最高，0=收在最低
    span = (df["high"] - df["low"]).replace(0, np.nan)
    df["clv"] = ((df["close"] - df["low"]) / span).fillna(0.5)

    # 基准：同一段区间的全市场中位收益。超额 = 个股 − 当期全市场中位。
    bench = df.groupby("date")["fwd_ret"].median().rename("bench")
    df = df.join(bench, on="date")
    df["fwd_excess"] = df["fwd_ret"] - df["bench"]

    df = df[df["fwd_excess"].notna()]
    df = df[(df["amount"] >= MIN_AMOUNT) & (df["close"] >= MIN_PRICE)]
    # 分层标签，供匹配对照使用
    df["b_prior"] = pd.cut(df["prior_ret20"], PRIOR_BUCKETS, labels=False)
    df["b_amount"] = pd.cut(df["amount"], AMOUNT_BUCKETS, labels=False)
    df = df[df["b_prior"].notna() & df["b_amount"].notna()]
    df["year"] = df["date"].str.slice(0, 4)
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------
# 统计件
# --------------------------------------------------------------------------
def _cluster_stats(values: list[float], clusters: list[str]) -> tuple[float, float, float, int]:
    """按交易日聚类的均值、t 值、双侧 p、聚类数。

    绝不用票级 n 做 t 检验：同一天入选的二十只票共享当天的市场环境，
    它们不是二十个独立观测。聚类后 n 是交易日数，t 会诚实地小很多。
    """
    by = defaultdict(list)
    for v, c in zip(values, clusters):
        by[c].append(v)
    means = [statistics.mean(v) for v in by.values()]
    n = len(means)
    if n < 2:
        return (statistics.mean(means) if means else 0.0), 0.0, 1.0, n
    m = statistics.mean(means)
    sd = statistics.stdev(means)
    if sd == 0:
        return m, 0.0, 1.0, n
    t = m / (sd / math.sqrt(n))
    # 正态近似即可：n 是交易日数，通常上百
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    return m, t, p, n


def _ci95(values: list[float], clusters: list[str]) -> tuple[float, float]:
    by = defaultdict(list)
    for v, c in zip(values, clusters):
        by[c].append(v)
    means = [statistics.mean(v) for v in by.values()]
    n = len(means)
    if n < 2:
        return (float("nan"), float("nan"))
    m, sd = statistics.mean(means), statistics.stdev(means)
    half = 1.96 * sd / math.sqrt(n)
    return m - half, m + half


def matched_increment(panel: pd.DataFrame, mask: pd.Series) -> tuple[list[float], list[float], list[str]]:
    """每笔信号相对「同日同档未触发信号」的增量。

    返回 (超额增量, 方向增量, 交易日)。同一层里没有对照票的信号直接丢弃 ——
    拿全市场当对照会把 beta 混进来，那正是这个函数要避免的事。
    """
    sig = panel[mask]
    if sig.empty:
        return [], [], []
    # 每层的对照均值（含信号票会自我污染，故按层减去信号自身）
    key = ["date", "b_prior", "b_amount"]
    agg = panel.groupby(key, observed=True)["fwd_excess"].agg(["sum", "count"])
    sig_agg = sig.groupby(key, observed=True)["fwd_excess"].agg(["sum", "count"])
    ctl = agg.join(sig_agg, rsuffix="_s", how="left").fillna(0.0)
    ctl["ctl_sum"] = ctl["sum"] - ctl["sum_s"]
    ctl["ctl_n"] = ctl["count"] - ctl["count_s"]
    ctl["ctl_mean"] = np.where(ctl["ctl_n"] > 0, ctl["ctl_sum"] / ctl["ctl_n"].replace(0, np.nan), np.nan)
    # 方向口径（胜率）同样要有对照
    win = panel.assign(w=(panel["fwd_excess"] > 0).astype(float))
    wagg = win.groupby(key, observed=True)["w"].agg(["sum", "count"])
    wsig = win[mask].groupby(key, observed=True)["w"].agg(["sum", "count"])
    wctl = wagg.join(wsig, rsuffix="_s", how="left").fillna(0.0)
    wctl["ctl_n"] = wctl["count"] - wctl["count_s"]
    wctl["ctl_win"] = np.where(
        wctl["ctl_n"] > 0, (wctl["sum"] - wctl["sum_s"]) / wctl["ctl_n"].replace(0, np.nan), np.nan)

    joined = sig.join(ctl["ctl_mean"], on=key).join(wctl["ctl_win"], on=key)
    joined = joined[joined["ctl_mean"].notna() & joined["ctl_win"].notna()]
    inc_ret = (joined["fwd_excess"] - joined["ctl_mean"]).tolist()
    inc_win = ((joined["fwd_excess"] > 0).astype(float) - joined["ctl_win"]).tolist()
    return inc_ret, inc_win, joined["date"].tolist()


def audit_one(panel: pd.DataFrame, mask: pd.Series, name: str, horizon: int) -> dict:
    sig = panel[mask]
    ex = sig["fwd_excess"].tolist()
    days = sig["date"].tolist()
    out: dict = {"rule": name, "horizon": horizon, "samples": len(ex)}
    if not ex:
        out["verdict"] = "无样本"
        return out

    mean_ex, t_ex, p_ex, n_clu = _cluster_stats(ex, days)
    pick_mean = statistics.mean(ex)
    out.update({
        "clusters": n_clu,
        "avg_excess": round(mean_ex, 3),            # 按交易日等权，推断用这个
        "avg_excess_by_pick": round(pick_mean, 3),  # 按票等权
        "median_excess": round(statistics.median(ex), 3),
        "win_rate": round(sum(1 for v in ex if v > 0) / len(ex), 4),
        "t": round(t_ex, 2),
        "p_raw": p_ex,
        "net_of_friction": round(mean_ex - FRICTION_PP, 3),
    })
    # 两个口径反号 = 好日子票少、坏日子票多（或反过来）。这本身就是结论的一部分，
    # 只报其中一个会让人误以为规则稳，所以显式标出来。
    out["weighting_conflict"] = (mean_ex > 0) != (pick_mean > 0)

    # 关 3：匹配对照增量
    inc_ret, inc_win, inc_days = matched_increment(panel, mask)
    if inc_ret:
        m_inc, _, p_inc, _ = _cluster_stats(inc_ret, inc_days)
        lo, hi = _ci95(inc_ret, inc_days)
        w_inc, _, _, _ = _cluster_stats(inc_win, inc_days)
        wlo, _ = _ci95(inc_win, inc_days)
        out.update({
            "matched_n": len(inc_ret),
            "inc_excess": round(m_inc, 3), "inc_ci_lo": round(lo, 3), "inc_ci_hi": round(hi, 3),
            "inc_win": round(w_inc, 4), "inc_win_ci_lo": round(wlo, 4),
            "p_inc": p_inc,
        })
    else:
        out.update({"matched_n": 0, "inc_excess": None, "inc_ci_lo": None, "p_inc": 1.0})

    # 关 4：右尾稳健 —— 砍掉最好的 5% 还剩什么
    cut = sorted(ex)[: max(1, int(len(ex) * (1 - TAIL_DROP)))]
    out["avg_excess_ex_tail"] = round(statistics.mean(cut), 3)

    # 关 5：时间稳定性。与表头同口径（按交易日等权），否则分年数字跟总数字反号没法对账。
    by_year: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for v, y, d in zip(ex, sig["year"].tolist(), days):
        by_year[y].append((v, d))
    years = {}
    for y, pairs in sorted(by_year.items()):
        if len(pairs) < 20:
            continue
        ym, _, _, _ = _cluster_stats([v for v, _ in pairs], [d for _, d in pairs])
        years[y] = round(ym, 3)
    out["by_year"] = years
    out["stable_years"] = sum(1 for v in years.values() if v > 0)
    return out


def holm(results: list[dict]) -> None:
    """Holm 校正：按 p 升序，第 i 个的阈值是 ALPHA/(m-i)。就地写回 p_holm/pass_holm。

    校正必须覆盖本次提交的全部规则。分几次跑再挑最好看的那条报出来，
    等于把校正绕过去了 —— 这正是 t=2.0 那次的教训。
    """
    scored = [r for r in results if r.get("p_inc") is not None]
    scored.sort(key=lambda r: r["p_inc"])
    m = len(scored)
    prev_reject = True
    for i, r in enumerate(scored):
        thr = ALPHA / (m - i)
        reject = prev_reject and r["p_inc"] < thr
        r["p_holm_threshold"] = round(thr, 5)
        r["pass_holm"] = bool(reject)
        prev_reject = reject


def verdict(r: dict) -> dict:
    """七关逐关判定。没通过的把原因写清楚，别只给一个「未通过」。"""
    gates = {
        "样本量": r.get("samples", 0) >= MIN_SAMPLES and r.get("clusters", 0) >= MIN_CLUSTERS,
        "超额为正": (r.get("avg_excess") or 0) > 0,
        "对照增量为正": (r.get("inc_ci_lo") is not None and r["inc_ci_lo"] > 0),
        "去右尾仍为正": (r.get("avg_excess_ex_tail") or 0) > 0,
        "时间稳定": r.get("stable_years", 0) >= MIN_STABLE_YEARS,
        "多重检验": bool(r.get("pass_holm")),
        "扣摩擦为正": (r.get("net_of_friction") or 0) > 0,
    }
    r["gates"] = gates
    r["passed"] = all(gates.values())
    r["failed_gates"] = [k for k, v in gates.items() if not v]
    return r


# --------------------------------------------------------------------------
# 内置规则：只允许读 prior_* 与当日量价，读 fwd_* 就是未来函数
# --------------------------------------------------------------------------
RULES = {
    "chase20": ("近20日已涨≥25%（追高）", lambda d: d["prior_ret20"] >= 25),
    "near_high": ("贴着20日高点（距高点≤3%）", lambda d: d["dist_high20"] >= -3),
    "chase_near_high": ("近20日≥25% 且贴着高点", lambda d: (d["prior_ret20"] >= 25) & (d["dist_high20"] >= -3)),
    "deep_drop": ("近20日跌≥20%（深跌）", lambda d: d["prior_ret20"] <= -20),
    "deep_drop_vol": ("深跌 + 放量强收", lambda d: (d["prior_ret20"] <= -20) & (d["vol_ratio"] >= 2.5) & (d["clv"] >= 0.7)),
    "consolidate": ("长期强、近期消化（60日强/20日温和/离高点≥8%）",
                    lambda d: (d["prior_ret20"].between(-5, 10)) & (d["dist_high20"] <= -8)),
}


def pool_mask(panel: pd.DataFrame, db: str, pool: str) -> pd.Series:
    """把线上留痕当成一条规则来审：(pool, 日期, 代码) 命中即为信号。"""
    conn = sqlite3.connect(db, timeout=60)
    try:
        rows = conn.execute(
            "SELECT pick_date, symbol FROM picks_history WHERE pool=?", (pool,)).fetchall()
    finally:
        conn.close()
    keys = {(str(d), str(s).zfill(6)) for d, s in rows}
    return pd.Series([(d, s) in keys for d, s in zip(panel["date"], panel["symbol"])],
                     index=panel.index)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--since", default="2019-01-01", help="面板起始日；越长时间稳定性越可信")
    ap.add_argument("--horizon", type=int, default=5, help="持有交易日数（T+N）")
    ap.add_argument("--rule", action="append", default=[], help="内置规则名，可重复")
    ap.add_argument("--pool", action="append", default=[], help="按留痕池审计，可重复")
    ap.add_argument("--list", action="store_true", help="列出内置规则")
    args = ap.parse_args()

    if args.list:
        for k, (desc, _) in RULES.items():
            print(f"  {k:18s} {desc}")
        return
    if not args.rule and not args.pool:
        ap.error("至少给一个 --rule 或 --pool")

    print(f"载入面板 since={args.since} horizon=T+{args.horizon} …")
    panel = build_panel(args.db, args.since, args.horizon)
    print(f"面板 {len(panel):,} 行 · {panel['symbol'].nunique()} 只 · "
          f"{panel['date'].nunique()} 个交易日")

    results = []
    for name in args.rule:
        if name not in RULES:
            raise SystemExit(f"未知规则 {name}，用 --list 看可用的")
        desc, fn = RULES[name]
        r = audit_one(panel, fn(panel), name, args.horizon)
        r["desc"] = desc
        results.append(r)
    for pool in args.pool:
        r = audit_one(panel, pool_mask(panel, args.db, pool), f"pool:{pool}", args.horizon)
        r["desc"] = f"线上 {pool} 池的真实留痕"
        results.append(r)

    holm(results)
    for r in results:
        verdict(r)

    os.makedirs(RESULTS, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = os.path.join(RESULTS, f"rule-audit-{stamp}.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"since": args.since, "horizon": args.horizon,
                   "thresholds": {"MIN_SAMPLES": MIN_SAMPLES, "MIN_CLUSTERS": MIN_CLUSTERS,
                                  "MIN_STABLE_YEARS": MIN_STABLE_YEARS, "TAIL_DROP": TAIL_DROP,
                                  "FRICTION_PP": FRICTION_PP, "ALPHA": ALPHA},
                   "results": results}, fh, ensure_ascii=False, indent=2)

    print("\n超额按交易日等权；「票权」是按票等权，带 ! 表示两者反号"
          "（好日子票少、坏日子票多，或反过来）。")
    print(f"{'规则':22s}{'样本':>7s}{'日数':>6s}{'超额':>8s}{'票权':>8s}{'去右尾':>9s}"
          f"{'对照增量':>10s}{'CI下沿':>9s}{'稳定年':>7s}  判定")
    for r in results:
        if not r.get("samples"):
            print(f"{r['rule']:22s}{'无样本':>7s}")
            continue
        mark = "通过" if r["passed"] else "未过: " + "/".join(r["failed_gates"][:2])
        flag = "!" if r.get("weighting_conflict") else " "
        print(f"{r['rule']:22s}{r['samples']:>7d}{r['clusters']:>6d}{r['avg_excess']:>8.2f}"
              f"{r['avg_excess_by_pick']:>7.2f}{flag}{r['avg_excess_ex_tail']:>9.2f}"
              f"{(r['inc_excess'] if r['inc_excess'] is not None else float('nan')):>10.2f}"
              f"{(r['inc_ci_lo'] if r['inc_ci_lo'] is not None else float('nan')):>9.2f}"
              f"{r['stable_years']:>7d}  {mark}")
    print(f"\n明细 -> {out_path}")


if __name__ == "__main__":
    main()
