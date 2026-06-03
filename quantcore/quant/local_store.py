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

    def kline_symbol_count(self) -> int:
        return self._conn().execute("SELECT COUNT(DISTINCT symbol) FROM daily_kline").fetchone()[0]

    def latest_snapshots(self) -> Dict[str, Dict[str, float]]:
        sql = """
        WITH ranked AS (
            SELECT symbol, close, amount,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
            FROM daily_kline
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
