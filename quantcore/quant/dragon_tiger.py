"""龙虎榜：当日榜单 + 个股席位明细（eastmoney datacenter，实测可达）。

榜单 stock_lhb_detail_em(start_date, end_date)；席位 stock_lhb_stock_detail_em(symbol, date, flag)。
金额单位元，转亿展示。失败统一降级 {empty, message}。
"""
from __future__ import annotations

from datetime import date as _date
from typing import Dict

import pandas as pd

from ._cache import cached

_EMPTY = {"empty": True, "message": "龙虎榜数据拉取失败或为空"}
_TTL = 6 * 3600  # 每日收盘后才更新


def _yi(v) -> float:
    try:
        return round(float(v) / 1e8, 2)
    except (TypeError, ValueError):
        return 0.0


def _f(v) -> float:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return 0.0


def _ymd(d: str) -> str:
    return (d or _date.today().isoformat()).replace("-", "")


def dragon_tiger_list(date: str = "") -> Dict[str, object]:
    d = date or _date.today().isoformat()

    def _compute():
        import akshare as ak
        ymd = _ymd(d)
        try:
            df = ak.stock_lhb_detail_em(start_date=ymd, end_date=ymd)
        except Exception:
            return {**_EMPTY, "date": d, "rows": []}
        if df is None or df.empty:
            return {"empty": True, "message": f"{d} 无龙虎榜数据（非交易日或未更新）",
                    "date": d, "rows": []}
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "symbol": str(r.get("代码", "")).zfill(6),
                "name": str(r.get("名称", "")),
                "pct": _f(r.get("涨跌幅")),
                "net_buy_yi": _yi(r.get("龙虎榜净买额")),
                "reason": str(r.get("上榜原因", "")),
                "interpret": str(r.get("解读", "")),
            })
        rows.sort(key=lambda x: x["net_buy_yi"], reverse=True)
        return {"empty": False, "date": d, "rows": rows}

    return cached(f"lhb:list:{d}", _TTL, _compute)


def dragon_tiger_seats(symbol: str, date: str = "") -> Dict[str, object]:
    symbol = str(symbol).zfill(6)
    d = date or _date.today().isoformat()

    def _compute():
        import akshare as ak
        ymd = _ymd(d)

        def _side(flag: str):
            try:
                df = ak.stock_lhb_stock_detail_em(symbol=symbol, date=ymd, flag=flag)
            except Exception:
                return []
            if df is None or df.empty:
                return []
            out = []
            for _, r in df.iterrows():
                out.append({
                    "name": str(r.get("交易营业部名称", "")),
                    "buy_yi": _yi(r.get("买入金额")),
                    "sell_yi": _yi(r.get("卖出金额")),
                })
            return out

        buy, sell = _side("买入"), _side("卖出")
        if not buy and not sell:
            return {**_EMPTY, "symbol": symbol, "date": d, "buy": [], "sell": []}
        return {"empty": False, "symbol": symbol, "date": d, "buy": buy, "sell": sell}

    return cached(f"lhb:seats:{symbol}:{d}", _TTL, _compute)
