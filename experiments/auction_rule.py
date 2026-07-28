"""竞价买入规则回测：现行「追高开」规则到底行不行，加什么过滤能救。

背景：线上竞价池的真实留痕是 231 笔、平均超额 −0.61pp、中位 −1.46pp、胜率 42%，
也就是说这条规则在亏钱。它的评分是 `高开×6 + 量比×8 + 板块共振×1.8 + 板块近段涨幅×0.6`，
四项全是追涨动量，既不看个股自身位置（是不是已经在高位、是不是已经破位），
也不看当天的市场环境。

口径：
- 当日开盘买入（竞价成交），T+1 收盘卖出
- 超额 = 个股收益 − 当期全市场中位收益（同一根轴上所有变体共用同一批观测，配对可比）
- 候选过滤：成交额 ≥1 亿、股价 ≥3 元，排除 ST/退市

不含的维度：板块共振与板块近段涨幅需要行业映射，而本地行业覆盖度只有约四成，
放进回测会引入系统性偏差。所以这里只检验**个股位置**与**市场环境**两类过滤，
它们恰好是现行规则完全缺失的两块。

    python experiments/snapshot_db.py
    python experiments/auction_rule.py --months 12 --top-k 10
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from factor_scores import DEFAULT_DB  # noqa: E402

MIN_AMOUNT = 1e8
MIN_PRICE = 3.0
RESULTS = os.path.join(HERE, "results")


def load_panel(db: str, months: int) -> dict:
    """{date: {symbol: (open, close, prev_close, ma20, peak120, amount)}}，按日期升序。"""
    conn = sqlite3.connect(db, timeout=120)
    dates = [r[0] for r in conn.execute(
        "SELECT date FROM daily_kline WHERE amount>0 GROUP BY date HAVING COUNT(*)>4000 "
        "ORDER BY date DESC LIMIT ?", (int(months * 21) + 130,))]
    dates = sorted(dates)
    rows = conn.execute(
        "SELECT symbol, date, open, close, amount FROM daily_kline WHERE date>=? AND amount>0",
        (dates[0],)).fetchall()
    conn.close()

    series: dict = {}
    for sym, d, o, c, a in rows:
        series.setdefault(sym, {})[d] = (o, c, a)
    return dates, series


def build_features(dates: list, series: dict) -> dict:
    """逐股预算 MA20 与 120 日高点，避免每期重扫（前缀型，无前视）。"""
    feats: dict = {}
    for sym, by_date in series.items():
        ds = sorted(by_date)
        closes = [by_date[d][1] for d in ds]
        f = {}
        for i, d in enumerate(ds):
            if i < 20:
                continue
            ma20 = sum(closes[i - 20:i]) / 20                  # 不含当日，避免用到当日收盘
            window = closes[max(0, i - 120):i]
            peak = max(window) if window else closes[i]
            f[d] = (ma20, peak)
        feats[sym] = (ds, {d: i for i, d in enumerate(ds)}, f)
    return feats


def sector_trend(dates, series, imap, i, window=5, top_k=10) -> set:
    """当日「近段趋势热门板块」——复刻线上 _compute_hot_industries 的口径：
    按行业内成分股最近 window 个交易日平均涨幅排序，取居前且为正的 top_k 个。"""
    if i < window:
        return set()
    d0, d1 = dates[i - window], dates[i - 1]
    by_ind: dict = {}
    for sym, bd in series.items():
        ind = imap.get(sym)
        if not ind or d0 not in bd or d1 not in bd:
            continue
        p0 = bd[d0][1]
        if p0 <= 0:
            continue
        by_ind.setdefault(ind, []).append((bd[d1][1] / p0 - 1) * 100)
    scored = {k: sum(v) / len(v) for k, v in by_ind.items() if len(v) >= 4}
    return {k for k, v in sorted(scored.items(), key=lambda kv: -kv[1])[:top_k] if v > 0}


def run(dates, series, feats, top_k: int, rule: str, imap=None, since=None) -> tuple:
    """返回 (每期超额序列, 总笔数)。"""
    per_session = []
    trades = 0
    for i in range(1, len(dates) - 1):
        d, prev, nxt = dates[i], dates[i - 1], dates[i + 1]
        if since and d < since:
            continue
        hot = sector_trend(dates, series, imap, i) if (rule == "S" and imap) else None
        picks = []
        rets_all = []
        for sym, by_date in series.items():
            if d not in by_date or prev not in by_date or nxt not in by_date:
                continue
            o, c, amt = by_date[d]
            pc = by_date[prev][1]
            if pc <= 0 or o <= 0:
                continue
            rets_all.append((by_date[nxt][1] / o - 1) * 100)
            if amt < MIN_AMOUNT or o < MIN_PRICE:
                continue
            gap = (o / pc - 1) * 100
            if not (1.5 <= gap <= 6.0):        # 现行规则的健康高开区间
                continue
            _ds, _idx, f = feats.get(sym, (None, None, {}))
            ma20, peak = f.get(d, (0.0, 0.0))
            if ma20 <= 0:
                continue
            above_ma20 = pc >= ma20
            drawdown = (pc / peak - 1) * 100 if peak > 0 else 0.0
            if rule in ("B", "E") and drawdown <= -15 and not above_ma20:
                continue                        # 排除「翻倍后崩塌」的高位风险股
            if rule in ("C", "E") and not above_ma20:
                continue                        # 个股趋势未坏才参与
            if hot is not None and imap.get(sym) not in hot:
                continue                        # 线上的「只在近段强势板块里选」
            picks.append((gap, sym))
        if not rets_all or len(rets_all) < 500:
            continue
        med = statistics.median(rets_all)
        if rule in ("D", "E"):
            # 市场环境：昨日成交额加权涨幅为负则空仓（钱在亏的日子不追高开）
            wsum = wtot = 0.0
            for sym, by_date in series.items():
                if prev not in by_date or dates[i - 2] not in by_date:
                    continue
                pc2 = by_date[dates[i - 2]][1]
                if pc2 <= 0:
                    continue
                wsum += ((by_date[prev][1] / pc2 - 1) * 100) * by_date[prev][2]
                wtot += by_date[prev][2]
            if wtot > 0 and wsum / wtot < 0:
                per_session.append(0.0)          # 空仓 = 不赚不亏，正确计入
                continue
        picks.sort(reverse=True)
        chosen = [s for _g, s in picks[:top_k]]
        if not chosen:
            continue
        ex = [(series[s][nxt][1] / series[s][d][0] - 1) * 100 - med for s in chosen]
        per_session.append(float(np.mean(ex)))
        trades += len(ex)
    return np.array(per_session, dtype=float), trades


def stats(a: np.ndarray) -> dict:
    if a.size < 3:
        return {}
    m, sd = float(a.mean()), float(a.std(ddof=1))
    return {"avg_pp": round(m, 3), "median_pp": round(float(np.median(a)), 3),
            "win": round(float((a > 0).mean()) * 100, 1),
            "t": round(m / sd * np.sqrt(a.size), 2) if sd > 0 else None, "n": int(a.size)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--top-k", type=int, default=10)
    a = ap.parse_args()

    dates, series = load_panel(a.db, a.months)
    feats = build_features(dates, series)
    print(f"sessions={len(dates)} symbols={len(series)}", flush=True)

    labels = {
        "A_current": "现行：只追健康高开",
        "B_no_topping": "A + 排除高位崩塌股",
        "C_trend_ok": "A + 个股仍在 MA20 上方",
        "D_market_gate": "A + 昨日加权收跌则空仓",
        "E_all": "B + C + D 全叠加",
    }
    out = {"months": a.months, "top_k": a.top_k, "variants": {}}
    base = None
    for key, desc in labels.items():
        arr, n = run(dates, series, feats, a.top_k, key[0])
        st = stats(arr)
        st["trades"] = n
        st["desc"] = desc
        if key == "A_current":
            base = arr
        elif base is not None and arr.size == base.size:
            d = arr - base
            sd = float(d.std(ddof=1))
            st["vs_A_pp"] = round(float(d.mean()), 3)
            st["vs_A_t"] = round(float(d.mean()) / sd * np.sqrt(d.size), 2) if sd > 0 else None
        out["variants"][key] = st
        vs = f"  vs A {st.get('vs_A_pp', 0):+.3f}pp (t={st.get('vs_A_t')})" if "vs_A_pp" in st else ""
        print(f"{key:<14} {desc:<24} 超额 {st['avg_pp']:+.3f}pp 中位 {st['median_pp']:+.3f} "
              f"胜率 {st['win']}% t={st['t']} 期数 {st['n']} 笔数 {n}{vs}", flush=True)

    os.makedirs(RESULTS, exist_ok=True)
    with open(os.path.join(RESULTS, "auction_rule.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2, default=float)


if __name__ == "__main__":
    main()
