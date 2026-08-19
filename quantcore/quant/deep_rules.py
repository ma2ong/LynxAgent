"""深度分析的规则实现：算出来的证据卡，不生成叙述。

为什么不用模板凑一篇散文：
原来无密钥时的兜底会吐出「上游原材料/核心零部件」「行业供应商」这种占位文字 ——
看着像内容，其实什么也没说，比空着更糟。而 LLM 版有编造前科（会写出数据里根本不存在
的数字）。对一个立身之本是「不撒谎」的产品，一张不会编造的证据卡比一段漂亮但可能造假
的行文更值钱。

所以这里只做一件事：把已有的本地数据算成**同业对位**——这只票在它自己的行业里站在
什么位置。这是主页面没有、又完全可复算、且不可能编造的信息。

成本：中位行业 25 只成分股，本地日线读+算 0.8 秒量级；超大行业（最多 243 只）按成交额
截断到 PEER_CAP 只，避免一次请求拖几秒。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

PEER_CAP = 60  # 同业采样上限：128 个行业里只有个位数超过这个数，截断影响很小

_METRICS = [
    ("composite", "综合分"),
    ("trend", "趋势"),
    ("momentum", "动量"),
    ("capital_flow", "资金流"),
    ("risk_control", "风控"),
]


def _pct_rank(value: Optional[float], pool: List[float]) -> Optional[float]:
    """value 在 pool 里的百分位（0-100，越高越靠前）。样本不足返回 None。"""
    if value is None or len(pool) < 5:
        return None
    below = sum(1 for x in pool if x < value)
    return round(below / len(pool) * 100, 1)


def _median(xs: List[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _rank_word(p: Optional[float]) -> str:
    if p is None:
        return "样本不足"
    if p >= 80:
        return "行业前列"
    if p >= 60:
        return "偏上"
    if p >= 40:
        return "居中"
    if p >= 20:
        return "偏下"
    return "行业末段"


def build_peer_position(
    symbol: str,
    industry: str,
    self_factors: Dict[str, Any],
    self_composite: Optional[float],
    peer_factors: Dict[str, Dict[str, Any]],
    peer_composites: Dict[str, float],
) -> Dict[str, Any]:
    """同业对位。纯函数：给定同业的因子读数，算出这只票各维度的百分位。"""
    rows: List[Dict[str, Any]] = []
    for key, label in _METRICS:
        if key == "composite":
            mine = None if self_composite is None else float(self_composite)
            pool = [float(v) for v in peer_composites.values()]
        else:
            raw = self_factors.get(key)
            mine = None if raw is None else float(raw)
            pool = [float(f[key]) for f in peer_factors.values() if f.get(key) is not None]
        pct = _pct_rank(mine, pool)
        rows.append({
            "key": key,
            "label": label,
            "value": None if mine is None else round(mine, 1),
            "industry_median": None if not pool else round(_median(pool), 1),
            "percentile": pct,
            "verdict": _rank_word(pct),
        })

    ranked = [r for r in rows if r["percentile"] is not None]
    lead = [r["label"] for r in ranked if r["percentile"] >= 70]
    lag = [r["label"] for r in ranked if r["percentile"] <= 30]

    if not ranked:
        summary = f"{industry or '该行业'}本地样本不足，无法给出同业对位。"
    elif lead and lag:
        summary = (f"在 {industry} 的 {len(peer_composites)} 只同业里，"
                   f"{'、'.join(lead)}排在前列，{'、'.join(lag)}落在后段。")
    elif lead:
        summary = f"在 {industry} 的 {len(peer_composites)} 只同业里，{'、'.join(lead)}排在前列。"
    elif lag:
        summary = f"在 {industry} 的 {len(peer_composites)} 只同业里，{'、'.join(lag)}落在后段。"
    else:
        summary = f"在 {industry} 的 {len(peer_composites)} 只同业里各维度都在中间位置，没有明显长短板。"

    return {
        "industry": industry,
        "peer_count": len(peer_composites),
        "metrics": rows,
        "leading": lead,
        "lagging": lag,
        "summary": summary,
        "method": "rules",
    }


def build_watch_points(factors: Dict[str, Any], plan: Dict[str, Any], last_close: Optional[float]) -> List[Dict[str, str]]:
    """跟踪要点：把当前实际处在的位置写成可核对的条件，不写"建议怎么做"。

    每条都必须能被后续行情直接判定真假 —— 这是它和"操作建议"的区别。
    """
    points: List[Dict[str, str]] = []
    rsi = factors.get("rsi")
    if rsi is not None:
        if float(rsi) >= 75:
            points.append({"item": "RSI 位置", "state": f"当前 {float(rsi):.0f}，处于历史偏高区间"})
        elif float(rsi) <= 30:
            points.append({"item": "RSI 位置", "state": f"当前 {float(rsi):.0f}，处于历史偏低区间"})
    trend = factors.get("trend")
    if trend is not None:
        points.append({"item": "趋势结构",
                       "state": f"趋势分 {float(trend):.0f}"
                                f"（{'均线多头' if float(trend) >= 60 else '均线未成多头' if float(trend) >= 40 else '均线空头'}）"})
    flow = factors.get("capital_flow")
    if flow is not None:
        points.append({"item": "资金流向",
                       "state": f"资金流分 {float(flow):.0f}"
                                f"（{'净流入偏强' if float(flow) >= 60 else '中性' if float(flow) >= 40 else '净流出偏强'}）"})
    stop = plan.get("stop_loss")
    if stop and last_close:
        gap = (float(last_close) / float(stop) - 1) * 100
        points.append({"item": "距结构下沿", "state": f"现价高于 {float(stop):.2f} 约 {gap:.1f}%"})
    return points
