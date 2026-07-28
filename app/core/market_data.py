"""行情数据与响应缓存的共享底座（从 lite_main 抽出）。

这一簇是全站最广的共享依赖：自选股、纸面交易、量化中心、后台保温器、洞察页、
个股分析全都要实时报价 / 行业映射 / 响应缓存。原先它们只能在 handler 里
`from app.lite_main import ...` 懒导入来绕开环依赖——每个用到的地方重复一行长
import，且任何模块想复用都得拖上 5700 行的 app 模块。

这里只依赖 `app.lite_auth.store` 与 `app.core.schema`，不 import lite_main，
因此可以在任何层的模块顶部正常 import。

模块级可变状态（进程内缓存与线程池）随函数一起搬来，保证「谁改缓存谁持有缓存」：
- lite_insights_cache：端点响应的内存缓存（_cache_get/_cache_set）
- lite_realtime_quotes_cache / _quotes_loading / _akshare_last_failure：全市场快照与其退避状态
- lite_data_executor：行情 I/O 专用小线程池，见 _run_data_task 的说明

lite_main 会 re-export 这些名字，历史的懒导入路径与测试里的 patch 目标保持有效。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from app.core.schema import ensure_lite_cache_table
from quantcore.quant.data import volume_to_lots
from app.lite_auth import store

lite_insights_cache: dict[str, tuple[datetime, Any]] = {}
lite_realtime_quotes_cache: tuple[datetime, dict[str, dict[str, Any]]] | None = None
_quotes_loading: bool = False
_akshare_last_failure: datetime | None = None  # backoff: skip akshare for 5 min after failure
lite_data_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="lite-data")


def _cache_get(key: str, ttl_seconds: int) -> Any | None:
    cached = lite_insights_cache.get(key)
    if not cached:
        return None
    created_at, value = cached
    if datetime.now(timezone.utc) - created_at > timedelta(seconds=ttl_seconds):
        lite_insights_cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any) -> Any:
    lite_insights_cache[key] = (datetime.now(timezone.utc), value)
    return value


async def _run_data_task(func, *args, timeout: float = 20.0):
    """Run market-data work in a small isolated executor.

    The default asyncio thread pool can be occupied by slow third-party data
    calls.  Keeping these page-facing computations on a bounded executor lets
    the API return a controlled timeout instead of leaving the UI loading.
    """
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(lite_data_executor, lambda: func(*args)),
        timeout=timeout,
    )


def _persistent_cache_get(key: str, ttl_seconds: int) -> Any | None:
    ensure_lite_cache_table()
    with store.connect() as conn:
        row = conn.execute(
            "SELECT payload_json, created_at FROM lite_response_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
    if not row:
        return None
    try:
        created_at = datetime.fromisoformat(row["created_at"])
    except ValueError:
        return None
    if datetime.now(timezone.utc) - created_at > timedelta(seconds=ttl_seconds):
        return None
    try:
        return json.loads(row["payload_json"])
    except json.JSONDecodeError:
        return None


def _persistent_cache_set(key: str, value: Any) -> Any:
    ensure_lite_cache_table()
    created_at = datetime.now(timezone.utc).isoformat()
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO lite_response_cache (cache_key, payload_json, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                payload_json = excluded.payload_json,
                created_at = excluded.created_at
            """,
            (key, json.dumps(value, ensure_ascii=False, default=str), created_at),
        )
        conn.commit()
    return value


def _persistent_cache_delete_prefix(prefix: str) -> None:
    ensure_lite_cache_table()
    with store.connect() as conn:
        conn.execute("DELETE FROM lite_response_cache WHERE cache_key LIKE ?", (f"{prefix}%",))
        conn.commit()


def _now_cn() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe_number(value: Any) -> float | None:
    try:
        if value in ("", "-", None):
            return None
        number = float(value)
        if number != number:
            return None
        return number
    except (TypeError, ValueError):
        return None


def _market_quote_code(symbol: str) -> str:
    clean_symbol = str(symbol).strip().zfill(6)
    if clean_symbol.startswith(("6", "9")):
        return f"sh{clean_symbol}"
    if clean_symbol.startswith(("8", "4")):
        return f"bj{clean_symbol}"
    return f"sz{clean_symbol}"


def _parse_tencent_quote_time(value: str) -> str:
    if re.fullmatch(r"\d{14}", value or ""):
        return f"{value[0:4]}/{value[4:6]}/{value[6:8]} {value[8:10]}:{value[10:12]}:{value[12:14]}"
    return datetime.now().astimezone().strftime("%Y/%m/%d %H:%M:%S")


def _fetch_tencent_realtime_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    clean_symbols = [symbol for symbol in dict.fromkeys(symbols) if re.fullmatch(r"\d{6}", symbol)]
    if not clean_symbols:
        return {}

    snapshot: dict[str, dict[str, Any]] = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    for start in range(0, len(clean_symbols), 200):
        chunk = clean_symbols[start:start + 200]
        query = ",".join(_market_quote_code(symbol) for symbol in chunk)
        session = requests.Session()
        session.trust_env = False
        response = session.get(f"https://qt.gtimg.cn/q={query}", headers=headers, timeout=8)
        response.encoding = "gbk"
        response.raise_for_status()
        for match in re.finditer(r'v_(?:sh|sz|bj)(\d{6})="([^"]*)"', response.text):
            symbol = match.group(1)
            fields = match.group(2).split("~")
            if len(fields) < 35:
                continue
            price = _safe_number(fields[3])
            pct = _safe_number(fields[32])
            prev_close = _safe_number(fields[4])
            volume_hands = _safe_number(fields[36]) if len(fields) > 36 else None
            amount_10k = _safe_number(fields[37]) if len(fields) > 37 else None
            snapshot[symbol] = {
                "symbol": symbol,
                "code": symbol,
                "name": fields[1].strip() or symbol,
                "price": price,
                "close": price,
                "current_price": price,
                "change_percent": pct,
                "pct_chg": pct,
                "change": _safe_number(fields[31]),
                "open": _safe_number(fields[5]),
                "high": _safe_number(fields[33]),
                "low": _safe_number(fields[34]),
                "prev_close": prev_close,
                # volume 对外统一给「股」；源对科创板按股给量，先折成手再×100，
                # 否则 688 的成交量会翻 100 倍（同 daily_kline 的历史 bug，见 volume_to_lots）
                "volume": volume_to_lots(symbol, volume_hands) * 100 if volume_hands is not None else None,
                "amount": amount_10k * 10000 if amount_10k is not None else None,
                "turnover_rate": _safe_number(fields[38]) if len(fields) > 38 else None,
                "amplitude": _safe_number(fields[43]) if len(fields) > 43 else None,
                "pe": _safe_number(fields[39]) if len(fields) > 39 else None,
                "total_mv": _safe_number(fields[44]) if len(fields) > 44 else None,
                "circ_mv": _safe_number(fields[45]) if len(fields) > 45 else None,
                "updated_at": _parse_tencent_quote_time(fields[30]),
                "quote_source": "tencent.realtime",
            }
    return snapshot


def _fetch_tencent_realtime_snapshot_from_local() -> dict[str, dict[str, Any]]:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from quantcore.quant.local_store import get_local_store

    symbols = [
        str(item.get("symbol") or "").strip().zfill(6)
        for item in get_local_store().load_meta()
        if re.fullmatch(r"\d{6}", str(item.get("symbol") or "").strip().zfill(6))
    ]
    if not symbols:
        return {}
    snapshot: dict[str, dict[str, Any]] = {}
    chunks = [symbols[start:start + 200] for start in range(0, len(symbols), 200)]
    with ThreadPoolExecutor(max_workers=min(28, len(chunks))) as executor:
        futures = [executor.submit(_fetch_tencent_realtime_quotes, chunk) for chunk in chunks]
        for future in as_completed(futures):
            try:
                snapshot.update(future.result())
            except Exception:
                continue
    if len(snapshot) < 500:
        raise RuntimeError(f"tencent realtime snapshot too small: {len(snapshot)}")
    return snapshot


def _fetch_eastmoney_realtime_snapshot() -> dict[str, dict[str, Any]]:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from math import ceil

    page_size = 100
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
    request_routes = (
        ("82.push2.eastmoney.com", False),
        ("88.push2.eastmoney.com", False),
        ("push2.eastmoney.com", True),
        ("70.push2.eastmoney.com", True),
    )
    base_params = {
        "po": 1,
        "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f12,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18,f20,f21,f8,f10",
    }

    def fetch_page(page: int) -> tuple[int, int, list[dict[str, Any]]]:
        params = {**base_params, "pn": page, "pz": page_size}
        last_error: Exception | None = None
        for host, trust_env in request_routes:
            try:
                session = requests.Session()
                session.trust_env = trust_env
                response = session.get(
                    f"https://{host}/api/qt/clist/get",
                    params=params,
                    headers=headers,
                    timeout=3,
                )
                response.encoding = "utf-8"
                response.raise_for_status()
                payload = response.json().get("data") or {}
                return int(payload.get("total") or 0), page, list(payload.get("diff") or [])
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        return 0, page, []

    total, _, first_rows = fetch_page(1)
    pages = max(1, min(80, ceil(total / page_size))) if total else 1
    all_rows = list(first_rows)
    if pages > 1:
        with ThreadPoolExecutor(max_workers=28) as executor:
            futures = [executor.submit(fetch_page, page) for page in range(2, pages + 1)]
            for future in as_completed(futures):
                try:
                    _, _, rows = future.result()
                    all_rows.extend(rows)
                except Exception:
                    continue

    updated_at = datetime.now().astimezone().strftime("%Y/%m/%d %H:%M:%S")
    snapshot: dict[str, dict[str, Any]] = {}
    for row in all_rows:
        symbol = str(row.get("f12") or "").strip().zfill(6)
        if not re.fullmatch(r"\d{6}", symbol):
            continue
        price = _safe_number(row.get("f2"))
        pct = _safe_number(row.get("f3"))
        prev_close = _safe_number(row.get("f18"))
        if price is None or price <= 0:
            continue
        volume_hands = _safe_number(row.get("f5"))
        amount = _safe_number(row.get("f6"))
        snapshot[symbol] = {
            "symbol": symbol,
            "code": symbol,
            "name": str(row.get("f14") or "").strip() or symbol,
            "price": price,
            "close": price,
            "current_price": price,
            "change_percent": pct,
            "pct_chg": pct,
            "change": _safe_number(row.get("f4")),
            "open": _safe_number(row.get("f17")),
            "high": _safe_number(row.get("f15")),
            "low": _safe_number(row.get("f16")),
            "prev_close": prev_close,
            # 同上：折成手再×100，避免科创板成交量翻 100 倍
            "volume": volume_to_lots(symbol, volume_hands) * 100 if volume_hands is not None else None,
            "amount": amount,
            "turnover_rate": _safe_number(row.get("f8")),
            "volume_ratio": _safe_number(row.get("f10")),
            "total_mv": _safe_number(row.get("f20")),
            "circ_mv": _safe_number(row.get("f21")),
            "updated_at": updated_at,
            "quote_source": "eastmoney.realtime",
        }
    if len(snapshot) < 500:
        raise RuntimeError(f"eastmoney realtime snapshot too small: {len(snapshot)}")
    return snapshot


_industry_map_cache: tuple[datetime, dict[str, str]] | None = None


def _fetch_industry_map() -> dict[str, str]:
    """全市场 代码->行业 映射（东财 clist f100 字段）。复用快照同款 host 级联与并发分页。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from math import ceil

    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
    request_routes = (
        ("82.push2.eastmoney.com", False),
        ("88.push2.eastmoney.com", False),
        ("push2.eastmoney.com", True),
        ("70.push2.eastmoney.com", True),
    )
    base_params = {
        "po": 1, "np": 1, "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2, "invt": 2, "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23", "fields": "f12,f100",
    }

    def fetch_page(page: int) -> tuple[int, list[dict[str, Any]]]:
        params = {**base_params, "pn": page, "pz": 100}
        for host, trust_env in request_routes:
            try:
                session = requests.Session()
                session.trust_env = trust_env
                resp = session.get(f"https://{host}/api/qt/clist/get", params=params, headers=headers, timeout=3)
                resp.encoding = "utf-8"
                resp.raise_for_status()
                payload = resp.json().get("data") or {}
                return int(payload.get("total") or 0), list(payload.get("diff") or [])
            except Exception:
                continue
        return 0, []

    total, first_rows = fetch_page(1)
    pages = max(1, min(80, ceil(total / 100))) if total else 1
    rows = list(first_rows)
    if pages > 1:
        with ThreadPoolExecutor(max_workers=28) as executor:
            futures = [executor.submit(fetch_page, page) for page in range(2, pages + 1)]
            for future in as_completed(futures):
                try:
                    _, page_rows = future.result()
                    rows.extend(page_rows)
                except Exception:
                    continue
    mapping: dict[str, str] = {}
    for row in rows:
        code = str(row.get("f12") or "").strip().zfill(6)
        industry = str(row.get("f100") or "").strip()
        if re.fullmatch(r"\d{6}", code) and industry and industry != "-":
            mapping[code] = industry
    return mapping


_INDUSTRY_MAP_PATH = "runtime/industry_map.json"
_industry_fetch_last_failure: datetime | None = None


def _load_industry_map(ttl_hours: int = 24) -> dict[str, str]:
    """代码->行业 映射。优先内存/磁盘缓存（离线、即时、不受东财限流影响）；过期且未近期失败
    时才后台从东财拉一次并落盘。东财行业极少变，落盘缓存 24h 足够。"""
    global _industry_map_cache, _industry_fetch_last_failure
    now = datetime.now()
    if _industry_map_cache and (now - _industry_map_cache[0]).total_seconds() < ttl_hours * 3600:
        return _industry_map_cache[1]
    # 内存空：尝试从磁盘加载
    if _industry_map_cache is None and os.path.exists(_INDUSTRY_MAP_PATH):
        try:
            with open(_INDUSTRY_MAP_PATH, "r", encoding="utf-8") as fh:
                disk = json.load(fh)
            mtime = datetime.fromtimestamp(os.path.getmtime(_INDUSTRY_MAP_PATH))
            if isinstance(disk, dict) and disk:
                _industry_map_cache = (mtime, {str(k).zfill(6): str(v) for k, v in disk.items()})
                if (now - mtime).total_seconds() < ttl_hours * 3600:
                    return _industry_map_cache[1]
        except Exception:
            pass
    # 缓存过期/缺失：限流退避（失败后 10 分钟内不再打东财），其余时间尝试刷新
    if _industry_fetch_last_failure and (now - _industry_fetch_last_failure) < timedelta(minutes=10):
        return _industry_map_cache[1] if _industry_map_cache else {}
    try:
        mapping = _fetch_industry_map()
    except Exception:
        mapping = {}
    if mapping:
        # 累积合并而非整体替换：东财分页并发常被限流，单次只回来一部分。直接覆盖会让
        # 覆盖度在「多→少」间抖动（热力图行业块忽有忽无）；合并保证覆盖度只增不减。
        merged = dict(_industry_map_cache[1]) if _industry_map_cache else {}
        merged.update(mapping)
        _industry_map_cache = (now, merged)
        _industry_fetch_last_failure = None
        try:
            os.makedirs(os.path.dirname(_INDUSTRY_MAP_PATH), exist_ok=True)
            with open(_INDUSTRY_MAP_PATH, "w", encoding="utf-8") as fh:
                json.dump(merged, fh, ensure_ascii=False)
        except Exception:
            pass
    else:
        _industry_fetch_last_failure = now
    return _industry_map_cache[1] if _industry_map_cache else {}


_hot_industries_cache: tuple[datetime, tuple, dict[str, float]] | None = None


def _compute_hot_industries(
    industry_map: dict[str, str],
    *, window: int = 5, top_k: int = 10, min_members: int = 4, ttl_minutes: int = 30,
) -> dict[str, float]:
    """近段趋势热门板块：按行业内成分股最近 window 个交易日的平均涨幅排序，取居前且为正的 top_k 个。

    动态识别"最近什么板块在走强"，不写死赛道——白酒/银行/食品等只要近期趋势起来，同样会入选。
    返回 {行业名: 平均近 window 日涨幅%}。无数据时返回空，调用方据此退回静态兜底白名单。
    缓存随参数(window/top_k/min_members)变化，调参后立即重算。
    """
    global _hot_industries_cache
    now = datetime.now()
    params = (window, top_k, min_members)
    if (_hot_industries_cache and _hot_industries_cache[1] == params
            and (now - _hot_industries_cache[0]).total_seconds() < ttl_minutes * 60):
        return _hot_industries_cache[2]
    fallback = _hot_industries_cache[2] if _hot_industries_cache else {}
    if not industry_map:
        return fallback
    try:
        from quantcore.quant.local_store import get_local_store
        returns = get_local_store().recent_returns(window=window)
    except Exception:
        return fallback
    by_industry: dict[str, list[float]] = {}
    for symbol, ret in returns.items():
        industry = industry_map.get(symbol)
        if industry:
            by_industry.setdefault(industry, []).append(ret)
    scored = {
        industry: round(sum(rets) / len(rets), 2)
        for industry, rets in by_industry.items()
        if len(rets) >= min_members
    }
    hot = {
        industry: score
        for industry, score in sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        if score > 0
    }
    if hot:
        _hot_industries_cache = (now, params, hot)
    return _hot_industries_cache[2] if _hot_industries_cache else {}


def _load_realtime_quotes_snapshot(ttl_seconds: int = 3) -> dict[str, dict[str, Any]]:
    global lite_realtime_quotes_cache, _quotes_loading
    now = datetime.now(timezone.utc)
    if lite_realtime_quotes_cache:
        created_at, snapshot = lite_realtime_quotes_cache
        if now - created_at <= timedelta(seconds=ttl_seconds):
            return snapshot
    if _quotes_loading:
        return lite_realtime_quotes_cache[1] if lite_realtime_quotes_cache else {}
    _quotes_loading = True
    try:
        # 快源腾讯/东财每次都试（~2s，稳）；只有慢源 akshare 有 5 分钟冷却，见 _do_load。
        # 之前顶层冷却会把整份快照连累置空 5 分钟——竞价窗口一次瞬时全失败就 blank 整段。
        return _do_load_realtime_quotes_snapshot()
    except Exception:
        return lite_realtime_quotes_cache[1] if lite_realtime_quotes_cache else {}
    finally:
        _quotes_loading = False


def _do_load_realtime_quotes_snapshot() -> dict[str, dict[str, Any]]:
    global lite_realtime_quotes_cache, _akshare_last_failure
    now = datetime.now(timezone.utc)

    try:
        snapshot = _fetch_tencent_realtime_snapshot_from_local()
        lite_realtime_quotes_cache = (now, snapshot)
        return snapshot
    except Exception:
        pass

    try:
        snapshot = _fetch_eastmoney_realtime_snapshot()
        lite_realtime_quotes_cache = (now, snapshot)
        return snapshot
    except Exception:
        pass

    # 慢源 akshare（4s 超时）会阻塞线程池：最近失败过就 5 分钟内跳过慢源，避免反复干等。
    # 冷却只关这一个慢源——上面的快源腾讯/东财每次都无条件试过了。
    if _akshare_last_failure and (datetime.now(timezone.utc) - _akshare_last_failure) < timedelta(minutes=5):
        raise RuntimeError("akshare 冷却中（近 5 分钟失败过），跳过慢源")

    import socket as _socket
    import akshare as ak

    _old_timeout = _socket.getdefaulttimeout()
    _socket.setdefaulttimeout(4)
    try:
        source_name = "akshare.stock_zh_a_spot"
        try:
            df = ak.stock_zh_a_spot()
        except Exception:
            source_name = "akshare.stock_zh_a_spot_em"
            df = ak.stock_zh_a_spot_em()
    except Exception:
        _akshare_last_failure = datetime.now(timezone.utc)
        raise
    finally:
        _socket.setdefaulttimeout(_old_timeout)
    _akshare_last_failure = None

    snapshot: dict[str, dict[str, Any]] = {}
    updated_at = datetime.now().astimezone().strftime("%Y/%m/%d %H:%M:%S")
    for _, row in df.iterrows():
        raw_symbol = str(row.get("代码", "")).strip()
        symbol_match = re.search(r"(\d{6})$", raw_symbol)
        symbol = symbol_match.group(1) if symbol_match else raw_symbol.zfill(6)
        if not re.fullmatch(r"\d{6}", symbol):
            continue
        row_time = str(row.get("时间戳", "")).strip()
        quote_updated_at = f"{datetime.now().astimezone().strftime('%Y/%m/%d')} {row_time}" if row_time else updated_at
        price = _safe_number(row.get("最新价"))
        pct = _safe_number(row.get("涨跌幅"))
        prev_close = _safe_number(row.get("昨收"))
        snapshot[symbol] = {
            "symbol": symbol,
            "code": symbol,
            "name": str(row.get("名称", "")).strip() or symbol,
            "price": price,
            "close": price,
            "current_price": price,
            "change_percent": pct,
            "pct_chg": pct,
            "change": _safe_number(row.get("涨跌额")),
            "open": _safe_number(row.get("今开")),
            "high": _safe_number(row.get("最高")),
            "low": _safe_number(row.get("最低")),
            "prev_close": prev_close,
            "volume": _safe_number(row.get("成交量")),
            "amount": _safe_number(row.get("成交额")),
            "turnover_rate": _safe_number(row.get("换手率")),
            "amplitude": _safe_number(row.get("振幅")),
            "volume_ratio": _safe_number(row.get("量比")),
            "pe": _safe_number(row.get("市盈率-动态")),
            "pb": _safe_number(row.get("市净率")),
            "total_mv": _safe_number(row.get("总市值")),
            "circ_mv": _safe_number(row.get("流通市值")),
            "updated_at": quote_updated_at,
            "quote_source": source_name,
        }
    lite_realtime_quotes_cache = (now, snapshot)
    return snapshot


async def _realtime_quotes(
    symbols: list[str] | set[str] | tuple[str, ...],
    allow_snapshot_fallback: bool = True,
) -> dict[str, dict[str, Any]]:
    clean_symbols = [str(symbol).strip().zfill(6) for symbol in symbols if re.fullmatch(r"\d{6}", str(symbol).strip().zfill(6))]
    if not clean_symbols:
        return {}
    quotes: dict[str, dict[str, Any]] = {}
    try:
        timeout = 5.0 if len(clean_symbols) <= 80 else 12.0
        quotes = await _run_data_task(_fetch_tencent_realtime_quotes, clean_symbols, timeout=timeout)
    except Exception:
        quotes = {}
    missing_symbols = [symbol for symbol in clean_symbols if symbol not in quotes]
    if missing_symbols and allow_snapshot_fallback:
        try:
            snapshot = await _run_data_task(_load_realtime_quotes_snapshot, timeout=8.0)
            quotes.update({symbol: snapshot[symbol] for symbol in missing_symbols if symbol in snapshot})
        except Exception:
            pass
    return quotes


def _apply_realtime_quote(item: dict[str, Any], quote: dict[str, Any] | None) -> dict[str, Any]:
    if not quote:
        return item
    price = quote.get("price") if quote.get("price") is not None else quote.get("close")
    pct = quote.get("change_percent") if quote.get("change_percent") is not None else quote.get("pct_chg")
    if price is not None:
        item["close"] = price
        item["price"] = price
        item["current_price"] = price
    if pct is not None:
        item["pct_chg"] = pct
        item["change_percent"] = pct
    for key in ("name", "volume", "amount", "turnover_rate", "amplitude", "volume_ratio", "open", "high", "low", "prev_close", "updated_at", "quote_source"):
        if quote.get(key) is not None:
            item[key] = quote[key]
    if quote.get("name"):
        item["stock_name"] = quote["name"]
    return item


async def _is_trading_day_now() -> bool:
    """用实时快照的行情时间判断今天是否交易日：节假日快照时间停留在上一交易日，
    此时 cron 不应把旧行情落成当日盘报。仅在明确看到 stale 时间戳时返回 False；
    快照拿不到/无时间戳则 fail-open 视为交易日，宁多生成不漏。"""
    try:
        snapshot = await _run_data_task(_load_realtime_quotes_snapshot, 300, timeout=8.0)
    except Exception:
        return True
    if not snapshot:
        return True
    quote_dates: set[str] = set()
    for q in list(snapshot.values())[:200]:
        m = re.match(r"(\d{4})/(\d{2})/(\d{2})", str(q.get("updated_at") or ""))
        if m:
            quote_dates.add(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    if not quote_dates:
        return True
    return datetime.now().strftime("%Y-%m-%d") in quote_dates
