"""选股规则的 point-in-time 历史回放验证。

用本地日线把 pattern / smart 两池规则回放到过去（默认 12 个月、每 5 个交易日一期），
每期取 top-N，统计 T+5 相对全市场中位的超额收益 → 回答「这套选股规则有没有效」。

口径说明（近似之处，结果解读时必须记住）：
- pattern 池：形态识别/因子/威科夫全部用截断到 as_of 的日线，严格 point-in-time；
  实时分量（realtime_score）用当日 bar 的涨跌幅/成交额，与收盘后扫描等价。
- smart 池：生产版输入是实时行情（量比/换手率），回放用日线近似重建
  （volume_ratio=当日量/前5日均量，turnover 缺失取 0），critic 融合层跳过。
- 排除规则用「当前」股票名称（ST/退市标记随时间变化，历史时点无法还原）。
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from .local_store import DEFAULT_DB_PATH, MIN_MARKET_COVERAGE, LocalQuantStore, get_local_store

PATTERN_MIN_STRENGTH = 70.0
PATTERN_MIN_AMOUNT = 3e7  # 形态分析只跑当日成交额 ≥3000 万的票，贴近实际候选并控算力
MIN_BARS = 80
HORIZON = 5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS replay_runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT,
    params_json TEXT,
    status TEXT,
    progress REAL,
    summary_json TEXT
);
CREATE TABLE IF NOT EXISTS replay_results (
    run_id TEXT,
    pool TEXT,
    as_of TEXT,
    symbol TEXT,
    name TEXT,
    rank INTEGER,
    score REAL,
    ret_t5 REAL,
    excess_t5 REAL,
    PRIMARY KEY (run_id, pool, as_of, symbol)
);
"""

_run_lock = threading.Lock()
_progress: Dict[str, object] = {"running": False, "run_id": "", "phase": "", "done": 0, "total": 0}


def _ensure_tables(store: LocalQuantStore) -> None:
    conn = store._conn()
    conn.executescript(_SCHEMA)
    conn.commit()


def _smart_score_approx(df: pd.DataFrame) -> Optional[float]:
    """engine.smart_pool v2 评分的日线近似（权重与公式对齐 engine.py:_score）。"""
    from .engine import _anti_chase_score, _stability_from_kline

    if len(df) < 6:
        return None
    closes = df["close"].astype(float)
    close = float(closes.iloc[-1])
    prev = float(closes.iloc[-2])
    if close <= 0 or prev <= 0:
        return None
    pct_chg = (close / prev - 1) * 100
    amount = float(df["amount"].iloc[-1] or 0)
    vols = df["volume"].astype(float)
    prev5 = float(vols.iloc[-6:-1].mean() or 0)
    volume_ratio = float(vols.iloc[-1]) / prev5 if prev5 > 0 else 0.0
    ma20 = float(closes.tail(20).mean()) if len(closes) >= 20 else 0.0
    ma20_adj = (6.0 if close > ma20 else -6.0) if ma20 else 0.0
    trend = max(0.0, min(100.0, 55 + pct_chg * 5.2 + min(amount / 1e8, 8) * 2.6 + ma20_adj))
    momentum = max(0.0, min(100.0, 50 + pct_chg * 6.0 + min(volume_ratio, 3) * 9.0))
    liquidity = max(0.0, min(100.0, 45 + min(amount / 1e8, 12) * 4.0))
    rsi_s = max(0.0, min(100.0, 50 + pct_chg * 3.0))
    risk_s = max(0.0, min(100.0, 78 - abs(pct_chg) * 2.8))  # turnover 历史不可得，取 0
    anti = _anti_chase_score(_stability_from_kline(df))
    return round(trend * 0.26 + momentum * 0.22 + liquidity * 0.16
                 + rsi_s * 0.10 + risk_s * 0.10 + anti * 0.16, 1)


def _pattern_score(symbol: str, df: pd.DataFrame) -> Optional[float]:
    """engine._pattern_scan_one 的 point-in-time 版（实时分量用当日 bar）。"""
    from .factors import composite_score, compute_factor_scores, latest_adx
    from .integrations import recognize_patterns
    from .wyckoff import analyze_wyckoff

    if len(df) < MIN_BARS:
        return None
    amount = float(df["amount"].iloc[-1] or 0)
    if amount < PATTERN_MIN_AMOUNT:
        return None
    recognition = recognize_patterns(symbol, df)
    matched = [p for p in recognition.patterns
               if p.get("active") and float(p.get("strength") or 0) >= PATTERN_MIN_STRENGTH]
    if not matched:
        return None
    closes = df["close"].astype(float)
    pct_chg = (float(closes.iloc[-1]) / float(closes.iloc[-2]) - 1) * 100 if float(closes.iloc[-2]) > 0 else 0.0
    factors = compute_factor_scores(df)
    quant_score = composite_score(factors)
    pattern_score = max(float(p.get("strength") or 0) for p in matched)
    realtime_score = max(0.0, min(100.0, 55 + pct_chg * 3.8 + min(amount / 1e8, 10) * 2.0))
    adx_val = latest_adx(df)
    adx_adj = 4.0 if adx_val >= 25 else (-4.0 if 0 < adx_val < 20 else 0.0)
    wyckoff = analyze_wyckoff(df)
    wyckoff_adj = (float(wyckoff.get("score") or 50.0) - 50.0) * 0.08
    return round(pattern_score * 0.52 + quant_score * 0.30 + realtime_score * 0.18 + adx_adj + wyckoff_adj, 1)


def _replay_symbol(payload: Dict[str, object]) -> List[Dict[str, object]]:
    """进程池 worker：单只股票在全部回放期的两池候选评分。顶层函数以便 pickle。"""
    symbol = str(payload["symbol"])
    name = str(payload.get("name") or symbol)
    sessions: List[str] = list(payload["sessions"])
    db_path = str(payload["db_path"])
    try:
        from .screening import exclusion_reason

        store = LocalQuantStore(db_path)
        rows = store._conn().execute(
            "SELECT date, open, high, low, close, volume, amount FROM daily_kline "
            "WHERE symbol=? AND amount>0 ORDER BY date", (symbol,),
        ).fetchall()
        if len(rows) < MIN_BARS:
            return []
        df_all = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
        dates = df_all["date"].tolist()
        out: List[Dict[str, object]] = []
        import bisect
        for as_of in sessions:
            i = bisect.bisect_right(dates, as_of) - 1
            if i < MIN_BARS - 1 or dates[i] != as_of:
                continue  # 当日无真实 bar（停牌/缺口）不参与该期
            df = df_all.iloc[: i + 1]
            close = float(df["close"].iloc[-1])
            amount = float(df["amount"].iloc[-1] or 0)
            if exclusion_reason(name, close, amount):
                continue
            s_score = _smart_score_approx(df.tail(30))
            if s_score is not None:
                out.append({"pool": "smart", "as_of": as_of, "symbol": symbol, "name": name, "score": s_score})
            p_score = _pattern_score(symbol, df.tail(540))
            if p_score is not None:
                out.append({"pool": "pattern", "as_of": as_of, "symbol": symbol, "name": name, "score": p_score})
        return out
    except Exception:
        return []


def _complete_trading_dates(store: LocalQuantStore, since: str) -> List[str]:
    """按截面覆盖率取「完整交易日」轴（排除整天缺失日），供采样与 T+5 对齐。"""
    rows = store._conn().execute(
        "SELECT date, COUNT(DISTINCT symbol) FROM daily_kline "
        "WHERE date >= ? AND amount > 0 GROUP BY date ORDER BY date", (since,),
    ).fetchall()
    if not rows:
        return []
    peak = max(int(r[1]) for r in rows)
    return [str(r[0]) for r in rows if int(r[1]) >= peak * MIN_MARKET_COVERAGE]


def run_replay(months: int = 12, step: int = 5, top_n: int = 20,
               store: Optional[LocalQuantStore] = None, workers: int = 6) -> Dict[str, object]:
    """执行一次回放（同步，耗时数分钟）。workers<=0 时串行（测试用）。"""
    store = store or get_local_store()
    _ensure_tables(store)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    since = (date.today() - timedelta(days=int(months * 30.5) + 40)).strftime("%Y-%m-%d")
    tdates = _complete_trading_dates(store, since)
    # 采样期：每 step 个交易日一期；末尾留足 T+5 前向窗口
    sessions = [d for idx, d in enumerate(tdates[:-HORIZON]) if idx % max(1, step) == 0]
    if not sessions:
        raise ValueError("本地日线不足，无法回放")

    conn = store._conn()
    conn.execute(
        "INSERT OR REPLACE INTO replay_runs(run_id, created_at, params_json, status, progress) "
        "VALUES(?,?,?,?,0)",
        (run_id, datetime.now().isoformat(timespec="seconds"),
         json.dumps({"months": months, "step": step, "top_n": top_n, "sessions": len(sessions)}), "running"),
    )
    conn.commit()

    metas = {str(r[0]): str(r[1] or r[0]) for r in conn.execute("SELECT symbol, name FROM stock_meta")}
    symbols = store.list_kline_symbols(min_rows=MIN_BARS)
    payloads = [{"symbol": s, "name": metas.get(s, s), "sessions": sessions, "db_path": store.db_path}
                for s in symbols]

    _progress.update({"running": True, "run_id": run_id, "phase": "scan",
                      "done": 0, "total": len(payloads)})
    candidates: List[Dict[str, object]] = []
    try:
        if workers and workers > 0:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                futures = [ex.submit(_replay_symbol, p) for p in payloads]
                for fut in as_completed(futures):
                    candidates.extend(fut.result() or [])
                    _progress["done"] = int(_progress["done"]) + 1
        else:
            for p in payloads:
                candidates.extend(_replay_symbol(p))
                _progress["done"] = int(_progress["done"]) + 1

        _progress["phase"] = "evaluate"
        # 每期每池 top-N
        by_key: Dict[tuple, List[Dict[str, object]]] = {}
        for c in candidates:
            by_key.setdefault((str(c["pool"]), str(c["as_of"])), []).append(c)
        picks: List[Dict[str, object]] = []
        for (pool, as_of), items in by_key.items():
            items.sort(key=lambda x: float(x["score"]), reverse=True)
            for rank, it in enumerate(items[:top_n], start=1):
                picks.append({**it, "rank": rank})

        # T+5 收益与全市场中位基准（复用完整交易日轴对齐）
        didx = {d: i for i, d in enumerate(tdates)}
        need_dates = set()
        for s in sessions:
            need_dates.add(s)
            j = didx[s] + HORIZON
            if j < len(tdates):
                need_dates.add(tdates[j])
        by_date: Dict[str, Dict[str, float]] = {}
        qmarks = ",".join("?" * len(need_dates))
        for d, sym, c in conn.execute(
            f"SELECT date, symbol, close FROM daily_kline WHERE date IN ({qmarks}) AND amount>0",
            sorted(need_dates),
        ):
            by_date.setdefault(str(d), {})[str(sym)] = float(c or 0)

        import statistics
        bench: Dict[str, Optional[float]] = {}
        for s in sessions:
            j = didx[s] + HORIZON
            if j >= len(tdates):
                bench[s] = None
                continue
            m0, m1 = by_date.get(s, {}), by_date.get(tdates[j], {})
            rets = [(m1[k] / m0[k] - 1) * 100 for k in m0.keys() & m1.keys() if m0[k] > 0]
            bench[s] = round(statistics.median(rets), 4) if rets else None

        rows_out = []
        for p in picks:
            s = str(p["as_of"])
            j = didx[s] + HORIZON
            tgt = tdates[j] if j < len(tdates) else None
            c0 = by_date.get(s, {}).get(str(p["symbol"]), 0.0)
            c1 = by_date.get(tgt, {}).get(str(p["symbol"]), 0.0) if tgt else 0.0
            ret = round((c1 / c0 - 1) * 100, 2) if c0 > 0 and c1 > 0 else None
            b = bench.get(s)
            excess = round(ret - b, 2) if ret is not None and b is not None else None
            rows_out.append((run_id, p["pool"], s, p["symbol"], p["name"], p["rank"],
                             float(p["score"]), ret, excess))
        conn.executemany(
            "INSERT OR REPLACE INTO replay_results(run_id,pool,as_of,symbol,name,rank,score,ret_t5,excess_t5) "
            "VALUES(?,?,?,?,?,?,?,?,?)", rows_out)
        summary = _summarize(store, run_id)
        conn.execute("UPDATE replay_runs SET status='done', progress=1, summary_json=? WHERE run_id=?",
                     (json.dumps(summary, ensure_ascii=False), run_id))
        conn.commit()
        return {"run_id": run_id, "status": "done", **summary}
    except Exception as exc:
        conn.execute("UPDATE replay_runs SET status='failed', summary_json=? WHERE run_id=?",
                     (json.dumps({"error": str(exc)[:300]}), run_id))
        conn.commit()
        raise
    finally:
        _progress["running"] = False
        _progress["phase"] = "done"


def _summarize(store: LocalQuantStore, run_id: str) -> Dict[str, object]:
    conn = store._conn()
    rows = conn.execute(
        "SELECT pool, as_of, ret_t5, excess_t5 FROM replay_results WHERE run_id=? ORDER BY as_of",
        (run_id,),
    ).fetchall()
    pools: Dict[str, Dict[str, object]] = {}
    for pool, as_of, ret, excess in rows:
        p = pools.setdefault(str(pool), {"picks": 0, "rets": [], "excesses": [],
                                         "monthly": {}, "curve": {}})
        p["picks"] += 1
        if ret is not None:
            p["rets"].append(float(ret))
        if excess is not None:
            p["excesses"].append(float(excess))
            month = str(as_of)[:7]
            p["monthly"].setdefault(month, []).append(float(excess))
            p["curve"].setdefault(str(as_of), []).append(float(excess))
    out: Dict[str, object] = {"pools": []}
    for pool, p in sorted(pools.items()):
        exs: List[float] = p["excesses"]
        monthly = [{"month": m, "picks": len(v),
                    "excess_win_rate": round(sum(1 for x in v if x > 0) / len(v), 4),
                    "avg_excess": round(sum(v) / len(v), 2)}
                   for m, v in sorted(p["monthly"].items())]
        cum = 0.0
        curve = []
        for d, v in sorted(p["curve"].items()):
            avg = sum(v) / len(v)
            cum += avg
            curve.append({"as_of": d, "avg_excess": round(avg, 2), "cum_excess": round(cum, 2)})
        out["pools"].append({
            "pool": pool,
            "picks": p["picks"],
            "evaluated": len(exs),
            "win_rate": round(sum(1 for x in p["rets"] if x > 0) / len(p["rets"]), 4) if p["rets"] else None,
            "avg_return": round(sum(p["rets"]) / len(p["rets"]), 2) if p["rets"] else None,
            "excess_win_rate": round(sum(1 for x in exs if x > 0) / len(exs), 4) if exs else None,
            "avg_excess": round(sum(exs) / len(exs), 2) if exs else None,
            "monthly": monthly,
            "curve": curve,
        })
    return out


def start_replay_async(months: int = 12, step: int = 5, top_n: int = 20, workers: int = 6) -> Dict[str, object]:
    """后台线程启动回放（防重入）。"""
    with _run_lock:
        if _progress.get("running"):
            return {"started": False, "reason": "已有回放在运行", **replay_status()}
        _progress.update({"running": True, "phase": "starting", "done": 0, "total": 0})
    threading.Thread(
        target=lambda: _safe_run(months, step, top_n, workers), daemon=True,
    ).start()
    return {"started": True, **replay_status()}


def _safe_run(months: int, step: int, top_n: int, workers: int) -> None:
    try:
        run_replay(months=months, step=step, top_n=top_n, workers=workers)
    except Exception:
        pass  # 失败状态已写入 replay_runs


def replay_status() -> Dict[str, object]:
    return dict(_progress)


def latest_replay_summary(store: Optional[LocalQuantStore] = None) -> Optional[Dict[str, object]]:
    store = store or get_local_store()
    _ensure_tables(store)
    row = store._conn().execute(
        "SELECT run_id, created_at, params_json, summary_json FROM replay_runs "
        "WHERE status='done' ORDER BY created_at DESC LIMIT 1",
    ).fetchone()
    if not row:
        return None
    out = {"run_id": row[0], "created_at": row[1], "params": json.loads(row[2] or "{}")}
    out.update(json.loads(row[3] or "{}"))
    return out
