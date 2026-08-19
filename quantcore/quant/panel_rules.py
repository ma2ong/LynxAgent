"""五方判读的规则实现：五种因子偏好各自打分，不调 LLM。

为什么从 LLM 换成规则：
那五个「人格」本来就不是人格，是五种因子偏好——价值派看 ROE/增速/利润率，趋势派看
均线与动量，游资派看弹性与量能，逆向派看超跌与位置，量化派看综合分。这些数字产品里
全都有，让模型再「扮演」一遍，等于把已知的数字绕一圈变成不可复现的文本。

换掉之后白拿三件事：
1. 可复现 —— LLM 每次跑结果都不同，experiments/panel_eval.py 想验证「共识分高的票
   后续是不是真的更好」，非确定性输入根本没法做这个实验；
2. 可回测 —— 能拉到长样本上跑，而不是只有 30 天 941 条；
3. 零成本、瞬时 —— 后台每天 31 次调用归零。

「分歧度」的含义也变实在了：从「模型模拟出来的意见不合」变成「不同因子族之间真实的
读数冲突」，那是个可度量、可检验的量。

刻意不做的事：不给任何一派加"经验权重"去拟合收益。这里只做口径转换（把因子读数翻译
成各派的分数），有没有预测力交给 panel_eval 去裁决 —— 先调参再检验，检验就没意义了。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# 与 investor_panel._PERSONAS 保持同名同 style，前端配色/分组不用改。
PERSONAS = [
    {"persona": "价值派", "style": "value", "desc": "看盈利质量与增速：ROE、净利率、营收与净利同比"},
    {"persona": "趋势派", "style": "trend", "desc": "看趋势与动量：均线结构、MACD、动量强弱"},
    {"persona": "游资派", "style": "hot_money", "desc": "看弹性与资金：短期涨幅、资金流、成交活跃度"},
    {"persona": "逆向派", "style": "contrarian", "desc": "看位置与超跌：RSI 高低、距离前高的回撤幅度"},
    {"persona": "量化派", "style": "quant", "desc": "看多因子综合分，不偏任何单一维度"},
]

_NEUTRAL = 50.0


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # 过滤 NaN


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, v))


def _band(value: Optional[float], lo: float, hi: float) -> Optional[float]:
    """把一个原始指标线性映射到 0-100。低于 lo 给 0，高于 hi 给 100。"""
    v = _num(value)
    if v is None or hi <= lo:
        return None
    return _clamp((v - lo) / (hi - lo) * 100.0)


def _avg(parts: List[Optional[float]]) -> Optional[float]:
    got = [p for p in parts if p is not None]
    return sum(got) / len(got) if got else None


def _stance(score: float) -> str:
    if score >= 62:
        return "看多"
    if score <= 38:
        return "看空"
    return "中性"


def _fmt(v: Optional[float], unit: str = "") -> str:
    return "—" if v is None else f"{v:g}{unit}"


def _value_view(fund: Dict[str, Any]) -> tuple[Optional[float], str]:
    """价值派：盈利质量与增速。ROE 与净利率看水平，两个同比看方向。"""
    roe = _band(fund.get("roe"), 0, 20)              # 0% → 0 分，20% → 满分
    margin = _band(fund.get("net_margin"), 0, 25)
    rev = _band(fund.get("revenue_yoy"), -20, 40)     # 同比 -20% → 0，+40% → 满分
    profit = _band(fund.get("net_profit_yoy"), -30, 60)
    score = _avg([roe, margin, rev, profit])
    if score is None:
        return None, "缺少可用的财务数据"
    reason = (f"ROE {_fmt(_num(fund.get('roe')), '%')}、净利率 {_fmt(_num(fund.get('net_margin')), '%')}；"
              f"营收同比 {_fmt(_num(fund.get('revenue_yoy')), '%')}、净利同比 {_fmt(_num(fund.get('net_profit_yoy')), '%')}")
    return score, reason


def _trend_view(f: Dict[str, Any]) -> tuple[Optional[float], str]:
    """趋势派：均线结构 + MACD + 动量，三项等权。"""
    score = _avg([_num(f.get("trend")), _num(f.get("macd")), _num(f.get("momentum"))])
    if score is None:
        return None, "缺少足够的日线数据"
    return score, (f"趋势 {_fmt(_num(f.get('trend')))}、MACD {_fmt(_num(f.get('macd')))}、"
                   f"动量 {_fmt(_num(f.get('momentum')))}")


def _hot_money_view(f: Dict[str, Any], chg60: Optional[float]) -> tuple[Optional[float], str]:
    """游资派：资金流 + 活跃度 + 近 60 日弹性。厌恶滞涨，所以涨幅越大给分越高。"""
    elasticity = _band(chg60, -20, 60)
    score = _avg([_num(f.get("capital_flow")), _num(f.get("liquidity")), elasticity])
    if score is None:
        return None, "缺少资金与成交数据"
    return score, (f"资金流 {_fmt(_num(f.get('capital_flow')))}、活跃度 {_fmt(_num(f.get('liquidity')))}；"
                   f"近 60 日 {_fmt(chg60, '%')}")


def _contrarian_view(f: Dict[str, Any], chg60: Optional[float]) -> tuple[Optional[float], str]:
    """逆向派：越超跌越喜欢。RSI 与近 60 日涨幅都取反向。"""
    rsi = _num(f.get("rsi"))
    rsi_inv = None if rsi is None else _clamp(100.0 - rsi)
    drawdown_pref = _band(chg60, 40, -30)  # 注意 lo>hi：涨得越多分越低
    if drawdown_pref is None and chg60 is not None:
        drawdown_pref = _clamp((40.0 - chg60) / 70.0 * 100.0)
    score = _avg([rsi_inv, drawdown_pref])
    if score is None:
        return None, "缺少位置与涨跌幅数据"
    return score, f"RSI {_fmt(rsi)}（越低越合口味）；近 60 日 {_fmt(chg60, '%')}"


def _quant_view(composite: Optional[float], f: Dict[str, Any]) -> tuple[Optional[float], str]:
    """量化派：直接用多因子综合分，缺失时退回可得因子的均值。"""
    score = _num(composite)
    if score is None:
        score = _avg([_num(f.get(k)) for k in
                      ("trend", "momentum", "rsi", "risk_control", "liquidity", "macd", "bollinger", "capital_flow")])
    if score is None:
        return None, "综合分不可用"
    return score, f"多因子综合 {_fmt(score)}；风控 {_fmt(_num(f.get('risk_control')))}"


def score_panel(
    factors: Dict[str, Any],
    fundamentals: Dict[str, Any],
    composite: Optional[float],
    chg60: Optional[float],
) -> Dict[str, Any]:
    """五派各自打分并聚合。纯函数，同样的输入永远得到同样的输出。"""
    factors = factors or {}
    fundamentals = fundamentals or {}

    raw = [
        ("价值派", "value", *_value_view(fundamentals)),
        ("趋势派", "trend", *_trend_view(factors)),
        ("游资派", "hot_money", *_hot_money_view(factors, chg60)),
        ("逆向派", "contrarian", *_contrarian_view(factors, chg60)),
        ("量化派", "quant", *_quant_view(composite, factors)),
    ]

    verdicts: List[Dict[str, Any]] = []
    for persona, style, score, reason in raw:
        # 某一派的数据缺了就按中性 50 记，并在理由里说明 —— 不静默当 0，
        # 否则一只缺财务的票会被价值派拖成"看空"，那是数据缺失不是判断。
        final = _NEUTRAL if score is None else round(score, 1)
        verdicts.append({
            "persona": persona,
            "style": style,
            "score": final,
            "stance": _stance(final),
            "reason": reason,
            "available": score is not None,
        })

    scores = [v["score"] for v in verdicts]
    consensus = round(sum(scores) / len(scores), 1)
    divergence = round(max(scores) - min(scores), 1)
    bull = sum(1 for v in verdicts if v["stance"] == "看多")
    bear = sum(1 for v in verdicts if v["stance"] == "看空")
    missing = [v["persona"] for v in verdicts if not v["available"]]

    summary = (
        f"{'分歧较大' if divergence >= 30 else '意见接近'}"
        f"（共识 {consensus} 分，极差 {divergence} 分），看多 {bull}/看空 {bear}。"
    )
    if missing:
        summary += f" {'、'.join(missing)}因数据缺失按中性计。"

    return {
        "verdicts": verdicts,
        "consensus": consensus,
        "divergence": divergence,
        "bull": bull,
        "bear": bear,
        "summary": summary,
        "method": "rules",
    }
