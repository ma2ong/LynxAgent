"""全市场日线同步服务：后台线程 + 进度跟踪。"""
from __future__ import annotations
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import requests

from .local_store import LocalQuantStore, get_local_store
from .data_sources import data_source_status, fetch_stock_pool

FULL_HISTORY_DAYS = 760  # 约 2 年自然日，覆盖形态与中长期均线


def _tencent_code(symbol: str) -> str:
    s = str(symbol).strip().zfill(6)
    if s.startswith(("6", "9")):
        return f"sh{s}"
    if s.startswith(("8", "4")):
        return f"bj{s}"
    return f"sz{s}"


def _f(v) -> float:
    try:
        x = float(v)
        return x if x == x else 0.0
    except (TypeError, ValueError):
        return 0.0


class MarketSyncService:
    def __init__(self, store: Optional[LocalQuantStore] = None, max_workers: int = 8):
        self.store = store or get_local_store()
        self.max_workers = max_workers
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._progress: Dict[str, object] = {
            "running": False, "phase": "idle", "done": 0, "total": 0,
            "errors_count": 0, "last_error": "", "started_at": "", "finished_at": "",
        }

    def _fetch_universe(self) -> List[Dict[str, object]]:
        local_rows = self.store.load_meta()
        if local_rows:
            self._progress["universe_source"] = "local-store"
            self._progress["source_errors"] = {}
            return [
                {"symbol": item.get("symbol"), "name": item.get("name", ""), "industry": item.get("industry", ""), "source": "local-store"}
                for item in local_rows
                if str(item.get("symbol") or "").isdigit()
            ]

        rows, source, errors = fetch_stock_pool(6000)
        self._progress["universe_source"] = source or "none"
        self._progress["source_errors"] = errors
        return [
            {"symbol": item.get("symbol"), "name": item.get("name", ""), "industry": "", "source": item.get("source", source)}
            for item in rows
            if str(item.get("symbol") or "").isdigit()
        ]

    def _fetch_kline(self, symbol: str, start: str) -> pd.DataFrame:
        """全市场同步只走腾讯快链路；外部库保留给单股查询，避免批量同步被三方源阻塞。"""
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
        for b in bars:
            if len(b) < 6:
                continue
            d = str(b[0])
            if d < start:
                continue
            # 腾讯字段顺序：date, open, close, high, low, volume(手)
            o, c, h, l, v = _f(b[1]), _f(b[2]), _f(b[3]), _f(b[4]), _f(b[5])
            rows.append({"date": d, "open": o, "high": h, "low": l, "close": c,
                         "volume": v, "amount": c * v * 100})
        return pd.DataFrame(rows)

    def _fetch_kline_external(self, symbol: str, start: str) -> pd.DataFrame:
        # 回退源：efinance / AKShare / BaoStock，带退避重试。
        import time
        from .data import fetch_stock_dataframe
        end = date.today().strftime("%Y-%m-%d")
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                df = fetch_stock_dataframe(symbol, start, end)
                if df is not None and not df.empty:
                    return df
            except Exception as exc:
                last_exc = exc
            time.sleep(0.6 * (attempt + 1))
        if last_exc is not None:
            raise last_exc
        return pd.DataFrame()

    def _fetch_fundamental_flags(self) -> List[Dict[str, object]]:
        """拉取最近报告期业绩预告，返回基本面利空股票标记（首亏/续亏/预减等）。批量、不踩限流。"""
        import akshare as ak
        from .screening import BAD_FORECAST_TYPES
        # 候选报告期：最近 5 个季度末，取第一个有数据的
        today = date.today()
        periods: List[str] = []
        y = today.year
        for (m, d) in [(12, 31), (9, 30), (6, 30), (3, 31)]:
            for yy in (y, y - 1):
                periods.append(f"{yy}{m:02d}{d:02d}")
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
            for _, r in df.iterrows():
                ftype = str(r.get("预告类型") or "")
                if not any(bad in ftype for bad in BAD_FORECAST_TYPES):
                    continue
                code = str(r.get(code_col) or "").zfill(6)
                if not code.isdigit():
                    continue
                rows.append({"symbol": code, "bad_forecast": True, "forecast_type": ftype,
                             "change": str(r.get(chg_col)) if chg_col else "", "period": period})
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
            status["data_sources"] = data_source_status()
        except Exception as exc:
            status["last_error"] = str(exc)[:200]
        return status

    def run_sync(self, full: bool = False, block: bool = False) -> Dict[str, object]:
        with self._lock:
            if self._progress["running"]:
                return self.status()
            self._progress.update({"running": True, "phase": "starting", "done": 0,
                                   "total": 0, "errors_count": 0, "last_error": "",
                                   "started_at": datetime.now().isoformat(timespec="seconds"),
                                   "finished_at": ""})
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
                self.store.upsert_kline(symbol, df)

            done = 0
            with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
                futures = {ex.submit(work, m): m for m in universe}
                for fut in as_completed(futures):
                    try:
                        fut.result()
                    except Exception as exc:
                        self._progress["errors_count"] = int(self._progress["errors_count"]) + 1
                        self._progress["last_error"] = str(exc)[:200]
                    done += 1
                    self._progress["done"] = done

            # 基本面利空标记（业绩预告），批量一次，失败不影响主流程
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
