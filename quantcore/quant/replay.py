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
    ret_t5_open REAL,
    excess_t5_open REAL,
    limitup_at_close INTEGER,
    PRIMARY KEY (run_id, pool, as_of, symbol)
);
CREATE TABLE IF NOT EXISTS replay_scan (
    param_key TEXT,
    symbol TEXT,
    candidates_json TEXT,
    PRIMARY KEY (param_key, symbol)
);
"""

_run_lock = threading.Lock()
_progress: Dict[str, object] = {"running": False, "run_id": "", "phase": "", "done": 0, "total": 0}


def _ensure_tables(store: LocalQuantStore) -> None:
    conn = store._conn()
    conn.executescript(_SCHEMA)
    # 老库升级：次日开盘可成交口径列（幂等，旧 run 的新列为 null）
    for col, typ in (("ret_t5_open", "REAL"), ("excess_t5_open", "REAL"),
                     ("limitup_at_close", "INTEGER")):
        try:
            conn.execute(f"ALTER TABLE replay_results ADD COLUMN {col} {typ}")
        except Exception:
            pass
    conn.commit()


def _limit_up_threshold(symbol: str) -> float:
    """按板块近似涨停幅度：创业板/科创板 20cm，其余 10cm（ST 5% 无法从代码判别，接受近似）。"""
    return 0.195 if symbol.startswith(("30", "68")) else 0.095


REGIME_WINDOW = 5  # 与 engine.market_context 的 recent_returns(window=5) 对齐


def _classify_regime(median_pct: float, breadth_up: float) -> str:
    """大盘环境分类，阈值与 engine.market_context 完全一致（口径必须同源，否则分层结论失真）。"""
    if median_pct >= 1.0 and breadth_up >= 0.55:
        return "偏暖"
    if median_pct <= -1.0 or breadth_up <= 0.40:
        return "偏冷"
    return "中性"


def _session_regimes(store: LocalQuantStore, sessions: List[str]) -> Dict[str, str]:
    """每个回放期 as_of 的 point-in-time 大盘环境（近 5 交易日全市场中位涨幅 + 上涨占比）。"""
    import statistics

    if not sessions:
        return {}
    conn = store._conn()
    since = (date.fromisoformat(min(sessions)) - timedelta(days=40)).strftime("%Y-%m-%d")
    tdates = [str(r[0]) for r in conn.execute(
        "SELECT DISTINCT date FROM daily_kline WHERE date>=? AND amount>0 ORDER BY date", (since,))]
    didx = {d: i for i, d in enumerate(tdates)}
    need = set()
    pair: Dict[str, tuple] = {}
    for s in sessions:
        i = didx.get(s)
        if i is None or i < REGIME_WINDOW:
            continue
        base = tdates[i - REGIME_WINDOW]
        pair[s] = (base, s)
        need.update((base, s))
    if not pair:
        return {}
    by_date: Dict[str, Dict[str, float]] = {}
    qmarks = ",".join("?" * len(need))
    for d, sym, c in conn.execute(
        f"SELECT date, symbol, close FROM daily_kline WHERE date IN ({qmarks}) AND amount>0",
        sorted(need),
    ):
        by_date.setdefault(str(d), {})[str(sym)] = float(c or 0)
    out: Dict[str, str] = {}
    for s, (base, cur) in pair.items():
        m0, m1 = by_date.get(base, {}), by_date.get(cur, {})
        rets = [(m1[k] / m0[k] - 1) * 100 for k in m0.keys() & m1.keys() if m0[k] > 0]
        if len(rets) < 10:
            continue
        median = statistics.median(rets)
        breadth = sum(1 for v in rets if v > 0) / len(rets)
        out[s] = _classify_regime(median, breadth)
    return out


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
               store: Optional[LocalQuantStore] = None, workers: int = 6,
               anchor: Optional[str] = None) -> Dict[str, object]:
    """执行一次回放（同步，耗时数分钟）。workers<=0 时串行（测试用）。

    anchor：会话轴锚定日（YYYY-MM-DD，默认今天）。跨天断点续跑必须传原 run 的
    anchor，否则 since/cutoff 随"今天"漂移 → param_key 变化 → 全部断点作废。
    """
    store = store or get_local_store()
    _ensure_tables(store)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    anchor_day = date.fromisoformat(anchor) if anchor else date.today()
    since = (anchor_day - timedelta(days=int(months * 30.5) + 40)).strftime("%Y-%m-%d")
    tdates = _complete_trading_dates(store, since)
    # 轴尾截断到锚定日 9 天前：近期日期可能被增量补数据改变「完整日」判定，
    # 导致断点续跑的会话轴漂移、缓存作废；旧数据是稳定的。
    cutoff = (anchor_day - timedelta(days=9)).strftime("%Y-%m-%d")
    tdates = [d for d in tdates if d <= cutoff]
    # 采样期：每 step 个交易日一期；末尾留足 T+5 前向窗口
    sessions = [d for idx, d in enumerate(tdates[:-HORIZON]) if idx % max(1, step) == 0]
    if not sessions:
        raise ValueError("本地日线不足，无法回放")

    conn = store._conn()
    # 上一次运行如果被杀，会留下 status='running' 的僵尸行，统一标记失败
    conn.execute("UPDATE replay_runs SET status='failed' WHERE status='running'")
    conn.execute(
        "INSERT OR REPLACE INTO replay_runs(run_id, created_at, params_json, status, progress) "
        "VALUES(?,?,?,?,0)",
        (run_id, datetime.now().isoformat(timespec="seconds"),
         json.dumps({"months": months, "step": step, "top_n": top_n, "sessions": len(sessions),
                     "anchor": anchor_day.isoformat()}), "running"),
    )
    conn.commit()

    metas = {str(r[0]): str(r[1] or r[0]) for r in conn.execute("SELECT symbol, name FROM stock_meta")}
    symbols = store.list_kline_symbols(min_rows=MIN_BARS)

    # 断点续跑：同参数（月数/步长/会话轴指纹）下已扫描过的 symbol 直接用缓存，
    # 进程被杀后重跑只补增量——本 harness 会不定期回收长任务，进度必须只增不减。
    param_key = f"m{months}-s{step}-{sessions[0]}-{sessions[-1]}-{len(sessions)}"
    cached: Dict[str, str] = {
        str(r[0]): str(r[1]) for r in conn.execute(
            "SELECT symbol, candidates_json FROM replay_scan WHERE param_key=?", (param_key,))
    }
    payloads = [{"symbol": s, "name": metas.get(s, s), "sessions": sessions, "db_path": store.db_path}
                for s in symbols if s not in cached]

    _progress.update({"running": True, "run_id": run_id, "phase": "scan",
                      "done": len(cached), "total": len(symbols)})
    candidates: List[Dict[str, object]] = []
    for blob in cached.values():
        candidates.extend(json.loads(blob))

    def _checkpoint(symbol: str, cands: List[Dict[str, object]]) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO replay_scan(param_key, symbol, candidates_json) VALUES(?,?,?)",
            (param_key, symbol, json.dumps(cands, ensure_ascii=False)))
        done = int(_progress["done"]) + 1
        _progress["done"] = done
        if done % 50 == 0:
            conn.commit()

    try:
        if workers and workers > 0:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                futures = {ex.submit(_replay_symbol, p): str(p["symbol"]) for p in payloads}
                for fut in as_completed(futures):
                    cands = fut.result() or []
                    candidates.extend(cands)
                    _checkpoint(futures[fut], cands)
        else:
            for p in payloads:
                cands = _replay_symbol(p)
                candidates.extend(cands)
                _checkpoint(str(p["symbol"]), cands)
        conn.commit()

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
        # 双口径：回测口径 close(as_of)→close(t5)；可成交口径 open(as_of+1)→close(t5)
        didx = {d: i for i, d in enumerate(tdates)}
        need_dates = set()
        open_dates = set()
        for s in sessions:
            need_dates.add(s)
            i = didx[s]
            if i > 0:
                need_dates.add(tdates[i - 1])  # 前收盘：判 as_of 是否涨停
            j = i + HORIZON
            if j < len(tdates):
                need_dates.add(tdates[j])
            if i + 1 < len(tdates):
                open_dates.add(tdates[i + 1])
        by_date: Dict[str, Dict[str, float]] = {}
        qmarks = ",".join("?" * len(need_dates))
        for d, sym, c in conn.execute(
            f"SELECT date, symbol, close FROM daily_kline WHERE date IN ({qmarks}) AND amount>0",
            sorted(need_dates),
        ):
            by_date.setdefault(str(d), {})[str(sym)] = float(c or 0)
        open_by_date: Dict[str, Dict[str, float]] = {}
        if open_dates:
            qmarks_o = ",".join("?" * len(open_dates))
            for d, sym, o in conn.execute(
                f"SELECT date, symbol, open FROM daily_kline WHERE date IN ({qmarks_o}) AND amount>0",
                sorted(open_dates),
            ):
                open_by_date.setdefault(str(d), {})[str(sym)] = float(o or 0)

        import statistics
        bench: Dict[str, Optional[float]] = {}
        bench_open: Dict[str, Optional[float]] = {}
        for s in sessions:
            i = didx[s]
            j = i + HORIZON
            if j >= len(tdates):
                bench[s] = bench_open[s] = None
                continue
            m0, m1 = by_date.get(s, {}), by_date.get(tdates[j], {})
            rets = [(m1[k] / m0[k] - 1) * 100 for k in m0.keys() & m1.keys() if m0[k] > 0]
            bench[s] = round(statistics.median(rets), 4) if rets else None
            mo = open_by_date.get(tdates[i + 1], {}) if i + 1 < len(tdates) else {}
            rets_o = [(m1[k] / mo[k] - 1) * 100 for k in mo.keys() & m1.keys() if mo[k] > 0]
            bench_open[s] = round(statistics.median(rets_o), 4) if rets_o else None

        rows_out = []
        for p in picks:
            s = str(p["as_of"])
            sym = str(p["symbol"])
            i = didx[s]
            j = i + HORIZON
            tgt = tdates[j] if j < len(tdates) else None
            c0 = by_date.get(s, {}).get(sym, 0.0)
            c1 = by_date.get(tgt, {}).get(sym, 0.0) if tgt else 0.0
            ret = round((c1 / c0 - 1) * 100, 2) if c0 > 0 and c1 > 0 else None
            b = bench.get(s)
            excess = round(ret - b, 2) if ret is not None and b is not None else None
            # 可成交口径：次日开盘买入
            o1 = open_by_date.get(tdates[i + 1], {}).get(sym, 0.0) if i + 1 < len(tdates) else 0.0
            ret_open = round((c1 / o1 - 1) * 100, 2) if o1 > 0 and c1 > 0 else None
            bo = bench_open.get(s)
            excess_open = round(ret_open - bo, 2) if ret_open is not None and bo is not None else None
            # as_of 收盘是否涨停（买不到回测价的近似标记）
            cprev = by_date.get(tdates[i - 1], {}).get(sym, 0.0) if i > 0 else 0.0
            limitup = 1 if (cprev > 0 and c0 > 0
                            and c0 / cprev - 1 >= _limit_up_threshold(sym)) else 0
            rows_out.append((run_id, p["pool"], s, sym, p["name"], p["rank"],
                             float(p["score"]), ret, excess, ret_open, excess_open, limitup))
        conn.executemany(
            "INSERT OR REPLACE INTO replay_results(run_id,pool,as_of,symbol,name,rank,score,"
            "ret_t5,excess_t5,ret_t5_open,excess_t5_open,limitup_at_close) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", rows_out)
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
    import statistics

    conn = store._conn()
    rows = conn.execute(
        "SELECT pool, as_of, ret_t5, excess_t5, excess_t5_open, limitup_at_close "
        "FROM replay_results WHERE run_id=? ORDER BY as_of",
        (run_id,),
    ).fetchall()
    regimes = _session_regimes(store, sorted({str(r[1]) for r in rows}))
    pools: Dict[str, Dict[str, object]] = {}
    for pool, as_of, ret, excess, excess_open, limitup in rows:
        p = pools.setdefault(str(pool), {"picks": 0, "rets": [], "excesses": [],
                                         "excesses_open": [], "limitups": 0,
                                         "monthly": {}, "curve": {}, "by_regime": {}})
        p["picks"] += 1
        if limitup:
            p["limitups"] += 1
        if ret is not None:
            p["rets"].append(float(ret))
        regime = regimes.get(str(as_of))
        if excess_open is not None:
            p["excesses_open"].append(float(excess_open))
        if excess is not None:
            p["excesses"].append(float(excess))
            month = str(as_of)[:7]
            p["monthly"].setdefault(month, []).append(float(excess))
            p["curve"].setdefault(str(as_of), []).append(float(excess))
            if regime:
                r = p["by_regime"].setdefault(regime, {"sessions": set(), "excesses": [],
                                                       "excesses_open": []})
                r["sessions"].add(str(as_of))
                r["excesses"].append(float(excess))
                if excess_open is not None:
                    r["excesses_open"].append(float(excess_open))
    out: Dict[str, object] = {"pools": []}
    for pool, p in sorted(pools.items()):
        exs: List[float] = sorted(p["excesses"])
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
        exs_o: List[float] = p["excesses_open"]
        open_entry = {
            "evaluated": len(exs_o),
            "excess_win_rate": round(sum(1 for x in exs_o if x > 0) / len(exs_o), 4) if exs_o else None,
            "avg_excess": round(sum(exs_o) / len(exs_o), 2) if exs_o else None,
            "median_excess": round(statistics.median(exs_o), 2) if exs_o else None,
        }
        regime_rows = []
        for rname in ("偏暖", "中性", "偏冷"):
            r = p["by_regime"].get(rname)
            if not r or not r["excesses"]:
                continue
            rex: List[float] = r["excesses"]
            rex_o: List[float] = r["excesses_open"]
            regime_rows.append({
                "regime": rname,
                "sessions": len(r["sessions"]),
                "picks": len(rex),
                "excess_win_rate": round(sum(1 for x in rex if x > 0) / len(rex), 4),
                "avg_excess": round(sum(rex) / len(rex), 2),
                "median_excess": round(statistics.median(rex), 2),
                "avg_excess_open": round(sum(rex_o) / len(rex_o), 2) if rex_o else None,
            })
        out["pools"].append({
            "pool": pool,
            "picks": p["picks"],
            "evaluated": len(exs),
            "regimes": regime_rows,
            "win_rate": round(sum(1 for x in p["rets"] if x > 0) / len(p["rets"]), 4) if p["rets"] else None,
            "avg_return": round(sum(p["rets"]) / len(p["rets"]), 2) if p["rets"] else None,
            "excess_win_rate": round(sum(1 for x in exs if x > 0) / len(exs), 4) if exs else None,
            "avg_excess": round(sum(exs) / len(exs), 2) if exs else None,
            "median_excess": round(statistics.median(exs), 2) if exs else None,
            "p10_excess": round(exs[int(len(exs) * 0.1)], 2) if exs else None,
            "p90_excess": round(exs[int(len(exs) * 0.9)], 2) if exs else None,
            "limitup_ratio": round(p["limitups"] / p["picks"], 4) if p["picks"] else None,
            "open_entry": open_entry,
            "monthly": monthly,
            "curve": curve,
        })
    return out


def start_replay_async(months: int = 12, step: int = 5, top_n: int = 20, workers: int = 6,
                       anchor: Optional[str] = None) -> Dict[str, object]:
    """后台线程启动回放（防重入）。"""
    with _run_lock:
        if _progress.get("running"):
            return {"started": False, "reason": "已有回放在运行", **replay_status()}
        _progress.update({"running": True, "phase": "starting", "done": 0, "total": 0})
    threading.Thread(
        target=lambda: _safe_run(months, step, top_n, workers, anchor), daemon=True,
    ).start()
    return {"started": True, **replay_status()}


def _safe_run(months: int, step: int, top_n: int, workers: int, anchor: Optional[str] = None) -> None:
    try:
        run_replay(months=months, step=step, top_n=top_n, workers=workers, anchor=anchor)
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
