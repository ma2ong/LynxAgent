"""主升浪开启形态检验：吸筹 → 试盘 → 缩量洗盘 → 多头排列放量突破。

起因（2026-08-13）：一张民间「主升浪开启形态」图。系统里已有它的**碎片**
（ma_convergence_expand / dry_volume_breakout / pressure_test_breakout），
缺的是原图的核心主张 —— 四个阶段**按顺序**发生。本脚本只检验这个主张。

已知逆风：长样本上「当日放量 vr20>1.5 → T+5 −0.47pp / t=−6.8」（见 README 缩量那轮），
而本形态的入场点恰恰要求放量突破。所以真正被检验的是一个交互：
**前三阶段的历史，能不能让第四阶段的放量换一种性质。**

预登记裁决标准（先写死，避免事后挑数）：
  1. 群体口径 T+5 与 T+10 同号为正，前后半同号；
  2. 产品可成交口径（次日收盘买入 → 再持 5 根）不归零；
  3. 消融后「完整序列」必须显著优于「只要入场点」—— 否则前三阶段是摆设。
  三条缺一不接。

    python experiments/mainwave_ab.py            # 群体检验 + 消融
    python experiments/mainwave_ab.py --overhead # 附：上方套牢量假说
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

import pool_lab as L

# 窗口（交易日）：d 为触发日
W_WASH, W_TEST, W_BASE = 11, 34, 60   # 洗盘 [d-11,d-1] / 试盘 [d-45,d-12] / 吸筹 [d-105,d-46]
OFF_TEST, OFF_BASE = 12, 46

F: dict = {}


def build_stage_masks() -> None:
    """把四个阶段各自铺成 日期×代码 的布尔矩阵。全部只看当日及之前，无前视。"""
    G = L.G
    c, h, l, v = G["close"], G["high"], G["low"], G["volume"]
    for n in (5, 10, 20, 60):
        F["ma%d" % n] = c.rolling(n).mean()
    F["vr20"] = v / v.shift(1).rolling(20).mean()

    # 阶段一 · 吸筹区：长期横盘，不是下跌中继
    base_rng = (h.rolling(W_BASE).max() / l.rolling(W_BASE).min() - 1).shift(OFF_BASE)
    base_trend = c.div(c.shift(W_BASE)).sub(1).mul(100).shift(OFF_BASE)
    F["A_base"] = (base_rng <= 0.40) & (base_trend >= -20) & (base_trend <= 30)

    # 阶段二 · 试盘：一次放量脉冲（放量 >2 倍且当日涨 >4%）
    pop = ((F["vr20"] > 2.0) & (G["r1"] > 4.0)).astype(float)
    F["B_test"] = pop.rolling(W_TEST).max().shift(OFF_TEST) > 0
    F["test_high"] = h.rolling(W_TEST).max().shift(OFF_TEST)
    v_test = v.rolling(W_TEST).mean().shift(OFF_TEST)

    # 阶段三 · 缩量洗盘：量缩到试盘期的七成半以下，回踩不破位
    v_wash = v.rolling(W_WASH).mean().shift(1)
    wash_low = l.rolling(W_WASH).min().shift(1)
    F["C_wash"] = ((v_wash < 0.75 * v_test)
                   & (wash_low >= 0.88 * F["test_high"])
                   & (c.shift(1) >= F["ma60"].shift(1)))

    # 阶段四 · 入场点：多头排列 + 放量突破试盘高点
    F["D_stack"] = (F["ma5"] > F["ma10"]) & (F["ma10"] > F["ma20"]) & (F["ma20"] > F["ma60"])
    F["D_break"] = c > F["test_high"] * 1.005
    F["D_vol"] = F["vr20"] > 1.5

    # 产品可成交口径：次日开盘买入 / 次日收盘买入，各再持 5 根
    o = G["open"]
    G["fwd"][20] = c.shift(-20).div(c).sub(1).mul(100)
    F["fwd_open5"] = c.shift(-6).div(o.shift(-1)).sub(1).mul(100)
    F["fwd_nclose5"] = c.shift(-6).div(c.shift(-1)).sub(1).mul(100)


def mask_of(stages: str, d):
    """stages 里出现哪几个字母就叠哪几个条件（A/B/C/S=多头排列/K=突破/V=放量）。"""
    m = L.base_mask(d)
    table = {"A": "A_base", "B": "B_test", "C": "C_wash",
             "S": "D_stack", "K": "D_break", "V": "D_vol",
             "E": "E_ma20", "Q": "E_quiet", "N": "E_nearhigh"}
    for ch in stages:
        m = m & F[table[ch]].loc[d].fillna(False)
    return m


def cohort(name: str, stages: str, step: int = 3, min_n: int = 3, quiet: bool = False):
    """符合形态的**全部**票等权 vs 当期全市场等权。先回答「形态本身有没有信息」。"""
    G = L.G
    fwds = {"T+2": G["fwd"][2], "T+5": G["fwd"][5], "T+10": G["fwd"][10], "T+20": G["fwd"][20],
            "开盘5": F["fwd_open5"], "次收5": F["fwd_nclose5"]}
    rows = []
    idx = [i for i, d in enumerate(G["dates"]) if d >= L.TEST_FROM and i + 21 < len(G["dates"])]
    for i in idx[::step]:
        d = G["dates"][i]
        base = L.base_mask(d)
        m = mask_of(stages, d)
        if m.sum() < min_n:
            continue
        for key, fw in fwds.items():
            f = fw.loc[d]
            uni, sub = f[base & f.notna()], f[m & f.notna()]
            if len(sub) < min_n or len(uni) < 500:
                continue
            rows.append({"date": d, "k": key, "n": int(len(sub)), "exc": sub.mean() - uni.mean()})
    r = pd.DataFrame(rows)
    if r.empty:
        print(f"  {name:22s} 命中过少，无有效期")
        return r
    if not quiet:
        for key in fwds:
            s = r[r.k == key]
            if s.empty:
                continue
            t = s.exc.mean() / (s.exc.std(ddof=1) / len(s) ** 0.5) if s.exc.std(ddof=1) > 0 else 0
            print(f"  {name:22s} {key:5s} 期{len(s):3d} 总n{s.n.sum():5d} 均n{s.n.mean():5.1f} "
                  f"超额{s.exc.mean():+6.2f}pp t={t:+6.2f} | 前半{s[s.date < L.SPLIT].exc.mean():+6.2f} "
                  f"后半{s[s.date >= L.SPLIT].exc.mean():+6.2f}")
    return r


def paired(base: pd.DataFrame, var: pd.DataFrame, label: str, keys=("T+5", "T+10", "次收5")):
    """同期配对：消掉市场环境的共同波动，只留规则差异。"""
    for key in keys:
        p = base[base.k == key].set_index("date").exc
        q = var[var.k == key].set_index("date").exc
        d = (q - p).dropna()
        if len(d) < 10 or d.std(ddof=1) == 0:
            continue
        t = d.mean() / (d.std(ddof=1) / len(d) ** 0.5)
        print(f"  {label:30s} {key:5s} 共同期{len(d):3d} {d.mean():+6.3f}pp t_paired={t:+5.2f}"
              f" | 前半{d[d.index < L.SPLIT].mean():+5.2f} 后半{d[d.index >= L.SPLIT].mean():+5.2f}")


def build_alt_masks() -> None:
    """原图那颗星其实画在洗盘末端、放量拉升之前 —— 这一读法的入场条件。"""
    c = L.G["close"]
    F["E_ma20"] = (c > F["ma20"]) & (c.shift(1) <= F["ma20"].shift(1))   # 当日重新站上 MA20
    F["E_quiet"] = F["vr20"] < 1.2                                        # 仍未放量
    F["E_nearhigh"] = c > F["test_high"] * 0.90                           # 洗盘不深


def sweep(step: int = 3) -> None:
    """两件事：(1) 阈值敏感性，防止结论只是某一组参数的产物；
    (2) 另一种入场点读法 —— 原图那颗星画在洗盘末端、放量拉升**之前**。
    """
    G, c = L.G, L.G["close"]
    print("\n=== 入场点阈值敏感性（S 多头排列 + 突破幅度 × 放量倍数）===")
    for brk in (1.000, 1.005, 1.02):
        for vol in (1.2, 1.5, 2.0, 3.0):
            F["D_break"] = c > F["test_high"] * brk
            F["D_vol"] = F["vr20"] > vol
            cohort(f"突破×{brk:.3f} 量×{vol:g}", "SKV", step)
    F["D_break"] = c > F["test_high"] * 1.005
    F["D_vol"] = F["vr20"] > 1.5

    print("\n=== 另一种读法：星标在洗盘末端（不等放量突破）===")
    build_alt_masks()
    for name, code in (("ABC+站上MA20", "ABCE"), ("ABC+站上MA20+未放量", "ABCEQ"),
                       ("ABC+排列(不要突破量)", "ABCS"), ("站上MA20+未放量(无前三段)", "EQ"),
                       ("ABC+洗盘浅+站上MA20", "ABCEN")):
        cohort(name, code, step)


def tail_check(step: int = 3) -> None:
    """右尾检验：均值差 ≠ 不能出大牛。这套评分的超额本来就 100% 来自右尾，
    所以要单独问「符合形态的票，20 日内翻倍/大涨的概率是不是更高」。"""
    G_ = L.G
    fw = G_["fwd"][20]
    print("\n=== 右尾：T+20 大涨概率（形态组 vs 当期全市场，逐期做差再跨期平均）===")
    for name, code in (("SKV 放量突破入场点", "SKV"), ("ABC 前三段", "ABC"),
                       ("ABC+站上MA20", "ABCE"), ("ABC+SKV 完整", "ABCSKV")):
        rows = []
        idx = [i for i, d in enumerate(G_["dates"]) if d >= L.TEST_FROM and i + 21 < len(G_["dates"])]
        for i in idx[::step]:
            d = G_["dates"][i]
            base = L.base_mask(d)
            m = mask_of(code, d)
            f = fw.loc[d]
            uni, sub = f[base & f.notna()], f[m & f.notna()]
            if len(sub) < 3 or len(uni) < 500:
                continue
            rows.append({"date": d,
                         "p20": (sub >= 20).mean() - (uni >= 20).mean(),
                         "p50": (sub >= 50).mean() - (uni >= 50).mean(),
                         "p90": sub.quantile(0.9) - uni.quantile(0.9)})
        r = pd.DataFrame(rows)
        if r.empty:
            continue
        def _t(col):
            s = r[col]
            return s.mean() / (s.std(ddof=1) / len(s) ** 0.5) if s.std(ddof=1) > 0 else 0
        print(f"  {name:18s} 期{len(r):3d} P(≥20%){r.p20.mean()*100:+5.2f}pp t={_t('p20'):+5.2f} "
              f"P(≥50%){r.p50.mean()*100:+5.2f}pp t={_t('p50'):+5.2f} "
              f"P90分位{r.p90.mean():+6.2f}pp t={_t('p90'):+5.2f}")


def overhead_supply(win: int = 120) -> None:
    """Allen 的套牢盘假说：前高上方成交量占比越小，突破越轻松。

    over = 近 win 日里「收盘价高于今日收盘」的那些天的成交量占比。
    小 = 头顶几乎没有套牢盘。
    """
    G = L.G
    c, v = G["close"].values, G["volume"].values
    dates = G["dates"]
    rows = []
    idx = [i for i, d in enumerate(dates) if d >= L.TEST_FROM and i + 21 < len(dates) and i >= win]
    for i in idx[::3]:
        d = dates[i]
        blk_c, blk_v = c[i - win + 1:i + 1], v[i - win + 1:i + 1]
        above = np.where(blk_c > c[i], blk_v, 0.0)
        tot = np.nansum(blk_v, axis=0)
        over = pd.Series(np.where(tot > 0, np.nansum(above, axis=0) / np.where(tot > 0, tot, 1), np.nan),
                         index=G["close"].columns)
        base = L.base_mask(d)
        near_high = G["dist_high"].loc[d] > -8      # 逼近 250 日高点
        for lo, hi in [(0.0, 0.05), (0.05, 0.15), (0.15, 0.30), (0.30, 1.01)]:
            for tag, extra in (("全市场", base), ("近前高", base & near_high.fillna(False))):
                m = extra & over.between(lo, hi)
                if m.sum() < 8:
                    continue
                for key, fw in (("T+5", G["fwd"][5]), ("T+20", G["fwd"][20])):
                    f = fw.loc[d]
                    uni, sub = f[base & f.notna()], f[m & f.notna()]
                    if len(sub) < 8 or len(uni) < 500:
                        continue
                    rows.append({"date": d, "band": f"{tag} 上方量 {lo:.0%}-{hi:.0%}",
                                 "k": key, "n": len(sub), "exc": sub.mean() - uni.mean()})
    r = pd.DataFrame(rows)
    print("\n=== 上方套牢量假说（近 120 日高于现价的成交量占比）===")
    for band in sorted(r.band.unique()):
        for key in ("T+5", "T+20"):
            s = r[(r.band == band) & (r.k == key)]
            if s.empty:
                continue
            t = s.exc.mean() / (s.exc.std(ddof=1) / len(s) ** 0.5) if s.exc.std(ddof=1) > 0 else 0
            print(f"  {band:26s} {key:4s} 期{len(s):3d} 均n{s.n.mean():6.1f} "
                  f"超额{s.exc.mean():+6.2f}pp t={t:+6.2f} | 前半{s[s.date < L.SPLIT].exc.mean():+6.2f} "
                  f"后半{s[s.date >= L.SPLIT].exc.mean():+6.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--overhead", action="store_true", help="只跑套牢量假说")
    ap.add_argument("--sweep", action="store_true", help="只跑阈值敏感性 + 另一种入场读法")
    ap.add_argument("--tail", action="store_true", help="只跑右尾检验")
    a = ap.parse_args()

    L.load(); L.build(); build_stage_masks()
    print(f"矩阵 {L.G['close'].shape}  {L.G['dates'][0]} ~ {L.G['dates'][-1]}")

    if a.overhead:
        overhead_supply()
        return
    if a.sweep:
        sweep(a.step)
        return
    if a.tail:
        build_alt_masks()
        tail_check(a.step)
        return

    print("\n=== 完整序列 vs 消融（群体等权 vs 全市场等权）===")
    full = cohort("ABC+SKV 完整", "ABCSKV", a.step)
    variants = {
        "SKV 只要入场点": "SKV",
        "SK 排列+突破(不要量)": "SK",
        "S 只要多头排列": "S",
        "V 只要当日放量": "V",
        "BC+SKV 去掉吸筹": "BCSKV",
        "AC+SKV 去掉试盘": "ACSKV",
        "AB+SKV 去掉洗盘": "ABSKV",
        "ABC+SK 突破不放量": "ABCSK",
        "ABC 只有前三段": "ABC",
    }
    subs = {}
    for name, code in variants.items():
        subs[name] = cohort(name, code, a.step)

    print("\n=== 配对：完整序列 - 各消融（正 = 被去掉的部分有贡献）===")
    for name, r in subs.items():
        if not r.empty and not full.empty:
            paired(r, full, f"完整 - ({name})")

    print("\n注：裁决看预登记三条，不看单个 t。")


if __name__ == "__main__":
    main()
