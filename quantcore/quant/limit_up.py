"""涨停分析：连板梯队 × 概念板块分布。

复用 market_sentiment 的 _limit_cause / _segment / _limit_threshold，
仅针对单日做精细化展示：
- 每只涨停股的连板高度
- 概念板块归因（AI算力/半导体/机器人/…/其他）
- 是否一字板（开盘即封）
- 成交额标记（大单）
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from .local_store import get_local_store
from .market_sentiment import _limit_cause, _limit_threshold, _segment


def compute_limit_up_distribution(target_date: str) -> Dict[str, object]:
    store = get_local_store()
    conn = store._conn()

    buffer_start = (
        datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=22)
    ).strftime("%Y-%m-%d")

    rows = conn.execute(
        "SELECT symbol, date, open, close, amount FROM daily_kline "
        "WHERE date >= ? AND date <= ? ORDER BY symbol, date",
        (buffer_start, target_date),
    ).fetchall()

    if not rows:
        return {
            "empty": True,
            "message": "本地行情为空，请先在「数据同步」做全量同步",
            "date": target_date,
        }

    df = pd.DataFrame(rows, columns=["symbol", "date", "open", "close", "amount"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["open"] = pd.to_numeric(df["open"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    df = df.dropna(subset=["close"]).copy()
    df = df[df["close"] > 0]

    # 688 成交额补偿（同 market_sentiment 注释）
    mask_688 = df["symbol"].astype(str).str.startswith("688")
    df.loc[mask_688, "amount"] = df.loc[mask_688, "amount"] / 100.0

    # 涨跌幅、涨停、连板高度
    g = df.groupby("symbol", sort=False)
    df["pct"] = g["close"].pct_change()
    thr_map = df["symbol"].map(_limit_threshold)
    df["limit_up"] = df["pct"] >= thr_map
    df["limit_down"] = df["pct"] <= -thr_map
    grp = (~df["limit_up"]).groupby(df["symbol"]).cumsum()
    df["board_height"] = (
        df["limit_up"]
        .groupby([df["symbol"], grp])
        .cumsum()
        .where(df["limit_up"], 0)
        .astype(int)
    )

    # meta
    meta_rows = conn.execute(
        "SELECT symbol, name, industry FROM stock_meta"
    ).fetchall()
    meta = {
        str(s).zfill(6): {"name": n or "", "industry": i or ""}
        for s, n, i in meta_rows
    }
    df["seg"] = df["symbol"].map(_segment)
    df["name"] = df["symbol"].map(
        lambda s: meta.get(str(s).zfill(6), {}).get("name", "")
    )
    df["industry"] = df["symbol"].map(
        lambda s: meta.get(str(s).zfill(6), {}).get("industry", "")
    )
    df["cause"] = df.apply(
        lambda r: _limit_cause(
            str(r.get("name") or ""),
            str(r.get("industry") or ""),
            str(r.get("seg") or ""),
        ),
        axis=1,
    )

    # 只取目标日
    day = df[df["date"] == target_date].copy()
    if day.empty:
        return {
            "empty": False,
            "date": target_date,
            "message": f"{target_date} 无行情数据（非交易日或未同步）",
            "total_limit_up": 0,
            "total_limit_down": 0,
            "stocks": [],
            "matrix": {},
            "causes": [],
            "kpi": {},
        }

    limit_up_day = day[day["limit_up"]].copy()
    limit_down_day = day[day["limit_down"]].copy()

    # 是否一字板：开盘 ≈ 收盘（涨停价开，未被打开）
    limit_up_day["is_one_price"] = (
        (limit_up_day["open"] - limit_up_day["close"]).abs()
        / limit_up_day["close"].replace(0, pd.NA)
        < 0.002
    ).fillna(False)

    # 大单标记：成交额 > 5 亿
    limit_up_day["is_big"] = limit_up_day["amount"] > 5e8

    # 整理 stocks 列表
    stocks: List[Dict] = []
    for _, r in limit_up_day.iterrows():
        b = int(r["board_height"])
        stocks.append(
            {
                "symbol": str(r["symbol"]).zfill(6),
                "name": r["name"] or str(r["symbol"]).zfill(6),
                "boards": b,
                "cause": r["cause"],
                "segment": r["seg"],
                "is_one_price": bool(r["is_one_price"]),
                "is_big": bool(r["is_big"]),
                "is_20pct": _limit_threshold(str(r["symbol"])) > 0.15,
                "close": round(float(r["close"]), 2),
                "pct_chg": round(float(r["pct"]) * 100, 2),
                "amount_yi": round(float(r["amount"]) / 1e8, 2),
            }
        )

    stocks.sort(key=lambda s: (-s["boards"], -s["amount_yi"]))

    # 构建矩阵 {board_label: {cause: [stock...]}}
    # 行：连板高度分级；列：概念板块
    from collections import Counter, defaultdict

    cause_counts = Counter(s["cause"] for s in stocks)
    # 保留出现 ≥ 2 次的概念，其余归入"其他"；最多展示 9 列
    top_causes = [c for c, _ in cause_counts.most_common(9) if cause_counts[c] >= 2]
    if not top_causes:
        top_causes = [c for c, _ in cause_counts.most_common(5)]

    def board_label(b: int) -> str:
        if b >= 5:
            return "5板+"
        if b == 1:
            return "首板"
        return f"{b}连板"

    matrix: Dict[str, Dict[str, List]] = defaultdict(lambda: defaultdict(list))
    for s in stocks:
        bl = board_label(s["boards"])
        cause = s["cause"] if s["cause"] in top_causes else "其他"
        matrix[bl][cause].append(
            {
                "symbol": s["symbol"],
                "name": s["name"],
                "is_one_price": s["is_one_price"],
                "is_big": s["is_big"],
                "is_20pct": s["is_20pct"],
            }
        )

    # 确保"其他"出现在 top_causes 末尾
    all_causes = list(top_causes)
    if any(s["cause"] not in top_causes for s in stocks):
        all_causes.append("其他")

    # 行顺序
    level_order = ["5板+", "4连板", "3连板", "2连板", "首板"]
    present_levels = [lv for lv in level_order if lv in matrix]

    # 每概念列的涨停数（底部汇总行）
    cause_total = {
        c: sum(len(matrix[lv].get(c, [])) for lv in present_levels)
        for c in all_causes
    }

    # KPI
    max_boards = max((s["boards"] for s in stocks), default=0)
    one_price_n = sum(1 for s in stocks if s["is_one_price"])
    boards_2plus = sum(1 for s in stocks if s["boards"] >= 2)

    return {
        "empty": False,
        "date": target_date,
        "total_limit_up": len(stocks),
        "total_limit_down": int(limit_down_day.shape[0]),
        "max_boards": max_boards,
        "one_price_count": one_price_n,
        "boards_2plus": boards_2plus,
        "causes": all_causes,
        "cause_total": cause_total,
        "level_order": present_levels,
        "matrix": {
            lv: {c: matrix[lv].get(c, []) for c in all_causes}
            for lv in present_levels
        },
        "level_counts": {
            lv: sum(len(v) for v in matrix[lv].values())
            for lv in present_levels
        },
        "stocks": stocks,
    }
