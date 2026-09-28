"""小市值周频组合：中小板里成交最冷清、最平稳的 5 只，每周一开盘换仓。

规则照搬 PTrade「Alpha70」策略（Allen 2026-09-28 提供），与 experiments/alpha70_replica.py
逐条一致——那个脚本在本地 2020-09~2026-09 复刻出累计 +359%、最大回撤 −32.9%，
7 个年份里 5 年跑赢同池等权（2021、2024 跑输）。存活偏差会高估这个数（退市股不在库里），
所以页面上必须同时给回撤和这条偏差。

规则：
- 池：深市中小板 002/003，剔除 ST/退市风险（按当前名称判断）与当日停牌
- 因子：换仓日之前 6 个完整交易日的成交额标准差，取最小的 5 只
- 每周第一个交易日开盘换仓；上周持仓若换仓前一日收盘涨停（≥前收×1.095），本周续持
- 本周收益：周一开盘 → 现价（盘中）或下周一开盘（已结束的周）
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

TOP = 5
STD_WINDOW = 6
LIMIT_UP = 1.095
POOL_PREFIX = ("002", "003")


def _iso_week(d: str) -> tuple:
    return date.fromisoformat(d).isocalendar()[:2]


def _load(store, lookback_days: int, today: date) -> tuple[pd.DataFrame, list]:
    # 只查中小板这 1000 来只，按代码区间走 (symbol, date) 索引（SQLite 对 LIKE 默认不用索引）；
    # 「完整交易日」也用这批票自己的覆盖率判断，不再对全市场按日期分组——
    # 那一步要回表读近百万行，原写法单次 9 秒。
    since = (today - timedelta(days=int(lookback_days * 1.6) + 20)).isoformat()
    k = pd.read_sql_query(
        "SELECT symbol, date, open, close, amount FROM daily_kline "
        "WHERE symbol >= '002000' AND symbol < '004000' AND date >= ?",
        store._conn(), params=(since,))
    if k.empty:
        return k, []
    counts = k[k["amount"] > 0].groupby("date").size()
    days = sorted(counts[counts >= counts.max() * 0.6].index)[-lookback_days:]
    return k[k["date"].isin(days)], days


def compute(store, weeks: int = 26, now: Optional[datetime] = None) -> Dict[str, object]:
    """本周组合 + 最近 weeks 周的逐周成绩（按同一规则回算）。"""
    from .screening import exclusion_reason

    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    after_close = now.hour * 60 + now.minute >= 15 * 60 + 30
    k, days = _load(store, weeks * 5 + 40, now.date())
    if k.empty:
        return {"items": [], "history": []}
    names = dict(store._conn().execute("SELECT symbol, name FROM stock_meta").fetchall())
    # 「完整日」用来算因子；今天盘中的占位 bar 只拿来取开盘价和最新价
    complete = [d for d in days if d < today or after_close]
    opens = k.pivot(index="date", columns="symbol", values="open")
    closes = k.pivot(index="date", columns="symbol", values="close")
    amounts = k.pivot(index="date", columns="symbol", values="amount")
    st = {s for s in amounts.columns if exclusion_reason(str(names.get(s) or s), 10.0, 1e9)}

    week_starts = [d for i, d in enumerate(days) if i == 0 or _iso_week(d) != _iso_week(days[i - 1])]
    week_starts = [w for w in week_starts if sum(1 for d in complete if d < w) >= STD_WINDOW]
    week_starts = week_starts[-(weeks + 1):]

    history: List[dict] = []
    hold: List[str] = []
    current: Optional[dict] = None
    for i, w in enumerate(week_starts):
        before = [d for d in complete if d < w][-STD_WINDOW:]
        std = amounts.loc[before].std()
        live_today = amounts.loc[w] if w in amounts.index else pd.Series(dtype=float)
        ok = std[(std > 0) & std.notna()]
        ok = ok[[s for s in ok.index if s not in st and float(live_today.get(s) or 0) > 0]]
        prev_day = before[-1]
        prev2 = [d for d in complete if d < prev_day][-1:]
        keep = []
        if hold and prev2:
            keep = [s for s in hold
                    if closes.at[prev_day, s] >= closes.at[prev2[0], s] * LIMIT_UP]
        picks = keep + [s for s in ok.nsmallest(TOP * 3).index if s not in keep][: TOP - len(keep)]
        nxt = week_starts[i + 1] if i + 1 < len(week_starts) else None
        entry = opens.loc[w, picks]
        if nxt:
            exit_px = opens.loc[nxt]
        else:
            exit_px = closes.loc[days[-1]]
        r = (exit_px[picks] / entry - 1).replace([np.inf, -np.inf], np.nan)
        pool = (exit_px[ok.index] / opens.loc[w, ok.index] - 1).replace([np.inf, -np.inf], np.nan).clip(-0.5, 1)
        row = {"week_start": w, "ret": round(float(r.mean() * 100), 2) if r.notna().any() else None,
               "pool_ret": round(float(pool.mean() * 100), 2), "symbols": picks, "finished": bool(nxt)}
        if nxt:
            history.append(row)
        else:
            current = {"row": row, "keep": keep, "entry": entry, "std": std}
        hold = picks

    items = []
    if current:
        row = current["row"]
        last = closes.loc[days[-1]]
        for s in row["symbols"]:
            e = float(current["entry"].get(s) or 0)
            p = float(last.get(s) or 0)
            items.append({
                "symbol": s, "name": str(names.get(s) or s),
                "entry_price": round(e, 2), "price": round(p, 2),
                "since_entry_pct": round((p / e - 1) * 100, 2) if e > 0 and p > 0 else None,
                "amount_std_wan": round(float(current["std"].get(s) or 0) / 1e4, 1),
                "kept_limit_up": s in current["keep"],
            })
    rets = [h["ret"] for h in history if h["ret"] is not None]
    pools = [h["pool_ret"] for h in history if h["ret"] is not None]
    summary = None
    if rets:
        nav = np.cumprod([1 + x / 100 for x in rets])
        pnav = np.cumprod([1 + x / 100 for x in pools])
        summary = {
            "weeks": len(rets),
            "cum_ret": round(float(nav[-1] - 1) * 100, 1),
            "pool_cum_ret": round(float(pnav[-1] - 1) * 100, 1),
            "win_vs_pool": round(sum(1 for a, b in zip(rets, pools) if a > b) / len(rets), 2),
            "max_drawdown": round(float((nav / np.maximum.accumulate(nav) - 1).min()) * 100, 1),
        }
    return {
        "week_start": current["row"]["week_start"] if current else None,
        "as_of": days[-1],
        "items": items,
        "history": list(reversed(history)),
        "summary": summary,
    }
