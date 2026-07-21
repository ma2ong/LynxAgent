"""相对强度筛选器（强势股研究清单）。

借鉴海外交易者的"筛强势股不筛低价股"思路（Movez 筛选器），全部指标从本地日线算、
point-in-time 可复算：
  · 距 250 日最低点 +70% 以上 —— 只要"已证明的上升趋势"，不接试图反弹的弱势股；
  · ADR（日均振幅）≥ 4.5% —— 大赢家需要波动空间，死水股很少出全垒打；
  · 站上 EMA8 且站上 EMA21 —— 已处于机构积累 + 强动量。
再叠加全市场横截面的相对强度评级（RS 1-99，动量百分位），按 RS 从高到低排序。

定位：这是"研究清单"不是"买入清单"——硬筛只做客观趋势/波动/位置，基本面深挖交给用户。
与 smart_pool（结构因子合成）互补：一个找"强度已兑现"的票，一个找"结构因子好"的票。
"""
from __future__ import annotations

from typing import Dict, List

import pandas as pd


def _ret(close: pd.Series, n: int) -> float | None:
    """n 个交易日的收益率%（数据不足返回 None）。"""
    if len(close) <= n:
        return None
    past = float(close.iloc[-1 - n])
    if past <= 0:
        return None
    return (float(close.iloc[-1]) / past - 1.0) * 100.0


def _momentum_raw(close: pd.Series) -> float:
    """RS 排名用的动量原始值：多周期加权收益，缺失周期按可得权重归一。

    权重仿 IBD RS：近端(63日)重、远端(126/189/252日)分摊，突出"持续走强"而非一波脉冲。
    """
    weights = {63: 0.4, 126: 0.2, 189: 0.2, 252: 0.2}
    acc = 0.0
    wsum = 0.0
    for n, w in weights.items():
        r = _ret(close, n)
        if r is not None:
            acc += w * r
            wsum += w
    if wsum == 0:
        return 0.0
    return acc / wsum


def compute_strength_metrics(df: pd.DataFrame) -> dict | None:
    """从日线 DataFrame（列含 high/low/close/amount）算相对强度原始指标。

    纯函数，脱离 SQLite 可单测。数据不足 120 根或收盘异常时返回 None。
    """
    if df is None or len(df) < 120:
        return None
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    last_close = float(close.iloc[-1])
    if last_close <= 0:
        return None

    # 距 250 日（不足则用可得区间）最低点涨幅
    window = min(250, len(df))
    min_low = float(low.iloc[-window:].min())
    dist_from_low = (last_close / min_low - 1.0) * 100.0 if min_low > 0 else 0.0

    # ADR%：近 20 日 (high/low) 均值 - 1
    hl = (high / low.replace(0, float("nan"))).iloc[-20:].dropna()
    adr = (float(hl.mean()) - 1.0) * 100.0 if len(hl) else 0.0

    # EMA8 / EMA21
    ema8 = float(close.ewm(span=8, adjust=False).mean().iloc[-1])
    ema21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])

    return {
        "close_local": last_close,
        "amount_local": float(df["amount"].iloc[-1] or 0) if "amount" in df else 0.0,
        "dist_from_low": round(dist_from_low, 1),
        "adr": round(adr, 2),
        "ema8": round(ema8, 3),
        "ema21": round(ema21, 3),
        "above_ema8": last_close > ema8,
        "above_ema21": last_close > ema21,
        "ema_stack": ema8 > ema21,  # 多头排列（加分项）
        "momentum_raw": round(_momentum_raw(close), 3),
    }


def strength_chunk(payload: Dict[str, object]) -> list:
    """进程池 worker：一段股票的相对强度指标（直连 SQLite，绕开 GIL）。

    顶层函数以便 pickle。payload: {db_path, cutoff, symbols, rt_amounts}
    每只票返回原始指标 + 动量原始值；横截面 RS 评级在主进程统一算。
    """
    from .local_store import LocalQuantStore

    store = LocalQuantStore(str(payload["db_path"]))
    conn = store._conn()
    out = []
    for sym in payload["symbols"]:
        rows = conn.execute(
            "SELECT date, high, low, close, amount FROM daily_kline "
            "WHERE symbol=? AND date>=? AND amount>0 ORDER BY date",
            (sym, payload["cutoff"])).fetchall()
        df = pd.DataFrame(rows, columns=["date", "high", "low", "close", "amount"])
        metrics = compute_strength_metrics(df)
        if metrics is None:
            continue
        out.append({"symbol": sym, **metrics})
    return out


def assign_rs_rating(rows: List[dict]) -> None:
    """横截面相对强度评级：动量百分位映射到 1-99，原地写入每行 rs_rating。

    RS 是"相对全市场"的强度，必须对全部有数据的票排名（不是只对入选票），否则失去相对意义。
    """
    if not rows:
        return
    ranked = sorted(rows, key=lambda r: r["momentum_raw"])
    n = len(ranked)
    for i, r in enumerate(ranked):
        # 百分位 1..99（最弱=1，最强=99）
        r["rs_rating"] = max(1, min(99, round((i + 0.5) / n * 98) + 1))
