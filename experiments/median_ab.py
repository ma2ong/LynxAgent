"""票级中位裁决：优化「买进去的每一只」而不是「一篮子的平均」。

为什么另起一个脚本而不是改 weight_ab：口径不同。`weight_ab.evaluate` 每期先
`mean(top_n 超额)` 再统计，所以它说的「胜率 69.4%」是**期级**的 —— 36 期里有多少期
篮子平均跑赢。但用户感知的是**票级**：买 20 只，几只跑赢大盘。回放报告里那个
「中位 −0.52pp、胜率 48%」正是票级数，它被期级均值完全掩盖了。

均值由右尾驱动（入选票 42.6% 当日已涨停），中位由主体驱动，两者大概率此消彼长。
本轮的裁决口径因此写死为：

    主指标：每期票级中位超额（跨期配对检验）
    副指标：票级胜率
    约束　：期级均值超额不得显著恶化

候选方案除权重外还允许**候选过滤**（追高闸门、风控闸门），因为要抬中位数，
砍掉右尾追高票比调权重直接得多。过滤会改变每期可选池，所以必须同时盯 avg_picks，
一个把半数期打到不足 top_n 的方案没有可比性。

    python experiments/snapshot_db.py
    python experiments/median_ab.py --anchor 2026-07-25 --top-n 20
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

# 每个方案 = 权重 + 可选闸门。闸门按剂量递进排列，看的是单调性：
# 一个真实的效应应该随阈值收紧而单调变化，只在某一个阈值上赢多半是噪声。
SCHEMES = {
    "A_baseline":   {"w": BASELINE},
    "B_chase_9":    {"w": BASELINE, "max_ret_1d": 0.09},
    "C_chase_5":    {"w": BASELINE, "max_ret_1d": 0.05},
    "D_chase_0":    {"w": BASELINE, "max_ret_1d": 0.00},
    "E_surge_5d":   {"w": BASELINE, "max_ret_5d": 0.20},
    "F_risk_gate":  {"w": BASELINE, "min_risk_control": 40.0},
    "G_combo":      {"w": BASELINE, "max_ret_1d": 0.05, "min_risk_control": 40.0},
}

MIN_PICKS = 5          # 一期可选票少于这个数就不计入，样本太薄没有意义
MIN_CANDIDATES = 200   # 与 weight_ab 一致：当期候选太少的会话跳过


def _eligible(vals: dict, spec: dict) -> bool:
    if "max_ret_1d" in spec and vals["ret_1d"] > spec["max_ret_1d"]:
        return False
    if "max_ret_5d" in spec and vals["ret_5d"] > spec["max_ret_5d"]:
        return False
    if "min_risk_control" in spec and vals["risk_control"] < spec["min_risk_control"]:
        return False
    return True


def evaluate(by_session: dict, spec: dict, top_n: int, caliber: str = "close") -> dict:
    """逐期选票，返回期级序列 + 汇总的逐票超额。

    caliber="close"：当日收盘买入 → T+5 收盘（研究口径，吃得到隔夜跳空）
    caliber="open" ：次日开盘买入 → T+5 收盘（可成交口径，与回放报告一致）
    """
    w = spec["w"]
    per_median, per_mean, per_win, per_count, pooled = [], [], [], [], []
    for s in sorted(by_session):
        items = by_session[s]
        if len(items) < MIN_CANDIDATES:
            continue
        if caliber == "open":
            rets = np.array([v["ret_open"] for v, _ in items], dtype=float)
        else:
            rets = np.array([r for _, r in items], dtype=float)
        med = np.median(rets)                       # 基准：当期全市场中位收益（同口径）
        keep = [i for i, (v, _) in enumerate(items) if _eligible(v, spec)]
        if len(keep) < MIN_PICKS:
            continue
        scores = np.array([min(100.0, max(0.0, sum(items[i][0][f] * w[f] for f in FACTORS)))
                           for i in keep], dtype=float)
        idx = np.array(keep)[np.argsort(-scores)[:top_n]]
        exc = rets[idx] - med
        per_median.append(float(np.median(exc)))
        per_mean.append(float(np.mean(exc)))
        per_win.append(float((exc > 0).mean()))
        per_count.append(len(idx))
        pooled.append(exc)
    return {"median": np.array(per_median), "mean": np.array(per_mean),
            "win": np.array(per_win), "picks": np.array(per_count, dtype=float),
            "pooled": np.concatenate(pooled) if pooled else np.array([])}


def stats(r: dict) -> dict:
    n = r["median"].size
    if n < 3:
        return {}
    p = r["pooled"]
    m, sd = float(r["mean"].mean()), float(r["mean"].std(ddof=1))
    return {
        # 主指标：每期票级中位超额，跨期平均
        "stock_median_pp": round(float(r["median"].mean()) * 100, 3),
        "stock_win_rate": round(float(r["win"].mean()) * 100, 1),
        # 约束项：原来的期级均值超额
        "period_mean_pp": round(m * 100, 3),
        "period_mean_t": round(m / sd * np.sqrt(n), 2) if sd > 0 else None,
        # 分布形状：右尾是不是唯一的收益来源
        "pooled_p10_pp": round(float(np.percentile(p, 10)) * 100, 2),
        "pooled_p90_pp": round(float(np.percentile(p, 90)) * 100, 2),
        "avg_picks": round(float(r["picks"].mean()), 1),
        "n_periods": n,
    }


def paired(base: np.ndarray, variant: np.ndarray) -> dict:
    """同轴逐期配对：把市场环境的共同波动消掉。"""
    n = min(base.size, variant.size)
    if n < 3 or base.size != variant.size:
        return {"note": "期数不等，过滤把某些期打没了，不做配对"}
    d = variant - base
    sd, m = float(d.std(ddof=1)), float(d.mean())
    return {"delta_pp": round(m * 100, 3),
            "t_paired": round(m / sd * np.sqrt(n), 2) if sd > 0 else None,
            "variant_better_pct": round(float((d > 0).mean()) * 100, 1)}


def _matrices(by_session: dict, keys: list) -> list:
    """把每期打包成 (因子矩阵, 收盘口径超额基准后的收益, 开盘口径收益)，供权重搜索复用。

    搜索要跑几百组权重，逐组再走一遍 Python 循环太慢；打成矩阵后一组权重只是一次
    X @ w。这一步不改变任何口径，纯粹是同样计算的向量化。
    """
    packed = []
    for s in keys:
        items = by_session[s]
        if len(items) < MIN_CANDIDATES:
            continue
        X = np.array([[v[f] for f in FACTORS] for v, _ in items], dtype=float)
        rc = np.array([r for _, r in items], dtype=float)
        ro = np.array([v["ret_open"] for v, _ in items], dtype=float)
        packed.append((X, rc - np.median(rc), ro - np.median(ro)))
    return packed


def _series(packed: list, w: np.ndarray, top_n: int, col: int) -> tuple:
    """给定权重，返回逐期的 (票级中位超额, 票级均值超额, 票级胜率)。col: 1=收盘 2=开盘"""
    meds, means, wins = [], [], []
    for row in packed:
        X, exc = row[0], row[col]
        idx = np.argpartition(-(X @ w), top_n)[:top_n]
        e = exc[idx]
        meds.append(np.median(e))
        means.append(np.mean(e))
        wins.append((e > 0).mean())
    return np.array(meds), np.array(means), np.array(wins)


def _search_once(train: list, test: list, top_n: int, n_iter: int, seed: int, col: int) -> dict:
    """一次随机搜索：在 train 上按票级中位数挑权重，在 test 上如实报告。"""
    base = np.array([BASELINE[f] for f in FACTORS], dtype=float)
    rng = np.random.default_rng(seed)
    best_w = base
    best_med = float(_series(train, base, top_n, col)[0].mean())
    for _ in range(n_iter):
        w = rng.dirichlet(np.ones(len(FACTORS)) * 1.5)
        med = float(_series(train, w, top_n, col)[0].mean())
        if med > best_med:
            best_w, best_med = w, med

    v_med, v_mean, v_win = _series(test, best_w, top_n, col)
    b_med, b_mean, b_win = _series(test, base, top_n, col)
    d = v_med - b_med
    sd = float(d.std(ddof=1))
    return {
        "seed": seed,
        "weights": {f: round(float(x), 4) for f, x in zip(FACTORS, best_w)},
        "test_median_pp": round(float(v_med.mean()) * 100, 3),
        "baseline_test_median_pp": round(float(b_med.mean()) * 100, 3),
        "delta_median_pp": round(float(d.mean()) * 100, 3),
        "t_paired": round(float(d.mean()) / sd * np.sqrt(d.size), 2) if sd > 0 else None,
        "test_mean_pp": round(float(v_mean.mean()) * 100, 3),
        "baseline_test_mean_pp": round(float(b_mean.mean()) * 100, 3),
        "test_win_rate": round(float(v_win.mean()) * 100, 1),
        "baseline_test_win_rate": round(float(b_win.mean()) * 100, 1),
    }


def search_weights(by_session: dict, keys: list, top_n: int, n_iter: int,
                   seeds=(11, 23, 37, 51, 67)) -> dict:
    """以「票级中位超额」为目标搜权重，多种子 × 双向切分。

    这是对「中位数提不动」的决定性检验：如果照着中位数直接搜出来的权重在样本外也失效，
    问题就不在权重，而在因子体系本身没有中位数方向的信息。

    单次搜索必然过拟合（400 组里挑最好的一组），所以只看**样本外**那一列；再用多个
    随机种子和正反两个切分方向去掉「碰巧挑到一组好的」的可能 —— 一个真实的效应应该
    在各个种子和两个方向上都为正，而不是只在某一组上。
    """
    half = len(keys) // 2
    front, back = _matrices(by_session, keys[:half]), _matrices(by_session, keys[half:])
    runs = {"forward(front->back)": [], "reverse(back->front)": []}
    for seed in seeds:
        runs["forward(front->back)"].append(_search_once(front, back, top_n, n_iter, seed, 1))
        runs["reverse(back->front)"].append(_search_once(back, front, top_n, n_iter, seed, 1))
    all_deltas = [r["delta_median_pp"] for rs in runs.values() for r in rs]
    return {"n_iter": n_iter, "seeds": list(seeds), "runs": runs,
            "delta_median_avg_pp": round(float(np.mean(all_deltas)), 3),
            "delta_median_positive_pct": round(float(np.mean([d > 0 for d in all_deltas])) * 100, 1)}


def profile_basket(by_session: dict, keys: list, top_n: int, col: int = 2) -> dict:
    """刻画一篮子的收益结构，给产品侧的说法提供依据（默认可成交口径）。

    要回答两个问题，都是「该怎么跟用户说」的前提：
      1. 池内名次有没有区分度 —— 如果第 1-5 名和第 16-20 名收益无差，
         那「只买排名最靠前的两只」就是没有依据的，必须劝阻。
      2. 「靠少数几只撑起来」是不是真的 —— 用「剔除每期最强的 1/2/3 只之后还剩多少」量化。
    """
    packed = _matrices(by_session, keys)
    w = np.array([BASELINE[f] for f in FACTORS], dtype=float)
    bucket = max(1, top_n // 4)
    buckets: dict = {}
    per_period, tail_counts, trimmed = [], [], {1: [], 2: [], 3: []}
    for X, exc_c, exc_o in packed:
        exc = exc_o if col == 2 else exc_c
        order = np.argsort(-(X @ w))[:top_n]      # 按分数排好序的名次
        e = exc[order]
        for b0 in range(0, top_n, bucket):
            buckets.setdefault(f"{b0 + 1}-{min(b0 + bucket, top_n)}", []).append(e[b0:b0 + bucket])
        per_period.append(float(e.mean()))
        tail_counts.append(int((e > 0.10).sum()))
        srt = np.sort(e)[::-1]
        for k in (1, 2, 3):
            trimmed[k].append(float(srt[k:].mean()))
    out = {"rank_buckets": {}, "n_periods": len(packed), "top_n": top_n}
    for label, chunks in buckets.items():
        v = np.concatenate(chunks)
        out["rank_buckets"][label] = {
            "median_pp": round(float(np.median(v)) * 100, 3),
            "mean_pp": round(float(v.mean()) * 100, 3),
            "win_rate": round(float((v > 0).mean()) * 100, 1),
        }
    tc = np.array(tail_counts, dtype=float)
    out["tail_stocks_per_period"] = {          # 每期超额 >+10pp 的「走出来」的票有几只
        "mean": round(float(tc.mean()), 2),
        "median": round(float(np.median(tc)), 1),
        "pct_periods_with_none": round(float((tc == 0).mean()) * 100, 1),
    }
    base_mean = float(np.mean(per_period))
    out["basket_mean_pp"] = round(base_mean * 100, 3)
    out["after_removing_best"] = {f"top{k}": round(float(np.mean(v)) * 100, 3)
                                  for k, v in trimmed.items()}
    out["subset_sim"] = _subset_sim(packed, w, top_n)
    return out


def _subset_sim(packed: list, w: np.ndarray, top_n: int, draws: int = 2000,
                seed: int = 5) -> dict:
    """只买名单里 k 只会怎样：从每期的 top_n 里随机抽 k 只，重复多次。

    名次无区分度（见 rank_buckets），所以「随机抽 k 只」就是用户任意挑 k 只的无偏刻画。
    期望收益不随 k 变，变的是**拿到那 1-2 只右尾票的概率**和**结果的离散度** ——
    这正是要讲给用户听的东西：买少了不是收益低，是变成抽奖。
    """
    rng = np.random.default_rng(seed)
    res = {}
    for k in (1, 2, 3, 5, 10, 20):
        if k > top_n:
            continue
        means, hits = [], []
        for X, _exc_c, exc_o in packed:
            e = exc_o[np.argsort(-(X @ w))[:top_n]]
            if k == top_n:
                picks = np.tile(np.arange(top_n), (draws, 1))
            else:
                picks = np.array([rng.choice(top_n, k, replace=False) for _ in range(draws)])
            sel = e[picks]
            means.append(sel.mean(axis=1))
            hits.append((sel > 0.10).any(axis=1))
        m = np.concatenate(means)
        h = np.concatenate(hits)
        res[f"k{k}"] = {
            "mean_pp": round(float(m.mean()) * 100, 2),
            "median_pp": round(float(np.median(m)) * 100, 2),
            "p_beat_market": round(float((m > 0).mean()) * 100, 1),
            "p_hit_tail": round(float(h.mean()) * 100, 1),
            "p10_pp": round(float(np.percentile(m, 10)) * 100, 2),
            "p90_pp": round(float(np.percentile(m, 90)) * 100, 2),
        }
    return res


def analyze_entry_drop(by_session: dict, keys: list, top_n: int) -> dict:
    """入选票在 T+1 已经大跌时，从那个价位买进去后面 5 天会怎样。

    产品实际口径：用户 T+1 盘中看到名单、按当时价格买入 —— 而回放买的是 T+1 开盘。
    两者在一只跳空低开又跌停的票上根本不是同一个入场价，所以「回放 +1.75pp」不能给
    这个场景背书。这里用 T+1 收盘近似盘中入场价，按用户看到的「今日涨跌幅」分档，
    看后续 HORIZON 根的超额。基准同口径：当期全市场同一入场口径的中位收益。

    这是「接刀还是捡便宜」的直接检验，不能靠直觉定。
    """
    edges = [(-1.0, -0.09), (-0.09, -0.05), (-0.05, -0.02), (-0.02, 0.0),
             (0.0, 0.03), (0.03, 1.0)]
    labels = ["≤-9%", "-9~-5%", "-5~-2%", "-2~0%", "0~+3%", ">+3%"]
    w = np.array([BASELINE[f] for f in FACTORS], dtype=float)
    buckets: dict = {lb: [] for lb in labels}
    kept_basket, all_basket = [], []
    for s in keys:
        items = by_session[s]
        if len(items) < MIN_CANDIDATES:
            continue
        rets = np.array([v["ret_entry_next_close"] for v, _ in items], dtype=float)
        moves = np.array([v["next_day_move"] for v, _ in items], dtype=float)
        med = np.median(rets)
        X = np.array([[v[f] for f in FACTORS] for v, _ in items], dtype=float)
        idx = np.argsort(-(X @ w))[:top_n]
        exc, mv = rets[idx] - med, moves[idx]
        all_basket.append(float(exc.mean()))
        keep = exc[mv > -0.05]                     # 剔掉「今日已跌超 5%」的票
        if keep.size:
            kept_basket.append(float(keep.mean()))
        for lb, (lo, hi) in zip(labels, edges):
            sel = exc[(mv > lo) & (mv <= hi)]
            if sel.size:
                buckets[lb].append(sel)
    out = {"buckets": {}, "n_periods": len(all_basket)}
    for lb in labels:
        if not buckets[lb]:
            continue
        v = np.concatenate(buckets[lb])
        out["buckets"][lb] = {
            "n": int(v.size),
            "mean_pp": round(float(v.mean()) * 100, 2),
            "median_pp": round(float(np.median(v)) * 100, 2),
            "win_rate": round(float((v > 0).mean()) * 100, 1),
        }
    a, k = np.array(all_basket), np.array(kept_basket)
    out["basket_all_pp"] = round(float(a.mean()) * 100, 3)
    out["basket_excl_big_drop_pp"] = round(float(k.mean()) * 100, 3)
    if a.size == k.size and a.size > 2:
        d = k - a
        sd = float(d.std(ddof=1))
        out["delta_pp"] = round(float(d.mean()) * 100, 3)
        out["t_paired"] = round(float(d.mean()) / sd * np.sqrt(d.size), 2) if sd > 0 else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--anchor", default="2026-07-25")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--workers", type=int, default=9)
    ap.add_argument("--search", type=int, default=0,
                    help="以票级中位数为目标随机搜权重的次数，0=不搜")
    ap.add_argument("--profile", action="store_true",
                    help="刻画一篮子的收益结构（名次区分度、右尾集中度），供产品侧引用")
    ap.add_argument("--entry-drop", action="store_true",
                    help="检验「入选票当日已大跌时买入」的后续表现（接刀还是捡便宜）")
    a = ap.parse_args()

    by_session = collect(a.db, a.anchor, a.months, a.step, a.workers)
    keys = sorted(by_session)
    half = len(keys) // 2
    splits = {"ALL": keys, "TRAIN(front half)": keys[:half], "TEST(back half)": keys[half:]}

    out = {"anchor": a.anchor, "months": a.months, "step": a.step, "top_n": a.top_n,
           "objective": "per-stock median excess (primary), win rate (secondary), "
                        "period mean excess (constraint)",
           "splits": {}}
    for label, ks in splits.items():
        sub = {k: by_session[k] for k in ks}
        out["splits"][label] = {}
        for caliber in ("close", "open"):
            series = {name: evaluate(sub, spec, a.top_n, caliber)
                      for name, spec in SCHEMES.items()}
            base = series["A_baseline"]
            block = {}
            for name, r in series.items():
                e = stats(r)
                if name != "A_baseline" and e:
                    e["vs_baseline_median"] = paired(base["median"], r["median"])
                    e["vs_baseline_period_mean"] = paired(base["mean"], r["mean"])
                block[name] = e
            out["splits"][label][caliber] = block
            tag = "收盘口径" if caliber == "close" else "可成交口径(次日开盘买入)"
            print(f"\n===== {label} / {tag}  ({stats(base).get('n_periods')} periods) =====")
            for name, e in block.items():
                if not e:
                    print(f"  {name:<14} (样本不足)")
                    continue
                vs = e.get("vs_baseline_median")
                tail = (f"  d_med={vs.get('delta_pp'):+.3f}pp t={vs.get('t_paired')}"
                        if vs and "delta_pp" in vs else "")
                print(f"  {name:<14} med={e['stock_median_pp']:+.3f}pp win={e['stock_win_rate']}% "
                      f"| mean={e['period_mean_pp']:+.3f}pp(t={e['period_mean_t']}) "
                      f"p10={e['pooled_p10_pp']} p90={e['pooled_p90_pp']} picks={e['avg_picks']}{tail}")

    if a.search:
        s = search_weights(by_session, keys, a.top_n, a.search)
        out["weight_search"] = s
        print(f"\n===== 以票级中位数为目标搜权重（每次 {s['n_iter']} 组，只看样本外）=====")
        for direction, rs in s["runs"].items():
            print(f"  -- {direction}")
            for r in rs:
                print(f"     seed={r['seed']:<3} med {r['baseline_test_median_pp']:+.3f} -> "
                      f"{r['test_median_pp']:+.3f}pp (d={r['delta_median_pp']:+.3f} "
                      f"t={r['t_paired']})  win {r['baseline_test_win_rate']}%->"
                      f"{r['test_win_rate']}%  mean {r['baseline_test_mean_pp']:+.3f}->"
                      f"{r['test_mean_pp']:+.3f}pp")
        print(f"  合计：样本外中位增量均值 {s['delta_median_avg_pp']:+.3f}pp，"
              f"为正的比例 {s['delta_median_positive_pct']}%")

    if a.profile:
        p = profile_basket(by_session, keys, a.top_n)
        out["basket_profile"] = p
        print(f"\n===== 一篮子收益结构（可成交口径，{p['n_periods']} 期 × top{p['top_n']}）=====")
        for label, b in p["rank_buckets"].items():
            print(f"  名次 {label:<6} 中位 {b['median_pp']:+.3f}pp  均值 {b['mean_pp']:+.3f}pp  "
                  f"胜率 {b['win_rate']}%")
        t = p["tail_stocks_per_period"]
        print(f"  每期超额 >+10pp 的票：均值 {t['mean']} 只 / 中位 {t['median']} 只，"
              f"{t['pct_periods_with_none']}% 的期一只都没有")
        r = p["after_removing_best"]
        print(f"  篮子均值 {p['basket_mean_pp']:+.3f}pp → 剔除每期最强 1 只 {r['top1']:+.3f}pp"
              f" / 2 只 {r['top2']:+.3f}pp / 3 只 {r['top3']:+.3f}pp")

    if a.entry_drop:
        d = analyze_entry_drop(by_session, keys, a.top_n)
        out["entry_drop"] = d
        print(f"\n===== 入选票「买入当日已经在跌」的后续 {d['n_periods']} 期 =====")
        print("  当日涨跌幅档   样本    后续均值超额   中位     胜率")
        for lb, b in d["buckets"].items():
            print(f"  {lb:<10} {b['n']:>6}   {b['mean_pp']:+8.2f}pp {b['median_pp']:+8.2f}pp  {b['win_rate']:>5}%")
        print(f"  整池 {d['basket_all_pp']:+.3f}pp → 剔除今日跌超5%的票 {d['basket_excl_big_drop_pp']:+.3f}pp"
              f"  (d={d.get('delta_pp'):+.3f}pp t={d.get('t_paired')})")

    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, f"median_ab_{a.anchor}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2, default=float)
    print(f"\n-> {path}")


if __name__ == "__main__":
    main()
