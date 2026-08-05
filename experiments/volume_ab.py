"""量能信号 A/B：把「当日放量/缩量」接进 composite 排序，用 top-N 超额裁决。

起因（2026-08-05）：中际旭创 8-03 缩量、8-04 大涨 13%，问「极致缩量能不能当埋伏信号」。
群体检验（scratchpad/dryup.py，270 期）先给出方向：**缩量那一侧几乎没有信息，
有信息的是反面 —— 当日放量是稳健负信号**（vr20>1.5 → T+5 −0.47pp，t=−6.8，前后半同号）。

本脚本回答产品层面的问题：把这个负信号接到现行 composite 上，top-20 超额会不会变好。
口径、配对方法与 weight_ab.py 完全一致，只把变量从「权重」换成「量能闸门/惩罚」。

预登记的裁决标准（先写死，避免事后挑数）：
  主指标 t_paired > 2 且前后半同号，否则不接。

    python experiments/snapshot_db.py
    python experiments/volume_ab.py --anchor 2026-08-04 --months 60 --top-n 20
"""
from __future__ import annotations

import argparse
import json
import os
import pickle

import numpy as np
import pandas as pd

from factor_scores import DEFAULT_DB, FACTORS, HERE, collect
from weight_ab import BASELINE, paired, stats

RESULTS = os.path.join(HERE, "results")
CACHE = os.path.join(HERE, ".cache")


def composite(items) -> np.ndarray:
    return np.array([min(100.0, max(0.0, sum(v[f] * BASELINE[f] for f in FACTORS)))
                     for v, _ in items], dtype=float)


def evaluate(by_session: dict, adjust, top_n: int, ret_key: str | None = None) -> dict:
    """adjust(scores, vals_list) -> 调整后的分数（-inf 表示排除）。返回 {会话日: 超额}。

    返回字典而不是数组：排除类变体会丢掉一些期，配对必须落在**共同会话日**上，
    拿两条长度不同的序列直接相减会静默错位。
    """
    out = {}
    for s in sorted(by_session):
        items = by_session[s]
        if len(items) < 200:
            continue
        rets = np.array([(r if ret_key is None else v[ret_key]) for v, r in items], dtype=float)
        bench = float(np.mean(rets))
        sc = adjust(composite(items), [v for v, _ in items])
        idx = np.argsort(-sc)[:top_n]
        idx = idx[np.isfinite(sc[idx])]
        if len(idx) < top_n * 0.6:
            continue
        out[s] = float(np.mean(rets[idx]) - bench)
    return out


def _vr(vals):
    return np.array([v.get("vol_ratio20", 1.0) for v in vals], dtype=float)


def _vp(vals):
    return np.array([v.get("vol_pct60", 50.0) for v in vals], dtype=float)


def build_variants() -> dict:
    V = {"A_baseline": lambda sc, vs: sc}
    for cut in (1.0, 1.15, 1.3, 1.5, 2.0):
        V[f"B_drop_vr>{cut}"] = lambda sc, vs, c=cut: np.where(_vr(vs) > c, -np.inf, sc)
    for q in (50, 65, 80):
        V[f"C_only_vp>={q}"] = lambda sc, vs, q=q: np.where(_vp(vs) >= q, sc, -np.inf)
    for k in (2.0, 5.0, 10.0):
        V[f"D_penalty_k{k:g}"] = lambda sc, vs, k=k: sc - k * np.maximum(0.0, _vr(vs) - 1.0)
    V["E_prefer_quiet"] = lambda sc, vs: sc + 3.0 * ((_vr(vs) > 0.5) & (_vr(vs) < 0.9))
    V["Z_invert_buy_vr>1.5"] = lambda sc, vs: np.where(_vr(vs) > 1.5, sc, -np.inf)
    return V


def run(by_session, top_n, ret_key, label):
    V = build_variants()
    base = evaluate(by_session, V["A_baseline"], top_n, ret_key)
    print(f"\n=== {label} (top-{top_n}, 期数={len(base)}) ===")
    rows = {}
    for name, fn in V.items():
        cur = evaluate(by_session, fn, top_n, ret_key)
        arr = np.array([cur[s] for s in sorted(cur)])
        st = stats(arr)
        line = (f"  {name:22s} 期{len(cur):3d} 超额{st['avg_excess_pp']:+6.2f}pp "
                f"t={st['t']:+5.2f} 胜{st['win_rate']:4.1f}%")
        if name != "A_baseline":
            common = sorted(set(base) & set(cur))
            b = np.array([base[s] for s in common])
            q = np.array([cur[s] for s in common])
            p = paired(b, q)
            d, half = q - b, len(common) // 2
            line += (f" | 配对{p['delta_pp']:+6.3f}pp t={p['t_paired']:+5.2f}"
                     f" 前半{d[:half].mean()*100:+5.2f} 后半{d[half:].mean()*100:+5.2f}")
            st.update(p)
        print(line)
        rows[name] = st
    # 基线分年拆解：60 个月跨越牛熊，整段均值会掩盖分段反号
    yr = {}
    for s, e in base.items():
        yr.setdefault(s[:4], []).append(e)
    print("  基线分年 " + "  ".join(
        f"{y}:{np.mean(v)*100:+5.2f}pp(n{len(v)})" for y, v in sorted(yr.items())))
    rows["_by_year"] = {y: [round(float(np.mean(v)) * 100, 3), len(v)] for y, v in sorted(yr.items())}
    return rows


def cohort_scan(step: int = 5) -> None:
    """群体检验：符合某形态的**全部**票等权 vs 当期全市场等权。

    先跑这个再跑 A/B —— 它回答的是「这个形态本身有没有信息」，不受排序耦合影响。
    用 pool_lab 的矩阵（2021-01 起 270 期），比 collect() 的会话轴更长。
    """
    import pool_lab as L

    L.load(); L.build()
    G = L.G
    c, v = G["close"], G["volume"]
    G["vr20"] = v / v.shift(1).rolling(20).mean()
    G["vpct60"] = v.rolling(61).apply(lambda x: (x[:-1] > x[-1]).mean() * 100, raw=True)
    G["fwd"].update({hh: c.shift(-hh).div(c).sub(1).mul(100) for hh in (1, 3)})

    def cohort(name, mask_fn, horizons=(1, 5), min_n=15):
        rows = []
        idx = [i for i, d in enumerate(G["dates"])
               if d >= L.TEST_FROM and i + max(horizons) < len(G["dates"])]
        for i in idx[::step]:
            d = G["dates"][i]
            base = L.base_mask(d)
            m = (mask_fn(d) & base).fillna(False)
            if m.sum() < min_n:
                continue
            for hh in horizons:
                f = G["fwd"][hh].loc[d]
                uni, sub = f[base & f.notna()], f[m & f.notna()]
                if len(sub) < min_n or len(uni) < 500:
                    continue
                rows.append({"date": d, "h": hh, "n": len(sub), "exc": sub.mean() - uni.mean()})
        r = pd.DataFrame(rows)
        for hh in horizons:
            s = r[r.h == hh] if not r.empty else r
            if s.empty:
                continue
            t = s.exc.mean() / (s.exc.std(ddof=1) / len(s) ** 0.5) if s.exc.std(ddof=1) > 0 else 0
            print(f"  {name:26s} T+{hh:<2d} 期{len(s):3d} 均n{s.n.mean():5.0f} "
                  f"超额{s.exc.mean():+6.2f}pp t={t:+6.2f} | 前半{s[s.date < L.SPLIT].exc.mean():+5.2f} "
                  f"后半{s[s.date >= L.SPLIT].exc.mean():+5.2f}")

    print("\n=== 缩量侧（vpct60 越大越缩量）===")
    for lo, hi in [(95, 101), (90, 95), (80, 90), (60, 80)]:
        cohort(f"vpct60 {lo}-{hi}", lambda d, lo=lo, hi=hi: G["vpct60"].loc[d].between(lo, hi))
    print("\n=== 放量侧 ===")
    cohort("vr20 > 1.5", lambda d: G["vr20"].loc[d] > 1.5)
    cohort("vpct60 < 20 (量为60日高档)", lambda d: G["vpct60"].loc[d] < 20)
    print("\n=== 民间说法的强版本：强势股高位缩量洗盘 ===")
    strong = lambda d: (G["r20"].loc[d] > 20) & (c.loc[d] > c.ewm(span=60, adjust=False).mean().loc[d])
    cohort("20日涨>20%(不看量)", strong)
    cohort("强势 + 缩量vr20<0.7", lambda d: strong(d) & (G["vr20"].loc[d] < 0.7))
    cohort("强势 + 放量vr20>1.5", lambda d: strong(d) & (G["vr20"].loc[d] > 1.5))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", action="store_true", help="只跑群体检验，不跑 top-N A/B")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--anchor", default="2026-08-04")
    ap.add_argument("--months", type=int, default=60)
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--workers", type=int, default=9)
    ap.add_argument("--reuse", action="store_true", help="复用上次采样缓存")
    a = ap.parse_args()

    if a.cohort:
        cohort_scan(a.step)
        return

    os.makedirs(CACHE, exist_ok=True)
    cache = os.path.join(CACHE, f"obs_{a.anchor}_{a.months}m_{a.step}.pkl")
    if a.reuse and os.path.exists(cache):
        by_session = pickle.load(open(cache, "rb"))
        print(f"reuse {cache} sessions={len(by_session)}")
    else:
        by_session = collect(a.db, a.anchor, a.months, a.step, a.workers)
        pickle.dump(by_session, open(cache, "wb"), protocol=4)
    obs = sum(len(v) for v in by_session.values())
    print(f"sessions={len(by_session)} observations={obs:,}")

    out = {"anchor": a.anchor, "months": a.months, "top_n": a.top_n, "observations": obs}
    out["close_T5"] = run(by_session, a.top_n, None, "收盘买入 → T+5 收盘")
    out["entry_next_close"] = run(by_session, a.top_n, "ret_entry_next_close",
                                  "次日收盘买入 → 再持 5 根（产品实际口径）")

    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, f"volume_ab_{a.anchor}.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n-> {path}")


if __name__ == "__main__":
    main()
