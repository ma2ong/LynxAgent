"""ML 因子链对外服务：带缓存 + 后台计算的一键运行。

全市场（5000+ 只）建面板 + 滚动训练耗时数分钟，无法塞进同步 HTTP 请求。
因此采用「后台线程计算 + 缓存 + 轮询」：
- 命中新鲜缓存 → 直接返回 status=ready。
- 未命中 → 启动后台线程计算，立即返回 status=computing，前端轮询直到 ready。
universe_limit<=0 表示全市场。结果缓存 6 小时。
"""
from __future__ import annotations

import time
import traceback
from threading import Lock, Thread
from typing import Dict, Optional

from ..local_store import get_local_store
from .dataset import DATE_COL, SYMBOL_COL, build_panel
from .pipeline import run_once, run_rolling

_CACHE: Dict[str, tuple] = {}
_INFLIGHT: Dict[str, dict] = {}
_LOCK = Lock()
_TTL = 6 * 3600  # 6 小时


def _key(**kw) -> str:
    return "|".join(f"{k}={kw[k]}" for k in sorted(kw))


def _params_key(universe_limit, horizon, k, mode, neutralize, retrain_every) -> str:
    return _key(u=universe_limit, h=horizon, k=k, m=mode, n=neutralize, r=retrain_every)


def _compute(universe_limit, horizon, k, mode, neutralize, retrain_every, min_rows) -> Dict[str, object]:
    all_syms = get_local_store().list_kline_symbols(min_rows=min_rows)
    symbols = all_syms if universe_limit and universe_limit > 0 else all_syms
    if universe_limit and universe_limit > 0:
        symbols = all_syms[:universe_limit]

    panel = build_panel(symbols=symbols, horizon=horizon, min_rows=min_rows)
    if panel.empty:
        return {"error": "no data"}

    # 过滤后的真实可投资域大小（最新交易日纳入的标的数）
    inv_universe = int(panel[panel[DATE_COL] == panel[DATE_COL].max()][SYMBOL_COL].nunique())

    if mode == "once":
        r = run_once(panel=panel, horizon=horizon, k=k, neutralize=neutralize)
    else:
        r = run_rolling(panel=panel, horizon=horizon, k=k,
                        retrain_every=retrain_every, neutralize=neutralize)

    bt = r.pop("_bt", None)
    return {
        "mode": r.get("mode"),
        "universe": inv_universe,
        "horizon": horizon,
        "k": k,
        "neutralized": r.get("neutralized"),
        "n_models": r.get("n_models"),
        "ic": r.get("test_ic"),
        "metrics": {
            "topk": r.get("backtest"),
            "benchmark": r.get("benchmark"),
            "long_short": r.get("long_short"),
        },
        "pick_date": r.get("pick_date"),
        "picks": r.get("picks", []),
        "top_features": r.get("top_features", {}),
        "curves": {
            "dates": bt.dates if bt else [],
            "topk": bt.equity_curve if bt else [],
            "benchmark": bt.benchmark_curve if bt else [],
            "long_short": bt.long_short_curve if bt else [],
        },
        "generated_at": int(time.time()),
    }


def run_ml_factor(
    universe_limit: int = 500,
    horizon: int = 5,
    k: int = 50,
    mode: str = "rolling",
    neutralize: bool = True,
    retrain_every: int = 20,
    min_rows: int = 250,
    force: bool = False,
) -> Dict[str, object]:
    """同步运行（阻塞至完成）。供 CLI / 脚本 / 预热使用。"""
    key = _params_key(universe_limit, horizon, k, mode, neutralize, retrain_every)
    now = time.time()
    if not force:
        with _LOCK:
            hit = _CACHE.get(key)
            if hit and now - hit[0] < _TTL:
                return {**hit[1], "cached": True, "age_sec": int(now - hit[0])}

    payload = _compute(universe_limit, horizon, k, mode, neutralize, retrain_every, min_rows)
    if "error" not in payload:
        with _LOCK:
            _CACHE[key] = (now, payload)
    return {**payload, "cached": False}


def _worker(key, args):
    try:
        payload = _compute(*args)
        if "error" not in payload:
            with _LOCK:
                _CACHE[key] = (time.time(), payload)
    except Exception:
        with _LOCK:
            _INFLIGHT[key] = {**_INFLIGHT.get(key, {}), "error": traceback.format_exc().splitlines()[-1]}
    finally:
        with _LOCK:
            info = _INFLIGHT.get(key, {})
            if "error" not in info:
                _INFLIGHT.pop(key, None)


def request_ml_factor(
    universe_limit: int = 500,
    horizon: int = 5,
    k: int = 50,
    mode: str = "rolling",
    neutralize: bool = True,
    retrain_every: int = 20,
    min_rows: int = 250,
    force: bool = False,
) -> Dict[str, object]:
    """非阻塞：命中缓存即返回 ready，否则后台计算并返回 computing，供前端轮询。"""
    key = _params_key(universe_limit, horizon, k, mode, neutralize, retrain_every)
    now = time.time()
    args = (universe_limit, horizon, k, mode, neutralize, retrain_every, min_rows)

    with _LOCK:
        hit = _CACHE.get(key)
        if hit and not force and now - hit[0] < _TTL:
            return {"status": "ready", **hit[1], "cached": True, "age_sec": int(now - hit[0])}

        inflight = _INFLIGHT.get(key)
        if inflight and not force:
            if inflight.get("error"):
                err = inflight.pop("error")
                _INFLIGHT.pop(key, None)
                return {"status": "error", "error": err}
            return {"status": "computing", "elapsed_sec": int(now - inflight["started"])}

        # 启动后台计算
        if force:
            _CACHE.pop(key, None)
        _INFLIGHT[key] = {"started": now}

    Thread(target=_worker, args=(key, args), daemon=True).start()
    return {"status": "computing", "elapsed_sec": 0}
