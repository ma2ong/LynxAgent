"""风险预警：市场级仓位红绿灯 + 全市场个股卖出信号扫描。

诚实边界——所有信号都来自系统已算出的真实数据，不编造：
- 市场级：赚钱效应温度（逐日广度加权，见 regime）、连续走弱天数、跌停潮家数、
  广度骤降。四项合成 0-100 风险分 → 四档（安全/警惕/危险/极危）+ 明确仓位动作。
- 个股级：七不买里的「破位下行」（收盘同时跌破 MA10/MA20）与「问题股」（ST/退市/
  预亏），批量算出全市场当前命中卖出信号的票。破位口径与 risk_check 完全一致。

仓位动作是规则化提示，非投资建议——历史锚（回放偏冷期短线超额转负）一并给出，
让用户看到「为什么这个环境要减仓」的客观依据，而不是凭一句话。
"""
from __future__ import annotations

from typing import Dict, List, Optional

# 风险分 → 档位阈值（分越高越危险）
LEVELS = [
    (75.0, "极危", "清仓观望：停止一切买入，仓位降到 1 成以下或空仓，等赚钱效应回暖再说。"),
    (55.0, "危险", "大幅减仓：停止买入，仓位降到 3 成以下，只留最强主线，破位个股无条件了结。"),
    (35.0, "警惕", "控制仓位：新买谨慎、只打强势方向，仓位不超过 5 成，浮亏个股设好止损。"),
    (0.0, "安全", "正常参与：赚钱效应尚可，按纪律选股，单票仓位仍需控制。"),
]


def _level(score: float) -> tuple:
    for th, name, action in LEVELS:
        if score >= th:
            return name, action
    return LEVELS[-1][1], LEVELS[-1][2]


def market_risk_gauge(daily: List[Dict], temp: float,
                      limitdown_share: Optional[float] = None,
                      breakdown_share: Optional[float] = None,
                      cold_excess: Optional[float] = None) -> Dict[str, object]:
    """市场级风险仪表。

    daily: engine.recent_daily_breadth 输出（逐日 median_pct/breadth_up，最新在前）。
    temp:  regime 加权温度分（0-100，50 中性），已由 market_context 算好，直接复用同源口径。
    limitdown_share: 当日跌超 ~9% 的股票占比（快照口径），可空。
    breakdown_share: 全市场跌破 MA10&MA20 的股票占比（结构性下跌强度），可空。
    cold_excess: 回放偏冷期 T+5 平均超额（pp），作为历史锚，可空。
    """
    signals: List[Dict[str, object]] = []
    score = 0.0

    # 1) 赚钱效应温度：与横幅同源。温度越低风险越高（40 冷线以下线性加分）
    temp_risk = max(0.0, min(40.0, (50.0 - temp) * 1.3))
    signals.append({
        "key": "temp", "name": "赚钱效应温度", "value": round(temp, 1),
        "risk": round(temp_risk, 1),
        "detail": f"加权温度 {temp:.0f}（50 中性、40 以下偏冷）——短线信号胜率随温度下降",
    })
    score += temp_risk

    # 2) 破位广度：全市场多少比例跌破 MA10&MA20。单日反弹掩盖不了结构，60%+ 破位
    # 意味着「大多数票在下降趋势里」，正是前期热门股腰斩的那种系统性伤害
    if breakdown_share is not None:
        bd_risk = min(30.0, max(0.0, (breakdown_share - 0.35)) * 70.0)
        signals.append({
            "key": "breakdown", "name": "破位广度", "value": round(breakdown_share * 100, 1),
            "risk": round(bd_risk, 1),
            "detail": f"全市场 {breakdown_share*100:.0f}% 个股跌破 MA10&MA20——" +
                      ("多数票处下降趋势，反弹多是下跌中继，勿重仓抄底"
                       if breakdown_share >= 0.5 else "结构尚可"),
        })
        score += bd_risk

    # 3) 连续走弱：最近连续几天 中位<0 或 广度<0.45。急跌的持续性比单日更危险
    weak_streak = 0
    for d in daily:
        if float(d.get("median_pct", 0)) < 0 or float(d.get("breadth_up", 0)) < 0.45:
            weak_streak += 1
        else:
            break
    streak_risk = min(25.0, weak_streak * 8.0)
    signals.append({
        "key": "streak", "name": "连续走弱", "value": weak_streak,
        "risk": round(streak_risk, 1),
        "detail": f"最近连续 {weak_streak} 个交易日普跌/广度不足——趋势性下跌，别急着抄底" if weak_streak
                  else "近日未见连续普跌",
    })
    score += streak_risk

    # 4) 跌停潮：当日跌停/暴跌占比高 = 流动性踩踏，最强的清仓信号之一
    ld_risk = 0.0
    if limitdown_share is not None:
        ld_risk = min(25.0, limitdown_share * 100 * 5.0)  # 1% 跌停潮 ≈ 5 分，5% 封顶
        signals.append({
            "key": "limitdown", "name": "跌停潮", "value": round(limitdown_share * 100, 1),
            "risk": round(ld_risk, 1),
            "detail": f"当日约 {limitdown_share * 100:.1f}% 个股跌超 9%——" +
                      ("流动性踩踏，严禁抄底" if limitdown_share >= 0.03 else "尚属正常波动"),
        })
        score += ld_risk

    # 5) 广度骤降：当日广度 明显低于 近5日均值 = 加速恶化
    if len(daily) >= 3:
        today_b = float(daily[0].get("breadth_up", 0.5))
        avg_b = sum(float(d.get("breadth_up", 0.5)) for d in daily) / len(daily)
        drop = max(0.0, avg_b - today_b)
        drop_risk = min(10.0, drop * 60.0)
        if drop_risk > 1.0:
            signals.append({
                "key": "breadth_drop", "name": "广度骤降", "value": round(today_b * 100, 1),
                "risk": round(drop_risk, 1),
                "detail": f"当日上涨家数占比 {today_b*100:.0f}%，低于近5日均值 {avg_b*100:.0f}%——恶化在加速",
            })
            score += drop_risk

    score = round(min(100.0, score), 1)
    level, action = _level(score)

    anchor = ""
    if cold_excess is not None:
        anchor = (f"历史依据：回放显示偏冷环境下短线池 T+5 平均超额 "
                  f"{'+' if cold_excess >= 0 else ''}{cold_excess:.2f}pp"
                  + ("，弱市追涨大概率跑输大盘。" if cold_excess < 0.5 else "。"))

    return {
        "score": score,
        "level": level,
        "action": action,
        "signals": signals,
        "history_anchor": anchor,
        "disclaimer": "规则化风险提示，不构成投资建议；仓位动作请结合自身成本与纪律执行。",
    }


def scan_sell_signals(metrics: Dict[str, Dict[str, float]],
                      names: Dict[str, str],
                      bad_forecast: Optional[set] = None,
                      limit: int = 200) -> Dict[str, object]:
    """全市场卖出信号扫描（批量，非逐只 load_kline）。

    metrics: local_store.breakdown_metrics() 输出（close/ma10/ma20/pct/amount）。
    命中规则（与七不买同口径）：
    - 破位下行：close < MA10 且 close < MA20；
    - 问题股：名称含 ST/退 或 业绩预亏集内。
    严重度：破位 + 当日跌超板块半幅 = 高；破位 = 中；仅问题股 = 中。
    """
    bad_forecast = bad_forecast or set()
    hits: List[Dict[str, object]] = []
    breakdown_n = 0
    for symbol, m in metrics.items():
        name = str(names.get(symbol) or symbol)
        close = float(m.get("close", 0))
        ma10 = float(m.get("ma10", 0))
        ma20 = float(m.get("ma20", 0))
        pct = float(m.get("pct", 0))
        reasons: List[str] = []
        severity = 0

        broke = close > 0 and close < ma10 and close < ma20
        if broke:
            breakdown_n += 1
            reasons.append(f"破位下行：收盘 {close:.2f} 跌破 MA10({ma10:.2f})/MA20({ma20:.2f})")
            severity = 2 if pct <= -4.0 else 1

        trouble = ("ST" in name.upper() or "退" in name)
        if trouble:
            reasons.append("问题股：ST/退市风险")
            severity = max(severity, 1)
        elif symbol in bad_forecast:
            reasons.append("问题股：业绩预亏/预减")
            severity = max(severity, 1)

        if not reasons:
            continue
        hits.append({
            "symbol": symbol, "name": name, "pct": round(pct, 2),
            "close": round(close, 2),
            "severity": severity,
            "signal": "卖出" if severity >= 2 else "减仓/回避",
            "reason": "；".join(reasons),
            "amount_yi": round(float(m.get("amount", 0)) / 1e8, 2),
        })

    # 严重度优先，其次当日跌幅（跌得多的排前面），再按成交额（流动性大的更该关注）
    hits.sort(key=lambda h: (-h["severity"], h["pct"], -h["amount_yi"]))
    return {
        "total_flagged": len(hits),
        "breakdown_count": breakdown_n,
        "items": hits[: max(1, min(limit, 500))],
    }
