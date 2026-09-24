"""线上「盘中追涨重排」层到底有没有贡献（2026-09-24）。

起因
----
线上留痕 2026-07~09（rule_audit --pool smart）：一键智选 T+10 超额 −5.16pp，
匹配对照增量 −1.65（CI −3.07~−0.23，过 Holm）——在同涨幅档、同流动性档的票里，
智选挑出来的那批**显著更差**。回放验证过的只有日 K 结构分；线上在它之上又叠了两层
从没过审计尺子的东西（app/lite_main.py `_apply_realtime_rerank`）：

1. 盘中强度 22%：0.78×结构分 + 0.22×(0.75×(50+5×当日涨幅) + 0.25×成交额分位)
   —— 当日每涨 1% 名次分 +0.825，涨停票凭当日涨幅就多 8 分；
2. 当日主题强弱 ±4 分：当日板块涨幅分位映射。

两者都是「当天谁在涨就往前排」。本脚本在 12 个月回放的**全量候选**上复刻这两层，
问一个事先定好的问题：**在结构分前 60 名里，按线上公式重排取前 20，比直接取结构分前
20 更好还是更差？** 阈值、候选深度、公式全部照抄线上，不在看到结果后调。

口径
----
- 候选：最近一次完成回放的 replay_scan（smart 池结构分，52 期，每 5 个交易日一期）
- 盘中量：用 as_of 当日收盘的涨幅与成交额近似「盘中看到的」——线上用户是盘中看的，
  这会让追涨层在本实验里拿到比实盘**更完整**的当日信息，结论若为负只会更负
- 入场：close = as_of 收盘买；open = 次日开盘买
- 超额：个股收益 − 同期全市场中位；每期先等权平均再跨期统计
- 配对：同一期 B−A，t 按期数算（H>5 时相邻期持有窗口重叠，t 偏乐观，已在输出标注）

用法
----
    python experiments/snapshot_db.py
    python experiments/intraday_rerank_ab.py
    python experiments/intraday_rerank_ab.py --oos 2026-08-04   # 回放没见过的窗口，逐日现算结构分
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "experiments" / ".snapshot.sqlite"
DEPTH = 60          # 线上 smart_pool(limit=20, spare=40) 的结构底池深度
TOP = 20
W_INTRADAY = 0.22   # app/lite_main.py SMART_POOL_INTRADAY_WEIGHT
SECTOR_BONUS = 4.0  # app/lite_main.py SMART_POOL_SECTOR_BONUS 默认值
HORIZONS = (1, 5, 10, 20)


def clip(x):
    return np.clip(x, 0.0, 100.0)


def load(conn):
    run = conn.execute("SELECT run_id, params_json FROM replay_runs WHERE status='done' "
                       "ORDER BY created_at DESC LIMIT 1").fetchone()
    params = json.loads(run[1])
    keys = [r[0] for r in conn.execute("SELECT DISTINCT param_key FROM replay_scan")]
    sessions = sorted({r[0] for r in conn.execute(
        "SELECT as_of FROM replay_results WHERE run_id=? AND pool='smart'", (run[0],))})
    key = next(k for k in keys if k.endswith(f"{sessions[0]}-{sessions[-1]}-{len(sessions)}")
               and k.startswith("v"))
    rows = []
    for (blob,) in conn.execute("SELECT candidates_json FROM replay_scan WHERE param_key=?", (key,)):
        rows += [c for c in json.loads(blob) if c["pool"] == "smart"]
    cands = pd.DataFrame(rows)[["as_of", "symbol", "score"]]
    cands["symbol"] = cands["symbol"].astype(str).str.zfill(6)
    print(f"回放 {run[0]} · {params.get('months')} 个月 · {len(sessions)} 期 · 候选 {len(cands):,} 行 · key={key}")
    return cands, sessions


def _oos_symbol(payload):
    """与 replay._replay_symbol 的 smart 分支同一套过滤与评分，只算 smart。"""
    import bisect
    from quantcore.quant.replay import MIN_BARS, PATTERN_MIN_AMOUNT, _factor_score
    from quantcore.quant.screening import exclusion_reason
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = c.execute("SELECT date, open, high, low, close, volume, amount FROM daily_kline "
                     "WHERE symbol=? AND amount>0 ORDER BY date", (payload["symbol"],)).fetchall()
    c.close()
    if len(rows) < MIN_BARS:
        return []
    df_all = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
    dates = df_all["date"].tolist()
    out = []
    for as_of in payload["sessions"]:
        i = bisect.bisect_right(dates, as_of) - 1
        if i < MIN_BARS - 1 or dates[i] != as_of:
            continue
        df = df_all.iloc[: i + 1]
        close, amount = float(df["close"].iloc[-1]), float(df["amount"].iloc[-1] or 0)
        if exclusion_reason(payload["name"], close, amount) or amount < PATTERN_MIN_AMOUNT:
            continue
        sc = _factor_score(df.tail(120))
        if sc is not None:
            out.append({"as_of": as_of, "symbol": payload["symbol"], "score": sc})
    return out


def load_oos(conn, since):
    from concurrent.futures import ProcessPoolExecutor
    days = [r[0] for r in conn.execute(
        "SELECT date FROM daily_kline WHERE date>=? GROUP BY date HAVING COUNT(*)>3000 ORDER BY date",
        (since,))]
    sessions = days[:-5]           # 末尾留 T+5 前向窗口；更长持有期缺数据的自动剔除
    names = dict(conn.execute("SELECT symbol, name FROM stock_meta"))
    syms = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM daily_kline WHERE date=?", (sessions[-1],))]
    payloads = [{"symbol": s, "name": str(names.get(s) or s), "sessions": sessions} for s in syms]
    rows = []
    with ProcessPoolExecutor(max_workers=8) as ex:
        for r in ex.map(_oos_symbol, payloads, chunksize=40):
            rows += r
    cands = pd.DataFrame(rows)
    cands["symbol"] = cands["symbol"].astype(str).str.zfill(6)
    print(f"样本外逐日 {sessions[0]}~{sessions[-1]} · {len(sessions)} 期 · 候选 {len(cands):,} 行（相邻期重叠，t 偏乐观）")
    return cands, sessions


def main():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    if "--oos" in sys.argv:
        cands, sessions = load_oos(conn, sys.argv[sys.argv.index("--oos") + 1])
    else:
        cands, sessions = load(conn)
    lo = sessions[0]
    k = pd.read_sql_query(
        "SELECT symbol, date, open, close, amount FROM daily_kline WHERE date>=? AND amount>0",
        conn, params=(str(pd.Timestamp(lo) - pd.Timedelta(days=10))[:10],))
    k["symbol"] = k["symbol"].astype(str).str.zfill(6)
    k = k.sort_values(["symbol", "date"])
    g = k.groupby("symbol", sort=False)
    k["pct"] = (k["close"] / g["close"].shift(1) - 1) * 100
    k["open1"] = g["open"].shift(-1)
    for h in HORIZONS:
        k[f"c{h}"] = g["close"].shift(-h)
    k = k.set_index(["date", "symbol"])

    ind = json.loads((ROOT / "runtime" / "industry_map.json").read_text(encoding="utf-8"))

    res = {h: {e: [] for e in ("close", "open")} for h in HORIZONS}
    prof = []
    for s in sessions:
        day = cands[cands["as_of"] == s].nlargest(DEPTH, "score").copy()
        try:
            snap = k.loc[s]
        except KeyError:
            continue
        day = day.join(snap, on="symbol", how="inner")
        if len(day) < TOP:
            continue
        # 盘中强度：成交额分位在候选池内排（与线上一致），当日涨幅
        amt_pct = day["amount"].rank(pct=True) * 100
        intra = clip(50 + day["pct"].fillna(0) * 5) * 0.75 + clip(amt_pct) * 0.25
        day["rt"] = clip(day["score"] * (1 - W_INTRADAY) + intra * W_INTRADAY)
        # 当日主题强弱：行业当日涨幅中位在全市场行业里的分位
        mk = snap[["pct"]].copy()
        mk["ind"] = [ind.get(sym) for sym in mk.index]
        ind_pct = mk.dropna().groupby("ind")["pct"].median().rank(pct=True)
        tp = day["symbol"].map(ind).map(ind_pct)
        sec = ((tp - 0.5) * 2 * SECTOR_BONUS).fillna(0)
        day["rt_sec"] = clip(day["rt"] + sec)
        # 剂量检验：权重减半 / 加半，以及只加主题不加盘中
        day["rt11"] = clip(day["score"] * 0.89 + intra * 0.11)
        day["rt33"] = clip(day["score"] * 0.67 + intra * 0.33)
        day["sec_only"] = clip(day["score"] + sec)

        picks = {
            "A_结构前20": day.nlargest(TOP, "score"),
            "B_+盘中22%": day.nlargest(TOP, "rt"),
            "C_+盘中+主题(线上)": day.nlargest(TOP, "rt_sec"),
            "D_盘中11%": day.nlargest(TOP, "rt11"),
            "E_盘中33%": day.nlargest(TOP, "rt33"),
            "F_只加主题": day.nlargest(TOP, "sec_only"),
        }
        prof.append({n: (p["pct"].median(), len(set(p["symbol"]) & set(picks["A_结构前20"]["symbol"])))
                     for n, p in picks.items()})
        for h in HORIZONS:
            if snap[f"c{h}"].notna().sum() < 1000:
                continue
            for e in ("close", "open"):
                base = snap["open1"] if e == "open" else snap["close"]
                mret = (snap[f"c{h}"] / base - 1).dropna()
                med = mret.median()
                row = {}
                for n, p in picks.items():
                    b = p["open1"] if e == "open" else p["close"]
                    r = (p[f"c{h}"] / b - 1).dropna()
                    row[n] = (r.mean() - med) * 100 if len(r) else np.nan
                res[h][e].append(row)

    names = list(prof[0])
    print(f"\n名单画像（期中位）：当日涨幅中位 / 与结构前20重合只数")
    for n in names:
        print(f"  {n:18s} 当日涨幅 {np.nanmedian([p[n][0] for p in prof]):+.2f}%  重合 {np.median([p[n][1] for p in prof]):.0f}/20")

    out = {}
    for e in ("close", "open"):
        print(f"\n=== 入场 {e} ===  （超额 pp，期等权；配对 = 该变体 − A）")
        print("H     " + "  ".join(f"{n}" for n in names) + "   | 各变体 − A (t)")
        for h in HORIZONS:
            df = pd.DataFrame(res[h][e]).dropna()
            line = f"T+{h:<2d} " + "".join(f"{df[n].mean():>8.2f}" for n in names) + "  |"
            for n in names[1:]:
                d = df[n] - df[names[0]]
                t = d.mean() / (d.std(ddof=1) / math.sqrt(len(d))) if len(d) > 2 and d.std() > 0 else float("nan")
                line += f"   {d.mean():+.2f} ({t:+.2f})"
                out[f"{e}_T{h}_{n}"] = {"diff": round(d.mean(), 3), "t": round(t, 2), "n": len(d)}
            print(line + ("   *窗口重叠" if h > 5 else ""))
    tag = "oos" if "--oos" in sys.argv else "replay"
    (ROOT / "experiments" / "results" / f"intraday_rerank_ab_{tag}_2026-09-24.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(ROOT))
    main()
