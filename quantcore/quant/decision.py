"""个股避雷决策：多角度综合 → 分档买卖建议。

在「七不买」硬规则(risk_check)之上，再叠加趋势/量能/资金/盘面四个角度，综合成一个分档结论
（结构完整 / 结构偏强 / 方向不明 / 结构存疑 / 风险项偏多 / 风险项密集 / 结构偏弱）与一句话状态描述。

数据口径诚实：
- 「资金」是价量派生的资金流代理（成交额 × 价格方向 + MFI/CMF），非真·主力净流入（无可靠数据源）；
- 「情绪」是大盘层面的情绪/资金温度（个股级情绪数据太薄，不硬编）；
- 输出一律附免责——规则化提示，不构成投资建议。
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from .factors import compute_factor_scores
from .risk_check import check_risks


def _state(score: float, hi: str, mid: str, lo: str, hi_th=62.0, lo_th=42.0) -> str:
    return hi if score >= hi_th else (lo if score < lo_th else mid)


def stock_decision(symbol: str, name: str, df: pd.DataFrame,
                   quote: Optional[Dict] = None,
                   market_env: str = "",
                   bad_forecast: bool = False,
                   market_temp: Optional[float] = None) -> Dict[str, object]:
    """多角度避雷决策。df 需含 OHLCV 日线（升序）。返回角度评分卡 + 分档建议。"""
    quote = quote or {}
    risk = check_risks(symbol, name, df, quote=quote, bad_forecast=bad_forecast)
    risk_count = int(risk["risk_count"])
    has_breakdown = any(f["key"] == "breakdown" for f in risk["flags"])

    try:
        factors = compute_factor_scores(df)
    except Exception:
        factors = {}
    trend = float(factors.get("trend", 50.0))
    macd = float(factors.get("macd", 50.0))
    momentum = float(factors.get("momentum", 50.0))
    liquidity = float(factors.get("liquidity", 50.0))
    capital = float(factors.get("capital_flow", 50.0))

    # 盘面/情绪（大盘）：优先用连续温度分。三档硬映射会把「刚企稳的偏冷」和「跌停潮」
    # 打成同一个 32 分，昨日反弹在个股评分里彻底消失；有 temp 就直接用。
    env = str(market_env or "")
    if market_temp is not None:
        market_score = max(0.0, min(100.0, float(market_temp)))
    elif any(k in env for k in ("暖", "强", "活跃")):
        market_score = 70.0
    elif any(k in env for k in ("冷", "弱", "谨慎")):
        market_score = 32.0
    else:
        market_score = 50.0

    # 五角度评分（0-100）
    risk_score = max(0.0, 100.0 - risk_count * 40.0)
    trend_angle = round(trend * 0.6 + macd * 0.4, 1)
    volume_angle = round(liquidity, 1)
    capital_angle = round(capital, 1)

    vr = float(quote.get("volume_ratio") or 0)
    vol_note = "量能温和"
    if vr >= 3:
        vol_note = f"放量（量比 {vr:.1f}），需辨别是抢筹还是出逃"
    elif 0 < vr < 0.7:
        vol_note = f"缩量（量比 {vr:.1f}），关注度不足"

    angles: List[Dict[str, object]] = [
        {"key": "risk", "label": "七不买风险", "score": round(risk_score, 1),
         "state": "无风险" if risk_count == 0 else ("单项风险" if risk_count == 1 else f"{risk_count}项风险"),
         "note": risk["advice"].split("（")[0]},
        {"key": "trend", "label": "趋势均线", "score": trend_angle,
         "state": _state(trend_angle, "多头向上", "方向不明", "空头向下"),
         "note": "站上关键均线、MACD 向上" if trend_angle >= 62 else ("均线纠缠、方向待定" if trend_angle >= 42 else "跌破均线、趋势转弱")},
        {"key": "volume", "label": "量能", "score": volume_angle,
         "state": _state(volume_angle, "活跃", "一般", "清淡"), "note": vol_note},
        {"key": "capital", "label": "资金(价量代理)", "score": capital_angle,
         "state": _state(capital_angle, "净流入", "中性", "净流出"),
         "note": "成交额随价上行，资金偏流入" if capital_angle >= 62 else ("资金进出均衡" if capital_angle >= 42 else "价跌量出，资金偏流出")},
        # 阈值用 regime 的 60/40，与顶部大盘横幅同源——同一个市场不能在两处显示不同冷暖
        {"key": "market", "label": "盘面/情绪(大盘)", "score": round(market_score, 1),
         "state": _state(market_score, "偏暖", "中性", "偏冷", hi_th=60.0, lo_th=40.0),
         "note": f"当前全市场赚钱效应：{env or '未知'}（个股中位+上涨广度口径，与指数涨跌可能背离）"},
    ]

    # 综合分（不含风险项——风险是硬否决）：趋势/资金/量能/盘面加权
    composite = round(trend_angle * 0.34 + capital_angle * 0.24
                      + volume_angle * 0.18 + market_score * 0.24, 1)

    # 分档：风险硬否决优先，其次看综合分。
    # 描述这只票当前处于什么状态，不写「该买/该卖/该减仓」——同样的分档、同样的阈值，
    # 只是把结论说成客观状态而不是操作指令。
    if risk_count >= 3 or (risk_count >= 2 and has_breakdown):
        level, stance = "风险项密集", f"同时命中 {risk_count} 项风险条件，是本体系里信号最集中的一档"
    elif risk_count == 2:
        level, stance = "风险项偏多", "命中 2 项风险条件，且趋势与资金面均未转好"
    elif risk_count == 1:
        # 命中 1 项风险：封顶到「结构存疑」，不给最高档
        level, stance = "结构存疑", f"命中「{next(f['name'] for f in risk['flags'] if f['level']=='risk')}」，其余维度尚可"
    elif composite >= 70:
        level, stance = "结构完整", "趋势、资金、量能三项同向，且未命中风险条件"
    elif composite >= 56:
        level, stance = "结构偏强", "多数维度偏正，但尚未形成合力"
    elif composite >= 44:
        level, stance = "方向不明", "各维度分歧，没有一致方向"
    else:
        level, stance = "结构偏弱", "多数维度偏负"

    return {
        "symbol": str(symbol).zfill(6),
        "name": name,
        "composite": composite,
        "verdict": {"level": level, "stance": stance},
        "angles": angles,
        # 保留七不买原始输出，向后兼容旧「体检卡」
        "flags": risk["flags"],
        "risk_count": risk_count,
        "advice": risk["advice"],
        "market_env": env,
        "disclaimer": "多角度规则化综合，不构成投资建议；资金为价量代理、情绪为大盘口径。",
    }
