"""全市场量化数据的本地 SQLite 存储（与认证库分离）。"""
from __future__ import annotations
import os
from pathlib import Path
import threading
from typing import Dict, List, Optional

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = os.environ.get("QUANT_DATA_DB_PATH", str(_PROJECT_ROOT / "runtime" / "quant_data.sqlite"))

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
CREATE TABLE IF NOT EXISTS sync_state (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS fundamental_flags (
    symbol TEXT PRIMARY KEY,
    bad_forecast INTEGER,
    forecast_type TEXT,
    change TEXT,
    period TEXT,
    updated_at TEXT
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
        needs_incremental_sync = bool(ready and today_is_weekday and daily_bar_due and not today_complete)
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

    def recent_returns(self, window: int = 5) -> Dict[str, float]:
        """每只股票最近 window 个交易日的涨跌幅%（最新完整 bar vs window 根前的 bar）。

        只用 amount>0 的真实 bar（跳过盘中占位 bar），供"近段趋势热门板块"动态识别。
        """
        sql = """
        WITH ranked AS (
            SELECT symbol, close,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
            FROM daily_kline
            WHERE amount > 0
        )
        SELECT cur.symbol, cur.close, base.close
        FROM ranked cur
        JOIN ranked base ON base.symbol = cur.symbol AND base.rn = ?
        WHERE cur.rn = 1
        """
        out: Dict[str, float] = {}
        for symbol, cur_close, base_close in self._conn().execute(sql, (window + 1,)).fetchall():
            c = _f(cur_close)
            b = _f(base_close)
            if b > 0:
                out[str(symbol).zfill(6)] = (c / b - 1) * 100
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

    def fundamental_flag_count(self) -> int:
        return self._conn().execute("SELECT COUNT(*) FROM fundamental_flags WHERE bad_forecast=1").fetchone()[0]


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
