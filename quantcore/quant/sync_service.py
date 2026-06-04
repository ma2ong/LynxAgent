"""Full-market daily K-line sync service: background thread + progress tracking.

This intentionally mirrors the working TradingAgents sync flow: every manual
incremental sync walks the whole local universe and upserts Tencent daily bars.
It avoids slow per-symbol third-party fallbacks during batch sync.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import requests

from .data_sources import data_source_status, fetch_stock_pool
from .local_store import LocalQuantStore, get_local_store

FULL_HISTORY_DAYS = 760


def _tencent_code(symbol: str) -> str:
    s = str(symbol).strip().zfill(6)
    if s.startswith(("6", "9")):
        return f"sh{s}"
    if s.startswith(("8", "4")):
        return f"bj{s}"
    return f"sz{s}"


def _f(value) -> float:
    try:
        number = float(value)
        return number if number == number else 0.0
    except (TypeError, ValueError):
        return 0.0


class MarketSyncService:
    def __init__(self, store: Optional[LocalQuantStore] = None, max_workers: int = 8):
        self.store = store or get_local_store()
        self.max_workers = max_workers
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._progress: Dict[str, object] = {
            "running": False,
            "phase": "idle",
            "done": 0,
            "total": 0,
            "errors_count": 0,
            "last_error": "",
            "started_at": "",
            "finished_at": "",
        }

    def _fetch_universe(self) -> List[Dict[str, object]]:
        local_rows = self.store.load_meta()
        if local_rows:
            self._progress["universe_source"] = "local-store"
            self._progress["source_errors"] = {}
            return [
                {
                    "symbol": item.get("symbol"),
                    "name": item.get("name", ""),
                    "industry": item.get("industry", ""),
                    "source": "local-store",
                }
                for item in local_rows
                if str(item.get("symbol") or "").isdigit()
            ]

        rows, source, errors = fetch_stock_pool(6000)
        self._progress["universe_source"] = source or "none"
        self._progress["source_errors"] = errors
        return [
            {
                "symbol": item.get("symbol"),
                "name": item.get("name", ""),
                "industry": "",
                "source": item.get("source", source),
            }
            for item in rows
            if str(item.get("symbol") or "").isdigit()
        ]

    def _fetch_kline(self, symbol: str, start: str) -> pd.DataFrame:
        df = self._fetch_kline_tencent(symbol, start)
        if df is not None and not df.empty:
            return df
        return pd.DataFrame()

    def _fetch_kline_tencent(self, symbol: str, start: str) -> pd.DataFrame:
        code = _tencent_code(symbol)
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,800,qfq"
        try:
            resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            payload = resp.json()["data"][code]
            bars = payload.get("qfqday") or payload.get("day") or []
        except Exception:
            return pd.DataFrame()

        rows = []
        for bar in bars:
            if len(bar) < 6:
                continue
            trade_date = str(bar[0])
            if trade_date < start:
                continue
            open_price = _f(bar[1])
            close_price = _f(bar[2])
            high_price = _f(bar[3])
            low_price = _f(bar[4])
            volume = _f(bar[5])
            rows.append(
                {
                    "date": trade_date,
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": volume,
                    "amount": close_price * volume * 100,
                }
            )
        return pd.DataFrame(rows)

    def _fetch_fundamental_flags(self) -> List[Dict[str, object]]:
        import akshare as ak

        from .screening import BAD_FORECAST_TYPES

        today = date.today()
        periods: List[str] = []
        current_year = today.year
        for month, day in [(12, 31), (9, 30), (6, 30), (3, 31)]:
            for year in (current_year, current_year - 1):
                periods.append(f"{year}{month:02d}{day:02d}")
        periods = sorted({p for p in periods if p <= today.strftime("%Y%m%d")}, reverse=True)[:5]

        for period in periods:
            try:
                df = ak.stock_yjyg_em(date=period)
            except Exception:
                continue
            if df is None or df.empty or "预告类型" not in df.columns:
                continue
            code_col = "股票代码" if "股票代码" in df.columns else df.columns[1]
            chg_col = "业绩变动幅度" if "业绩变动幅度" in df.columns else None
            rows: List[Dict[str, object]] = []
            for _, row in df.iterrows():
                forecast_type = str(row.get("预告类型") or "")
                if not any(bad in forecast_type for bad in BAD_FORECAST_TYPES):
                    continue
                code = str(row.get(code_col) or "").zfill(6)
                if not code.isdigit():
                    continue
                rows.append(
                    {
                        "symbol": code,
                        "bad_forecast": True,
                        "forecast_type": forecast_type,
                        "change": str(row.get(chg_col)) if chg_col else "",
                        "period": period,
                    }
                )
            if rows:
                return rows
        return []

    def status(self) -> Dict[str, object]:
        status = dict(self._progress)
        try:
            status["local_meta_symbols"] = self.store.symbol_count()
            status["local_kline_symbols"] = self.store.kline_symbol_count()
            status["last_full_sync"] = self.store.get_state("last_full_sync") or ""
            status["last_incremental_sync"] = self.store.get_state("last_incremental_sync") or ""
            if hasattr(self.store, "kline_health"):
                status["health"] = self.store.kline_health()
            status["data_sources"] = data_source_status()
        except Exception as exc:
            status["last_error"] = str(exc)[:200]
        return status

    def run_sync(self, full: bool = False, block: bool = False) -> Dict[str, object]:
        with self._lock:
            if self._progress["running"]:
                return self.status()
            self._progress.update(
                {
                    "running": True,
                    "phase": "starting",
                    "done": 0,
                    "total": 0,
                    "errors_count": 0,
                    "last_error": "",
                    "started_at": datetime.now().isoformat(timespec="seconds"),
                    "finished_at": "",
                }
            )
        if block:
            self._do_sync(full)
        else:
            self._thread = threading.Thread(target=self._do_sync, args=(full,), daemon=True)
            self._thread.start()
        return self.status()

    def _do_sync(self, full: bool) -> None:
        try:
            self._progress["phase"] = "meta"
            universe = self._fetch_universe()
            if universe:
                self.store.upsert_meta(universe)
            self._progress["total"] = len(universe)

            self._progress["phase"] = "kline"
            full_start = (date.today() - timedelta(days=FULL_HISTORY_DAYS)).strftime("%Y-%m-%d")

            def work(meta):
                symbol = str(meta.get("symbol"))
                if full:
                    start = full_start
                else:
                    last = self.store.last_kline_date(symbol)
                    start = last or full_start
                df = self._fetch_kline(symbol, start)
                return self.store.upsert_kline(symbol, df)

            done = 0
            written_rows = 0
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {executor.submit(work, meta): meta for meta in universe}
                for future in as_completed(futures):
                    try:
                        written_rows += int(future.result() or 0)
                    except Exception as exc:
                        self._progress["errors_count"] = int(self._progress["errors_count"]) + 1
                        self._progress["last_error"] = str(exc)[:200]
                    done += 1
                    self._progress["done"] = done
                    self._progress["written_rows"] = written_rows

            self._progress["phase"] = "fundamental"
            try:
                flags = self._fetch_fundamental_flags()
                if flags:
                    self.store.upsert_fundamental_flags(flags)
            except Exception as exc:
                self._progress["last_error"] = ("fundamental: " + str(exc))[:200]

            key = "last_full_sync" if full else "last_incremental_sync"
            self.store.set_state(key, datetime.now().isoformat(timespec="seconds"))
            self._progress["phase"] = "done"
        finally:
            self._progress["running"] = False
            self._progress["finished_at"] = datetime.now().isoformat(timespec="seconds")


_service_singleton: Optional[MarketSyncService] = None
_service_lock = threading.Lock()


def get_sync_service() -> MarketSyncService:
    global _service_singleton
    if _service_singleton is None:
        with _service_lock:
            if _service_singleton is None:
                _service_singleton = MarketSyncService()
    return _service_singleton
