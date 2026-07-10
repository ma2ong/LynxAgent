"""用户模拟组合：一键把选中的票加入虚拟持仓，按真实价格跟踪盈亏，规则化卖出信号。

定位是「选股跟踪」而非模拟交易：每笔固定预算整手买入（含 A 股交易成本），
收盘 cron 结算每日快照；卖出信号（破位 MA20 / 止损 / 超时未盈利）帮用户解决
「只管买不管卖」的半截子问题。
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from .backtest import BUY_COST, SELL_COST
from .local_store import LocalQuantStore, get_local_store

DEFAULT_BUDGET = 10000.0
STOP_LOSS_PCT = -8.0       # 止损线（相对每股成本）
TIMEOUT_DAYS = 10          # 持有超过 N 个交易日仍未盈利 → 超时信号

_SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT,
    shares INTEGER,
    buy_price REAL,
    cost REAL,
    buy_date TEXT,
    source TEXT,
    status TEXT DEFAULT 'open',
    sell_date TEXT,
    sell_price REAL,
    sell_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_portfolio_user ON portfolio_positions(user, status);
CREATE TABLE IF NOT EXISTS portfolio_nav (
    user TEXT,
    date TEXT,
    market_value REAL,
    cost_value REAL,
    realized_pnl REAL,
    bench_pct REAL,
    PRIMARY KEY (user, date)
);
"""


def ensure_tables(store: Optional[LocalQuantStore] = None) -> LocalQuantStore:
    store = store or get_local_store()
    conn = store._conn()
    conn.executescript(_SCHEMA)
    conn.commit()
    return store


def add_position(user: str, symbol: str, name: str, price: float,
                 budget: float = DEFAULT_BUDGET, source: str = "",
                 store: Optional[LocalQuantStore] = None) -> Dict[str, object]:
    """按当前价整手买入。同一用户同一票已有持仓则拒绝（避免重复点按堆仓）。"""
    store = ensure_tables(store)
    symbol = str(symbol).zfill(6)
    if price <= 0:
        raise ValueError("无有效价格，无法加入组合")
    conn = store._conn()
    row = conn.execute(
        "SELECT id FROM portfolio_positions WHERE user=? AND symbol=? AND status='open'",
        (user, symbol)).fetchone()
    if row:
        raise ValueError(f"{symbol} 已在组合中")
    shares = int(budget / (price * (1 + BUY_COST)) / 100) * 100
    if shares < 100:
        raise ValueError("预算不足一手（100 股）")
    cost = round(price * (1 + BUY_COST), 4)
    today = datetime.now().strftime("%Y-%m-%d")
    cur = conn.execute(
        "INSERT INTO portfolio_positions(user,symbol,name,shares,buy_price,cost,buy_date,source) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (user, symbol, str(name or symbol), shares, float(price), cost, today, str(source or "")))
    conn.commit()
    return {"id": cur.lastrowid, "symbol": symbol, "name": name, "shares": shares,
            "buy_price": price, "cost": cost, "buy_date": today}


def close_position(user: str, position_id: int, price: float, reason: str = "manual",
                   store: Optional[LocalQuantStore] = None) -> Dict[str, object]:
    store = ensure_tables(store)
    conn = store._conn()
    row = conn.execute(
        "SELECT id, symbol, shares, cost FROM portfolio_positions "
        "WHERE user=? AND id=? AND status='open'", (user, int(position_id))).fetchone()
    if not row:
        raise ValueError("持仓不存在或已卖出")
    if price <= 0:
        raise ValueError("无有效价格，无法卖出")
    today = datetime.now().strftime("%Y-%m-%d")
    net_price = price * (1 - SELL_COST)
    pnl = round((net_price - float(row[3])) * int(row[2]), 2)
    conn.execute(
        "UPDATE portfolio_positions SET status='closed', sell_date=?, sell_price=?, sell_reason=? "
        "WHERE id=?", (today, float(price), str(reason), int(row[0])))
    conn.commit()
    return {"id": int(row[0]), "symbol": row[1], "sell_price": price, "pnl": pnl}


def sell_signals(store: LocalQuantStore, symbol: str, cost: float, buy_date: str,
                 latest_price: float) -> List[Dict[str, str]]:
    """开仓持仓的规则化卖出信号（可叠加多条）。"""
    out: List[Dict[str, str]] = []
    if latest_price <= 0 or cost <= 0:
        return out
    pnl_pct = (latest_price / cost - 1) * 100
    if pnl_pct <= STOP_LOSS_PCT:
        out.append({"key": "stop_loss", "label": f"止损{STOP_LOSS_PCT:.0f}%",
                    "detail": f"现价较成本 {pnl_pct:+.1f}%，已触及止损线"})
    rows = store._conn().execute(
        "SELECT date, close FROM daily_kline WHERE symbol=? AND amount>0 ORDER BY date DESC LIMIT 30",
        (str(symbol).zfill(6),)).fetchall()
    if rows:
        closes = [float(r[1]) for r in rows]
        if len(closes) >= 20:
            ma20 = sum(closes[:20]) / 20
            if latest_price < ma20:
                out.append({"key": "below_ma20", "label": "跌破MA20",
                            "detail": f"现价 {latest_price:.2f} 低于 20 日均线 {ma20:.2f}，趋势破位"})
        held = sum(1 for r in rows if str(r[0]) > str(buy_date))
        if held >= TIMEOUT_DAYS and pnl_pct <= 0:
            out.append({"key": "timeout", "label": f"持有{held}日未盈利",
                        "detail": f"已持有 {held} 个交易日仍未盈利，考虑换仓"})
    return out


def list_portfolio(user: str, prices: Dict[str, float],
                   store: Optional[LocalQuantStore] = None) -> Dict[str, object]:
    store = ensure_tables(store)
    conn = store._conn()
    rows = conn.execute(
        "SELECT id,symbol,name,shares,buy_price,cost,buy_date,source,status,sell_date,sell_price,sell_reason "
        "FROM portfolio_positions WHERE user=? ORDER BY status='open' DESC, buy_date DESC, id DESC",
        (user,)).fetchall()
    open_items: List[Dict[str, object]] = []
    closed_items: List[Dict[str, object]] = []
    total_cost = total_mv = realized = 0.0
    closed_wins = 0
    for r in rows:
        item = {"id": int(r[0]), "symbol": r[1], "name": r[2], "shares": int(r[3]),
                "buy_price": float(r[4]), "cost": float(r[5]), "buy_date": r[6],
                "source": r[7], "status": r[8]}
        if r[8] == "open":
            price = float(prices.get(str(r[1]), 0) or 0)
            if price <= 0:
                # 实时价缺失退本地日线最新收盘
                row2 = conn.execute(
                    "SELECT close FROM daily_kline WHERE symbol=? AND amount>0 ORDER BY date DESC LIMIT 1",
                    (str(r[1]),)).fetchone()
                price = float(row2[0]) if row2 else 0.0
            mv = price * int(r[3])
            cost_v = float(r[5]) * int(r[3])
            item.update({
                "price": price, "market_value": round(mv, 2),
                "pnl": round(mv - cost_v, 2),
                "pnl_pct": round((price / float(r[5]) - 1) * 100, 2) if float(r[5]) > 0 and price > 0 else None,
                "signals": sell_signals(store, str(r[1]), float(r[5]), str(r[6]), price),
            })
            total_cost += cost_v
            total_mv += mv
            open_items.append(item)
        else:
            net = float(r[10] or 0) * (1 - SELL_COST)
            pnl = round((net - float(r[5])) * int(r[3]), 2)
            item.update({"sell_date": r[9], "sell_price": r[10], "sell_reason": r[11], "pnl": pnl,
                         "pnl_pct": round((net / float(r[5]) - 1) * 100, 2) if float(r[5]) > 0 else None})
            realized += pnl
            if pnl > 0:
                closed_wins += 1
            closed_items.append(item)
    return {
        "open": open_items,
        "closed": closed_items[:100],
        "summary": {
            "open_count": len(open_items),
            "closed_count": len(closed_items),
            "total_cost": round(total_cost, 2),
            "market_value": round(total_mv, 2),
            "unrealized_pnl": round(total_mv - total_cost, 2),
            "unrealized_pnl_pct": round((total_mv / total_cost - 1) * 100, 2) if total_cost > 0 else None,
            "realized_pnl": round(realized, 2),
            "closed_win_rate": round(closed_wins / len(closed_items), 4) if closed_items else None,
        },
    }


def settle_daily(date: str, prices: Dict[str, float],
                 store: Optional[LocalQuantStore] = None) -> int:
    """收盘快照：每个有持仓的用户记当日市值/成本/已实现盈亏 + 当日全市场中位涨幅（幂等）。"""
    store = ensure_tables(store)
    conn = store._conn()
    users = [r[0] for r in conn.execute(
        "SELECT DISTINCT user FROM portfolio_positions").fetchall()]
    if not users:
        return 0
    bench = _market_median_pct(store, date)
    n = 0
    for user in users:
        snap = list_portfolio(user, prices, store)
        s = snap["summary"]
        if not s["open_count"] and not s["closed_count"]:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO portfolio_nav(user,date,market_value,cost_value,realized_pnl,bench_pct) "
            "VALUES(?,?,?,?,?,?)",
            (user, date, s["market_value"], s["total_cost"], s["realized_pnl"], bench))
        n += 1
    conn.commit()
    return n


def _market_median_pct(store: LocalQuantStore, date: str) -> Optional[float]:
    import statistics
    conn = store._conn()
    prev = conn.execute(
        "SELECT MAX(date) FROM daily_kline WHERE date < ? AND amount > 0", (date,)).fetchone()[0]
    if not prev:
        return None
    rows = conn.execute(
        "SELECT a.close, b.close FROM daily_kline a JOIN daily_kline b "
        "ON a.symbol=b.symbol AND a.date=? AND b.date=? WHERE a.amount>0 AND b.amount>0",
        (date, str(prev))).fetchall()
    rets = [(float(c) / float(p) - 1) * 100 for c, p in rows if p and float(p) > 0]
    return round(statistics.median(rets), 4) if len(rets) >= 100 else None


def nav_series(user: str, store: Optional[LocalQuantStore] = None) -> List[Dict[str, object]]:
    store = ensure_tables(store)
    rows = store._conn().execute(
        "SELECT date, market_value, cost_value, realized_pnl, bench_pct FROM portfolio_nav "
        "WHERE user=? ORDER BY date", (user,)).fetchall()
    out: List[Dict[str, object]] = []
    bench_cum = 0.0
    for d, mv, cv, rp, bp in rows:
        total_pnl = float(mv or 0) - float(cv or 0) + float(rp or 0)
        pnl_pct = (total_pnl / float(cv)) * 100 if cv else 0.0
        if bp is not None:
            bench_cum += float(bp)
        out.append({"date": d, "market_value": mv, "cost_value": cv,
                    "pnl_pct": round(pnl_pct, 2), "bench_cum_pct": round(bench_cum, 2)})
    return out
