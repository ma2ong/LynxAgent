"""实验共用底座：会话轴、候选采样、整段向量化的因子分。

**为什么可以整段算一次**：`enrich_indicators` 里全部是 rolling / ewm / cummax /
pct_change，都只看当前行及之前，没有任何前视。所以「整段算一次，再按会话日取那一行」
与线上「切到当日重算一次」结果完全相同，但省掉了每期重算的开销（52 期约快 50 倍）。

这个等价性不是推理出来就算数的 —— `tests/test_factor_vectorization.py` 会拿本模块的
`factor_frame` 逐点对照生产的 `compute_factor_scores`，改动因子公式而忘了同步这里时，
测试会直接失败。

口径与线上 smart 池 / replay 对齐：
- 会话轴：anchor 往前 months 个月，每 step 个交易日一期，末尾留足 T+5，
  尾部截断到 anchor-9 天（近期日期可能被增量补数据改变「完整日」判定）
- 候选：当日成交额 >= 3e7、历史 >= 80 根
- 前向收益：T+5 收盘 / 当日收盘 - 1
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

DEFAULT_DB = os.path.join(HERE, ".snapshot.sqlite")
MIN_BARS = 80
HORIZON = 5
MIN_AMOUNT = 3e7
FACTORS = ["trend", "momentum", "rsi", "risk_control", "liquidity", "macd", "bollinger", "capital_flow"]


def _clip(s: pd.Series) -> pd.Series:
    return s.clip(lower=0.0, upper=100.0)


def factor_frame(df: pd.DataFrame) -> pd.DataFrame:
    """compute_factor_scores 的整段向量化版：逐行给出 8 个因子分 + 合成分。"""
    from quantcore.quant.factors import _WEIGHTS, enrich_indicators

    d = enrich_indicators(df)
    close = d["close"]
    ma20 = d["ma20"].fillna(close)
    ma60 = d["ma60"].fillna(close) if "ma60" in d.columns else close
    amount_ma20 = d["amount_ma20"]

    out = pd.DataFrame(index=d.index)
    out["trend"] = _clip(50 + (close / ma20 - 1) * 250 + (ma20 / ma60 - 1) * 180)
    out["momentum"] = _clip(50 + d["momentum_20"].fillna(0) * 180 + d["momentum_60"].fillna(0) * 120)
    out["rsi"] = _clip(100 - (d["rsi14"].fillna(50.0) - 55).abs() * 2.2)
    out["risk_control"] = _clip(100 - d["volatility20"].fillna(0.35) * 120
                                - d["drawdown"].fillna(0).abs() * 80)
    liq = _clip(40 + amount_ma20.fillna(0) / 10_000_000 * 8)
    out["liquidity"] = liq.where(amount_ma20.fillna(0) != 0, 50.0)
    out["macd"] = _clip(50 + (d["macd_line"].fillna(0) - d["macd_signal"].fillna(0)) * 2000)

    bb_u = d["bb_upper"].fillna(close * 1.05)
    bb_l = d["bb_lower"].fillna(close * 0.95)
    bb_pos = (close - bb_l) / (bb_u - bb_l).replace(0, np.nan)
    out["bollinger"] = _clip(100 - (bb_pos - 0.5).abs() * 120).fillna(50.0)

    cf = _clip(50 + (d["ret"].fillna(0) * amount_ma20.fillna(0) / 1e8) * 5)
    out["capital_flow"] = cf.where(amount_ma20.fillna(0) != 0, 50.0)

    out["composite"] = _clip(sum(out[k] * w for k, w in _WEIGHTS.items()))
    out["close"] = close
    out["amount"] = d["amount"] if "amount" in d.columns else np.nan
    out["date"] = d["date"].astype(str) if "date" in d.columns else d.index.astype(str)
    return out


def sample_symbols(payload: dict) -> list:
    """进程池 worker：一批股票 → [(会话日, 代码, {因子: 分}, T+5 收益)]。

    顶层函数以便 pickle。db 路径随 payload 传，子进程各开各的只读连接。
    """
    from quantcore.quant.data import normalize_ohlcv

    symbols, sessions, since = payload["symbols"], payload["sessions"], payload["since"]
    conn = sqlite3.connect(payload.get("db", DEFAULT_DB), timeout=60)
    rows_out = []
    for sym in symbols:
        raw = pd.read_sql_query(
            "SELECT date, open, high, low, close, volume, amount FROM daily_kline "
            "WHERE symbol=? AND date>=? ORDER BY date", conn, params=(sym, since))
        if len(raw) < MIN_BARS + HORIZON:
            continue
        try:
            nd = normalize_ohlcv(raw)
            ff = factor_frame(nd).reset_index(drop=True)
        except Exception:
            continue
        dates = ff["date"].tolist()
        pos = {d: i for i, d in enumerate(dates)}
        closes = ff["close"].to_numpy(dtype=float)
        # 开盘价从归一化后的帧取，保证与 ff 逐行对齐（normalize_ohlcv 会丢弃脏行）
        opens = pd.to_numeric(nd["open"], errors="coerce").to_numpy(dtype=float)
        amounts = pd.to_numeric(ff["amount"], errors="coerce").fillna(0).to_numpy(dtype=float)
        cols = {f: ff[f].to_numpy(dtype=float) for f in FACTORS}
        cols["composite"] = ff["composite"].to_numpy(dtype=float)
        for s in sessions:
            i = pos.get(s)
            # 多留一根：要算「T+1 收盘买入 → 再持有 HORIZON 根」这条产品实际口径
            if i is None or i < MIN_BARS - 1 or i + HORIZON + 1 >= len(dates):
                continue
            if amounts[i] < MIN_AMOUNT:
                continue
            c0, c1 = closes[i], closes[i + HORIZON]
            if not (c0 > 0 and c1 > 0):
                continue
            vals = {k: float(v[i]) for k, v in cols.items()}
            # 追高过滤要用的当日/近期涨幅。i >= MIN_BARS-1，往前取 5 根一定在界内。
            prev1, prev5 = closes[i - 1], closes[i - 5]
            vals["ret_1d"] = float(c0 / prev1 - 1.0) if prev1 > 0 else 0.0
            vals["ret_5d"] = float(c0 / prev5 - 1.0) if prev5 > 0 else 0.0
            # 可成交口径：次日开盘买入 → T+5 收盘。收盘口径吃不到的隔夜跳空在这里现形。
            o1 = opens[i + 1]
            vals["ret_open"] = float(c1 / o1 - 1.0) if o1 > 0 else float(c1 / c0 - 1.0)
            # 产品实际口径：用户在 T+1 盘中看到名单、按当时价买入。用 T+1 收盘近似入场价，
            # 再持有 HORIZON 根。`next_day_move` 就是用户在页面上看到的那个「今日涨跌幅」。
            c_entry, c_exit = closes[i + 1], closes[i + 1 + HORIZON]
            vals["next_day_move"] = float(c_entry / c0 - 1.0) if c0 > 0 else 0.0
            vals["ret_entry_next_close"] = float(c_exit / c_entry - 1.0) if c_entry > 0 else 0.0
            rows_out.append((s, sym, vals, c1 / c0 - 1.0))
    conn.close()
    return rows_out


def sessions_axis(conn, anchor: str, months: int, step: int) -> tuple[list, str]:
    anchor_day = date.fromisoformat(anchor)
    since = (anchor_day - timedelta(days=int(months * 30.5) + 40)).strftime("%Y-%m-%d")
    cutoff = (anchor_day - timedelta(days=9)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT date, COUNT(*) c FROM daily_kline WHERE date>=? AND amount>0 GROUP BY date ORDER BY date",
        (since,)).fetchall()
    if not rows:
        raise SystemExit("本地日线为空，先跑 experiments/snapshot_db.py")
    peak = max(r[1] for r in rows)
    tdates = [r[0] for r in rows if r[1] >= peak * 0.6 and r[0] <= cutoff]
    return [d for i, d in enumerate(tdates[:-HORIZON]) if i % max(1, step) == 0], since


def collect(db: str, anchor: str, months: int, step: int, workers: int) -> dict:
    """跑满一轮采样，返回 {会话日: [(因子分, T+5 收益), ...]}。"""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    conn = sqlite3.connect(db, timeout=60)
    sessions, since = sessions_axis(conn, anchor, months, step)
    syms = [r[0] for r in conn.execute(
        "SELECT symbol, COUNT(*) n FROM daily_kline WHERE date>=? GROUP BY symbol HAVING n>=?",
        (since, MIN_BARS + HORIZON))]
    conn.close()
    print(f"sessions={len(sessions)} ({sessions[0]}..{sessions[-1]}) symbols={len(syms)}", flush=True)

    chunks = [syms[i::workers * 3] for i in range(workers * 3)]
    payloads = [{"symbols": c, "sessions": sessions, "since": since, "db": db} for c in chunks if c]
    by_session: dict = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(sample_symbols, p) for p in payloads]
        for i, f in enumerate(as_completed(futs), 1):
            for s, _sym, vals, ret in f.result():
                by_session.setdefault(s, []).append((vals, ret))
            print(f"  chunk {i}/{len(payloads)}", flush=True)
    return by_session
