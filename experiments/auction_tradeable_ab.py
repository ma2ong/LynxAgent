"""竞价规则在**可交易口径**下到底还剩多少超额。

为什么要新写一个而不是改 auction_rule.py
----------------------------------------
`auction_rule.py` 给出的 A_current = +0.438pp / t=2.57 是这个功能上线的依据，但它有
两处口径问题，任何一处都会把数字抬上去：

1. **候选用当日全天成交额筛**（`amt < MIN_AMOUNT` 里的 amt 是当日成交额）。09:25 决策
   时这个数不存在。等于先知道了哪些票今天会放量，再回头挑它们。
2. **组合取均值、基准取中位**。README 已经写过这条：个股收益右偏，实测全市场均值比
   中位高 +0.134pp/日（盘中）、+0.196pp/日（T+1），这部分与选股能力无关。

本文件把两处都改掉：流动性看**昨日**成交额，基准与组合同为均值、且限定在同一个可交易
范围内（昨日成交额≥1亿、开盘价≥3）。

还补了原文件缺的一件事：**卖点**。A 股 T+1，当日买入最早次日才能卖，所以「买开盘、当日
收盘卖」这一列只能当参照，不能当收益。真正要看的是 T+1 开盘 / T+1 收盘。

口径
----
| 项 | 取值 |
|---|---|
| 买点 | 当日开盘价（= 09:25 集合竞价撮合价） |
| 卖点 | 当日收盘(不可交易,参照) / T+1 开盘 / T+1 收盘 / T+2 收盘 |
| 可交易范围 | 昨日成交额 ≥1 亿、开盘价 ≥3 元 |
| 超额 | 组合均值 − 同范围均值（两边同一统计量） |
| t 值 | **按交易日算**，不按笔——同一天多只票高度相关，按笔算会把 t 吹大数倍 |
| 样本外 | 时间轴后 1/3 只做验证，不参与挑规则 |

    python experiments/snapshot_db.py
    python experiments/auction_tradeable_ab.py --months 12
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, ".snapshot.sqlite")
RESULTS = os.path.join(HERE, "results")

MIN_AMOUNT = 1e8      # 流动性门槛，看昨日
MIN_PRICE = 3.0
TOPK = 5              # 线上展示上限
HORIZONS = ("当日收盘(不可交易)", "T+1开盘", "T+1收盘", "T+2收盘")


def board_limit(symbol: str) -> float:
    if symbol.startswith(("688", "689", "300", "301")):
        return 20.0
    if symbol.startswith(("8", "4", "920")):
        return 30.0
    return 10.0


# 每条规则 = (准入谓词, 排序键)。排序键小的排前面。
RULES = {
    "A_线上现行":      (lambda c: 1.5 <= c["gap"] <= c["lim"] * 0.6, lambda c: -c["gap"]),
    "B_高开上限5%":    (lambda c: 1.5 <= c["gap"] < 5.0, lambda c: -c["gap"]),
    "C_高开上限4%":    (lambda c: 1.5 <= c["gap"] < 4.0, lambda c: -c["gap"]),
    "D_高开但取最温和": (lambda c: 1.5 <= c["gap"] <= c["lim"] * 0.6, lambda c: c["gap"]),
    "E_低开反转":      (lambda c: c["gap"] <= -1.0 and c["above_ma20"], lambda c: c["gap"]),
    "F_低开反转不含跌停": (lambda c: -9.0 <= c["gap"] <= -1.0 and c["above_ma20"], lambda c: c["gap"]),
}


def load(db: str, months: int):
    conn = sqlite3.connect(db, timeout=120)
    dates = sorted(r[0] for r in conn.execute(
        "SELECT date FROM daily_kline WHERE amount>0 GROUP BY date HAVING COUNT(*)>4000"
        " ORDER BY date DESC LIMIT ?", (int(months * 21) + 10,)))
    rows = conn.execute("SELECT symbol,date,open,close,amount FROM daily_kline"
                        " WHERE date>=? AND amount>0", (dates[0],)).fetchall()
    conn.close()
    series: dict = {}
    for sym, d, o, c, a in rows:
        series.setdefault(sym, {})[d] = (o, c, a)
    return dates, series


def build_ma20(series: dict) -> dict:
    """不含当日的 20 日均线（当日收盘在 09:25 还不存在，不能用）。"""
    out = {}
    for sym, by_date in series.items():
        ds = sorted(by_date)
        closes = [by_date[d][1] for d in ds]
        out[sym] = {ds[i]: sum(closes[i - 20:i]) / 20 for i in range(20, len(ds))}
    return out


def sessions(dates, series, ma20):
    """逐日产出 (日期, 同范围各卖点均值, 候选列表)。"""
    for i in range(21, len(dates) - 2):
        d, prev, n1, n2 = dates[i], dates[i - 1], dates[i + 1], dates[i + 2]
        univ = {h: [] for h in HORIZONS}
        cands = []
        for sym, bd in series.items():
            if d not in bd or prev not in bd or n1 not in bd or n2 not in bd:
                continue
            op, close, _amt = bd[d]
            pc = bd[prev][1]
            # 流动性看昨日：当日成交额在 09:25 还不存在，拿它筛候选就是未来函数
            if pc <= 0 or op <= 0 or bd[prev][2] < MIN_AMOUNT or op < MIN_PRICE:
                continue
            ret = {
                "当日收盘(不可交易)": (close / op - 1) * 100,
                "T+1开盘": (bd[n1][0] / op - 1) * 100,
                "T+1收盘": (bd[n1][1] / op - 1) * 100,
                "T+2收盘": (bd[n2][1] / op - 1) * 100,
            }
            for h in HORIZONS:
                univ[h].append(ret[h])
            ma = ma20.get(sym, {}).get(d)
            if ma is None:
                continue
            cands.append({"sym": sym, "gap": (op / pc - 1) * 100, "lim": board_limit(sym),
                          "above_ma20": pc >= ma, "ret": ret})
        if len(univ[HORIZONS[0]]) < 100:
            continue
        yield d, {h: float(np.mean(univ[h])) for h in HORIZONS}, cands


def stat(values) -> dict | None:
    a = np.array(values, dtype=float)
    if a.size < 10:
        return None
    m, sd = float(a.mean()), float(a.std(ddof=1))
    return {"pp": round(m, 3), "win": round(float((a > 0).mean()) * 100, 1),
            "t": round(m / sd * np.sqrt(a.size), 2) if sd else None, "days": int(a.size)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--months", type=int, default=12)
    args = ap.parse_args()

    dates, series = load(args.db, args.months)
    print(f"面板 {len(dates)} 个交易日 · {len(series)} 只", flush=True)
    ma20 = build_ma20(series)
    snap = list(sessions(dates, series, ma20))
    split = snap[int(len(snap) * 0.67)][0]
    print(f"可用 {len(snap)} 天，{split} 之后为样本外", flush=True)

    acc = {k: {seg: {h: [] for h in HORIZONS} for seg in ("IS", "OOS")} for k in RULES}
    for d, base, cands in snap:
        seg = "IS" if d < split else "OOS"
        for key, (ok, order) in RULES.items():
            sel = sorted([c for c in cands if ok(c)], key=order)[:TOPK]
            if not sel:
                continue
            for h in HORIZONS:
                acc[key][seg][h].append(float(np.mean([c["ret"][h] for c in sel])) - base[h])

    out = {"months": args.months, "top_k": TOPK, "split": split, "rules": {}}
    for h in HORIZONS:
        print(f"\n—— 卖点：{h} ——")
        print(f"{'规则':<22}{'样本内超额':>11}{'胜率':>8}{'t':>7}{'样本外超额':>11}{'胜率':>8}{'t':>7}")
        for key in RULES:
            a, b = stat(acc[key]["IS"][h]), stat(acc[key]["OOS"][h])
            if not a or not b:
                continue
            out["rules"].setdefault(key, {})[h] = {"is": a, "oos": b}
            print(f"{key:<22}{a['pp']:>+11.3f}{a['win']:>7.1f}%{a['t']:>7.2f}"
                  f"{b['pp']:>+11.3f}{b['win']:>7.1f}%{b['t']:>7.2f}")

    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, "auction_tradeable_ab.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(f"\n明细 -> {path}")


if __name__ == "__main__":
    main()
