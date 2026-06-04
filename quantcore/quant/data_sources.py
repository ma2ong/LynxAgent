from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
import importlib.util
import queue
import threading
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd


@dataclass
class DataSourceInfo:
    key: str
    name: str
    installed: bool
    enabled: bool
    priority: int
    capabilities: List[str]
    notes: str


DEFAULT_SOURCE_ORDER = ("akshare", "efinance", "baostock")
DISPLAY_SOURCE_ORDER = ("akshare", "efinance", "baostock")


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _safe_str(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _compact_date(value: Optional[str]) -> Optional[str]:
    return value.replace("-", "") if value else None


def _iso_date(value: Optional[str]) -> str:
    if value:
        return value.replace("/", "-")[:10]
    return (date.today() - timedelta(days=420)).strftime("%Y-%m-%d")


def _source_sequence(preferred: Optional[Sequence[str]] = None) -> List[str]:
    if not preferred:
        return list(DEFAULT_SOURCE_ORDER)
    seen = set()
    ordered: List[str] = []
    for key in list(preferred) + list(DEFAULT_SOURCE_ORDER):
        if key in SOURCE_REGISTRY and key not in seen:
            ordered.append(key)
            seen.add(key)
    return ordered


def _a_share_symbol(symbol: str) -> str:
    clean = _safe_str(symbol)
    return clean[-6:].zfill(6) if clean[-6:].isdigit() else clean


def _baostock_code(symbol: str) -> str:
    clean = _a_share_symbol(symbol)
    if clean.startswith(("6", "9")):
        return f"sh.{clean}"
    return f"sz.{clean}"


def _call_with_timeout(func, timeout: float, label: str):
    result_queue: queue.Queue = queue.Queue(maxsize=1)

    def run():
        try:
            result_queue.put((True, func()))
        except Exception as exc:
            result_queue.put((False, exc))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"{label} timed out after {timeout:g}s")
    ok, value = result_queue.get_nowait()
    if ok:
        return value
    raise value


class EFinanceSource:
    key = "efinance"
    name = "efinance"
    priority = 2
    notes = "东方财富链路，适合拉日线；本机实时股票池可能较慢，因此作为备用源使用。"
    capabilities = ["A股实时股票池", "A股日线K线", "海外股票日线"]

    def installed(self) -> bool:
        return _module_available("efinance")

    def enabled(self) -> bool:
        return True

    def stock_pool(self, limit: int = 5000) -> List[Dict[str, Any]]:
        def fetch() -> pd.DataFrame:
            import efinance as ef

            return ef.stock.get_realtime_quotes()

        df = _call_with_timeout(fetch, 10, "efinance realtime quotes")
        if df is None or df.empty:
            return []
        rows: List[Dict[str, Any]] = []
        for _, row in df.head(limit).iterrows():
            symbol = _safe_str(row.get("股票代码") or row.get("代码"))
            name = _safe_str(row.get("股票名称") or row.get("名称"))
            if symbol and symbol[-6:].isdigit():
                rows.append({"symbol": symbol[-6:].zfill(6), "name": name, "market": "A股", "source": self.key})
        return rows

    def history(self, symbol: str, start_date: Optional[str], end_date: Optional[str]) -> pd.DataFrame:
        def fetch() -> pd.DataFrame:
            import efinance as ef

            return ef.stock.get_quote_history(
                stock_code=_a_share_symbol(symbol),
                beg=_compact_date(start_date) or _compact_date(_iso_date(None)),
                end=_compact_date(end_date) or _compact_date(date.today().strftime("%Y-%m-%d")),
                klt=101,
                fqt=1,
            )

        return _call_with_timeout(fetch, 12, "efinance quote history")


class AKShareSource:
    key = "akshare"
    name = "AKShare"
    priority = 1
    notes = "覆盖面最广，作为主源拉股票池、历史日线和专题数据。"
    capabilities = ["A股股票池", "A股日线K线", "财经专题数据"]

    def installed(self) -> bool:
        return _module_available("akshare")

    def stock_pool(self, limit: int = 5000) -> List[Dict[str, Any]]:
        import akshare as ak

        df = ak.stock_info_a_code_name()
        if df is None or df.empty:
            return []
        rows: List[Dict[str, Any]] = []
        for _, row in df.head(limit).iterrows():
            symbol = _safe_str(row.get("code") or row.get("证券代码") or row.get("股票代码"))
            name = _safe_str(row.get("name") or row.get("证券简称") or row.get("股票简称"))
            if symbol and symbol[-6:].isdigit():
                rows.append({"symbol": symbol[-6:].zfill(6), "name": name, "market": "A股", "source": self.key})
        return rows

    def history(self, symbol: str, start_date: Optional[str], end_date: Optional[str]) -> pd.DataFrame:
        def fetch() -> pd.DataFrame:
            import akshare as ak

            return ak.stock_zh_a_hist(
                symbol=_a_share_symbol(symbol),
                period="daily",
                start_date=_compact_date(start_date) or _compact_date(_iso_date(None)),
                end_date=_compact_date(end_date) or _compact_date(date.today().strftime("%Y-%m-%d")),
                adjust="qfq",
            )

        return _call_with_timeout(fetch, 12, "akshare quote history")


class BaoStockSource:
    key = "baostock"
    name = "BaoStock"
    priority = 3
    notes = "免费证券数据平台，需要 login/logout；作为独立线路兜底。"
    capabilities = ["A股股票池", "A股日线K线"]

    def installed(self) -> bool:
        return _module_available("baostock")

    def _login(self):
        import baostock as bs

        lg = bs.login()
        if getattr(lg, "error_code", "0") != "0":
            raise RuntimeError(getattr(lg, "error_msg", "BaoStock login failed"))
        return bs

    def stock_pool(self, limit: int = 5000) -> List[Dict[str, Any]]:
        bs = self._login()
        try:
            for offset in range(0, 10):
                day = (date.today() - timedelta(days=offset)).strftime("%Y-%m-%d")
                rs = bs.query_all_stock(day=day)
                if getattr(rs, "error_code", "0") != "0":
                    continue
                rows: List[Dict[str, Any]] = []
                fields = list(getattr(rs, "fields", []) or [])
                while rs.next():
                    data = dict(zip(fields, rs.get_row_data()))
                    symbol = _safe_str(data.get("code")).split(".")[-1]
                    name = _safe_str(data.get("code_name"))
                    status = _safe_str(data.get("status"))
                    if symbol and symbol.isdigit() and status != "0":
                        rows.append({"symbol": symbol.zfill(6), "name": name, "market": "A股", "source": self.key})
                    if len(rows) >= limit:
                        return rows
                if rows:
                    return rows[:limit]
            return []
        finally:
            bs.logout()

    def history(self, symbol: str, start_date: Optional[str], end_date: Optional[str]) -> pd.DataFrame:
        bs = self._login()
        try:
            fields = "date,code,open,high,low,close,volume,amount"
            rs = bs.query_history_k_data_plus(
                _baostock_code(symbol),
                fields,
                start_date=_iso_date(start_date),
                end_date=_iso_date(end_date or date.today().strftime("%Y-%m-%d")),
                frequency="d",
                adjustflag="2",
            )
            if getattr(rs, "error_code", "0") != "0":
                return pd.DataFrame()
            rows: List[List[str]] = []
            while rs.next():
                rows.append(rs.get_row_data())
            return pd.DataFrame(rows, columns=list(getattr(rs, "fields", []) or fields.split(",")))
        finally:
            bs.logout()


SOURCE_REGISTRY = {
    "efinance": EFinanceSource(),
    "akshare": AKShareSource(),
    "baostock": BaoStockSource(),
}


def data_source_status() -> Dict[str, Any]:
    sources: List[DataSourceInfo] = []
    for key in DISPLAY_SOURCE_ORDER:
        source = SOURCE_REGISTRY[key]
        installed = source.installed()
        enabled = installed and bool(getattr(source, "enabled", lambda: True)())
        sources.append(
            DataSourceInfo(
                key=source.key,
                name=source.name,
                installed=installed,
                enabled=enabled,
                priority=source.priority,
                capabilities=list(source.capabilities),
                notes=source.notes,
            )
        )
    active = [key for key in DEFAULT_SOURCE_ORDER if SOURCE_REGISTRY[key].installed()]
    return {
        "sources": [asdict(item) for item in sources],
        "active_order": active,
        "primary": active[0] if active else "",
        "fallback_enabled": len(active) > 1,
    }


def fetch_stock_pool(limit: int = 5000, preferred: Optional[Sequence[str]] = None) -> Tuple[List[Dict[str, Any]], str, Dict[str, str]]:
    errors: Dict[str, str] = {}
    for key in _source_sequence(preferred):
        source = SOURCE_REGISTRY[key]
        if not source.installed():
            errors[key] = "not installed"
            continue
        try:
            rows = source.stock_pool(limit)
            if rows:
                return rows[:limit], key, errors
            errors[key] = "empty result"
        except Exception as exc:
            errors[key] = str(exc)[:200]
    return [], "", errors


def iter_history_sources(
    symbol: str,
    start_date: Optional[str],
    end_date: Optional[str],
    preferred: Optional[Sequence[str]] = None,
) -> Iterable[Tuple[str, pd.DataFrame]]:
    for key in _source_sequence(preferred):
        source = SOURCE_REGISTRY[key]
        if not source.installed():
            continue
        yield key, source.history(symbol, start_date, end_date)


def fetch_history(
    symbol: str,
    start_date: Optional[str],
    end_date: Optional[str],
    preferred: Optional[Sequence[str]] = None,
) -> Tuple[pd.DataFrame, str, Dict[str, str]]:
    errors: Dict[str, str] = {}
    for key in _source_sequence(preferred):
        source = SOURCE_REGISTRY[key]
        if not source.installed():
            errors[key] = "not installed"
            continue
        try:
            df = source.history(symbol, start_date, end_date)
            if df is not None and not df.empty:
                return df, key, errors
            errors[key] = "empty result"
        except Exception as exc:
            errors[key] = str(exc)[:200]
    return pd.DataFrame(), "", errors
