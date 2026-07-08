"""聪明钱（A股语境）：龙虎榜活跃席位榜 / 席位胜率排行 / 基金共识重仓。

数据源 akshare（东财 datacenter），实测可达（2026-07-07 验证）；北向持股 2024-08 起
停止每日披露，砍掉。取数失败统一降级 {empty, message}；纯 DataFrame 变换拆成
_agg/_shape 函数便于测试。6h 缓存（每日收盘后才更新）。
"""
from __future__ import annotations

from datetime import date as _date, timedelta
from typing import Dict, List

import pandas as pd

from ._cache import cached

_EMPTY = {"empty": True, "message": "聪明钱数据拉取失败或为空"}
_TTL = 6 * 3600


def _f(v, default=0.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return round(x, 2) if x == x else default  # x != x 判 NaN，否则破坏 JSON 序列化


def _agg_active_seats(df: pd.DataFrame, top: int = 50) -> List[Dict]:
    """活跃营业部按席位聚合：上榜次数/买卖总额/净买额/最近上榜/买过的票。"""
    rows: Dict[str, Dict] = {}
    for _, r in df.iterrows():
        seat = str(r.get("营业部名称") or "").strip()
        if not seat:
            continue
        item = rows.setdefault(seat, {"seat": seat, "count": 0, "buy_yi": 0.0,
                                      "sell_yi": 0.0, "net_yi": 0.0, "last_date": "", "stocks": []})
        item["count"] += 1
        buy, sell = _f(r.get("买入总金额")), _f(r.get("卖出总金额"))
        item["buy_yi"] += buy / 1e8
        item["sell_yi"] += sell / 1e8
        item["net_yi"] += (buy - sell) / 1e8
        d = str(r.get("上榜日") or "")[:10]
        if d > item["last_date"]:
            item["last_date"] = d
        for s in str(r.get("买入股票") or "").split():
            if s and s not in item["stocks"]:
                item["stocks"].append(s)
    out = []
    for item in rows.values():
        out.append({**item, "buy_yi": round(item["buy_yi"], 2), "sell_yi": round(item["sell_yi"], 2),
                    "net_yi": round(item["net_yi"], 2), "stocks": " ".join(item["stocks"][:12])})
    out.sort(key=lambda x: x["net_yi"], reverse=True)
    return out[:top]


def _shape_seat_winrate(df: pd.DataFrame, min_trades: int = 5, top: int = 50) -> List[Dict]:
    """营业部排行：取 5 日口径的平均涨幅/上涨概率（外加 1 日参考），样本太少的过滤。"""
    out = []
    for _, r in df.iterrows():
        seat = str(r.get("营业部名称") or "").strip()
        trades = int(_f(r.get("上榜后5天-买入次数")))
        if not seat or trades < min_trades:
            continue
        out.append({"seat": seat, "trades_5d": trades,
                    "avg_chg_5d": _f(r.get("上榜后5天-平均涨幅")),
                    "win_rate_5d": _f(r.get("上榜后5天-上涨概率")),
                    "avg_chg_1d": _f(r.get("上榜后1天-平均涨幅")),
                    "win_rate_1d": _f(r.get("上榜后1天-上涨概率"))})
    out.sort(key=lambda x: (x["win_rate_5d"], x["trades_5d"]), reverse=True)
    return out[:top]


def _shape_fund_hold(df: pd.DataFrame, top: int = 100) -> List[Dict]:
    """基金重仓：按持股市值降序。"""
    out = []
    for _, r in df.iterrows():
        out.append({"symbol": str(r.get("股票代码") or "").zfill(6),
                    "name": str(r.get("股票简称") or ""),
                    "funds": int(_f(r.get("持有基金家数"))),
                    "mv_yi": round(_f(r.get("持股市值")) / 1e8, 2),
                    "change": str(r.get("持股变化") or ""),
                    "change_pct": _f(r.get("持股变动比例"))})
    out.sort(key=lambda x: x["mv_yi"], reverse=True)
    return out[:top]


def active_seats(days: int = 30) -> Dict[str, object]:
    def _compute():
        import akshare as ak
        end = _date.today()
        start = end - timedelta(days=days)
        try:
            df = ak.stock_lhb_hyyyb_em(start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))
        except Exception:
            return {**_EMPTY, "rows": []}
        if df is None or df.empty:
            return {**_EMPTY, "rows": []}
        return {"empty": False, "days": days, "rows": _agg_active_seats(df)}
    return cached(f"sm:seats:{days}", _TTL, _compute)


def seat_winrate() -> Dict[str, object]:
    def _compute():
        import akshare as ak
        try:
            df = ak.stock_lhb_yybph_em(symbol="近一月")
        except Exception:
            return {**_EMPTY, "rows": []}
        if df is None or df.empty:
            return {**_EMPTY, "rows": []}
        return {"empty": False, "window": "近一月", "rows": _shape_seat_winrate(df)}
    return cached("sm:winrate", _TTL, _compute)


def _recent_quarter_ends(n: int = 4) -> List[str]:
    today = _date.today()
    ends, y = [], today.year
    while len(ends) < n:
        for m, d in ((12, 31), (9, 30), (6, 30), (3, 31)):
            q = _date(y, m, d)
            if q < today:
                ends.append(q.strftime("%Y%m%d"))
                if len(ends) >= n:
                    break
        y -= 1
    return ends


def fund_consensus() -> Dict[str, object]:
    def _compute():
        import akshare as ak
        # 季度末刚过时该季报尚未披露完整（往往只有个位数行），阈值 100 行才认，否则回退上一季
        for quarter in _recent_quarter_ends():
            try:
                df = ak.stock_report_fund_hold(symbol="基金持仓", date=quarter)
            except Exception:
                continue
            if df is not None and len(df) >= 100:
                return {"empty": False, "quarter": quarter, "rows": _shape_fund_hold(df)}
        return {**_EMPTY, "rows": []}
    return cached("sm:fund", _TTL, _compute)
