"""资金流向（同花顺源）：行业 / 概念 / 个股 主力净流入排行（即时快照）。

eastmoney push2 资金流主机本机不可达，改用同花顺 10jqka 接口（实测可达）。
行业/概念的「净额/流入/流出」单位为亿元数值；个股「净额」是带"万/亿"的字符串需解析。
失败统一降级 {empty, message}。
"""
from __future__ import annotations

from typing import Dict

from ._cache import cached

_EMPTY = {"empty": True, "message": "资金流数据拉取失败或为空，请稍后重试"}
_TTL = 300  # 盘中实时，5 分钟


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _pct(v) -> float:
    if isinstance(v, str):
        v = v.replace("%", "").strip()
    return round(_num(v), 2)


def _amount_to_yi(v) -> float:
    """同花顺金额字段：'1.74亿' / '-4349.01万' / 数值 → 亿元 float。"""
    if isinstance(v, (int, float)):
        return round(float(v) / 1e8, 2)
    s = str(v or "").strip().replace(",", "")
    if not s or s in ("-", "--"):
        return 0.0
    mult = 1.0
    if s.endswith("亿"):
        mult, s = 1e8, s[:-1]
    elif s.endswith("万"):
        mult, s = 1e4, s[:-1]
    try:
        return round(float(s) * mult / 1e8, 2)
    except ValueError:
        return 0.0


def _sector_rank(func: str, key: str) -> Dict[str, object]:
    def _compute():
        import akshare as ak
        try:
            df = getattr(ak, func)(symbol="即时")
        except Exception:
            return dict(_EMPTY)
        if df is None or df.empty:
            return dict(_EMPTY)
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "name": str(r.get("行业", "")),
                "net_yi": round(_num(r.get("净额")), 2),
                "pct": _pct(r.get("行业-涨跌幅")),
                "inflow_yi": round(_num(r.get("流入资金")), 2),
                "outflow_yi": round(_num(r.get("流出资金")), 2),
                "companies": int(_num(r.get("公司家数"))),
                "leader": str(r.get("领涨股", "")),
                "leader_pct": _pct(r.get("领涨股-涨跌幅")),
            })
        rows.sort(key=lambda x: x["net_yi"], reverse=True)
        return {"empty": False, "rows": rows}
    return cached(f"capital:{key}", _TTL, _compute)


def industry_fund_flow_rank() -> Dict[str, object]:
    return _sector_rank("stock_fund_flow_industry", "industry")


def concept_fund_flow_rank() -> Dict[str, object]:
    return _sector_rank("stock_fund_flow_concept", "concept")


def individual_fund_flow_rank(limit: int = 50) -> Dict[str, object]:
    def _compute():
        import akshare as ak
        try:
            df = ak.stock_fund_flow_individual(symbol="即时")
        except Exception:
            return dict(_EMPTY)
        if df is None or df.empty:
            return dict(_EMPTY)
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "symbol": str(r.get("股票代码", "")).zfill(6),
                "name": str(r.get("股票简称", "")),
                "price": _num(r.get("最新价")),
                "pct": _pct(r.get("涨跌幅")),
                "net_yi": _amount_to_yi(r.get("净额")),
            })
        rows.sort(key=lambda x: x["net_yi"], reverse=True)
        return {"empty": False, "rows": rows[:limit]}
    return cached(f"capital:individual:{limit}", _TTL, _compute)
