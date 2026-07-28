"""全市场量化数据的本地 SQLite 存储（与认证库分离）。"""
from __future__ import annotations
import os
from pathlib import Path
import threading
from typing import Dict, List, Optional

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = os.environ.get("QUANT_DATA_DB_PATH", str(_PROJECT_ROOT / "runtime" / "quant_data.sqlite"))

# 复盘统计的市场覆盖率守卫：T+N 目标日的日线覆盖数低于窗口内峰值的 60% 时，
# 视为数据未就绪（同步缺口），该 horizon 不进统计。
MIN_MARKET_COVERAGE = 0.6

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_meta (
    symbol TEXT PRIMARY KEY,
    name TEXT,
    industry TEXT,
    list_date TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS daily_kline (
    symbol TEXT,
    date TEXT,
    open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_kline_symbol_date ON daily_kline(symbol, date);
CREATE INDEX IF NOT EXISTS idx_kline_date ON daily_kline(date);
CREATE TABLE IF NOT EXISTS sync_state (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS fundamental_flags (
    symbol TEXT PRIMARY KEY,
    bad_forecast INTEGER,
    forecast_type TEXT,
    change TEXT,
    period TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS picks_history (
    pick_date TEXT,
    pool TEXT,
    symbol TEXT,
    name TEXT,
    score REAL,
    close REAL,
    rank INTEGER,
    patterns TEXT,
    PRIMARY KEY (pick_date, pool, symbol)
);
CREATE TABLE IF NOT EXISTS latest_picks (
    pool TEXT,
    symbol TEXT,
    pick_date TEXT,
    batch_at TEXT,
    name TEXT,
    score REAL,
    close REAL,
    rank INTEGER,
    patterns TEXT,
    PRIMARY KEY (pool, symbol)
);
CREATE INDEX IF NOT EXISTS idx_latest_picks_pool_rank ON latest_picks(pool, rank);
CREATE TABLE IF NOT EXISTS daily_reports (
    date TEXT,
    kind TEXT,
    content_json TEXT,
    created_at TEXT,
    PRIMARY KEY (date, kind)
);
CREATE TABLE IF NOT EXISTS panel_scores (
    date TEXT,
    symbol TEXT,
    consensus REAL,
    divergence REAL,
    bull INTEGER,
    bear INTEGER,
    verdicts_json TEXT,
    summary TEXT,
    created_at TEXT,
    PRIMARY KEY (date, symbol)
);
CREATE TABLE IF NOT EXISTS arena_state (
    persona TEXT PRIMARY KEY,
    cash REAL
);
CREATE TABLE IF NOT EXISTS arena_positions (
    persona TEXT,
    symbol TEXT,
    shares INTEGER,
    avg_cost REAL,
    PRIMARY KEY (persona, symbol)
);
CREATE TABLE IF NOT EXISTS arena_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    persona TEXT,
    symbol TEXT,
    side TEXT,
    price REAL,
    shares INTEGER,
    reason TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS arena_nav (
    date TEXT,
    persona TEXT,
    nav REAL,
    cash REAL,
    comment TEXT,
    PRIMARY KEY (date, persona)
);
CREATE TABLE IF NOT EXISTS signal_stats_cache (
    cache_key TEXT PRIMARY KEY,
    stamp TEXT,
    payload_json TEXT,
    created_at TEXT
);
"""

_COLS = ["date", "open", "high", "low", "close", "volume", "amount"]


class LocalQuantStore:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._local = threading.local()
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self):
        import sqlite3
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    # ---- 元信息 ----
    def upsert_meta(self, rows: List[Dict[str, object]]) -> None:
        from datetime import datetime
        now = datetime.now().isoformat(timespec="seconds")
        conn = self._conn()
        conn.executemany(
            "INSERT INTO stock_meta(symbol,name,industry,list_date,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET name=excluded.name, "
            "industry=COALESCE(NULLIF(excluded.industry,''), stock_meta.industry), "
            "list_date=COALESCE(NULLIF(excluded.list_date,''), stock_meta.list_date), "
            "updated_at=excluded.updated_at",
            [(str(r.get("symbol")), r.get("name") or "", r.get("industry") or "",
              r.get("list_date") or "", now) for r in rows],
        )
        conn.commit()

    def load_meta(self) -> List[Dict[str, object]]:
        conn = self._conn()
        cur = conn.execute("SELECT symbol,name,industry,list_date FROM stock_meta ORDER BY symbol")
        return [{"symbol": s, "name": n, "industry": i, "list_date": ld, "market": "A股"} for s, n, i, ld in cur.fetchall()]

    def symbol_count(self) -> int:
        return self._conn().execute("SELECT COUNT(*) FROM stock_meta").fetchone()[0]

    # ---- 日线 ----
    def upsert_kline(self, symbol: str, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        conn = self._conn()
        rows = []
        for _, r in df.iterrows():
            d = str(r.get("date"))[:10]
            rows.append((symbol, d, _f(r.get("open")), _f(r.get("high")), _f(r.get("low")),
                         _f(r.get("close")), _f(r.get("volume")), _f(r.get("amount"))))
        conn.executemany(
            "INSERT INTO daily_kline(symbol,date,open,high,low,close,volume,amount) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(symbol,date) DO UPDATE SET "
            "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,"
            "volume=excluded.volume,amount=excluded.amount", rows)
        conn.commit()
        return len(rows)

    def load_kline(self, symbol: str, limit: Optional[int] = None) -> pd.DataFrame:
        conn = self._conn()
        sql = "SELECT date,open,high,low,close,volume,amount FROM daily_kline WHERE symbol=? ORDER BY date"
        cur = conn.execute(sql, (symbol,))
        data = cur.fetchall()
        df = pd.DataFrame(data, columns=_COLS)
        if limit and len(df) > limit:
            df = df.tail(limit).reset_index(drop=True)
        return df

    def last_kline_date(self, symbol: str) -> Optional[str]:
        row = self._conn().execute("SELECT MAX(date) FROM daily_kline WHERE symbol=?", (symbol,)).fetchone()
        return row[0] if row and row[0] else None

    def recent_bar_counts(self, since_date: str) -> Dict[str, int]:
        """每只股票在 since_date 之后的日线 bar 数 {symbol: count}，用于按近窗连续性判断缺口。

        不用 MAX(date)：批量快照会把 MAX 写成今天、掩盖中间缺口（如 6-16..6-22），
        单看最新日期会漏判；按近窗 bar 数才能准确发现不连续。
        """
        # 只数「有成交额的真实 bar」：盘中快照会写入 amount=0 的占位 bar（close=前收），
        # 若按存在与否计数会把占位符当成「已就绪」→ 跳过真实日线回补 → 连板/成交额/情绪全错。
        # 改为只数 amount>0 的真实 bar，占位符不计入 → 触发逐股回补，自愈坏数据。
        rows = self._conn().execute(
            "SELECT symbol, COUNT(*) FROM daily_kline WHERE date >= ? AND amount > 0 GROUP BY symbol",
            (since_date,),
        ).fetchall()
        return {r[0]: int(r[1]) for r in rows}

    def placeholder_symbols(self, since_date: str) -> set:
        """近窗内存在「历史交易日占位 bar」(amount<=0 且 date<今天) 的股票集合。

        盘中快照会写 amount=0 的占位 bar（close=前收）；过去交易日本应有真实成交额，
        若仍为 0 即说明真实日线回补漏了。仅靠 bar 计数无法发现「13 根里混 1 根占位」的情况，
        故单独识别，纳入增量同步回补目标，自愈坏数据（连板/成交额/情绪依赖它）。
        """
        from datetime import date as _date
        today = _date.today().strftime("%Y-%m-%d")
        rows = self._conn().execute(
            "SELECT DISTINCT symbol FROM daily_kline WHERE date >= ? AND date < ? AND amount <= 0",
            (since_date, today),
        ).fetchall()
        return {str(r[0]).zfill(6) for r in rows}

    def bulk_upsert_kline_snapshot(self, bars: List[tuple]) -> int:
        """批量写入多只股票的单日 bar。bars: [(symbol,date,open,high,low,close,volume,amount), ...]。"""
        if not bars:
            return 0
        conn = self._conn()
        conn.executemany(
            "INSERT INTO daily_kline(symbol,date,open,high,low,close,volume,amount) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(symbol,date) DO UPDATE SET "
            "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,"
            "volume=excluded.volume,amount=excluded.amount",
            bars,
        )
        conn.commit()
        return len(bars)

    def kline_symbol_count(self) -> int:
        return self._conn().execute("SELECT COUNT(DISTINCT symbol) FROM daily_kline").fetchone()[0]

    def list_kline_symbols(self, min_rows: int = 0) -> List[str]:
        """列出本地 K 线库里所有代码；min_rows>0 时只返回历史长度足够的代码。"""
        if min_rows and min_rows > 0:
            sql = "SELECT symbol FROM daily_kline GROUP BY symbol HAVING COUNT(*) >= ? ORDER BY symbol"
            rows = self._conn().execute(sql, (min_rows,)).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT DISTINCT symbol FROM daily_kline ORDER BY symbol"
            ).fetchall()
        return [r[0] for r in rows]

    def whole_day_gap_dates(self, days: int = 18) -> List[str]:
        """近 days 自然日内「整天缺失」的交易日：当日真实 bar 覆盖数低于窗口峰值的 60%（今天除外）。

        单日全市场缺失时逐股近窗 bar 计数几乎不降（MIN_RECENT_BARS 容忍 4-5 天），
        只有按日期截面才能发现；历史上 7-01/7-02 数据洞靠手动全量修复，此检测用于自愈。
        """
        from datetime import date as _date, timedelta as _td
        since = (_date.today() - _td(days=max(1, days))).strftime("%Y-%m-%d")
        today = _date.today().strftime("%Y-%m-%d")
        rows = self._conn().execute(
            "SELECT date, COUNT(DISTINCT symbol) FROM daily_kline "
            "WHERE date >= ? AND amount > 0 GROUP BY date ORDER BY date",
            (since,),
        ).fetchall()
        if not rows:
            return []
        peak = max(int(r[1]) for r in rows)
        return [str(r[0]) for r in rows
                if str(r[0]) != today and int(r[1]) < peak * MIN_MARKET_COVERAGE]

    def symbols_missing_on_dates(self, dates: List[str]) -> set:
        """在给定交易日中缺真实 bar（amount>0）的股票集合，用于定向回补。"""
        if not dates:
            return set()
        conn = self._conn()
        all_syms = {str(r[0]) for r in conn.execute("SELECT symbol FROM stock_meta").fetchall()}
        if not all_syms:
            all_syms = {str(r[0]) for r in conn.execute("SELECT DISTINCT symbol FROM daily_kline").fetchall()}
        missing: set = set()
        for d in dates:
            have = {str(r[0]) for r in conn.execute(
                "SELECT symbol FROM daily_kline WHERE date = ? AND amount > 0", (d,)).fetchall()}
            missing |= all_syms - have
        return missing

    def kline_health(self) -> Dict[str, object]:
        from datetime import date, datetime, time

        conn = self._conn()
        meta_count = int(conn.execute("SELECT COUNT(*) FROM stock_meta").fetchone()[0] or 0)
        kline_symbols = int(conn.execute("SELECT COUNT(DISTINCT symbol) FROM daily_kline").fetchone()[0] or 0)
        rows = conn.execute(
            "SELECT date, COUNT(DISTINCT symbol) FROM daily_kline GROUP BY date ORDER BY date DESC LIMIT 20"
        ).fetchall()
        latest_date = str(rows[0][0]) if rows else ""
        latest_count = int(rows[0][1]) if rows else 0
        today = date.today().strftime("%Y-%m-%d")
        today_count = next((int(count) for d, count in rows if str(d) == today), 0)
        threshold = max(500, int(meta_count * 0.45)) if meta_count else 500
        latest_complete_date = ""
        latest_complete_count = 0
        for d, count in rows:
            if int(count) >= threshold:
                latest_complete_date = str(d)
                latest_complete_count = int(count)
                break
        now = datetime.now()
        weekday = datetime.strptime(today, "%Y-%m-%d").weekday()
        today_is_weekday = weekday < 5
        daily_bar_due = now.time() >= time(15, 15)
        ready = kline_symbols >= 500 and bool(latest_complete_date)
        today_complete = today_count >= threshold
        gap_dates = self.whole_day_gap_dates(days=18)
        needs_incremental_sync = bool(
            (ready and today_is_weekday and daily_bar_due and not today_complete)
            or (ready and gap_dates)
        )
        if not ready:
            status = "empty" if kline_symbols == 0 else "insufficient"
            message = "本地K线不足，请先做一次全量同步。"
        elif today_complete:
            status = "fresh"
            message = f"数据已更新到今天，覆盖 {today_count}/{meta_count} 只。"
        elif today_is_weekday and not daily_bar_due:
            status = "intraday"
            message = f"今日仍在交易或尚未收盘，盘中页面使用实时行情 + 最近完整交易日 {latest_complete_date}。"
        elif needs_incremental_sync:
            status = "partial_today" if today_count else "stale_today"
            message = f"今天数据正在补齐或尚未同步，当前覆盖 {today_count}/{meta_count} 只；筛选会先使用最近完整交易日 {latest_complete_date}。"
        else:
            status = "ready"
            message = f"本地数据可用，最近完整交易日 {latest_complete_date}。"
        return {
            "status": status,
            "ready": ready,
            "meta_count": meta_count,
            "kline_symbols": kline_symbols,
            "latest_date": latest_date,
            "latest_date_count": latest_count,
            "today": today,
            "today_count": today_count,
            "complete_threshold": threshold,
            "latest_complete_date": latest_complete_date,
            "latest_complete_count": latest_complete_count,
            "today_complete": today_complete,
            "needs_incremental_sync": needs_incremental_sync,
            "gap_dates": gap_dates,
            "recent_days": [{"date": str(d), "count": int(c)} for d, c in rows[:10]],
            "message": message,
        }

    def latest_snapshots(self) -> Dict[str, Dict[str, float]]:
        # 只取「最近一根有成交额的完整日线」：盘中增量同步会写入当日 bar（有 close 无
        # amount=0），若直接取最新 bar 会让全市场 amount=0 而被筛股的流动性过滤误判为停牌，
        # 导致候选池被砍到只剩零星几只。amount>0 过滤跳过当日不完整 bar，回到最近完整交易日。
        sql = """
        WITH ranked AS (
            SELECT symbol, close, amount,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
            FROM daily_kline
            WHERE amount > 0
        )
        SELECT latest.symbol, latest.close, latest.amount, prev.close
        FROM ranked latest
        LEFT JOIN ranked prev ON latest.symbol = prev.symbol AND prev.rn = 2
        WHERE latest.rn = 1
        """
        rows = self._conn().execute(sql).fetchall()
        out: Dict[str, Dict[str, float]] = {}
        for symbol, close, amount, prev_close in rows:
            close_f = _f(close)
            prev_f = _f(prev_close)
            out[str(symbol)] = {
                "price": close_f,
                "amount": _f(amount),
                "pct_chg": (close_f / prev_f - 1) * 100 if prev_f else 0.0,
            }
        return out

    def latest_real_bar_date(self) -> str:
        """最后一根真实日线（amount>0）的日期。盘中占位 bar 不算，与 recent_returns 口径一致。"""
        row = self._conn().execute("SELECT MAX(date) FROM daily_kline WHERE amount > 0").fetchone()
        return str(row[0]) if row and row[0] else ""

    def recent_returns(self, window: int = 5) -> Dict[str, float]:
        """每只股票最近 window 个交易日的涨跌幅%（最新完整 bar vs window 根前的 bar）。

        只用 amount>0 的真实 bar（跳过盘中占位 bar），供"近段趋势热门板块"动态识别。
        只扫近 45 天窗口（idx_kline_date）：全表窗口函数要扫数百万行、冷查询 10-20s；
        window≤6 根 bar 在 45 天内必然齐全，停牌超 45 天的股票本就不可交易，丢弃无害。
        """
        from datetime import date as _date, timedelta as _td
        cutoff = (_date.today() - _td(days=45)).strftime("%Y-%m-%d")
        sql = """
        WITH ranked AS (
            SELECT symbol, close,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
            FROM daily_kline
            WHERE amount > 0 AND date >= ?
        )
        SELECT cur.symbol, cur.close, base.close
        FROM ranked cur
        JOIN ranked base ON base.symbol = cur.symbol AND base.rn = ?
        WHERE cur.rn = 1
        """
        out: Dict[str, float] = {}
        for symbol, cur_close, base_close in self._conn().execute(sql, (cutoff, window + 1)).fetchall():
            c = _f(cur_close)
            b = _f(base_close)
            if b > 0:
                out[str(symbol).zfill(6)] = (c / b - 1) * 100
        return out

    def recent_daily_breadth(self, days: int = 5) -> List[Dict[str, float]]:
        """最近 days 个交易日的**逐日**全市场中位涨幅% / 上涨占比，最新一日在前。

        与 recent_returns 的累计口径互补：环境温度要的是「每天多冷多热」，累计口径会被
        单根大阴线锁死整整一个窗口（见 regime 模块注释）。
        同样只用 amount>0 的真实 bar + 45 天窗口，缺 bar 的股票当日不计入。
        """
        import statistics
        from datetime import date as _date, timedelta as _td
        cutoff = (_date.today() - _td(days=45)).strftime("%Y-%m-%d")
        sql = """
        WITH d AS (
            SELECT DISTINCT date FROM daily_kline
            WHERE amount > 0 AND date >= ? ORDER BY date DESC LIMIT ?
        ), lagged AS (
            SELECT date, close,
                   LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS prev
            FROM daily_kline
            WHERE amount > 0 AND date IN (SELECT date FROM d)
        )
        SELECT date, close, prev FROM lagged WHERE prev IS NOT NULL
        """
        buckets: Dict[str, List[float]] = {}
        for d, close, prev in self._conn().execute(sql, (cutoff, days + 1)).fetchall():
            p = _f(prev)
            if p > 0:
                buckets.setdefault(str(d), []).append((_f(close) / p - 1) * 100)
        out: List[Dict[str, float]] = []
        for d in sorted(buckets, reverse=True)[:days]:
            rets = buckets[d]
            if len(rets) < 100:  # 样本太少（同步残缺日）不足以代表全市场，跳过而非给出假广度
                continue
            out.append({
                "date": d,
                "median_pct": round(statistics.median(rets), 2),
                "breadth_up": round(sum(1 for v in rets if v > 0) / len(rets), 4),
                "count": len(rets),
            })
        return out

    def latest_daily_stats(self) -> Dict[str, Dict[str, float]]:
        """每只股票最新真实 bar 的当日涨跌幅%/成交额/收盘价（快照不可用时热力图兜底）。

        与 recent_returns 同款 45 天窗口 + amount>0 过滤；涨跌幅 = 最新 bar vs 前一根。
        """
        from datetime import date as _date, timedelta as _td
        cutoff = (_date.today() - _td(days=45)).strftime("%Y-%m-%d")
        sql = """
        WITH ranked AS (
            SELECT symbol, close, amount,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
            FROM daily_kline
            WHERE amount > 0 AND date >= ?
        )
        SELECT cur.symbol, cur.close, cur.amount, prev.close
        FROM ranked cur
        JOIN ranked prev ON prev.symbol = cur.symbol AND prev.rn = 2
        WHERE cur.rn = 1
        """
        out: Dict[str, Dict[str, float]] = {}
        for symbol, close, amount, prev_close in self._conn().execute(sql, (cutoff,)).fetchall():
            c = _f(close)
            p = _f(prev_close)
            if p > 0:
                out[str(symbol).zfill(6)] = {"pct": round((c - p) / p * 100, 2), "amount": _f(amount), "close": c}
        return out

    def breakdown_metrics(self, ma_window: int = 60) -> Dict[str, Dict[str, float]]:
        """批量生成持仓风险复核所需的趋势、量价和资金代理指标。"""
        from datetime import date as _date, timedelta as _td
        cutoff = (_date.today() - _td(days=150)).strftime("%Y-%m-%d")
        sql = """
        WITH ranked AS (
            SELECT symbol, date, open, high, low, close, volume, amount,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
            FROM daily_kline
            WHERE amount > 0 AND date >= ?
        )
        SELECT symbol, rn, open, high, low, close, volume, amount FROM ranked WHERE rn <= ?
        """
        rows_by_symbol: Dict[str, Dict[int, tuple]] = {}
        for row in self._conn().execute(sql, (cutoff, max(60, ma_window) + 1)).fetchall():
            symbol, rn, open_, high, low, close, volume, amount = row
            rows_by_symbol.setdefault(str(symbol).zfill(6), {})[int(rn)] = (
                _f(open_), _f(high), _f(low), _f(close), _f(volume), _f(amount)
            )
        out: Dict[str, Dict[str, float]] = {}
        for symbol, by_rn in rows_by_symbol.items():
            if 1 not in by_rn or len(by_rn) < 21:
                continue
            ordered = [by_rn[r] for r in sorted(by_rn)]
            closes_desc = [row[3] for row in ordered if row[3] > 0]
            if len(closes_desc) < 21:
                continue
            close = closes_desc[0]
            open_, high, low, _, volume, amount = ordered[0]
            ma10 = sum(closes_desc[:10]) / 10
            ma20 = sum(closes_desc[:20]) / 20
            ma60 = sum(closes_desc[:60]) / 60 if len(closes_desc) >= 60 else 0.0
            prev = closes_desc[1] if len(closes_desc) > 1 else 0.0
            prev_ma10 = sum(closes_desc[1:11]) / 10 if len(closes_desc) >= 11 else ma10
            prev_ma20 = sum(closes_desc[1:21]) / 20 if len(closes_desc) >= 21 else ma20
            prev_amounts = [row[5] for row in ordered[1:21] if row[5] > 0]
            amount_ma20 = sum(prev_amounts) / len(prev_amounts) if prev_amounts else 0.0
            pct = (close / prev - 1) * 100 if prev > 0 else 0.0

            signed_amount = total_amount = 0.0
            consecutive_down = 0
            for idx in range(min(5, len(ordered) - 1)):
                day_close = ordered[idx][3]
                prior_close = ordered[idx + 1][3]
                day_amount = ordered[idx][5]
                if prior_close <= 0 or day_amount <= 0:
                    continue
                day_change = day_close / prior_close - 1
                signed_amount += day_amount if day_change > 0 else (-day_amount if day_change < 0 else 0)
                total_amount += day_amount
                if idx == consecutive_down and day_change < 0:
                    consecutive_down += 1

            day_range = max(0.0, high - low)
            close_position = (close - low) / day_range if day_range > 0 else 0.5
            lower_shadow = (min(open_, close) - low) / day_range if day_range > 0 else 0.0
            return_5d = (close / closes_desc[5] - 1) * 100 if len(closes_desc) >= 6 else 0.0
            return_20d = (close / closes_desc[20] - 1) * 100 if len(closes_desc) >= 21 else 0.0

            # 高位见顶识别用的区间形态。用「低点→高点的涨幅」而不是「当前累计涨幅」：
            # 一只翻倍后又跌回来的票，60 日累计涨幅可能只剩十几个点，但它确实涨过一倍，
            # 正是要预警的对象。所以先定位区间最高点，再往更早找它涨起来的起点。
            peak_idx = min(range(len(closes_desc)), key=lambda i: -closes_desc[i])
            peak = closes_desc[peak_idx]
            trough_before_peak = min(closes_desc[peak_idx:]) if peak_idx < len(closes_desc) else close
            runup = (peak / trough_before_peak - 1) * 100 if trough_before_peak > 0 else 0.0
            drawdown_from_peak = (close / peak - 1) * 100 if peak > 0 else 0.0
            out[symbol] = {"close": round(close, 2), "ma10": round(ma10, 2),
                           "ma20": round(ma20, 2), "ma60": round(ma60, 2),
                           "pct": round(pct, 2),
                           "prev_close": round(prev, 2),
                           "prev_ma10": round(prev_ma10, 2),
                           "prev_ma20": round(prev_ma20, 2),
                           "open": round(open_, 2), "high": round(high, 2),
                           "low": round(low, 2), "volume": volume,
                           "amount": amount,
                           "amount_ratio": round(amount / amount_ma20, 2) if amount_ma20 > 0 else 0.0,
                           "capital_flow_5d": round(signed_amount / total_amount * 100, 2)
                           if total_amount > 0 else 0.0,
                           "return_5d": round(return_5d, 2),
                           "return_20d": round(return_20d, 2),
                           "peak": round(peak, 2),
                           "runup_pct": round(runup, 2),
                           "drawdown_from_peak": round(drawdown_from_peak, 2),
                           "days_since_peak": peak_idx,
                           "window_bars": len(closes_desc),
                           "close_position": round(close_position, 3),
                           "lower_shadow": round(lower_shadow, 3),
                           "consecutive_down": consecutive_down}
        return out

    # ---- 选股留痕与胜率复盘 ----
    def record_picks(self, pool: str, items: List[Dict[str, object]]) -> int:
        """历史表保留当日首次快照；latest_picks 原子替换为本次完整名单。"""
        if not items:
            return 0
        from datetime import date as _date, datetime as _datetime
        today = _date.today().strftime("%Y-%m-%d")
        batch_at = _datetime.now().astimezone().isoformat(timespec="seconds")
        rows = []
        for rank, it in enumerate(items, start=1):
            raw = str(it.get("symbol") or it.get("code") or "").strip()
            symbol = raw.zfill(6)
            if not raw or not symbol.isdigit() or len(symbol) != 6:
                continue
            patterns = ",".join(
                str(p.get("name") or "") for p in (it.get("patterns") or []) if isinstance(p, dict)
            )[:200]
            rows.append((today, pool, symbol, str(it.get("name") or ""),
                         _f(it.get("score")), _f(it.get("close")), rank, patterns))
        if not rows:
            return 0
        conn = self._conn()
        latest_rows = [
            (pool, symbol, pick_date, batch_at, name, score, close, rank, patterns)
            for pick_date, _, symbol, name, score, close, rank, patterns in rows
        ]
        with conn:
            conn.executemany(
                "INSERT OR IGNORE INTO picks_history(pick_date,pool,symbol,name,score,close,rank,patterns) "
                "VALUES(?,?,?,?,?,?,?,?)", rows)
            conn.execute("DELETE FROM latest_picks WHERE pool = ?", (pool,))
            conn.executemany(
                "INSERT INTO latest_picks(pool,symbol,pick_date,batch_at,name,score,close,rank,patterns) "
                "VALUES(?,?,?,?,?,?,?,?,?)", latest_rows)
        return len(rows)

    def load_latest_picks(self, pool: Optional[str] = None) -> List[Dict[str, object]]:
        sql = (
            "SELECT pick_date,pool,symbol,name,score,close,rank,patterns,batch_at "
            "FROM latest_picks"
        )
        params: List[object] = []
        if pool:
            sql += " WHERE pool = ?"
            params.append(pool)
        sql += " ORDER BY pool, rank, symbol"
        rows = self._conn().execute(sql, params).fetchall()
        return [{
            "pick_date": str(row[0]),
            "pool": str(row[1]),
            "symbol": str(row[2]),
            "name": str(row[3] or ""),
            "score": _f(row[4]),
            "close": _f(row[5]),
            "rank": int(row[6] or 0),
            "patterns": str(row[7] or ""),
            "batch_at": str(row[8] or ""),
        } for row in rows]

    def evaluate_picks(self, days: int = 30, pool: Optional[str] = None,
                       refresh: bool = False) -> Dict[str, object]:
        """复盘最近 days 天的选股留痕：按池统计 T+1/T+3/T+5 胜率与平均收益。

        基准价 = 留痕时的价格（扫描当时用户看到的价格）；缺失时退回 pick_date 当日
        （或之前最近）的真实日线收盘。T+N = pick_date 之后第 N 根真实日线（amount>0）的收盘。

        全量重算要为每个 horizon 扫全市场收盘截面（实测空闲 20s、机器繁忙时 110s，
        超过前端 30s 超时 → 复盘页留痕区整块消失）。按 (days, pool, 最新真实 bar 日,
        留痕条数) 缓存：新交易日数据落库或新留痕写入都会换 key，统计不会读到陈旧值。
        """
        import bisect
        import json as _json
        from datetime import date as _date, datetime as _dt, timedelta as _td
        since = (_date.today() - _td(days=max(1, days))).strftime("%Y-%m-%d")
        conn = self._conn()

        picks_n = conn.execute(
            "SELECT COUNT(*) FROM picks_history WHERE pick_date >= ?", (since,)).fetchone()[0]
        cache_key = f"picks:{days}:{pool or '*'}"
        stamp = f"{self.latest_real_bar_date()}|{picks_n}"
        if not refresh:
            row = conn.execute(
                "SELECT payload_json FROM signal_stats_cache WHERE cache_key=? AND stamp=?",
                (cache_key, stamp)).fetchone()
            if row:
                try:
                    cached = _json.loads(row[0])
                    cached["latest"] = self.load_latest_picks(pool)
                    return cached
                except Exception:
                    pass
        sql = ("SELECT pick_date,pool,symbol,name,score,close,rank,patterns FROM picks_history "
               "WHERE pick_date >= ?")
        params: List[object] = [since]
        if pool:
            sql += " AND pool = ?"
            params.append(pool)
        sql += " ORDER BY pick_date DESC, pool, rank"
        picks = conn.execute(sql, params).fetchall()

        import statistics

        # ---- 全市场收盘矩阵：基准（中位收益）与覆盖率守卫共用 ----
        by_date: Dict[str, Dict[str, float]] = {}
        for d, sym, c in conn.execute(
            "SELECT date, symbol, close FROM daily_kline WHERE date >= ? AND amount > 0",
            (since,),
        ):
            by_date.setdefault(str(d), {})[str(sym)] = _f(c)
        trading_dates = sorted(by_date)
        max_coverage = max((len(v) for v in by_date.values()), default=0)

        def _cover_ok(d: str) -> bool:
            return max_coverage <= 0 or len(by_date.get(d, {})) >= max_coverage * MIN_MARKET_COVERAGE

        bench_cache: Dict[tuple, Optional[float]] = {}

        def _bench(d0: str, d1: str) -> Optional[float]:
            """d0→d1 全市场中位涨跌幅（%），仅统计两日都有 bar 的股票。"""
            key = (d0, d1)
            if key not in bench_cache:
                m0, m1 = by_date.get(d0, {}), by_date.get(d1, {})
                rets = [(m1[s] / m0[s] - 1) * 100 for s in m0.keys() & m1.keys() if m0[s] > 0]
                bench_cache[key] = round(statistics.median(rets), 4) if rets else None
            return bench_cache[key]

        kline_cache: Dict[str, List[tuple]] = {}

        def _bars(sym: str) -> List[tuple]:
            if sym not in kline_cache:
                kline_cache[sym] = conn.execute(
                    "SELECT date, close FROM daily_kline WHERE symbol=? AND amount>0 ORDER BY date",
                    (sym,)).fetchall()
            return kline_cache[sym]

        horizons = (1, 3, 5)
        detail: List[Dict[str, object]] = []
        agg: Dict[str, Dict[int, List[float]]] = {}
        ex_agg: Dict[str, Dict[int, List[float]]] = {}
        for pick_date, pool_name, symbol, name, score, close, rank, patterns_str in picks:
            bars = _bars(str(symbol))
            dates = [b[0] for b in bars]
            idx = bisect.bisect_right(dates, str(pick_date)) - 1
            base = _f(close)
            if base <= 0 and idx >= 0:
                base = _f(bars[idx][1])
            i_mkt = bisect.bisect_right(trading_dates, str(pick_date)) - 1
            rets: Dict[str, Optional[float]] = {}
            for h in horizons:
                j = idx + h
                jm = i_mkt + h
                tgt = trading_dates[jm] if 0 <= i_mkt and jm < len(trading_dates) else None
                ready = (idx >= 0 and base > 0 and j < len(bars)
                         and tgt is not None and _cover_ok(tgt))
                if ready:
                    ret = round((_f(bars[j][1]) / base - 1) * 100, 2)
                    rets[f"t{h}"] = ret
                    bench = _bench(trading_dates[i_mkt], tgt)
                    rets[f"excess_t{h}"] = round(ret - bench, 2) if bench is not None else None
                else:
                    rets[f"t{h}"] = None
                    rets[f"excess_t{h}"] = None
            detail.append({
                "pick_date": pick_date, "pool": pool_name, "symbol": symbol, "name": name,
                "score": _f(score), "rank": int(rank or 0), "base_close": round(base, 2),
                "patterns": str(patterns_str or ""), **rets,
            })
            bucket = agg.setdefault(str(pool_name), {h: [] for h in horizons})
            ex_bucket = ex_agg.setdefault(str(pool_name), {h: [] for h in horizons})
            for h in horizons:
                v = rets[f"t{h}"]
                if v is not None:
                    bucket[h].append(v)
                ev = rets[f"excess_t{h}"]
                if ev is not None:
                    ex_bucket[h].append(ev)

        pools_out: List[Dict[str, object]] = []
        for pool_name in sorted(agg):
            stats: Dict[str, object] = {}
            for h in horizons:
                vals = agg[pool_name][h]
                evals = ex_agg[pool_name][h]
                stats[f"t{h}"] = {
                    "samples": len(vals),
                    "win_rate": round(sum(1 for v in vals if v > 0) / len(vals), 4) if vals else None,
                    "avg_return": round(sum(vals) / len(vals), 2) if vals else None,
                    "excess_win_rate": round(sum(1 for v in evals if v > 0) / len(evals), 4) if evals else None,
                    "avg_excess": round(sum(evals) / len(evals), 2) if evals else None,
                }
            pools_out.append({
                "pool": pool_name,
                "picks": sum(1 for p in detail if p["pool"] == pool_name),
                "horizons": stats,
            })
        out = {
            "days": days, "since": since, "total_picks": len(picks),
            "pools": pools_out, "items": detail[:300],
        }
        conn.execute(
            "INSERT INTO signal_stats_cache(cache_key,stamp,payload_json,created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(cache_key) DO UPDATE SET stamp=excluded.stamp, "
            "payload_json=excluded.payload_json, created_at=excluded.created_at",
            (cache_key, stamp, _json.dumps(out, ensure_ascii=False),
             _dt.now().isoformat(timespec="seconds")))
        conn.commit()
        out["latest"] = self.load_latest_picks(pool)
        return out

    def signal_stats(self, pool: str, days: int = 90, refresh: bool = False) -> Dict[str, object]:
        """信号历史表现：池级（留痕 + 最近回放）与形态级（留痕 T+5 超额）聚合，供入选理由卡。

        全量重算要扫 90 天留痕 ×全市场收盘矩阵（实测约 20 秒），结果按
        (pool, days, 最新日线日, 自然日) 缓存——T+N 统计只在新交易日数据落库后才变化，
        当日新增留痕只是「待更新」行，不影响已结算统计。
        """
        import json as _json
        from datetime import date as _date

        cache_key = f"{pool}:{days}"
        stamp = f"{_date.today().isoformat()}|{self.latest_real_bar_date() or ''}"
        conn = self._conn()
        if not refresh:
            row = conn.execute(
                "SELECT payload_json FROM signal_stats_cache WHERE cache_key=? AND stamp=?",
                (cache_key, stamp)).fetchone()
            if row:
                try:
                    return _json.loads(row[0])
                except Exception:
                    pass
        stats = self.evaluate_picks(days=days, pool=pool)
        pool_stat = next((p for p in stats["pools"] if p["pool"] == pool), None)
        pat_agg: Dict[str, List[float]] = {}
        for it in stats["items"]:
            ev = it.get("excess_t5")
            if ev is None:
                continue
            for pname in str(it.get("patterns") or "").split(","):
                pname = pname.strip()
                if pname:
                    pat_agg.setdefault(pname, []).append(float(ev))
        patterns = [{
            "name": k, "samples": len(v),
            "excess_win_rate": round(sum(1 for x in v if x > 0) / len(v), 4),
            "avg_excess": round(sum(v) / len(v), 2),
        } for k, v in sorted(pat_agg.items(), key=lambda kv: -len(kv[1]))]
        replay_stat = None
        try:
            from .replay import latest_replay_summary
            summ = latest_replay_summary(self)
            if summ:
                rp = next((p for p in summ.get("pools", []) if p.get("pool") == pool), None)
                if rp:
                    replay_stat = {
                        "picks": rp.get("picks"), "evaluated": rp.get("evaluated"),
                        "excess_win_rate": rp.get("excess_win_rate"), "avg_excess": rp.get("avg_excess"),
                        "run_id": summ.get("run_id"), "created_at": summ.get("created_at"),
                    }
        except Exception:
            replay_stat = None
        out = {
            "pool": pool, "days": days,
            "live": (pool_stat or {}).get("horizons"),
            "live_picks": (pool_stat or {}).get("picks", 0),
            "patterns": patterns,
            "replay": replay_stat,
        }
        from datetime import datetime as _dt
        conn.execute(
            "INSERT INTO signal_stats_cache(cache_key,stamp,payload_json,created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(cache_key) DO UPDATE SET stamp=excluded.stamp, "
            "payload_json=excluded.payload_json, created_at=excluded.created_at",
            (cache_key, stamp, _json.dumps(out, ensure_ascii=False),
             _dt.now().isoformat(timespec="seconds")))
        conn.commit()
        return out

    # ---- 状态 ----
    def set_state(self, key: str, value: str) -> None:
        conn = self._conn()
        conn.execute("INSERT INTO sync_state(key,value) VALUES(?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        conn.commit()

    def get_state(self, key: str) -> Optional[str]:
        row = self._conn().execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    # ---- 基本面利空标记 ----
    def upsert_fundamental_flags(self, rows: List[Dict[str, object]]) -> None:
        """rows: [{symbol, bad_forecast(bool/int), forecast_type, change, period}]"""
        from datetime import datetime
        now = datetime.now().isoformat(timespec="seconds")
        conn = self._conn()
        conn.executemany(
            "INSERT INTO fundamental_flags(symbol,bad_forecast,forecast_type,change,period,updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET "
            "bad_forecast=excluded.bad_forecast, forecast_type=excluded.forecast_type, "
            "change=excluded.change, period=excluded.period, updated_at=excluded.updated_at",
            [(str(r.get("symbol")), 1 if r.get("bad_forecast") else 0, str(r.get("forecast_type") or ""),
              str(r.get("change") or ""), str(r.get("period") or ""), now) for r in rows],
        )
        conn.commit()

    def load_bad_forecast_symbols(self) -> set:
        cur = self._conn().execute("SELECT symbol FROM fundamental_flags WHERE bad_forecast=1")
        return {row[0] for row in cur.fetchall()}

    def load_fundamental_flags(self) -> Dict[str, Dict[str, object]]:
        cur = self._conn().execute(
            "SELECT symbol,bad_forecast,forecast_type,change,period,updated_at FROM fundamental_flags"
        )
        return {
            str(symbol).zfill(6): {
                "bad_forecast": bool(bad_forecast),
                "forecast_type": str(forecast_type or ""),
                "change": str(change or ""),
                "period": str(period or ""),
                "updated_at": str(updated_at or ""),
            }
            for symbol, bad_forecast, forecast_type, change, period, updated_at in cur.fetchall()
        }

    def fundamental_flag_count(self) -> int:
        return self._conn().execute("SELECT COUNT(*) FROM fundamental_flags WHERE bad_forecast=1").fetchone()[0]

    # ---- 每日盘报 ----
    def save_daily_report(self, date: str, kind: str, content: Dict[str, object]) -> None:
        import json
        from datetime import datetime
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO daily_reports(date, kind, content_json, created_at) VALUES (?,?,?,?)",
            (date, kind, json.dumps(content, ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()

    def load_daily_report(self, date: str, kind: str) -> Optional[Dict[str, object]]:
        import json
        row = self._conn().execute(
            "SELECT content_json FROM daily_reports WHERE date=? AND kind=?", (date, kind)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def latest_daily_report(self, kind: str) -> Optional[Dict[str, object]]:
        import json
        row = self._conn().execute(
            "SELECT content_json FROM daily_reports WHERE kind=? ORDER BY date DESC LIMIT 1", (kind,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def list_report_dates(self, limit: int = 30) -> List[Dict[str, str]]:
        rows = self._conn().execute(
            "SELECT date, kind FROM daily_reports ORDER BY date DESC, kind LIMIT ?", (limit,)
        ).fetchall()
        return [{"date": r[0], "kind": r[1]} for r in rows]

    # ---- 五方判读批量评分 ----
    def save_panel_score(self, date: str, symbol: str, payload: Dict[str, object]) -> None:
        import json
        from datetime import datetime
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO panel_scores"
            "(date, symbol, consensus, divergence, bull, bear, verdicts_json, summary, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (date, symbol,
             float(payload.get("consensus_score") or 0.0),
             float(payload.get("divergence") or 0.0),
             int(payload.get("bull_count") or 0),
             int(payload.get("bear_count") or 0),
             json.dumps(payload.get("verdicts") or [], ensure_ascii=False),
             str(payload.get("summary") or ""),
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()

    def load_panel_scores(self, date: str, symbols: Optional[List[str]] = None) -> Dict[str, Dict[str, object]]:
        import json
        if symbols is not None and not symbols:
            return {}
        sql = ("SELECT symbol, consensus, divergence, bull, bear, verdicts_json, summary"
               " FROM panel_scores WHERE date=?")
        params: List[object] = [date]
        if symbols:
            sql += f" AND symbol IN ({','.join('?' * len(symbols))})"
            params.extend(symbols)
        out: Dict[str, Dict[str, object]] = {}
        for row in self._conn().execute(sql, params).fetchall():
            try:
                verdicts = json.loads(row[5] or "[]")
            except ValueError:
                verdicts = []
            out[row[0]] = {
                "consensus_score": row[1], "divergence": row[2],
                "bull_count": row[3], "bear_count": row[4],
                "verdicts": verdicts, "summary": row[6],
            }
        return out

    def load_picks_symbols(self, date: str, pool: str, limit: int = 20) -> List[str]:
        rows = self._conn().execute(
            "SELECT symbol FROM picks_history WHERE pick_date=? AND pool=?"
            " ORDER BY COALESCE(rank, 999), symbol LIMIT ?",
            (date, pool, limit),
        ).fetchall()
        return [r[0] for r in rows]

    # ---- Arena 虚拟盘 ----
    ARENA_INITIAL_CASH = 1_000_000.0

    def arena_cash(self, persona: str) -> float:
        row = self._conn().execute("SELECT cash FROM arena_state WHERE persona=?", (persona,)).fetchone()
        return float(row[0]) if row else self.ARENA_INITIAL_CASH

    def set_arena_cash(self, persona: str, cash: float) -> None:
        conn = self._conn()
        conn.execute("INSERT OR REPLACE INTO arena_state(persona, cash) VALUES(?,?)", (persona, float(cash)))
        conn.commit()

    def arena_positions(self, persona: str) -> List[Dict[str, object]]:
        rows = self._conn().execute(
            "SELECT symbol, shares, avg_cost FROM arena_positions WHERE persona=? ORDER BY symbol",
            (persona,)).fetchall()
        return [{"symbol": r[0], "shares": int(r[1]), "avg_cost": float(r[2])} for r in rows]

    def upsert_arena_position(self, persona: str, symbol: str, shares: int, avg_cost: float) -> None:
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO arena_positions(persona, symbol, shares, avg_cost) VALUES(?,?,?,?)",
            (persona, symbol, int(shares), float(avg_cost)))
        conn.commit()

    def delete_arena_position(self, persona: str, symbol: str) -> None:
        conn = self._conn()
        conn.execute("DELETE FROM arena_positions WHERE persona=? AND symbol=?", (persona, symbol))
        conn.commit()

    def insert_arena_trade(self, date: str, persona: str, symbol: str, side: str,
                           price: float, shares: int, reason: str) -> None:
        from datetime import datetime
        conn = self._conn()
        conn.execute(
            "INSERT INTO arena_trades(date, persona, symbol, side, price, shares, reason, created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (date, persona, symbol, side, float(price), int(shares), str(reason or ""),
             datetime.now().isoformat(timespec="seconds")))
        conn.commit()

    def load_arena_trades(self, persona: str, limit: int = 50) -> List[Dict[str, object]]:
        rows = self._conn().execute(
            "SELECT date, symbol, side, price, shares, reason FROM arena_trades"
            " WHERE persona=? ORDER BY id DESC LIMIT ?", (persona, int(limit))).fetchall()
        return [{"date": r[0], "symbol": r[1], "side": r[2], "price": float(r[3]),
                 "shares": int(r[4]), "reason": r[5]} for r in rows]

    def arena_nav_exists(self, date: str, persona: str) -> bool:
        return self._conn().execute(
            "SELECT 1 FROM arena_nav WHERE date=? AND persona=?", (date, persona)).fetchone() is not None

    def save_arena_nav(self, date: str, persona: str, nav: float, cash: float, comment: str = "") -> None:
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO arena_nav(date, persona, nav, cash, comment) VALUES(?,?,?,?,?)",
            (date, persona, float(nav), float(cash), str(comment or "")))
        conn.commit()

    def load_arena_nav_series(self) -> Dict[str, List[Dict[str, object]]]:
        out: Dict[str, List[Dict[str, object]]] = {}
        for date, persona, nav, comment in self._conn().execute(
                "SELECT date, persona, nav, comment FROM arena_nav ORDER BY date").fetchall():
            out.setdefault(persona, []).append({"date": date, "nav": float(nav), "comment": comment or ""})
        return out


def _f(v) -> float:
    try:
        x = float(v)
        return x if x == x else 0.0
    except (TypeError, ValueError):
        return 0.0


_store_singleton: Optional[LocalQuantStore] = None
_store_lock = threading.Lock()


def get_local_store() -> LocalQuantStore:
    global _store_singleton
    if _store_singleton is None:
        with _store_lock:
            if _store_singleton is None:
                _store_singleton = LocalQuantStore()
    return _store_singleton
