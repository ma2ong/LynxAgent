"""行业/个股热力图聚合（纯本地计算，零 LLM）。

输入 = 实时快照（或日线兜底伪快照）+ 代码->行业映射，输出 ECharts treemap 友好的
行业块/个股块列表。面积 = 总市值（亿，缺市值用成交额亿兜底），颜色 = 当日涨跌幅。
市值单位不一：腾讯快照是亿、东财/akshare 是元，>1e6 判定为元并 ÷1e8 归一。
"""
from __future__ import annotations

from typing import Dict, List, Optional

_UNMAPPED = "其他"


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _mv_yi(value) -> float:
    v = _num(value)
    if v <= 0:
        return 0.0
    return v / 1e8 if v > 1_000_000 else v


def _stock_item(symbol: str, q: Dict) -> Optional[Dict]:
    pct = q.get("pct_chg", q.get("change_percent"))
    if pct is None:
        return None
    mv = _mv_yi(q.get("total_mv"))
    amount_yi = max(0.0, _num(q.get("amount"))) / 1e8
    value = mv if mv > 0 else amount_yi
    if value <= 0:
        return None
    return {"symbol": symbol, "name": str(q.get("name") or symbol),
            "pct": round(float(pct), 2), "value": round(value, 2),
            "mv_yi": round(mv, 2), "amount_yi": round(amount_yi, 2)}


def build_heatmap_industry(snapshot: Dict[str, Dict], industry_map: Dict[str, str]) -> List[Dict]:
    """行业块列表：面积=行业总市值（亿），颜色=市值加权当日涨跌幅，按面积降序。

    不含「其他」（未归类）：行业源覆盖不全时，未归类桶会聚起数千只股票、市值总和碾压
    所有真实行业，把整张热力图变成一块灰色巨块。它不是一个行业，放进来只会误导——
    宁可只画已归类的行业，覆盖度由接口另行如实报告。
    """
    groups: Dict[str, List[Dict]] = {}
    for symbol, q in snapshot.items():
        item = _stock_item(symbol, q)
        if item is None:
            continue
        name = industry_map.get(symbol) or _UNMAPPED
        if name == _UNMAPPED:
            continue
        groups.setdefault(name, []).append(item)
    out: List[Dict] = []
    for name, items in groups.items():
        total = sum(i["value"] for i in items)
        if total <= 0:
            continue
        pct = sum(i["pct"] * i["value"] for i in items) / total
        out.append({"name": name, "count": len(items),
                    "value": round(total, 2), "pct": round(pct, 2),
                    "amount_yi": round(sum(i["amount_yi"] for i in items), 2)})
    out.sort(key=lambda x: x["value"], reverse=True)
    return out


def heatmap_coverage(snapshot: Dict[str, Dict], industry_map: Dict[str, str]) -> Dict[str, float]:
    """已归类 vs 未归类的诚实覆盖度：股票数 + 未归类市值占比。热力图副标题据此提示。"""
    classified = unclassified = 0
    mapped_val = unmapped_val = 0.0
    for symbol, q in snapshot.items():
        item = _stock_item(symbol, q)
        if item is None:
            continue
        if industry_map.get(symbol):
            classified += 1
            mapped_val += item["value"]
        else:
            unclassified += 1
            unmapped_val += item["value"]
    total_val = mapped_val + unmapped_val
    return {"classified": classified, "unclassified": unclassified,
            "unmapped_value_share": round(unmapped_val / total_val, 4) if total_val > 0 else 0.0}


def build_heatmap_stocks(snapshot: Dict[str, Dict], industry_map: Dict[str, str], industry: str) -> List[Dict]:
    """指定行业的个股块列表，按面积降序。"""
    out = [item for symbol, q in snapshot.items()
           if (industry_map.get(symbol) or _UNMAPPED) == industry
           and (item := _stock_item(symbol, q)) is not None]
    out.sort(key=lambda x: x["value"], reverse=True)
    return out
