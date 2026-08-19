"""风险预警：市场级仓位红绿灯 + 全市场个股卖出信号扫描。

诚实边界——所有信号都来自系统已算出的真实数据，不编造：
- 市场级：赚钱效应温度（逐日广度加权，见 regime）、连续走弱天数、跌停潮家数、
  广度骤降。四项合成 0-100 风险分 → 四档（安全/警惕/危险/极危）+ 该档市场状况描述。
- 个股级：七不买里的「破位下行」（收盘同时跌破 MA10/MA20）与「问题股」（ST/退市/
  预亏），批量算出全市场当前命中卖出信号的票。破位口径与 risk_check 完全一致。

分档描述的是市场状况，不是操作建议——历史锚（回放偏冷期短线超额转负）一并给出，
让用户看到这个读数在历史上对应过什么，自己判断，而不是听一句话。
"""
from __future__ import annotations

from typing import Dict, List, Optional

# 风险分 → 档位阈值（分越高越危险）
LEVELS = [
    # 每一档描述这个环境「是什么样」，不写「你该做什么」。分档阈值与计分口径未变。
    (75.0, "极危", "广度与流动性同时恶化，历史上这一档区间内多数方向都在走弱，反弹持续性差。"),
    (55.0, "危险", "赚钱效应明显偏差，仅少数主线还在维持，多数个股处于均线下方。"),
    (35.0, "警惕", "强弱分化明显，普涨不成立，同一天里主线与杂毛的差别被拉大。"),
    (0.0, "安全", "赚钱效应处于正常区间，广度未见明显恶化。"),
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
# 阈值是扫出来的，不是拍的（2026-07-28，以「近一个月腰斩的前期大牛股」为覆盖标的）：
# 决定名单长度的主要是流动性，不是确认条数——成交额门槛 2 亿→10 亿，入选从 492 降到
# 180（8.9%→3.3%）而覆盖率纹丝不动；反过来把确认条数从 1 提到 3，只是把「跌了 44%
# 但今天恰好没连跌」这类票误杀掉（覆盖 17/20 → 11/20）。
# 原因：确认项里「连跌 3 日 / 放量下跌 / 资金转出」都是当日信号，逐日抖动大，不适合
# 当硬闸门。真正的判据是硬门槛那四条（涨幅、回撤、失守 MA20、流动性），确认项负责
# 定严重度。最终 165 只 / 5525（3.0%）。
HIGH_POS_MIN_RUNUP = 100.0       # 区间低点→高点涨幅（%）：就盯「翻倍」这一档
HIGH_POS_MIN_DRAWDOWN = 15.0     # 距区间高点回撤（%）：低于此仍算正常波动，不算见顶
HIGH_POS_MIN_AMOUNT = 10.0e8     # 当日成交额：好票的前提是走得掉；这条最能压住名单长度
HIGH_POS_MIN_CONFIRM = 1         # 走坏确认：硬门槛已承担判据，这里只要求至少有一条佐证
HIGH_POS_FRESH_BARS = 12         # 距高点多少个交易日内算「刚见顶」——提示的黄金窗口
HIGH_POS_DEEP_DRAWDOWN = 35.0    # 回撤超过此值视为「已深跌」，动作从催卖改为别抄底


def market_risk_gauge(daily: List[Dict], temp: float,
                      limitdown_share: Optional[float] = None,
                      breakdown_share: Optional[float] = None,
                      cold_excess: Optional[float] = None,
                      index_pcts: Optional[List[Dict]] = None,
                      leader_breakdown: Optional[int] = None) -> Dict[str, object]:
    """市场级风险仪表。

    daily: engine.recent_daily_breadth 输出（逐日 median_pct/breadth_up，最新在前）。
    temp:  regime 加权温度分（0-100，50 中性），已由 market_context 算好，直接复用同源口径。
    limitdown_share: 当日跌超 ~9% 的股票占比（快照口径），可空。
    breakdown_share: 全市场跌破 MA10&MA20 的股票占比（结构性下跌强度），可空。
    cold_excess: 回放偏冷期 T+5 平均超额（pp），作为历史锚，可空。
    index_pcts: 三大指数当日涨跌幅 [{name, change_percent}]，可空。
    leader_breakdown: 高位风险名单里正在走坏（刚见顶+下跌中继）的只数，可空。

    为什么必须有后两项（2026-07-28 的教训）：那天个股中位 +0.31%、上涨占比 55%，
    广度口径一片祥和，仪表给出「安全 · 可以进攻」；而同一天创业板 −4.69%、深证 −2.98%，
    兆易创新等一批前期龙头直接跌停。原因是**全部信号都是广度口径**——几千只小票的
    中位数把权重股和龙头的崩塌完全淹没了。广度只回答「多少只在涨」，不回答「钱在哪里亏」。
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

    # 6) 指数跌幅：广度完全看不见的一维。小票普涨时指数照样可以暴跌，
    # 而用户的钱多半在权重和龙头里——那才是真实亏损发生的地方。
    worst_index_pct = 0.0
    if index_pcts:
        drops = [float(i.get("change_percent") or 0) for i in index_pcts]
        if drops:
            worst_index_pct = min(drops)
            idx_risk = min(30.0, max(0.0, -worst_index_pct - 0.5) * 6.0)
            if idx_risk > 0.5:
                worst_name = next((str(i.get("name") or "指数") for i in index_pcts
                                   if float(i.get("change_percent") or 0) == worst_index_pct), "指数")
                signals.append({
                    "key": "index_drop", "name": "指数跌幅", "value": round(worst_index_pct, 2),
                    "risk": round(idx_risk, 1),
                    "detail": f"{worst_name} 当日 {worst_index_pct:.2f}%——" +
                              ("权重与龙头正在杀跌，广度指标看不见这层伤害"
                               if worst_index_pct <= -2 else "指数走弱，注意仓位"),
                })
                score += idx_risk

    # 7) 个股与指数背离：小票普涨、指数大跌。这是典型的行情末期特征——
    # 资金从龙头撤出后在小票里做最后一轮扩散，此时「赚钱效应」读数最具欺骗性。
    if index_pcts and daily:
        today_median = float(daily[0].get("median_pct", 0))
        if today_median > 0 and worst_index_pct <= -1.5:
            div_risk = min(15.0, (today_median - worst_index_pct) * 2.0)
            signals.append({
                "key": "divergence", "name": "个股与指数背离", "value": round(today_median - worst_index_pct, 2),
                "risk": round(div_risk, 1),
                "detail": f"个股中位 {today_median:+.2f}% 却伴随指数 {worst_index_pct:.2f}%——"
                          "小票普涨掩盖权重杀跌，赚钱效应读数在此刻并不代表可以进攻",
            })
            score += div_risk

    # 8) 龙头崩塌：前期翻倍的高流动性个股正在成规模走坏。
    # 广度和指数都可能滞后，但龙头见顶是最先出现、也最伤持仓的信号——
    # 小票的普涨不足以抵消它，历史上这种背景下追涨大概率被埋。
    if leader_breakdown is not None and leader_breakdown > 0:
        lead_risk = min(20.0, leader_breakdown / 4.0)
        if lead_risk > 0.5:
            signals.append({
                "key": "leader_breakdown", "name": "龙头崩塌", "value": int(leader_breakdown),
                "risk": round(lead_risk, 1),
                "detail": f"{leader_breakdown} 只前期翻倍的高流动性个股正在见顶或下跌中继——"
                          "主线资金在撤退，此时的普涨多为扩散末期",
            })
            score += lead_risk

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
        "disclaimer": "规则化数据提示，不构成投资建议；如何应对请自行判断。",
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
                            limit: int = 500) -> Dict[str, object]:
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

        # 按「离见顶多久」分档，而不是按跌了多深。
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
