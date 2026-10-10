"""一键智选「完整版」逐日复原 + 只挑 2 只的十种挑法对比（2026-10-10）。

weight_top2.py 只比了 8 个基础因子的权重。线上排序在结构分之上还有共振加分（低位形态 +1.5、
站上 EMA8/21 强度 +1.0、均线多头 +0.5、三重确认 +1.0，封顶 4）和七不买风险硬闸门（风险 ≥2 剔除）。
这里把这些用生产同一批函数（recognize_patterns / compute_strength_metrics / check_risks）
在历史每一天的截断日线上重算，复原线上名单，再加上智选里没有的规则一起比。

复原不了的部分（如实说明）：盘中实时信号、新闻催化、早盘时间折价 —— 只存在于当天盘中。
ST 判断用当前名称。

口径：anchor 2026-10-09 往前 24 个月、每 3 个交易日一期；候选 = 当日成交额 ≥3000 万、满 80 根；
每期结构分前 50 → 共振/风险 → 线上前 20。次日开盘买 → T+5 收盘，对当期全部候选均值。
挑法（看结果前写定，见 SCHEMES）；分前 60% / 后 40%（7 月后新风格）报，两段都赢「线上前 20 平均」才算。

用法：python experiments/snapshot_db.py && python experiments/smart_full_lab.py
"""
from __future__ import annotations

import json
import os
import pickle
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd

from factor_scores import DEFAULT_DB, HERE, REPO, sample_symbols, sessions_axis

CACHE = os.path.join(HERE, ".cache")
POOL = 50


def enrich_symbol(payload: dict) -> list:
    """一只股票在若干会话日的共振 / 风险 / 位置，全部只用截至当日的日线。"""
    from quantcore.quant.data import normalize_ohlcv
    from quantcore.quant.integrations import recognize_patterns
    from quantcore.quant.relative_strength import compute_strength_metrics
    from quantcore.quant.risk_check import check_risks

    sym, name, sessions, db = payload["symbol"], payload["name"], payload["sessions"], payload["db"]
    conn = sqlite3.connect(db, timeout=60)
    raw = pd.read_sql_query("SELECT date, open, high, low, close, volume, amount FROM daily_kline "
                            "WHERE symbol=? ORDER BY date", conn, params=(sym,))
    conn.close()
    nd = normalize_ohlcv(raw)
    dates = pd.to_datetime(nd["date"]).dt.strftime("%Y-%m-%d") if "date" in nd else pd.Series(nd.index.astype(str).str[:10])
    out = []
    for s in sessions:
        df = nd[(dates <= s).to_numpy()].tail(540)
        if len(df) < 60:
            continue
        try:
            rec = recognize_patterns(sym, df)
            pattern = any(p.get("active") and float(p.get("strength") or 0) >= 70 for p in rec.patterns)
        except Exception:
            pattern = False
        try:
            sm = compute_strength_metrics(df)
        except Exception:
            sm = None
        try:
            risk = int(check_risks(sym, name, df).get("risk_count") or 0)
        except Exception:
            risk = 0
        bonus = 1.5 if pattern else 0.0
        strong = bool(sm and sm["above_ema8"] and sm["above_ema21"])
        if strong:
            bonus += 1.0 + (0.5 if sm["ema_stack"] else 0.0)
        if pattern and strong:
            bonus += 1.0
        c = df["close"].astype(float)
        h = (df["high"] if "high" in df else df["close"]).astype(float)
        ret20 = float(c.iloc[-1] / c.iloc[-21] - 1) if len(c) > 21 else np.nan
        dh20 = float(c.iloc[-1] / h.iloc[-21:-1].max() - 1) if len(c) > 21 else np.nan
        out.append((s, sym, {"pattern": pattern, "strong": strong, "bonus": min(bonus, 4.0), "risk": risk,
                             "ret20": ret20, "dist_high20": dh20}))
    return out


def build(db: str, anchor: str, months: int, step: int, workers: int):
    pk = os.path.join(CACHE, f"smart_full_{anchor}_{months}m.pkl")
    if os.path.exists(pk):
        return pickle.load(open(pk, "rb"))
    conn = sqlite3.connect(db, timeout=60)
    sessions, since = sessions_axis(conn, anchor, months, step)
    syms = [r[0] for r in conn.execute(
        "SELECT symbol, COUNT(*) n FROM daily_kline WHERE date>=? GROUP BY symbol HAVING n>=86", (since,))]
    names = dict(conn.execute("SELECT symbol, name FROM stock_meta"))
    conn.close()
    print(f"sessions={len(sessions)} ({sessions[0]}..{sessions[-1]}) symbols={len(syms)}", flush=True)
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        chunks = [syms[i::workers * 3] for i in range(workers * 3)]
        futs = [ex.submit(sample_symbols, {"symbols": c, "sessions": sessions, "since": since, "db": db}) for c in chunks]
        for f in as_completed(futs):
            rows += [(s, sym, v) for s, sym, v, _ in f.result()]
    cand = pd.DataFrame([{"d": s, "symbol": sym, **v} for s, sym, v in rows])
    cand["name"] = cand.symbol.map(names).fillna("")
    cand = cand[~cand.symbol.str.startswith(("8", "4", "92"))]
    st = cand.name.str.upper().str.contains("ST") | cand.name.str.contains("退")
    cand["ex"] = cand.ret_open - cand.groupby("d").ret_open.transform("mean")   # 对全部候选（含 ST）的均值
    top = cand[~st].sort_values(["d", "composite"], ascending=[True, False]).groupby("d").head(POOL)
    need = top.groupby("symbol").d.apply(list).to_dict()
    print(f"复原共振/风险：{len(top)} 个（会话×股票），{len(need)} 只", flush=True)
    enr = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(enrich_symbol, {"symbol": s, "name": names.get(s, ""), "sessions": ds, "db": db})
                for s, ds in need.items()]
        for i, f in enumerate(as_completed(futs), 1):
            for s, sym, v in f.result():
                enr[(s, sym)] = v
            if i % 300 == 0:
                print(f"  {i}/{len(futs)}", flush=True)
    e = pd.DataFrame([{"d": s, "symbol": sym, **v} for (s, sym), v in enr.items()])
    top = top.merge(e, on=["d", "symbol"], how="left")
    pickle.dump((cand[["d", "symbol", "ex"]], top), open(pk, "wb"))
    return cand[["d", "symbol", "ex"]], top


def extras(top: pd.DataFrame, db: str) -> pd.DataFrame:
    """智选里没有的信息：行业 20 日动量、多因子模型分。"""
    conn = sqlite3.connect(db, timeout=60)
    k = pd.read_sql_query("SELECT symbol, date, close FROM daily_kline WHERE date >= '2024-06-01'", conn)
    conn.close()
    cl = k.pivot(index="date", columns="symbol", values="close")
    r20 = cl / cl.shift(20) - 1
    ind = json.load(open(os.path.join(REPO, "runtime", "industry_map.json"), encoding="utf-8"))
    ind_mom = r20.T.groupby(pd.Series(ind).reindex(r20.columns)).transform("mean").T
    ml = pd.read_pickle(os.path.join(CACHE, "ml_preds_noev_drop-lprice.pkl"))
    ml["mlp"] = ml.groupby("date")["score"].rank(pct=True)
    mlp = ml.pivot(index="date", columns="symbol", values="mlp")
    look = lambda mat, d, s: mat.at[d, s] if d in mat.index and s in mat.columns else np.nan  # noqa: E731
    top = top.copy()
    top["ind_mom"] = [look(ind_mom, d, s) for d, s in zip(top.d, top.symbol)]
    top["ml"] = [look(mlp, d, s) for d, s in zip(top.d, top.symbol)]
    return top


def online20(x: pd.DataFrame) -> pd.DataFrame:
    x = x[x.risk.fillna(0) < 2]
    return x.assign(rank_key=x.composite + x.bonus.fillna(0)).nlargest(20, "rank_key")


SCHEMES = {
    "线上前20平均（参照）": lambda x: online20(x),
    "线上第1-2名": lambda x: online20(x).head(2),
    "仅结构分前2（不加共振）": lambda x: x.nlargest(2, "composite"),
    "三重确认里最高2": lambda x: online20(x).query("pattern == True and strong == True").head(2),
    "有低位形态里最高2": lambda x: online20(x).query("pattern == True").head(2),
    "风险0且近20日涨<10%": lambda x: online20(x).query("risk == 0 and ret20 < 0.10").head(2),
    "线上前20里回调最深2": lambda x: online20(x).nsmallest(2, "dist_high20"),
    "线上前20里当日涨最少2": lambda x: online20(x).nsmallest(2, "ret_1d"),
    "线上前20里行业最强2": lambda x: online20(x).nlargest(2, "ind_mom"),
    "线上前20里模型分最高2": lambda x: online20(x).dropna(subset=["ml"]).nlargest(2, "ml"),
    "前50里模型分最高2": lambda x: x.dropna(subset=["ml"]).nlargest(2, "ml"),
    "共振且不追高（有形态或强度、20日涨<15%）": lambda x: online20(x).query("(pattern == True or strong == True) and ret20 < 0.15").head(2),
}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--anchor", default="2026-10-09")
    ap.add_argument("--months", type=int, default=24)
    ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    _, top = build(a.db, a.anchor, a.months, a.step, a.workers)
    top = extras(top, a.db)
    days = sorted(top.d.unique())
    cut = days[len(days) * 6 // 10]
    print(f"\n{len(days)} 期 {days[0]} ~ {days[-1]}；后 40% 从 {cut} 起。次日开盘买→T+5 收盘，对全部候选均值")
    for name, fn in SCHEMES.items():
        per, hits = [], []
        for d, x in top.groupby("d"):
            sel = fn(x)
            if len(sel):
                per.append((d, sel.ex.mean()))
                hits += list(sel.ex > 0)
        t = pd.DataFrame(per, columns=["d", "ex"])
        f, b = t[t.d < cut].ex, t[t.d >= cut].ex
        tt = t.ex.mean() / t.ex.std() * np.sqrt(len(t))
        print(f"{name:34s} 全段 {t.ex.mean()*100:+.2f}pp t={tt:+.2f} 单票胜率 {np.mean(hits):.0%} ({len(t)}期) | "
              f"前60% {f.mean()*100:+.2f} | 后40% {b.mean()*100:+.2f}", flush=True)


if __name__ == "__main__":
    main()
