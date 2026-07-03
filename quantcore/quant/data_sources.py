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
        def fetch() -> pd.DataFrame:
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

        # baostock 走原生 socket 且无自带超时，挂死会冻结整个同步线程池（增量同步卡死、
        # 当晚自动同步无法启动）；与 akshare/efinance 一致加超时兜底。
        return _call_with_timeout(fetch, 15, "baostock quote history")


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


def data_source_health(local_health: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return a product-facing health snapshot for the data center page.

    This is intentionally lightweight: it checks installed/enabled source state
    and combines it with the local SQLite K-line coverage instead of probing
    every remote endpoint on page load.
    """
    status = data_source_status()
    sources = status.get("sources", [])
    active = [item for item in sources if item.get("enabled")]
    local_health = local_health or {}

    ready = bool(local_health.get("ready"))
    today_complete = bool(local_health.get("today_complete"))
    local_status = str(local_health.get("status") or "")
    if local_status == "intraday":
        grade = "fresh"
        message = "盘中页面使用实时行情 + 最近完整日线；无需等待今日日 K 同步完成。"
    elif ready and today_complete:
        grade = "fresh"
        message = "本地行情已覆盖最近交易日，选股和热点可以优先使用本地高速数据。"
    elif ready:
        grade = "usable"
        message = "本地数据可用，但最新交易日覆盖不完整；建议执行增量同步。"
    elif active:
        grade = "source_ready"
        message = "远程数据源可用，但本地数据池不足；建议执行全量同步。"
    else:
        grade = "blocked"
        message = "没有可用数据源；请安装或修复 AKShare、efinance、BaoStock。"

    policy = [
        {"step": 1, "name": "本地 SQLite K线", "role": "页面查询、智能选股、形态扫描优先走本地缓存，避免每次刷新打远程源。"},
        {"step": 2, "name": "腾讯日线同步", "role": "批量同步优先使用稳定轻量的行情接口，按全市场遍历补齐。"},
        {"step": 3, "name": "AKShare", "role": "股票池、专题数据、公告研报等宽覆盖数据。"},
        {"step": 4, "name": "efinance / BaoStock", "role": "当主源不可用时兜底；BaoStock 作为独立线路验证。"},
    ]

    return {
        **status,
        "grade": grade,
        "message": message,
        "local": local_health,
        "active_count": len(active),
        "policy": policy,
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
