"""一键智选「追涨风格是否正在失灵」：上一批已兑现名单的真实表现。

依据（experiments/structure_decay.py，2026-09-28）：结构分的好坏几乎完全取决于
「近 20 日强者恒强」这一风格在当期灵不灵（逐月相关 +0.71，5 年）。风格有延续性：
上一期（已兑现的 10 个交易日）结构前 20 跑输候选均值时，下一期均值 −1.79pp、胜率 32%；
跑赢时 −0.15pp、胜率 50%。10 个不重叠起点方向全部一致，但相关只有 +0.12 ——
够用来**提示**，不够用来改排序或停推（Allen 2026-09-28 选择照常推荐 + 醒目提示）。

口径与研究一致：收盘到收盘，持有 10 个完整交易日，基准是当日成交额 ≥3000 万的全体均值。
名单取 picks_history 里用户实际看到的 smart 池。
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

HOLD_DAYS = 10
MIN_AMOUNT = 3e7
MIN_MARKET_COVERAGE = 0.6


def _complete_days(conn, today: str, include_today: bool) -> list:
    rows = conn.execute(
        "SELECT date, COUNT(*) FROM daily_kline WHERE date >= date(?, '-60 day') AND amount > 0 "
        "GROUP BY date ORDER BY date", (today,)).fetchall()
    if not rows:
        return []
    peak = max(int(r[1]) for r in rows)
    days = [str(r[0]) for r in rows if int(r[1]) >= peak * MIN_MARKET_COVERAGE]
    # 盘中当天的占位 bar 也带成交额，收盘前不能把今天当完整日
    return [d for d in days if d < today or (include_today and d == today)]


def smart_style_health(store, now: Optional[datetime] = None) -> Optional[Dict[str, object]]:
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    conn = store._conn()
    days = _complete_days(conn, today, include_today=now.hour * 60 + now.minute >= 15 * 60 + 30)
    if len(days) <= HOLD_DAYS:
        return None
    eval_date = days[-1]
    anchor_limit = days[-1 - HOLD_DAYS]
    row = conn.execute(
        "SELECT MAX(pick_date) FROM picks_history WHERE pool='smart' AND pick_date <= ?",
        (anchor_limit,)).fetchone()
    pick_date = row[0] if row else None
    if not pick_date or pick_date not in days:
        return None
    symbols = [r[0] for r in conn.execute(
        "SELECT symbol FROM picks_history WHERE pool='smart' AND pick_date=?", (pick_date,))]
    closes = conn.execute(
        "SELECT a.symbol, a.close, b.close, a.amount FROM daily_kline a "
        "JOIN daily_kline b ON b.symbol = a.symbol AND b.date = ? "
        "WHERE a.date = ? AND a.amount > 0 AND a.close > 0 AND b.close > 0",
        (eval_date, pick_date)).fetchall()
    rets = {sym: c1 / c0 - 1 for sym, c0, c1, _amt in closes}
    market = [c1 / c0 - 1 for _sym, c0, c1, amt in closes if (amt or 0) >= MIN_AMOUNT]
    picked = [rets[s] for s in symbols if s in rets]
    if len(picked) < 5 or len(market) < 500:
        return None
    pick_ret = sum(picked) / len(picked) * 100
    market_ret = sum(market) / len(market) * 100
    excess = pick_ret - market_ret
    return {
        "pick_date": pick_date,
        "eval_date": eval_date,
        "hold_days": days.index(eval_date) - days.index(pick_date),
        "pick_ret": round(pick_ret, 2),
        "market_ret": round(market_ret, 2),
        "excess": round(excess, 2),
        "win_rate": round(sum(1 for r in picked if r > 0) / len(picked), 2),
        "samples": len(picked),
        "failing": excess < 0,
    }
