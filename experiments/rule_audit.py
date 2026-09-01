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


SECTOR_MIN_MEMBERS = 5     # 成分不足这个数的板块不参与强弱排序（三只票不是一个板块）
DRYUP_PCT = 0.10           # 「地量」= 成交额在自身近 60 日的分位 ≤ 这个数


def _attach_stock_volume(df: pd.DataFrame) -> None:
    """个股量能分位：当日成交额在自身近 60 个交易日中的位置（0=最低，1=最高）。

    用分位而不是「量比」，是因为量比的分母 ma20 本身会被前期一根天量拉高，
    导致「地量」在放量后的第二天就自动成立。分位只关心排序，没有这个毛病。
    """
    df["vol_pct60"] = df.groupby("symbol", sort=False)["amount"].transform(
        lambda s: s.rolling(60, min_periods=40).rank(pct=True))
    # 人气/流量：近 20 日平均成交额在**当天全市场**的横截面分位。
    # 这跟 vol_pct60 是两回事：后者问「这只票今天比自己平时清淡吗」（地量），
    # 前者问「这只票在市场上有没有人气」（剔垃圾股/仙股/常年无人问津的票）。
    amt_ma20 = df.groupby("symbol", sort=False)["amount"].transform(
        lambda s: s.rolling(20, min_periods=20).mean())
    df["amt_ma20_q"] = amt_ma20.groupby(df["date"]).rank(pct=True)


def _attach_shape(df: pd.DataFrame) -> None:
    """K 线形态族：震荡度、ATR 通道方向、通道突破、天量那天的方向。

    全部按公开标准定义实现，只吃截至当日的 OHLC —— 没有一列读到未来 bar。
    它们的共同点是描述「价格走成什么形状」，而现有因子清一色是截面统计量
    （涨了多少、量比多少、板块排第几）。信息基本不重叠，所以值得单独审一轮。
    """
    g = df.groupby("symbol", sort=False)
    prev_close = g["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["_tr"] = tr
    bar_i = g.cumcount()

    # --- Choppiness Index(14)：14 日真实波幅之和 / 同期高低区间，取 log 归一到 0~100。
    # 低 = 单边趋势（走的路程≈位移），高 = 来回震荡（路程远但位移小）。常用分界
    # 38.2 / 61.8。它和 ADX 问的不是同一件事：ADX 问「方向有多强」，它问「到底有
    # 没有方向」，两者可以同时低。系统现在没有任何「震荡 vs 趋势」的判别维度。
    tr_sum = df.groupby("symbol", sort=False)["_tr"].transform(
        lambda s: s.rolling(14, min_periods=14).sum())
    hh14 = g["high"].transform(lambda s: s.rolling(14, min_periods=14).max())
    ll14 = g["low"].transform(lambda s: s.rolling(14, min_periods=14).min())
    rng14 = (hh14 - ll14).replace(0, np.nan)
    df["chop14"] = 100 * np.log10(tr_sum / rng14) / np.log10(14)

    # --- Donchian 突破：收盘价超过**前** N 日的最高价。shift(1) 排除当日，否则
    # 「今天创今天的新高」恒真。这是「N 日新高」的规范定义，比现有 dist_high20
    # （用收盘价滚动最大、且含当日）严格：那个衡量「离高点多近」，这个是「破没破」。
    for n in (20, 55):
        prior_hi = g["high"].transform(
            lambda s, w=n: s.rolling(w, min_periods=w).max().shift(1))
        df[f"donchian{n}"] = df["close"] > prior_hi

    # --- Supertrend(10, 3)：ATR 通道加一道只能朝有利方向收紧的棘轮，价格穿过就翻向。
    # 定义是递归的，只能逐行推 —— 下面是全文件唯一一处显式循环，走 numpy 数组。
    # 与现有 chandelier_long 的区别：那个只是个静态止损位，这个带方向状态和翻转时点。
    atr10 = df.groupby("symbol", sort=False)["_tr"].transform(
        lambda s: s.ewm(alpha=1 / 10, adjust=False).mean())
    hl2 = (df["high"] + df["low"]) / 2
    ub = (hl2 + 3 * atr10).to_numpy()
    lb = (hl2 - 3 * atr10).to_numpy()
    close = df["close"].to_numpy()
    sym = df["symbol"].to_numpy()
    n_rows = len(df)
    direction = np.zeros(n_rows, dtype=np.int8)
    fu = np.empty(n_rows)
    fl = np.empty(n_rows)
    for i in range(n_rows):
        if i == 0 or sym[i] != sym[i - 1]:
            fu[i], fl[i], direction[i] = ub[i], lb[i], 1
            continue
        fu[i] = ub[i] if (ub[i] < fu[i - 1] or close[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lb[i] if (lb[i] > fl[i - 1] or close[i - 1] < fl[i - 1]) else fl[i - 1]
        if direction[i - 1] > 0:
            direction[i] = -1 if close[i] < fl[i] else 1
        else:
            direction[i] = 1 if close[i] > fu[i] else -1
    # 前 30 根是 ATR 的热身段，方向不作数（否则每只票上市头一个月都白送一个"多头"）
    warm = (bar_i < 30).to_numpy()
    direction[warm] = 0
    same_sym = np.empty(n_rows, dtype=bool)
    same_sym[0] = False
    same_sym[1:] = sym[1:] == sym[:-1]
    prev_dir = np.roll(direction, 1)
    df["st_dir"] = direction
    df["st_flip_up"] = (direction == 1) & (prev_dir == -1) & same_sym

    # --- 天量那天的方向：近 60 日成交额最高的那根 K 线，是阴线还是阳线。
    # 不用逐行找 argmax：把阳线/阴线的成交额分成两路各取滚动最大，谁大谁就是那根
    # 天量。天量收阴 = 有人在最活跃的一天派发筹码，这是筹码结构的判据，现有
    # 因子里没有任何一个看得到它（vol_pct60 只看量的大小，不看那天的方向）。
    up_amt = df["amount"].where(df["close"] >= df["open"], -1.0)
    dn_amt = df["amount"].where(df["close"] < df["open"], -1.0)
    roll_max = lambda s: s.rolling(60, min_periods=40).max()
    up_max = up_amt.groupby(df["symbol"], sort=False).transform(roll_max)
    dn_max = dn_amt.groupby(df["symbol"], sort=False).transform(roll_max)
    df["maxvol60_valid"] = up_max.notna()
    df["maxvol60_down"] = df["maxvol60_valid"] & (dn_max > up_max)

    df.drop(columns=["_tr"], inplace=True)


def _attach_sector(df: pd.DataFrame) -> None:
    """挂上板块归属，以及板块层面的强弱、个股在板块内的位置。

    **行业映射是当前快照，不是历史**。行业分类几乎不变（且不含价格信息），拿今天的
    映射回溯历史带来的偏差远小于「根本没有板块维度」——但它确实是一个已知近似，
    结论里必须写明。真正的前视风险在价格上，所有板块强度都只用截至当日的行情算。
    """
    from quantcore.quant.industry import industry_map
    mapping = industry_map()
    if not mapping:
        raise SystemExit("runtime/industry_map.json 为空，板块规则无从算起")
    df["industry"] = df["symbol"].map(mapping)

    sub = df[df["industry"].notna()]
    # 板块日收益 = 成分股当日涨幅中位数（中位而非均值：一只涨停票不能代表板块）
    sec = sub.groupby(["date", "industry"], sort=False).agg(
        sec_ret=("ret1", "median"), sec_n=("symbol", "size")).reset_index()
    sec_amt = sub.groupby(["date", "industry"], sort=False)["amount"].sum().rename("sec_amt")
    sec = sec.merge(sec_amt.reset_index(), on=["date", "industry"])
    sec = sec[sec["sec_n"] >= SECTOR_MIN_MEMBERS].sort_values(["industry", "date"])
    g = sec.groupby("industry", sort=False)["sec_ret"]
    sec["sec_mom20"] = g.transform(lambda s: s.rolling(20, min_periods=20).sum())
    sec["sec_mom5"] = g.transform(lambda s: s.rolling(5, min_periods=5).sum())
    # 复现生产 industry_stage_scores 的口径，好跟 mom20 头对头：主导项是量能扩张
    # （近5日日均额/近20日日均额−1，系数 40），5 日涨幅只有系数 1.8，mom20 不进分。
    ga = sec.groupby("industry", sort=False)["sec_amt"]
    amt5 = ga.transform(lambda s: s.rolling(5, min_periods=5).mean())
    amt20 = ga.transform(lambda s: s.rolling(20, min_periods=20).mean())
    sec["sec_vol_exp"] = amt5 / amt20 - 1
    sec["sec_stage"] = (50.0 + sec["sec_vol_exp"].clip(-0.5, 1.0) * 40
                        + sec["sec_mom5"].clip(-8.0, 8.0) * 1.8)
    # 2026-08-28 起生产改用的新系数（mom20 主导、量能扩张退成辅助）
    sec["sec_stage_new"] = (50.0 + sec["sec_mom20"].clip(-20.0, 20.0) * 2.0
                            + sec["sec_mom5"].clip(-8.0, 8.0) * 1.8
                            + sec["sec_vol_exp"].clip(-0.5, 1.0) * 10)
    # 横截面分位：当天所有可排板块里的相对位置。无量纲，普涨普跌都能排出强弱。
    for col in ("sec_mom20", "sec_mom5", "sec_vol_exp", "sec_stage", "sec_stage_new"):
        sec[col + "_q"] = sec.groupby("date", sort=False)[col].rank(pct=True)
    df_idx = df.set_index(["date", "industry"]).index
    for col in ("sec_mom20_q", "sec_mom5_q", "sec_vol_exp_q", "sec_stage_q",
                "sec_stage_new_q", "sec_n"):
        df[col] = sec.set_index(["date", "industry"])[col].reindex(df_idx).to_numpy()

    # 个股在板块内的位置：近 5 日涨幅的组内分位（1 = 板块里最强的那只 = Lucas 的「辨识度」）
    df["prior_ret5"] = (df["close"] / df.groupby("symbol", sort=False)["close"].shift(5) - 1) * 100
    ok = df["industry"].notna() & (df["sec_n"] >= SECTOR_MIN_MEMBERS)
    df["in_sec_q5"] = np.nan
    df["in_sec_q20"] = np.nan
    grp = df[ok].groupby(["date", "industry"], sort=False)
    df.loc[ok, "in_sec_q5"] = grp["prior_ret5"].rank(pct=True)
    df.loc[ok, "in_sec_q20"] = grp["prior_ret20"].rank(pct=True)


def _attach_regime(df: pd.DataFrame) -> None:
    """挂上每个交易日的大盘环境标签（偏暖 / 中性 / 偏冷）。

    口径直接复用生产的 regime 模块，不在这里另写一套 —— 线上横幅、回放分层、
    本审计三处必须同源，否则「冷环境该不该出手」的结论跟用户屏幕上看到的标签对不上。

    环境是**择时**维度：同一天全市场共享一个标签，所以它不能当选股规则塞进匹配对照
    （那样每天所有票同时命中，增量恒为 0）。它的正确用法是**分层**：把同一条规则
    分别在冷/中/暖三个子样本里各审一遍，问「这条规则的 alpha 在什么环境下还在」。
    """
    from quantcore.quant.regime import blend_temp, classify
    day = df.groupby("date", sort=False).agg(
        med=("ret1", "median"), up=("ret1", lambda s: float((s > 0).mean())))
    day = day.dropna().sort_index()
    dates = list(day.index)
    med = day["med"].to_numpy()
    up = day["up"].to_numpy()
    temp = {}
    for i, d in enumerate(dates):
        lo = max(0, i - 4)
        hist = [(med[j], up[j]) for j in range(i, lo - 1, -1)]   # 最新一日在前
        temp[d] = blend_temp(hist)
    df["mkt_temp"] = df["date"].map(temp)
    df["regime"] = df["mkt_temp"].map(lambda t: classify(t) if t == t else None)


def _market_env(df: pd.DataFrame) -> pd.DataFrame:
    """全市场量能环境（按交易日一行）。这是**择时**维度，不做横截面区分，
    所以不能当选股规则塞进匹配对照里审——那样每天所有票同时命中，增量恒为 0。
    """
    day = df.groupby("date", sort=False).agg(
        mkt_amount=("amount", "sum"), mkt_ret=("ret1", "median")).sort_index()
    day["mkt_amt_pct60"] = day["mkt_amount"].rolling(60, min_periods=40).rank(pct=True)
    return day


# --------------------------------------------------------------------------
# 面板：一次性把全市场日线摊成一张长表，规则写成向量化谓词
# --------------------------------------------------------------------------
def build_panel(db: str, since: str, horizon: int, entry: str = "close") -> pd.DataFrame:
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
    df["ret1"] = g.pct_change() * 100
    df["prior_ret60"] = (df["close"] / g.shift(60) - 1) * 100
    ma20 = g.transform(lambda s: s.rolling(20, min_periods=20).mean())
    df["dist_ma20"] = (df["close"] / ma20 - 1) * 100
    # 入场口径。信号在 T 日收盘后才产生，名单也是收盘后生成的 —— 所以 close 口径
    # 天然含一段用户吃不到的隔夜收益。缩量那轮的 +1pp 全长在这一段里（见模块开头），
    # 因此任何新规则都必须用 open 口径复核：T+1 开盘买入，T+horizon 收盘卖出。
    if entry == "open0":
        # 当日开盘买入。给集合竞价这类**信号在开盘前就成立**的池用：名单 09:25 由
        # 集合竞价定出，而当日 open 正是那次竞价的成交价 —— 用它当买入价既不是未来
        # 函数，也正好等于用户 09:30 能拿到的价。
        # 另两种口径对竞价池都晚一天，量的不是同一个产品。
        # 配合 --horizon 0 即「买开盘、当日收盘」，也就是竞价页写的那个日内口径。
        df["fwd_ret"] = (g.shift(-horizon) / df["open"] - 1) * 100
    elif entry == "open":
        buy = df.groupby("symbol", sort=False)["open"].shift(-1)
        df["fwd_ret"] = (g.shift(-horizon) / buy - 1) * 100
    else:
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

    _attach_stock_volume(df)
    _attach_sector(df)
    _attach_shape(df)
    _attach_regime(df)

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


def trimmed_increment(panel: pd.DataFrame, mask: pd.Series) -> float | None:
    """关 4（右尾稳健）：信号组和对照组**各自**砍掉最好的 TAIL_DROP，再比。

    为什么不能直接看「信号组去右尾后的绝对超额是否 > 0」——那是这个文件 2026-08-27
    之前的写法，是个死关：fwd_excess 以全市场**中位**为基准，而个股收益右偏（均值比
    中位高约 0.6pp），砍掉右尾后剩下的必然低于中位。实测随机买入（零 alpha）在旧口径
    下得 −0.39，也就是说任何规则都过不了，线上 smart 池自己也过不了（−0.53）。
    闸门想拦的「超额全靠少数几只暴涨股」是真问题，但要跟同样被砍过右尾的对照组比才
    答得出来：零 alpha → 0，真右尾依赖 → 负。
    """
    key = ["date", "b_prior", "b_amount"]
    sig, ctl = panel[mask], panel[~mask]
    if sig.empty or ctl.empty:
        return None
    s_t = sig[sig["fwd_excess"] <= sig["fwd_excess"].quantile(1 - TAIL_DROP)]
    c_t = ctl[ctl["fwd_excess"] <= ctl["fwd_excess"].quantile(1 - TAIL_DROP)]
    s_grp = s_t.groupby(key, observed=True)["fwd_excess"].agg(["mean", "size"])
    c_mean = c_t.groupby(key, observed=True)["fwd_excess"].mean()
    common = s_grp.index.intersection(c_mean.index)
    if len(common) == 0:
        return None
    # 用信号组的层分布给对照组加权，否则比的是两组的层结构差异，不是规则本身
    w = s_grp.loc[common, "size"].to_numpy()
    s_mean = float((s_grp.loc[common, "mean"].to_numpy() * w).sum() / w.sum())
    ctl_mean = float((c_mean.loc[common].to_numpy() * w).sum() / w.sum())
    return s_mean - ctl_mean


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

    # 关 4：右尾稳健 —— 两组各自砍掉最好的 5% 之后的增量（见 trimmed_increment）
    ti = trimmed_increment(panel, mask)
    out["inc_ex_tail"] = round(ti, 3) if ti is not None else None
    cut = sorted(ex)[: max(1, int(len(ex) * (1 - TAIL_DROP)))]
    out["avg_excess_ex_tail"] = round(statistics.mean(cut), 3)   # 旧口径，仅留作参考

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
        "去右尾仍为正": (r.get("inc_ex_tail") or 0) > 0,
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
    # 三档追高闸门做剂量反应：只在某一档有效、邻档无效，多半是噪音而不是机制
    "chase15": ("近20日已涨≥15%", lambda d: d["prior_ret20"] >= 15),
    "chase20": ("近20日已涨≥25%（追高）", lambda d: d["prior_ret20"] >= 25),
    "chase35": ("近20日已涨≥35%", lambda d: d["prior_ret20"] >= 35),
    "near_high": ("贴着20日高点（距高点≤3%）", lambda d: d["dist_high20"] >= -3),
    "chase_near_high": ("近20日≥25% 且贴着高点", lambda d: (d["prior_ret20"] >= 25) & (d["dist_high20"] >= -3)),
    "deep_drop": ("近20日跌≥20%（深跌）", lambda d: d["prior_ret20"] <= -20),
    "deep_drop_vol": ("深跌 + 放量强收", lambda d: (d["prior_ret20"] <= -20) & (d["vol_ratio"] >= 2.5) & (d["clv"] >= 0.7)),
    "consolidate": ("长期强、近期消化（60日强/20日温和/离高点≥8%）",
                    lambda d: (d["prior_ret20"].between(-5, 10)) & (d["dist_high20"] <= -8)),

    # ---- 地量族（2026-08-27）。个股缩量在 volume_ab 里已判过一次否，这里换成
    # 分位口径重测，并且必须用 --entry open 复核。三条做剂量/形态对照：光有地量、
    # 地量+趋势在（「缩量+高弹性，必须具备攻击性」）、主升一之后的缩量洗盘。
    "dryup": ("个股地量（成交额近60日≤10分位）", lambda d: d["vol_pct60"] <= DRYUP_PCT),
    "dryup_strong": ("地量 + 趋势仍在（60日涨幅≥20% 且未破20日线）",
                     lambda d: (d["vol_pct60"] <= DRYUP_PCT) & (d["prior_ret60"] >= 20)
                               & (d["dist_ma20"] >= 0)),
    "dryup_washout": ("主升一后缩量洗盘（60日≥30%、离高点3~18%、地量）",
                      lambda d: (d["vol_pct60"] <= DRYUP_PCT) & (d["prior_ret60"] >= 30)
                                & d["dist_high20"].between(-18, -3)),

    # ---- 板块族（2026-08-27）。这是 Lucas 那套的可提取内核：先定主线板块，
    # 再在板块里挑位置。系统现在完全没有板块维度，这批是空白检验。
    "sector_hot": ("所属板块20日强度前20%", lambda d: d["sec_mom20_q"] >= 0.8),
    # 5 日窗口。这条曾标注为「生产现行口径」，2026-08-28 重配系数后已经不是了
    # （现行是 mom20 主导的 sector_stage_new），描述留着会误导下一次决策。
    # 2026-08-31 六条同批复核：5 日 +0.11 < 20 日 +0.14 < 现行 +0.17，窗口不用改。
    "sector_hot5": ("所属板块5日强度前20%", lambda d: d["sec_mom5_q"] >= 0.8),
    # 生产现行 industry_heat 的两个成分，各自单独审：量能扩张（系数40，主导项）
    # 和复现出来的完整阶段分。与 sector_hot(mom20) 头对头决定要不要换口径。
    "sector_volexp": ("板块量能扩张前20%（生产阶段分的主导项）",
                      lambda d: d["sec_vol_exp_q"] >= 0.8),
    "sector_stage": ("板块阶段分前20%（≈生产现行 industry_heat 口径）",
                     lambda d: d["sec_stage_q"] >= 0.8),
    "sector_stage_new": ("板块阶段分前20%（2026-08-28 起的生产新口径）",
                         lambda d: d["sec_stage_new_q"] >= 0.8),
    "sector_hot_mix": ("板块5日与20日强度均值前20%（混合口径候选）",
                       lambda d: ((d["sec_mom5_q"] + d["sec_mom20_q"]) / 2) >= 0.8),
    "sector_accel": ("板块刚启动（5日强度前20%、20日强度未过60%）",
                     lambda d: (d["sec_mom5_q"] >= 0.8) & (d["sec_mom20_q"] < 0.6)),
    "sector_leader": ("强板块里的龙头（板块内5日涨幅前10%）",
                      lambda d: (d["sec_mom20_q"] >= 0.8) & (d["in_sec_q5"] >= 0.9)),
    "sector_laggard": ("强板块里的补涨位（板块内20日涨幅后50%）",
                       lambda d: (d["sec_mom20_q"] >= 0.8) & (d["in_sec_q20"] <= 0.5)),
    "handover": ("板块龙头易主（近5日跃到板块前10%、近20日仍在后50%）",
                 lambda d: (d["sec_mom20_q"] >= 0.8) & (d["in_sec_q5"] >= 0.9)
                           & (d["in_sec_q20"] <= 0.5)),
    "hot_dryup": ("强板块 + 个股地量（两套说法的交集）",
                  lambda d: (d["sec_mom20_q"] >= 0.8) & (d["vol_pct60"] <= DRYUP_PCT)),
    # Allen 2026-08-27 指定的完整口径：地量 + 趋势在 + 有人气（剔垃圾/仙股）+ 热门板块。
    # 逐条加码，看每一档还剩多少票、增量往哪边走。
    "dryup_allen": ("地量+60日≥20%+未破20日线+人气前30%+热门板块",
                    lambda d: (d["vol_pct60"] <= DRYUP_PCT) & (d["prior_ret60"] >= 20)
                              & (d["dist_ma20"] >= 0) & (d["amt_ma20_q"] >= 0.7)
                              & (d["sec_mom20_q"] >= 0.8)),
    "dryup_allen_nosec": ("同上但不要求热门板块（拆开看板块那一条的贡献）",
                          lambda d: (d["vol_pct60"] <= DRYUP_PCT) & (d["prior_ret60"] >= 20)
                                    & (d["dist_ma20"] >= 0) & (d["amt_ma20_q"] >= 0.7)),

    # ---- 形态族（2026-08-31）。**八条全否，别再重跑。** 结论记在下面，留着是为了
    # 后来的人不用再试一遍，不是因为它们还在候选里。
    # 系统现有因子全是截面统计量，没有一个描述 K 线形状，所以这批值得审一轮 —— 审完了，
    # 形态维度在 T+5 横截面上没有可用的独立 alpha：
    #   · Donchian 突破是又一个「隔夜段」案例：close 口径增量 −0.09，产品口径 −0.47。
    #     突破日的收益整个长在收盘→次日开盘的跳空里，追开盘是明确亏钱的。
    #   · Choppiness 双向都是负增量（趋势态 −0.04 / 震荡态 −0.09），不是方向选反，
    #     是这个维度本身没区分力。KAMA 的效率比与它数学同源，不用再测。
    #   · 天量方向符号一致但幅度不值钱（收阳 +0.02 / 收阴 −0.01），扣不动 0.312 摩擦。
    #   · Supertrend 存量方向和翻转时点都为负，翻转那条去右尾 −0.28。
    #   · chop_trend 当闸门装在 smart 池上出过 +1.74pp/t=2.07，看着像发现 —— 装到
    #     pattern 池只剩 +0.11pp/t=0.23，不复现，且留存率 22%→49%（两个池里咬的
    #     根本不是同一批票）。这是「t≈2 是多重检验产物」的又一个标本。
    # 这批四个概念各带一条对照，一次提交一起过 Holm：只报好看的那条等于自己绕过校正。
    #   震荡度  chop_trend / chop_range   —— 反向对照，两边都为正就说明是噪音
    #   通道方向 supertrend_up / _flip     —— 存量状态 vs 翻转时点，分开问「哪一段有用」
    #   通道突破 donchian20 / 55           —— 剂量对照，只有一档灵多半是噪音
    #   天量方向 maxvol_down / _up         —— 派发判据的两侧，预期一负一正
    "chop_trend": ("处在单边趋势态（Choppiness ≤ 38.2）", lambda d: d["chop14"] <= 38.2),
    "chop_range": ("处在震荡态（Choppiness ≥ 61.8）", lambda d: d["chop14"] >= 61.8),
    "supertrend_up": ("Supertrend(10,3) 方向为多", lambda d: d["st_dir"] == 1),
    "supertrend_flip": ("Supertrend 当日刚翻多（新信号）", lambda d: d["st_flip_up"]),
    "donchian20": ("突破前 20 日最高价（20 日新高）", lambda d: d["donchian20"]),
    "donchian55": ("突破前 55 日最高价（55 日新高）", lambda d: d["donchian55"]),
    "maxvol_down": ("近 60 日天量那天收阴（疑似派发）", lambda d: d["maxvol60_down"]),
    "maxvol_up": ("近 60 日天量那天收阳（派发判据的另一侧）",
                  lambda d: d["maxvol60_valid"] & ~d["maxvol60_down"]),

    # ---- 环境族（2026-08-31）。这些是**择时**谓词：同一天全市场共享一个值，
    # 所以它们的「对照增量」没有意义（每天所有票同时命中，增量恒为 0），单独 --rule
    # 审它们是误用。正确用法只有两种：
    #   1) --by-regime 分层，问「某条选股规则的 alpha 在什么环境下还在」；
    #   2) --replay <池> --variant "base,-regime_cold"，问「这个闸门装上去池子会不会变好」，
    #      此时该看的是「较基线」那一列的同日配对差，不是对照增量。
    # ---- 开盘区间族（2026-09-01）。答的是「该不该在竞价就动手」。
    # 竞价池的名单 09:25 就定了、开盘即可买；另一种做法是等当天把方向走出来再买。
    # 手上没有分钟数据（intraday_snapshots 每天只有一张下午的快照），所以「开盘 30
    # 分钟不破竞价价」的字面版本做不了，这里用日线能表达的最接近口径：
    # 当天收盘有没有守住开盘价。配 --entry close 使用 —— 这个条件要到收盘才知道，
    # 所以只能收盘买，不能拿它去筛开盘买入的名单（那是未来函数）。
    # 2026-09-01 结论：**否决，别再重跑日线版本**。全市场 1590 个交易日：
    #   守住开盘价 +0.02 / 跌破开盘价 +0.01 —— 正反两面都贴着零且同号，
    #   典型的双向检验失败，说明这个维度没有区分力（同 chop 族那次）。
    #   held_open_firm 的 +0.04 也远低于 0.312 的摩擦线。
    #   竞价池上曾出现 +0.46/+0.78（t=1.0~1.2），是 36 天样本的噪音。
    # 注意边界：被否的是**日线口径**。真正的开盘区间突破用开盘头 15~30 分钟的区间，
    # 本地没有分钟数据（intraday_snapshots 每天只有一张下午快照），那个版本仍未检验。
    "held_open": ("当天守住开盘价（收盘≥开盘）", lambda d: d["close"] >= d["open"]),
    "held_open_firm": ("守住开盘价且盘中没深跌（最低价不低于开盘 2%）",
                       lambda d: (d["close"] >= d["open"]) & (d["low"] >= d["open"] * 0.98)),
    "lost_open": ("当天跌破开盘价（收盘<开盘）——上面那条的反面",
                  lambda d: d["close"] < d["open"]),

    "regime_cold": ("选出日大盘偏冷", lambda d: d["regime"] == "偏冷"),
    "regime_warm": ("选出日大盘偏暖", lambda d: d["regime"] == "偏暖"),
    # 大样本里唯一呈单调剂量反应的组合：追高型选股的增量随环境转冷单调恶化
    # （偏暖 +0.01 / 中性 −0.34 / 偏冷 −1.05）。单调是它值得当闸门试的理由。
    "chase_in_cold": ("冷环境里的追高票（近20日≥25% 且贴高点 且大盘偏冷）",
                      lambda d: (d["prior_ret20"] >= 25) & (d["dist_high20"] >= -3)
                                & (d["regime"] == "偏冷")),
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


def replay_mask(panel: pd.DataFrame, db: str, pool: str) -> pd.Series:
    """把最近一次回放选出来的票当信号。

    比线上留痕更适合审闸门：留痕只有两个月、25 个交易日，过不了样本闸；回放是
    point-in-time 重建的 12 个月 × 52 期 × top20，日数和样本量都够，且与线上共用
    同一套评分函数。代价是它按收盘价、每 5 个交易日一期，不含盘中时机层。
    """
    conn = sqlite3.connect(db, timeout=60)
    try:
        run = conn.execute(
            "SELECT run_id FROM replay_runs WHERE status='done' "
            "ORDER BY created_at DESC LIMIT 1").fetchone()
        if not run:
            raise SystemExit("快照里没有跑完的回放，先在页面上跑一次「重跑回放」")
        rows = conn.execute(
            "SELECT as_of, symbol FROM replay_results WHERE run_id=? AND pool=?",
            (run[0], pool)).fetchall()
    finally:
        conn.close()
    keys = {(str(d), str(s).zfill(6)) for d, s in rows}
    return pd.Series([(d, s) in keys for d, s in zip(panel["date"], panel["symbol"])],
                     index=panel.index)


def parse_variant(spec: str) -> tuple[str, list[tuple[str, str]]]:
    """"base,-chase20,+consolidate" -> ("base", [("-","chase20"),("+","consolidate")])。

    `-` 是剔除（这些票不要了），`+` 是必须满足。闸门只能收窄名单，不能引入新票 ——
    「加一条规则把别的票捞进来」是换一个池，不是给这个池装闸门，两者不可比。
    """
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    if not parts:
        raise SystemExit(f"空的 --variant：{spec!r}")
    gates = []
    for p in parts[1:]:
        if p[0] not in "+-":
            raise SystemExit(f"闸门要以 + 或 - 开头：{p!r}")
        if p[1:] not in RULES:
            raise SystemExit(f"未知规则 {p[1:]}，用 --list 看可用的")
        gates.append((p[0], p[1:]))
    return parts[0], gates


def paired_vs_base(panel: pd.DataFrame, base: pd.Series, variant: pd.Series) -> dict:
    """变体相对基线的日配对差：同一天两边各自的平均超额相减。

    这才是「闸门要不要装」的决策数字。变体自己的对照增量回答的是另一个问题
    （这批票有没有 alpha），两者都要看：闸门可能提高了均值却把样本砍到没法用。
    """
    b = panel[base].groupby("date")["fwd_excess"].mean()
    v = panel[variant].groupby("date")["fwd_excess"].mean()
    both = pd.concat([b.rename("b"), v.rename("v")], axis=1).dropna()
    if len(both) < 2:
        return {"vs_base": None, "vs_base_t": None, "vs_base_days": len(both)}
    diff = (both["v"] - both["b"]).tolist()
    m = statistics.mean(diff)
    sd = statistics.stdev(diff)
    t = m / (sd / math.sqrt(len(diff))) if sd else 0.0
    return {"vs_base": round(m, 3), "vs_base_t": round(t, 2), "vs_base_days": len(diff)}


def report_market(panel: pd.DataFrame, horizon: int) -> None:
    """全市场量能环境 → 之后 horizon 日全市场收益。

    直接对撞两种说法：「地量=变盘买点」还是「缩量=没有赚钱效应、不在右侧环境」。
    单位是交易日不是个股，所以这里答的是择时，跟选谁无关。

    t 值用**不重叠**子样本算：相邻交易日的 T+5 收益共用 4 天行情，按全部日算 t 会
    把标准误压小好几倍，凭空造出显著性。
    """
    day = _market_env(panel)
    fwd = panel.groupby("date")["fwd_ret"].median().rename("fwd")
    day = day.join(fwd).dropna(subset=["mkt_amt_pct60", "fwd"])
    bands = [(0.0, 0.1, "极致地量 ≤10%"), (0.1, 0.25, "地量 10~25%"),
             (0.25, 0.75, "中性 25~75%"), (0.75, 0.9, "放量 75~90%"),
             (0.9, 1.01, "天量 ≥90%")]
    overall = day["fwd"].mean()
    print(f"\n全市场量能环境 → 之后 T+{horizon} 全市场中位收益"
          f"（{len(day)} 个交易日，总体均值 {overall:+.2f}%）")
    print(f"  {'量能分位档':16s}{'日数':>6s}{'之后收益':>10s}{'相对总体':>10s}{'不重叠t':>9s}")
    for lo, hi, label in bands:
        sel = day[(day["mkt_amt_pct60"] >= lo) & (day["mkt_amt_pct60"] < hi)]
        if sel.empty:
            continue
        # 不重叠子样本：同一档里按 horizon 间隔挑，避免共用行情的重复计数
        idx = sorted(sel.index)
        keep, last = [], None
        for d in idx:
            if last is None or (pd.Timestamp(d) - pd.Timestamp(last)).days >= horizon * 1.4:
                keep.append(d)
                last = d
        vals = (sel.loc[keep, "fwd"] - overall).to_numpy()
        t = (vals.mean() / (vals.std(ddof=1) / math.sqrt(len(vals)))) if len(vals) > 2 else float("nan")
        print(f"  {label:16s}{len(sel):>6d}{sel['fwd'].mean():>+9.2f}%"
              f"{sel['fwd'].mean() - overall:>+9.2f}%{t:>9.2f}")


def report_regime(panel: pd.DataFrame, rule_names: list[str], horizon: int) -> None:
    """把每条规则按大盘环境分层再审一遍。

    为什么绝对收益必须和超额一起看
    ------------------------------
    本文件其余部分一律用「相对全市场中位的超额」，因为审的是选股能力。但产品的用户
    买的是股票，不是价差 —— 冷环境里「跑赢中位 +2pp」和「亏 5%」完全可以同时成立。
    「今天该不该出手」这个问题只能用绝对收益回答，所以这张表两个都给。
    """
    print(f"\n按大盘环境分层（口径同生产 regime 模块）· T+{horizon}")
    base = panel.groupby("regime")["fwd_ret"].agg(["mean", "size"])
    print("  全市场基准：" + " | ".join(
        f"{r} {base.loc[r, 'mean']:+.2f}%（{panel[panel['regime'] == r]['date'].nunique()} 天）"
        for r in ("偏暖", "中性", "偏冷") if r in base.index))
    print(f"  {'规则':22s}{'环境':>6s}{'样本':>8s}{'天数':>6s}{'绝对':>9s}{'超额':>8s}"
          f"{'对照增量':>10s}{'CI下沿':>9s}")
    for name in rule_names:
        desc, fn = RULES[name]
        mask = fn(panel)
        for reg in ("偏暖", "中性", "偏冷"):
            sub = panel[panel["regime"] == reg]
            if sub.empty:
                continue
            m = mask.reindex(sub.index).fillna(False)
            sel = sub[m]
            if len(sel) < 50:
                continue
            inc, _base_v, clusters = matched_increment(sub, m)
            if inc:
                mean_inc, t_inc, _p, _n = _cluster_stats(inc, clusters)
                lo, _hi = _ci95(inc, clusters)
            else:
                mean_inc = t_inc = lo = float("nan")
            print(f"  {name:22s}{reg:>6s}{len(sel):>8d}{sel['date'].nunique():>6d}"
                  f"{sel['fwd_ret'].mean():>+8.2f}%{sel['fwd_excess'].mean():>+8.2f}"
                  f"{mean_inc:>10.2f}{lo:>9.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--since", default="2019-01-01", help="面板起始日；越长时间稳定性越可信")
    ap.add_argument("--horizon", type=int, default=5,
                    help="持有交易日数（T+N）。配 --entry open0 时 0 = 买开盘、当日收盘")
    ap.add_argument("--rule", action="append", default=[], help="内置规则名，可重复")
    ap.add_argument("--pool", action="append", default=[], help="按线上留痕审计，可重复")
    ap.add_argument("--replay", default="", help="以最近一次回放的该池选股为基线，配合 --variant")
    ap.add_argument("--variant", action="append", default=[],
                    help='闸门变体，如 "base" / "base,-chase20" / "base,-chase20,+consolidate"，可重复')
    ap.add_argument("--list", action="store_true", help="列出内置规则")
    ap.add_argument("--entry", choices=("close", "open", "open0"), default="close",
                    help="入场口径：close=当日收盘买入（含用户吃不到的隔夜段）；"
                         "open=次日开盘买入（收盘后才产生的信号用这个复核）；"
                         "open0=当日开盘买入（信号在开盘前就成立的池，如集合竞价）")
    ap.add_argument("--by-regime", action="store_true",
                    help="把每条 --rule 按大盘环境（偏暖/中性/偏冷）分层再审一遍，"
                         "同时给绝对收益——回答「这条规则在什么环境下还成立」")
    ap.add_argument("--market", action="store_true",
                    help="额外输出全市场量能环境的择时统计（不参与规则审计）")
    args = ap.parse_args()

    if args.list:
        for k, (desc, _) in RULES.items():
            print(f"  {k:18s} {desc}")
        return
    if not args.rule and not args.pool and not args.replay:
        ap.error("至少给一个 --rule / --pool / --replay")
    if args.variant and not (args.replay or args.pool):
        ap.error("--variant 需要配合 --replay 或 --pool 指定基线池")
    if args.variant and args.replay and args.pool:
        ap.error("--variant 一次只能挂在一个基线上：--replay 或 --pool 二选一")

    print(f"载入面板 since={args.since} horizon=T+{args.horizon} entry={args.entry} …")
    panel = build_panel(args.db, args.since, args.horizon, args.entry)
    print(f"面板 {len(panel):,} 行 · {panel['symbol'].nunique()} 只 · "
          f"{panel['date'].nunique()} 个交易日 · "
          f"有板块归属 {panel['sec_mom20_q'].notna().mean():.0%}")

    if args.market:
        report_market(panel, args.horizon)
    if args.by_regime and args.rule:
        report_regime(panel, args.rule, args.horizon)

    results = []
    for name in args.rule:
        if name not in RULES:
            raise SystemExit(f"未知规则 {name}，用 --list 看可用的")
        desc, fn = RULES[name]
        r = audit_one(panel, fn(panel), name, args.horizon)
        r["desc"] = desc
        results.append(r)
    for pool in args.pool:
        base_mask = pool_mask(panel, args.db, pool)
        # 不带 --variant 时照旧只审这个池本身
        specs = args.variant if (args.variant and not args.replay) else []
        if not specs:
            r = audit_one(panel, base_mask, f"pool:{pool}", args.horizon)
            r["desc"] = f"线上 {pool} 池的真实留痕"
            results.append(r)
            continue
        # 带 --variant 时用**真实留痕**当基线跑闸门对照。比 --replay 更可信：
        # 留痕是用户当时真看到的名单，回放是事后按同一套评分重建的。
        print(f"留痕基线 {pool}: {int(base_mask.sum())} 笔 · "
              f"{panel[base_mask]['date'].nunique()} 个交易日")
        for spec in specs:
            _, gates = parse_variant(spec)
            mask = base_mask.copy()
            for sign, name in gates:
                gate = RULES[name][1](panel)
                mask &= (~gate) if sign == "-" else gate
            r = audit_one(panel, mask, f"{pool}:{spec}", args.horizon)
            r["desc"] = f"留痕 {pool} 池 + 闸门 {spec}"
            r.update(paired_vs_base(panel, base_mask, mask))
            r["kept_share"] = round(float(mask.sum()) / max(1, float(base_mask.sum())), 3)
            results.append(r)

    if args.replay:
        base_mask = replay_mask(panel, args.db, args.replay)
        print(f"回放基线 {args.replay}: {int(base_mask.sum())} 笔 · "
              f"{panel[base_mask]['date'].nunique()} 期")
        specs = args.variant or ["base"]
        for spec in specs:
            _, gates = parse_variant(spec)
            mask = base_mask.copy()
            for sign, name in gates:
                gate = RULES[name][1](panel)
                mask &= (~gate) if sign == "-" else gate
            r = audit_one(panel, mask, f"{args.replay}:{spec}", args.horizon)
            r["desc"] = f"回放 {args.replay} 池 + 闸门 {spec}"
            r.update(paired_vs_base(panel, base_mask, mask))
            r["kept_share"] = round(float(mask.sum()) / max(1, float(base_mask.sum())), 3)
            results.append(r)

    holm(results)
    for r in results:
        verdict(r)

    os.makedirs(RESULTS, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = os.path.join(RESULTS, f"rule-audit-{stamp}.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"since": args.since, "horizon": args.horizon,
                   # 入场口径必须落盘：同一条规则 close 与 open 的结论可以完全相反
                   # （donchian20 是 −0.09 对 −0.47），不记就等于这份结果无法复述。
                   "entry": args.entry,
                   "generated_at": datetime.now().isoformat(timespec="seconds"),
                   "thresholds": {"MIN_SAMPLES": MIN_SAMPLES, "MIN_CLUSTERS": MIN_CLUSTERS,
                                  "MIN_STABLE_YEARS": MIN_STABLE_YEARS, "TAIL_DROP": TAIL_DROP,
                                  "FRICTION_PP": FRICTION_PP, "ALPHA": ALPHA},
                   "results": results}, fh, ensure_ascii=False, indent=2)

    has_variants = any("vs_base" in r for r in results)
    print("\n超额按交易日等权；「票权」是按票等权，带 ! 表示两者反号"
          "（好日子票少、坏日子票多，或反过来）。")
    if has_variants:
        print("「较基线」= 同日配对差，正数表示这个闸门改善了池子；「留存」= 还剩多少票。")
    head = (f"{'规则':26s}{'样本':>7s}{'日数':>6s}{'超额':>8s}{'去右尾':>8s}"
            f"{'对照增量':>10s}{'CI下沿':>9s}")
    head += f"{'较基线':>9s}{'t':>7s}{'留存':>7s}" if has_variants else f"{'稳定年':>7s}"
    print(head + "  判定")
    for r in results:
        if not r.get("samples"):
            print(f"{r['rule']:26s}{'无样本':>7s}")
            continue
        mark = "通过" if r["passed"] else "未过: " + "/".join(r["failed_gates"][:2])
        flag = "!" if r.get("weighting_conflict") else " "
        nan = float("nan")
        line = (f"{r['rule']:26s}{r['samples']:>7d}{r['clusters']:>6d}{r['avg_excess']:>7.2f}{flag}"
                f"{(r.get('inc_ex_tail') if r.get('inc_ex_tail') is not None else float('nan')):>8.2f}"
                f"{(r['inc_excess'] if r['inc_excess'] is not None else nan):>10.2f}"
                f"{(r['inc_ci_lo'] if r['inc_ci_lo'] is not None else nan):>9.2f}")
        if has_variants:
            line += (f"{(r.get('vs_base') if r.get('vs_base') is not None else nan):>9.2f}"
                     f"{(r.get('vs_base_t') if r.get('vs_base_t') is not None else nan):>7.2f}"
                     f"{(r.get('kept_share') if r.get('kept_share') is not None else nan):>7.0%}")
        else:
            line += f"{r['stable_years']:>7d}"
        print(line + f"  {mark}")
    print(f"\n明细 -> {out_path}")


if __name__ == "__main__":
    main()
