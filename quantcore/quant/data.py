from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import pandas as pd


REQUIRED_PRICE_COLUMNS = {"open", "high", "low", "close"}


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [str(col[0]) for col in out.columns]
    column_map = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
        "vol": "volume",
        "amount": "amount",
        "trade_date": "date",
        "Date": "date",
        "Datetime": "date",
        "日期": "date",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
        "成交额": "amount",
    }
    out = out.rename(columns={col: column_map.get(col, col) for col in out.columns})

    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
        out = out.dropna(subset=["date"]).sort_values("date")
    else:
        out = out.reset_index().rename(columns={"index": "date"})
        out = out.rename(columns={c: column_map.get(c, c) for c in out.columns})
        out["date"] = pd.to_datetime(out["date"], errors="coerce")

    for col in REQUIRED_PRICE_COLUMNS | {"volume", "amount"}:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out.dropna(subset=list(REQUIRED_PRICE_COLUMNS))
    out = out[out["close"] > 0]
    return out.reset_index(drop=True)


def default_start_date(days: int = 420) -> str:
    return (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")


def _compact_date(value: Optional[str]) -> Optional[str]:
    return value.replace("-", "") if value else None


def _fetch_from_akshare(symbol: str, start_date: Optional[str], end_date: Optional[str]) -> pd.DataFrame:
    import akshare as ak

    clean_symbol = symbol.strip()
    return ak.stock_zh_a_hist(
        symbol=clean_symbol,
        period="daily",
        start_date=_compact_date(start_date) or _compact_date(default_start_date()),
        end_date=_compact_date(end_date) or _compact_date(date.today().strftime("%Y-%m-%d")),
        adjust="qfq",
    )


def _fetch_from_yfinance(symbol: str, start_date: Optional[str], end_date: Optional[str]) -> pd.DataFrame:
    import yfinance as yf

    candidates = [symbol]
    clean_symbol = symbol.strip()
    if clean_symbol.isdigit() and len(clean_symbol) == 6:
        suffix = ".SS" if clean_symbol.startswith("6") else ".SZ"
        candidates.insert(0, f"{clean_symbol}{suffix}")

    for candidate in candidates:
        df = yf.download(candidate, start=start_date or default_start_date(), end=end_date, progress=False, auto_adjust=False)
        if df is not None and not df.empty:
            return df
    return pd.DataFrame()


def fetch_stock_dataframe(symbol: str, start_date: Optional[str], end_date: Optional[str]) -> pd.DataFrame:
    for fetcher in (_fetch_from_akshare, _fetch_from_yfinance):
        try:
            df = normalize_ohlcv(fetcher(symbol, start_date, end_date))
            if not df.empty:
                return df
        except Exception:
            continue

    return pd.DataFrame()


def load_local_kline(symbol: str, days: int | None = None) -> "pd.DataFrame":
    """从本地 SQLite 读取日线并归一化；无数据返回空 DataFrame。"""
    from quantcore.quant.local_store import get_local_store
    store = get_local_store()
    raw = store.load_kline(symbol, limit=days)
    if raw is None or raw.empty:
        return pd.DataFrame()
    return normalize_ohlcv(raw)
