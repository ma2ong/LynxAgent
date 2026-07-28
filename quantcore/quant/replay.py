"""选股规则的 point-in-time 历史回放验证。

用本地日线把各池选股规则回放到过去（默认 12 个月、每 5 个交易日一期），
每期取 top-N，统计 T+5 相对全市场中位的超额收益 → 回答「这套选股规则有没有效」。

口径说明（结果解读时必须记住）：
- pattern 池：形态识别/因子/威科夫全部用截断到 as_of 的日线，严格 point-in-time；
  实时分量（realtime_score）用当日 bar 的涨跌幅/成交额，与收盘后扫描等价。
- smart 池（v3，2026-07-14 起）：结构因子合成分，与线上 engine.smart_pool 共用同一
  评分函数（compute_factor_scores + composite_score，成交额 ≥3000 万门槛），无近似差。
- strength 池：门槛（距 250 日低点 ≥70%、ADR ≥4.5%、站上 EMA8/21）与线上一致；排序用
  momentum_raw 代替 rs_rating——后者是横截面百分位、worker 逐股跑拿不到，但两者单调等价，
  按每期 top-N 取样结果相同。
- auction 池：只用高开幅度（评分里权重最大也最干净的一项），量比/板块共振/板块趋势依赖
  行业映射，而本地行业覆盖仅约七成且是「当前」映射，塞进回放会引入系统性偏差，故略去
  ——这是保守近似，该池的真实排序能力只会比回放显示的更好、不会更差。
  另注意：线上竞价是当日开盘买入，而回放统一按各池同一口径（收盘/次日开盘）计算，
  等于比真实执行晚一个开盘价，这条线因此偏保守，与其他池横向比时要记得这个差异。
- 排除规则用「当前」股票名称（ST/退市标记随时间变化，历史时点无法还原）。
"""
from __future__ import annotations

import json
import logging
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
# 扫描断点缓存的口径版本：_replay_symbol 的评分器集合/公式一旦变化必须 +1，
# 否则同轴续跑会复用旧口径的候选（实测：加 smart_fac 池后旧缓存里没有它）。
# v3：smart 评分切换为结构因子合成（原 smart_fac 实验转正），smart_fac 池移除。
# v4：形态识别新增三不卖低位形态（三军会师/双管齐下/五阳上阵），pattern 候选集变化。
# v5：新增 strength / auction 两池的 point-in-time 评分器，候选集合变化。
SCAN_VERSION = 5

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


REGIME_WINDOW = 5  # 与 engine.market_context 的 recent_daily_breadth(days=5) 对齐


def _session_regimes(store: LocalQuantStore, sessions: List[str]) -> Dict[str, str]:
    """每个回放期 as_of 的 point-in-time 大盘环境。

    口径与 engine.market_context 同源：逐日中位涨幅 + 上涨广度 → regime 加权温度分。
    不同源分层结论就会与线上标签矛盾（同一天回放说偏冷、横幅说中性）。
    """
    import statistics

    from .regime import blend_temp, classify

    if not sessions:
        return {}
    conn = store._conn()
    since = (date.fromisoformat(min(sessions)) - timedelta(days=40)).strftime("%Y-%m-%d")
    tdates = [str(r[0]) for r in conn.execute(
        "SELECT DISTINCT date FROM daily_kline WHERE date>=? AND amount>0 ORDER BY date", (since,))]
    didx = {d: i for i, d in enumerate(tdates)}
    # 每个 session 需要 REGIME_WINDOW 个日收益 → REGIME_WINDOW+1 根 bar
    need = set()
    spans: Dict[str, List[str]] = {}
    for s in sessions:
        i = didx.get(s)
        if i is None or i < REGIME_WINDOW:
            continue
        span = tdates[i - REGIME_WINDOW: i + 1]
        spans[s] = span
        need.update(span)
    if not spans:
        return {}
    by_date: Dict[str, Dict[str, float]] = {}
    qmarks = ",".join("?" * len(need))
    for d, sym, c in conn.execute(
        f"SELECT date, symbol, close FROM daily_kline WHERE date IN ({qmarks}) AND amount>0",
        sorted(need),
    ):
        by_date.setdefault(str(d), {})[str(sym)] = float(c or 0)
    out: Dict[str, str] = {}
    for s, span in spans.items():
        days: List[tuple] = []
        for prev_d, cur_d in zip(span, span[1:]):
            m0, m1 = by_date.get(prev_d, {}), by_date.get(cur_d, {})
            rets = [(m1[k] / m0[k] - 1) * 100 for k in m0.keys() & m1.keys() if m0[k] > 0]
            if len(rets) < 10:
                continue
            days.append((statistics.median(rets), sum(1 for v in rets if v > 0) / len(rets)))
        if not days:
            continue
        out[s] = classify(blend_temp(list(reversed(days))))  # blend_temp 要求最新一日在前
    return out


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


def _factor_score(df: pd.DataFrame) -> Optional[float]:
    """smart 池 v3 评分：结构因子合成分（与线上 engine.smart_pool 同一函数）。"""
    from .factors import composite_score, compute_factor_scores

    if len(df) < MIN_BARS:
        return None
    try:
        return float(composite_score(compute_factor_scores(df)))
    except Exception:
        return None


def _strength_score(df: pd.DataFrame) -> Optional[float]:
    """强势股池的 point-in-time 评分（与线上 engine.strength_pool 同源）。

    线上按横截面 rs_rating（动量百分位 1-99）排序并设三道门槛：距 250 日低点涨幅 ≥70%、
    ADR ≥4.5%、站上 EMA8 与 EMA21。这里返回 momentum_raw 而不是 rs_rating——rs_rating 是
    对当期全市场排名后的百分位，而 worker 是逐股跑的、拿不到横截面；但 rs_rating 是
    momentum_raw 的单调变换，回放按每期 top-N 取样，排序结果完全一致。
    """
    from .relative_strength import compute_strength_metrics

    m = compute_strength_metrics(df)
    if not m:
        return None
    if m["dist_from_low"] < 70.0 or m["adr"] < 4.5:
        return None
    if not (m["above_ema8"] and m["above_ema21"]):
        return None
    return float(m["momentum_raw"])


def _auction_score(df: pd.DataFrame) -> Optional[float]:
    """竞价池的 point-in-time 评分：当日高开幅度（今开/昨收）。

    线上评分还含量比、板块共振与板块趋势，但后两者依赖行业映射，而本地行业覆盖只有约
    七成、且是「当前」映射而非历史映射，硬塞进回放会引入系统性偏差。高开幅度是评分里
    权重最大也最干净的一项，用它代表该池的排序能力，属于保守近似。

    健康高开区间与线上一致（下限 1.5%、上限按 10% 板×0.6）；一字板或低开都不入选。
    """
    if len(df) < 2:
        return None
    prev_close = float(df["close"].iloc[-2])
    open_px = float(df["open"].iloc[-1])
    if prev_close <= 0 or open_px <= 0:
        return None
    gap = (open_px / prev_close - 1.0) * 100.0
    if not (1.5 <= gap <= 6.0):
        return None
    return round(gap, 3)


def _parent_alive() -> bool:
    """父进程是否还在（查不了就当活着，不误杀）。"""
    try:
        from multiprocessing import parent_process
        parent = parent_process()
        return parent is None or parent.is_alive()
    except Exception:
        return True


def _lower_priority() -> None:
    """把 worker 降到低优先级：回放是后台批量任务，不能和用户请求抢 CPU。

    站点公网化后实测：6 个满负荷 worker 把整机吃到 100%，页面响应掉到 1.5 秒。
    降优先级后 CPU 仍被用满（回放照常推进），但操作系统会优先调度处理请求的线程。
    """
    try:
        import os
        import sys
        if sys.platform == "win32":
            import ctypes
            BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            ctypes.windll.kernel32.SetPriorityClass(
                ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        else:
            os.nice(10)
    except Exception:
        pass  # 降级失败不影响回放本身


_priority_lowered = False


def _ensure_low_priority() -> None:
    """每个 worker 进程只降一次优先级。"""
    global _priority_lowered
    if _priority_lowered:
        return
    _priority_lowered = True
    _lower_priority()


_watchdog_started = False


def _ensure_orphan_watchdog(interval: float = 5.0) -> None:
    """worker 内守护线程：父进程一死就硬退出。

    后端被强杀（TerminateProcess）时子进程收不到通知：任务队列断了，worker 既取不到
    新任务、也不会退出，实测 6 个 worker 各烧掉一个多小时 CPU，拖垮整机（接口 20s→110s、
    后端起不来）。守卫不能放在任务里——父进程死后任务根本不会再被派发；必须由独立线程
    定时检查。os._exit 而非 sys.exit：父进程已死，正常关闭路径本身也走不通。
    """
    global _watchdog_started
    if _watchdog_started:
        return
    _watchdog_started = True

    def _watch() -> None:
        import os
        import time as _time
        while True:
            _time.sleep(interval)
            if not _parent_alive():
                os._exit(0)

    threading.Thread(target=_watch, daemon=True).start()


def _replay_symbol(payload: Dict[str, object]) -> List[Dict[str, object]]:
    """进程池 worker：单只股票在全部回放期各池的候选评分。顶层函数以便 pickle。"""
    _ensure_orphan_watchdog()
    _ensure_low_priority()
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
            # smart v3：结构因子合成分（与线上 engine.smart_pool 同一评分函数与门槛）
            if amount >= PATTERN_MIN_AMOUNT:
                s_score = _factor_score(df.tail(120))
                if s_score is not None:
                    out.append({"pool": "smart", "as_of": as_of, "symbol": symbol, "name": name, "score": s_score})
            p_score = _pattern_score(symbol, df.tail(540))
            if p_score is not None:
                out.append({"pool": "pattern", "as_of": as_of, "symbol": symbol, "name": name, "score": p_score})
            # 强势股与竞价：合并进一键智选后这两套逻辑停了留痕，只靠实盘攒样本要好几周；
            # 放进回放就能立刻拿到 12 个月的同轴对比。
            if amount >= PATTERN_MIN_AMOUNT:
                r_score = _strength_score(df.tail(260))
                if r_score is not None:
                    out.append({"pool": "strength", "as_of": as_of, "symbol": symbol, "name": name, "score": r_score})
                a_score = _auction_score(df.tail(2))
                if a_score is not None:
                    out.append({"pool": "auction", "as_of": as_of, "symbol": symbol, "name": name, "score": a_score})
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
    # 上一次运行如果被杀，会留下 status='running' 的僵尸行，统一标记失败。
    # 这几句写在建 run 行之前，任何异常（典型是数据同步占着写锁 → database is locked）
    # 都还没有 run_id 可挂错误，必须自己带上下文抛出，否则调用方只看到一个空的 failed 行。
    try:
        conn.execute("UPDATE replay_runs SET status='failed' WHERE status='running'")
    except Exception as exc:
        raise RuntimeError(
            f"回放启动失败，拿不到 SQLite 写锁（{exc}）。通常是数据同步/其他写入正占着库，"
            f"稍后重试；要彻底避开竞争就对库做快照后在副本上跑。"
        ) from exc
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
    param_key = f"v{SCAN_VERSION}-m{months}-s{step}-{sessions[0]}-{sessions[-1]}-{len(sessions)}"
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
        _progress.update({"running": True, "phase": "starting", "done": 0, "total": 0, "error": ""})
    threading.Thread(
        target=lambda: _safe_run(months, step, top_n, workers, anchor), daemon=True,
    ).start()
    return {"started": True, **replay_status()}


def _safe_run(months: int, step: int, top_n: int, workers: int, anchor: Optional[str] = None) -> None:
    """后台线程入口：异常不能外泄（会静默杀掉线程），但必须留下可诊断的痕迹。

    之前这里是 `except: pass`，注释说"失败状态已写入 replay_runs"——只有建了 run 行
    之后的失败才写得进去。启动阶段抢不到写锁时连行都没有，于是回放从 2026-07-18 起
    连续失败了九天而没有任何人能看出原因。错误现在既进日志也进 _progress（/status 端点）。
    """
    try:
        run_replay(months=months, step=step, top_n=top_n, workers=workers, anchor=anchor)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("replay").exception("replay run failed")
        _progress["error"] = str(exc)[:300]


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
