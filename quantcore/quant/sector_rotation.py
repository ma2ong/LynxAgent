"""板块轮动相对强度排名。

借鉴海外"板块决定方向、大盘决定仓位、个股潜伏龙头"的思路（每周日看 11 个 SPDR 行业 ETF 的
4周/12周相对强度，前 3 关注、后 3 拉黑）。A 股用行业替代 ETF：
  · 每个行业算成分股的 20日(4周) / 60日(12周) 收益中位；
  · 市场基准 = 全市场收益中位（等价于 SPY/QQQ 的"大盘"）；
  · 相对强度 RS = 行业中位收益 − 市场中位收益（>0 强于大盘，<0 弱于大盘）；
  · 按 12周 RS 排名，标出领先(前3) / 落后(后3)。

用途：资金在往哪个方向流。领先板块里的平庸个股，往往强过落后板块里的完美形态。
"""
from __future__ import annotations

import statistics
from typing import Dict, List

import pandas as pd


def sector_return_chunk(payload: Dict[str, object]) -> list:
    """进程池 worker：一段股票的 5/20/60 日收益（直连 SQLite）。顶层函数以便 pickle。

    payload: {db_path, cutoff, symbols}
    """
    from .local_store import LocalQuantStore

    store = LocalQuantStore(str(payload["db_path"]))
    conn = store._conn()
    out = []
    for sym in payload["symbols"]:
        rows = conn.execute(
            "SELECT date, close, amount FROM daily_kline "
            "WHERE symbol=? AND date>=? AND amount>0 ORDER BY date",
            (sym, payload["cutoff"])).fetchall()
        if len(rows) < 61:  # 需要至少 60 日历史才能算 12 周动量
            continue
        close = [float(r[1]) for r in rows]
        last = close[-1]
        if last <= 0:
            continue

        def _ret(n: int) -> float | None:
            if len(close) <= n or close[-1 - n] <= 0:
                return None
            return (last / close[-1 - n] - 1.0) * 100.0

        out.append({
            "symbol": sym,
            "ret_5": _ret(5),
            "ret_20": _ret(20),
            "ret_60": _ret(60),
            "amount": float(rows[-1][2] or 0),
        })
    return out


def rank_sectors(rows: List[dict], industry_map: Dict[str, str],
                 min_members: int = 3) -> Dict[str, object]:
    """按行业聚合收益中位、算相对大盘的 RS、按 12 周 RS 排名。

    rows: sector_return_chunk 的输出（每股 ret_5/20/60）。
    返回 {market, sectors:[…按12周RS降序…], leaders, laggards}
    """
    # 市场基准：全市场各周期收益中位（= 大盘 SPY 等价）
    def _median(vals: List[float]) -> float:
        clean = [v for v in vals if v is not None]
        return round(statistics.median(clean), 2) if clean else 0.0

    mkt_5 = _median([r.get("ret_5") for r in rows])
    mkt_20 = _median([r.get("ret_20") for r in rows])
    mkt_60 = _median([r.get("ret_60") for r in rows])

    by_ind: Dict[str, List[dict]] = {}
    for r in rows:
        ind = industry_map.get(str(r["symbol"]).zfill(6))
        if not ind:
            continue
        by_ind.setdefault(ind, []).append(r)

    sectors: List[dict] = []
    for ind, members in by_ind.items():
        if len(members) < min_members:
            continue
        ret_20 = _median([m.get("ret_20") for m in members])
        ret_60 = _median([m.get("ret_60") for m in members])
        ret_5 = _median([m.get("ret_5") for m in members])
        # 领涨龙头：60日收益最高的成分股
        lead = max(members, key=lambda m: (m.get("ret_60") or -1e9))
        sectors.append({
            "name": ind,
            "ret_5": ret_5,
            "ret_20": ret_20,
            "ret_60": ret_60,
            "rs_4w": round(ret_20 - mkt_20, 2),    # 4周相对强度
            "rs_12w": round(ret_60 - mkt_60, 2),   # 12周相对强度
            "member_count": len(members),
            "leader": {"code": str(lead["symbol"]).zfill(6), "ret_60": lead.get("ret_60")},
        })

    # 按 12 周 RS 降序（主排序），4 周 RS 为次；正=强于大盘
    sectors.sort(key=lambda s: (s["rs_12w"], s["rs_4w"]), reverse=True)
    for i, s in enumerate(sectors):
        s["rank"] = i + 1
    leaders = [s["name"] for s in sectors[:3]]
    laggards = [s["name"] for s in sectors[-3:]] if len(sectors) >= 3 else []
    return {
        "market": {"ret_5": mkt_5, "ret_20": mkt_20, "ret_60": mkt_60},
        "sectors": sectors,
        "leaders": leaders,
        "laggards": laggards,
    }
