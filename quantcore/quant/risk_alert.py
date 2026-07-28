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
    (75.0, "极危", "极低仓位观察：停止新增买入，优先保留现金；持仓仅在基本面硬风险或趋势、资金、成交量等多维度确认恶化时退出，不因单一破位一刀切。"),
    (55.0, "危险", "大幅减仓：停止新增买入，总仓位降到 3 成以下；持仓逐只复核，单一均线破位先观察反包与修复，多维风险共振后再执行退出。"),
    (35.0, "警惕", "控制仓位：新买谨慎、只打强势方向，仓位不超过 5 成；浮亏个股结合趋势、资金、成交量与基本面综合复核。"),
    (0.0, "安全", "正常参与：赚钱效应尚可，按纪律选股，单票仓位仍需控制。"),
]


def _level(score: float) -> tuple:
    for th, name, action in LEVELS:
        if score >= th:
            return name, action
    return LEVELS[-1][1], LEVELS[-1][2]


# ---- 高位风险名单的准入门槛 ----
# 这份名单要回答的是一个很具体的问题：**好票涨过头了，该不该走。**
# 2026 年 6 月的 MLCC / 存储 / PCB / 芯片就是样板——基本面没问题、方向也对，
# 但两个月翻了一倍，7 月起连跌一个月没反转。这种票值得单独拎出来提示，
# 而 ST、退市、预亏这些本来就不该碰的垃圾股不在此列，混进来只会稀释名单。
#
# 所以准入是「且」的关系，宁可漏也不放宽：涨得够多、确实见了顶、趋势真的坏了、
# 而且有真实成交量能撑得起卖出动作。任何一条不满足就不进这份名单。
HIGH_POS_MIN_RUNUP = 80.0        # 区间低点→高点涨幅（%）：奔着「翻倍级」去，不收普通上涨
HIGH_POS_MIN_DRAWDOWN = 12.0     # 距区间高点回撤（%）：低于此仍算正常波动，不算见顶
HIGH_POS_MIN_AMOUNT = 2.0e8      # 当日成交额：小票破位噪音大也卖不掉，要能真正走得掉
HIGH_POS_MIN_CONFIRM = 3         # 见顶确认信号条数：两条容易误报，三条才算趋势确实坏了
HIGH_POS_FRESH_BARS = 12         # 距高点多少个交易日内算「刚见顶」——提示的黄金窗口
HIGH_POS_DEEP_DRAWDOWN = 35.0    # 回撤超过此值视为「已深跌」，动作从催卖改为别抄底


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
                      limit: int = 200,
                      realtime_quotes: Optional[Dict[str, dict]] = None,
                      fundamental_flags: Optional[Dict[str, dict]] = None) -> Dict[str, object]:
    """多因子持仓风险复核；单一均线破位永不直接触发退出建议。"""
    import statistics

    bad_forecast = bad_forecast or set()
    realtime_quotes = realtime_quotes or {}
    fundamental_flags = fundamental_flags or {}
    hits: List[Dict[str, object]] = []
    layer_counts = {
        "new_breakdown": 0,
        "confirmed_breakdown": 0,
        "persistent_weakness": 0,
        "trouble": 0,
    }

    market_pcts = [float(metric.get("pct", 0)) for metric in metrics.values()]
    market_median = statistics.median(market_pcts) if market_pcts else 0.0
    market_up_share = (
        sum(1 for value in market_pcts if value > 0) / len(market_pcts)
        if market_pcts else 0.0
    )
    breakdown_n = sum(
        1 for metric in metrics.values()
        if float(metric.get("close", 0)) > 0
        and float(metric.get("close", 0)) < float(metric.get("ma10", 0))
        and float(metric.get("close", 0)) < float(metric.get("ma20", 0))
    )
    breakdown_share = breakdown_n / len(metrics) if metrics else 0.0
    broad_retreat = breakdown_share >= 0.5

    for symbol, metric in metrics.items():
        name = str(names.get(symbol) or symbol)
        close = float(metric.get("close", 0))
        ma10 = float(metric.get("ma10", 0))
        ma20 = float(metric.get("ma20", 0))
        ma60 = float(metric.get("ma60", 0))
        prev_close = float(metric.get("prev_close", 0))
        prev_ma10 = float(metric.get("prev_ma10", 0))
        prev_ma20 = float(metric.get("prev_ma20", 0))
        pct = float(metric.get("pct", 0))
        amount_ratio = float(metric.get("amount_ratio", 0))
        capital_flow = float(metric.get("capital_flow_5d", 0))
        return_20d = float(metric.get("return_20d", 0))
        close_position = float(metric.get("close_position", 0.5))
        lower_shadow = float(metric.get("lower_shadow", 0))
        consecutive_down = int(metric.get("consecutive_down", 0))
        relative_pct = pct - market_median
        risk_points = 0
        protection_points = 0
        risk_dimensions: set[str] = set()
        risk_factors: List[str] = []
        protect_factors: List[str] = []
        context_factors: List[str] = []
        layer = ""

        def add_risk(dimension: str, points: int, text: str) -> None:
            nonlocal risk_points
            risk_points += points
            risk_dimensions.add(dimension)
            risk_factors.append(text)

        def add_protection(points: int, text: str) -> None:
            nonlocal protection_points
            protection_points += points
            protect_factors.append(text)

        broke = close > 0 and close < ma10 and close < ma20
        prev_broke = (
            prev_close > 0 and prev_ma10 > 0 and prev_ma20 > 0
            and prev_close < prev_ma10 and prev_close < prev_ma20
        )
        if broke:
            layer = (
                "new_breakdown" if not prev_broke
                else "confirmed_breakdown" if pct <= -2.0 or amount_ratio >= 1.3
                else "persistent_weakness"
            )
            layer_counts[layer] += 1
            add_risk(
                "trend", 1,
                f"趋势：收盘 {close:.2f} 低于 MA10({ma10:.2f})/MA20({ma20:.2f})，仅作为弱证据"
            )
            if ma60 > 0 and close < ma60:
                add_risk("trend", 2, f"中期趋势：同时跌破 MA60({ma60:.2f})")
            elif ma60 > 0:
                add_protection(1, f"中期结构仍在 MA60({ma60:.2f}) 上方")
            depth_to_ma20 = (close / ma20 - 1) * 100 if ma20 > 0 else 0.0
            if depth_to_ma20 <= -5:
                add_risk("trend", 1, f"偏离：低于 MA20 {abs(depth_to_ma20):.1f}%")
            if return_20d <= -15:
                add_risk("trend", 1, f"近20日累计下跌 {abs(return_20d):.1f}%")
            elif return_20d >= 8 and ma60 > 0 and close >= ma60:
                add_protection(1, f"近20日仍上涨 {return_20d:.1f}%，中期强势结构未破")

        flag = fundamental_flags.get(symbol) or {}
        trouble_name = "ST" in name.upper() or "退" in name
        bad_fundamental = trouble_name or symbol in bad_forecast or bool(flag.get("bad_forecast"))
        if trouble_name:
            add_risk("fundamental", 6, "基本面硬风险：ST/退市风险标记")
        elif bad_fundamental:
            detail = str(flag.get("forecast_type") or "业绩预亏/预减")
            change = str(flag.get("change") or "").strip()
            add_risk("fundamental", 5, f"基本面硬风险：{detail}{f'（{change}）' if change else ''}")
        else:
            context_factors.append("未命中ST/退市或业绩预亏预减标签，不代表基本面已全面验证")

        if bad_fundamental:
            if layer:
                layer_counts[layer] -= 1
            layer = "trouble"
            layer_counts["trouble"] += 1

        if not broke and not bad_fundamental:
            continue

        if pct < 0 and amount_ratio >= 1.5:
            add_risk("volume", 2, f"量能：下跌同时成交额放大至20日均值 {amount_ratio:.1f} 倍")
        elif broke and 0 < amount_ratio <= 0.9:
            add_protection(1, f"量能：破位缩量，仅为20日均额 {amount_ratio:.1f} 倍")

        if capital_flow <= -25:
            add_risk("capital", 2, f"价量资金代理：近5日下跌日成交占优（{capital_flow:.0f}）")
        elif capital_flow >= 15:
            add_protection(1, f"价量资金代理：近5日上涨日成交占优（+{capital_flow:.0f}）")
        else:
            context_factors.append(f"价量资金代理近5日中性（{capital_flow:+.0f}）")

        if relative_pct <= -3:
            add_risk(
                "relative", 1,
                f"相对市场：跑输全市场中位涨幅 {abs(relative_pct):.1f} 个百分点"
            )
        elif relative_pct >= 1:
            add_protection(1, f"相对市场：跑赢全市场中位涨幅 {relative_pct:.1f} 个百分点")

        if pct <= -3 and close_position <= 0.25:
            add_risk("price_action", 1, "K线：大跌且收在当日振幅下沿")
        if lower_shadow >= 0.35:
            add_protection(1, "K线：长下影显示盘中承接")
        if consecutive_down >= 3:
            add_risk("price_action", 1, f"连续性：已连续下跌 {consecutive_down} 个交易日")

        if broad_retreat:
            context_factors.append(
                f"市场背景：全市场 {breakdown_share:.0%} 个股处于双均线下方，单只破位区分度下降"
            )
            if relative_pct >= -1.5:
                add_protection(1, "退潮期与市场同步走弱，暂未出现明显独立弱势")
        else:
            context_factors.append(
                f"市场背景：全市场中位涨幅 {market_median:+.2f}%，上涨占比 {market_up_share:.0%}"
            )

        quote = realtime_quotes.get(symbol) or {}
        current_price = float(
            quote.get("price") or quote.get("current_price") or quote.get("close") or 0
        )
        current_pct = float(
            quote.get("change_percent")
            if quote.get("change_percent") is not None
            else quote.get("pct_chg") or 0
        )
        realtime_recovery = False
        if current_price > 0:
            if current_price >= ma10 and current_price >= ma20:
                realtime_recovery = True
                add_protection(
                    4,
                    f"实时反包：现价 {current_price:.2f}（{current_pct:+.2f}%）已收复 MA10/MA20"
                )
            elif current_pct >= 3:
                add_protection(2, f"实时修复：现价上涨 {current_pct:.2f}%，等待收复均线确认")
            elif current_pct <= -3 and current_price < ma10 and current_price < ma20:
                add_risk("realtime", 2, f"实时确认：继续下跌 {abs(current_pct):.2f}% 且仍在双均线下方")

        net_points = max(0, risk_points - protection_points)
        dimension_count = len(risk_dimensions)
        if realtime_recovery and not bad_fundamental:
            signal, severity = "反包观察", 1
        elif bad_fundamental and broke and risk_points >= 7 and dimension_count >= 3:
            signal, severity = "退出/止损", 3
        elif net_points >= 9 and dimension_count >= 4:
            signal, severity = "退出/止损", 3
        elif (bad_fundamental and net_points >= 4) or (net_points >= 6 and dimension_count >= 3):
            signal, severity = "减仓防守", 2
        else:
            signal, severity = "持有观察", 1

        confidence = min(95, 40 + abs(net_points) * 5 + dimension_count * 5)
        summary = (
            f"{signal}：{dimension_count} 个风险维度，风险点 {risk_points}、保护点 {protection_points}。"
            "跌破均线未被单独视为卖出依据。"
        )
        hits.append({
            "symbol": symbol,
            "name": name,
            "pct": round(pct, 2),
            "close": round(close, 2),
            "current_price": round(current_price, 2) if current_price > 0 else None,
            "current_pct": round(current_pct, 2) if current_price > 0 else None,
            "severity": severity,
            "signal": signal,
            "layer": layer,
            "confidence": confidence,
            "risk_score": min(100, net_points * 12),
            "risk_dimensions": sorted(risk_dimensions),
            "risk_factors": risk_factors,
            "protect_factors": protect_factors,
            "context_factors": context_factors,
            "reason": summary,
            "amount_yi": round(float(metric.get("amount", 0)) / 1e8, 2),
            "amount_ratio": round(amount_ratio, 2),
            "capital_flow_5d": round(capital_flow, 2),
            "relative_pct": round(relative_pct, 2),
        })

    hits.sort(key=lambda item: (-item["severity"], -item["risk_score"], item["pct"]))

    # 按综合建议配额挑选返回，避免退出/减仓被海量「持有观察」挤出列表；每类内已按严重度排序
    max_items = max(1, min(limit, 500))
    quotas = {"退出/止损": 150, "减仓防守": 150, "反包观察": 100, "持有观察": 100}
    selected: List[Dict[str, object]] = []
    selected_symbols: set[str] = set()
    for signal, quota in quotas.items():
        picked = 0
        for item in hits:
            if item["signal"] != signal or str(item["symbol"]) in selected_symbols:
                continue
            if picked >= quota:
                break
            selected.append(item)
            selected_symbols.add(str(item["symbol"]))
            picked += 1
    if len(selected) < max_items:
        for item in hits:
            if str(item["symbol"]) in selected_symbols:
                continue
            selected.append(item)
            selected_symbols.add(str(item["symbol"]))
            if len(selected) >= max_items:
                break
    selected.sort(key=lambda item: (-item["severity"], -item["risk_score"], item["pct"]))

    recommendation_counts = {
        "exit": sum(1 for item in hits if item["signal"] == "退出/止损"),
        "reduce": sum(1 for item in hits if item["signal"] == "减仓防守"),
        "rebound": sum(1 for item in hits if item["signal"] == "反包观察"),
        "watch": sum(1 for item in hits if item["signal"] == "持有观察"),
    }
    return {
        "total_flagged": len(hits),
        "breakdown_count": breakdown_n,
        "urgent_count": recommendation_counts["exit"],
        "actionable_count": recommendation_counts["exit"] + recommendation_counts["reduce"],
        "recommendation_counts": recommendation_counts,
        "layer_counts": layer_counts,
        "market_context": {
            "median_pct": round(market_median, 2),
            "up_share": round(market_up_share, 4),
            "breakdown_share": round(breakdown_share, 4),
            "broad_retreat": broad_retreat,
        },
        "method_note": "均线破位仅为弱证据；退出建议至少需要三个风险维度共振，盘中反包会动态降级。",
        "items": selected[:max_items],
    }


def scan_high_position_risk(metrics: Dict[str, Dict[str, float]],
                            names: Dict[str, str],
                            bad_forecast: Optional[set] = None,
                            realtime_quotes: Optional[Dict[str, dict]] = None,
                            fundamental_flags: Optional[Dict[str, dict]] = None,
                            limit: int = 120) -> Dict[str, object]:
    """高位风险名单：涨幅已经兑现、指标显示该走的**好票**。

    与 scan_sell_signals 分开跑，因为两者回答的问题不同。那边是「全市场谁在破位」，
    什么票都会进；这边只问「哪些原本不错的票涨过头了、现在该减该走」。

    刻意排除 ST/退市/预亏：这些票任何时候都不该买，把它们混进来只会让名单变长、
    让真正需要看的高位票被淹没。它们归到另一份「其他关注」名单里。

    门槛是「且」的关系（见模块顶部常量），任何一条不满足就不进这份名单：
    涨得够多 → 确实见了顶 → 趋势真的坏了 → 有成交量撑得住卖出。
    盘中已收复双均线的（反包）也剔除——那说明还没走坏，不该催人卖。
    """
    bad_forecast = bad_forecast or set()
    realtime_quotes = realtime_quotes or {}
    fundamental_flags = fundamental_flags or {}
    items: List[Dict[str, object]] = []

    for symbol, metric in metrics.items():
        name = str(names.get(symbol) or symbol)
        flag = fundamental_flags.get(symbol) or {}
        if "ST" in name.upper() or "退" in name or symbol in bad_forecast or flag.get("bad_forecast"):
            continue  # 垃圾股不进这份名单，另一份名单负责

        close = float(metric.get("close", 0))
        ma10 = float(metric.get("ma10", 0))
        ma20 = float(metric.get("ma20", 0))
        ma60 = float(metric.get("ma60", 0))
        amount = float(metric.get("amount", 0))
        runup = float(metric.get("runup_pct", 0))
        drawdown = float(metric.get("drawdown_from_peak", 0))
        days_since_peak = int(metric.get("days_since_peak", 0))
        window_bars = int(metric.get("window_bars", 0))
        if window_bars < 40 or close <= 0 or ma20 <= 0:
            continue  # 历史不足，区间高低点不可信
        if amount < HIGH_POS_MIN_AMOUNT:
            continue
        if runup < HIGH_POS_MIN_RUNUP:
            continue
        if drawdown > -HIGH_POS_MIN_DRAWDOWN:
            continue
        if close >= ma20:
            continue  # 中期趋势还没坏，不催卖

        pct = float(metric.get("pct", 0))
        amount_ratio = float(metric.get("amount_ratio", 0))
        capital_flow = float(metric.get("capital_flow_5d", 0))
        return_20d = float(metric.get("return_20d", 0))
        consecutive_down = int(metric.get("consecutive_down", 0))

        # 盘中已收复双均线：趋势在修复，不该出现在「该卖」名单里
        quote = realtime_quotes.get(symbol) or {}
        current_price = float(
            quote.get("price") or quote.get("current_price") or quote.get("close") or 0)
        current_pct = float(
            quote.get("change_percent") if quote.get("change_percent") is not None
            else quote.get("pct_chg") or 0)
        if current_price > 0 and current_price >= ma10 and current_price >= ma20:
            continue

        confirms: List[str] = []
        if close < ma10:
            confirms.append(f"跌破 MA10({ma10:.2f})/MA20({ma20:.2f})，短中期趋势同时走坏")
        if ma60 > 0 and close < ma60:
            confirms.append(f"跌破 MA60({ma60:.2f})，中期趋势失守")
        if pct < 0 and amount_ratio >= 1.4:
            confirms.append(f"放量下跌：成交额达 20 日均值 {amount_ratio:.1f} 倍")
        if capital_flow <= -20:
            confirms.append(f"资金转出：近 5 日下跌日成交占优（{capital_flow:.0f}）")
        if consecutive_down >= 3:
            confirms.append(f"已连续下跌 {consecutive_down} 个交易日")
        if return_20d <= -10:
            confirms.append(f"近 20 日累计下跌 {abs(return_20d):.1f}%")
        if len(confirms) < HIGH_POS_MIN_CONFIRM:
            continue

        # 仓位动作按「离见顶多久」分档，而不是按跌了多深。
        # 跌 60% 再喊清仓没有意义——那时候该说的是别去抄底。真正有价值的提示窗口是
        # 刚见顶、跌幅还不深的那十几个交易日，那时候减仓还来得及。
        if drawdown <= -HIGH_POS_DEEP_DRAWDOWN:
            action, severity = "已深跌，勿抄底；反弹至均线附近减磅", 1
            stage = "深跌未反转"
        elif days_since_peak <= HIGH_POS_FRESH_BARS:
            action, severity = "见顶初期，减仓至三成以下", 3
            stage = "刚见顶"
        else:
            action, severity = "趋势已坏，逢反弹继续减仓", 2
            stage = "下跌中继"

        items.append({
            "symbol": symbol,
            "name": name,
            "close": round(close, 2),
            "pct": round(pct, 2),
            "current_price": round(current_price, 2) if current_price > 0 else None,
            "current_pct": round(current_pct, 2) if current_price > 0 else None,
            "peak": metric.get("peak"),
            "runup_pct": round(runup, 1),
            "drawdown_from_peak": round(drawdown, 1),
            "days_since_peak": days_since_peak,
            "amount_yi": round(amount / 1e8, 2),
            "amount_ratio": round(amount_ratio, 2),
            "return_20d": round(return_20d, 2),
            "action": action,
            "stage": stage,
            "severity": severity,
            "confirms": confirms,
            "reason": (
                f"区间涨幅 {runup:.0f}% 后自高点回撤 {abs(drawdown):.0f}%，"
                f"{len(confirms)} 项指标确认趋势走坏"
            ),
        })

    # 排序：动作越果断越靠前，同档按回撤深度——先看跌得最狠的
    # 刚见顶的排最前——那是唯一还来得及行动的一档；同档内涨得越猛的越优先看
    items.sort(key=lambda it: (-it["severity"], -it["runup_pct"]))
    return {
        "total": len(items),
        "counts": {
            "fresh": sum(1 for it in items if it["stage"] == "刚见顶"),
            "falling": sum(1 for it in items if it["stage"] == "下跌中继"),
            "deep": sum(1 for it in items if it["stage"] == "深跌未反转"),
        },
        "criteria": (
            f"区间涨幅 ≥{HIGH_POS_MIN_RUNUP:.0f}% · 距高点回撤 ≥{HIGH_POS_MIN_DRAWDOWN:.0f}% · "
            f"收盘失守 MA20 · 成交额 ≥{HIGH_POS_MIN_AMOUNT / 1e8:.1f}亿 · "
            f"≥{HIGH_POS_MIN_CONFIRM} 项走坏确认；已剔除 ST/退市/预亏与盘中反包"
        ),
        "items": items[:max(1, limit)],
    }
