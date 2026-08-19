"""个股研报生成与深度分析富化（从 lite_main 抽出）。

一整簇纯函数：评分/风险画像、专业版单股研报文本、深度分析框架的格式化与审计、
多智能体复议。加上它们的两个适配器——真 LLM 优先、无 key 回退确定性模板的
LiteDeepAnalysisLLM，以及深度框架要求但 Lite 不做磁盘缓存的 LiteNoopCacheManager。

抽出的理由：这簇近千行没有任何 FastAPI 依赖，却和路由混在同一个文件里，导致
analysis 路由无法独立拆分。这里除 `_safe_number` 外不依赖 lite_main，可安全 import。

`enrich_lite_result_with_deep_analysis` 会真的调 LLM（有 key 时），单测覆盖不到，
改动需在预览环境实跑 /api/analysis/single 验证。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.core.market_data import _safe_number


_DEEP_ANALYSIS_SYSTEM = (
    "你是严谨的 A 股卖方分析师，基于公开信息与常识做研究分析，不夸大、不编造数据。"
    "严格按用户要求的格式输出：要求 JSON 时只输出合法 JSON（不要 markdown 代码块标记）；"
    "要求只输出评级词时只输出那个词，不加任何解释。"
)


class LiteDeepAnalysisLLM:
    """深度分析框架的 chat 适配器：优先真 LLM（BYOK 用户密钥），无 key/出错时回退确定性模板。"""

    def __init__(self, code: str, name: str, llm_override: dict | None = None):
        self.code = code
        self.name = name
        self.call_count = 0
        # BYOK：站点不配密钥，这里拿的是发起这次分析的用户自己的配置。
        self.llm_override = llm_override

    def chat(self, prompt: str) -> str:
        self.call_count += 1
        # 优先真 LLM；失败/无 key 回退模板，保证框架不崩。
        try:
            from quantcore.quant import llm as _qllm
            if _qllm.available(override=self.llm_override):
                text = _qllm.chat(prompt, system=_DEEP_ANALYSIS_SYSTEM, deep=True,
                                  max_tokens=1500, override=self.llm_override)
                if text and text.strip():
                    return text.strip()
        except Exception:
            pass
        return self._canned(prompt)

    def _canned(self, prompt: str) -> str:
        # 确定性模板兜底：框架并发解析特定 JSON，按 prompt 内容分发（不靠调用顺序）。
        if "产业链" in prompt:
            return json.dumps(
                {
                    "chain": {
                        "upstream": [{"name": "上游原材料/核心零部件", "companies": ["行业供应商", "核心设备商"]}],
                        "midstream": [{"name": "公司所在主营环节", "companies": [self.name]}],
                        "downstream": [{"name": "下游客户/应用场景", "companies": ["产业客户", "终端渠道"]}],
                    },
                    "peers": [
                        {"code": "同业A", "name": "可比公司A", "pe": 28.0, "roe": 12.0},
                        {"code": "同业B", "name": "可比公司B", "pe": 32.0, "roe": 10.5},
                        {"code": "同业C", "name": "可比公司C", "pe": 24.0, "roe": 14.0},
                    ],
                    "position": "midstream",
                    "moat": "核心壁垒需要结合产品竞争力、客户结构、成本控制和行业周期验证；短中期更应关注订单、价格和资金行为是否共振。",
                },
                ensure_ascii=False,
            )
        if "打分" in prompt or "fundamental" in prompt:
            return json.dumps(
                {
                    "fundamental": 68,
                    "governance": 65,
                    "competitive": 70,
                    "growth": 72,
                    "valuation": 62,
                    "rationale": "质量评分采用 Lite 默认估计，重点用于补齐深度框架结构，最终判断仍叠加量化趋势和风险控制。",
                },
                ensure_ascii=False,
            )
        if "投资风险" in prompt:
            return json.dumps(
                [
                    {"risk": "趋势失效风险", "mitigation": "若跌破关键均线或量能明显萎缩，应降低关注权重或等待重新放量确认。"},
                    {"risk": "事件兑现不及预期", "mitigation": "跟踪公告、业绩、订单和行业政策，避免只凭题材热度提高权重。"},
                    {"risk": "波动放大风险", "mitigation": "控制单票风险暴露，使用明确失效线。"},
                ],
                ensure_ascii=False,
            )
        if "跟踪计划" in prompt:
            return json.dumps(
                {
                    "metrics": [
                        {"name": "趋势结构", "threshold": "短中期均线保持多头或回踩后重新放量"},
                        {"name": "量能确认", "threshold": "成交额和量比维持活跃，不能缩量冲高"},
                        {"name": "风险阈值", "threshold": "最大回撤和波动率不继续恶化"},
                    ],
                    "next_review": "下一个交易日收盘后复盘量价结构，遇重大公告时立即复盘。",
                },
                ensure_ascii=False,
            )
        if "综合评级" in prompt:
            return "持有"
        # macro and any other free-text prompt
        return f"{self.name}（{self.code}）的宏观分析应结合行业景气、政策方向、利率环境和资金偏好判断，当前结论以公开数据和量化信号为主。"


class LiteNoopCacheManager:
    def get_cached_daily(self, code: str) -> None:
        return None

    def cache_daily(self, code: str, df: Any, ttl: int | None = None) -> None:
        return None


def _fmt_score(value: Any) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "0.0"


def _score_profile(score: float) -> dict[str, str]:
    if score >= 85:
        return {
            "grade": "A",
            "label": "高确定性强势",
            "stance": "趋势质量和因子共振较强，适合纳入核心候选，但仍要防止高位拥挤后的快速回撤。",
            "bias": "强势跟踪",
        }
    if score >= 78:
        return {
            "grade": "A-",
            "label": "强趋势候选",
            "stance": "分数已经进入强势区间，核心问题是观察位、风险线和验证节奏是否清晰。",
            "bias": "积极跟踪但不提高过高权重",
        }
    if score >= 72:
        return {
            "grade": "B+",
            "label": "中高胜率候选",
            "stance": "趋势和动量有优势，但尚未达到高确定性，适合等待回踩确认或突破放量后再提高关注级别。",
            "bias": "偏积极",
        }
    if score >= 65:
        return {
            "grade": "B",
            "label": "机会型观察",
            "stance": "有跟踪价值，但确定性来自局部因子，若风控或RSI拖累，需要降低关注权重。",
            "bias": "谨慎跟踪",
        }
    if score >= 58:
        return {
            "grade": "C+",
            "label": "结构分歧",
            "stance": "部分指标改善，但整体胜率一般，适合观察，不适合当作主线品种。",
            "bias": "观察优先",
        }
    if score >= 50:
        return {
            "grade": "C",
            "label": "中性偏弱",
            "stance": "缺少足够的趋势或资金确认，当前更适合等待下一轮数据改善。",
            "bias": "暂不主动",
        }
    return {
        "grade": "D",
        "label": "弱势回避",
        "stance": "主要因子不足，除非有明确基本面催化或极强反转信号，否则不纳入重点跟踪。",
        "bias": "暂不纳入",
    }


def _rsi_profile(rsi: float) -> str:
    if rsi >= 85:
        return "RSI处于极高位，短线筹码明显拥挤，继续上行的回撤代价偏高。"
    if rsi >= 70:
        return "RSI偏高，说明动量强但短线已不便宜，更适合观察回踩或盘中分歧。"
    if rsi >= 55:
        return "RSI处于偏强区间，动量仍在，但没有明显过热。"
    if rsi >= 45:
        return "RSI中性，价格方向更多依赖趋势延续和成交确认。"
    if rsi >= 30:
        return "RSI偏弱，短线修复可能存在，但胜率需要趋势配合。"
    return "RSI低位，存在技术修复空间，但也说明近期承压明显，不能只按低位反弹处理。"


def _risk_level(volatility: float, max_drawdown: float) -> str:
    if volatility >= 0.45 or max_drawdown <= -0.30:
        return "高"
    if volatility >= 0.25 or max_drawdown <= -0.18:
        return "中"
    return "低"


def _risk_profile(risk_level: str, volatility: float, max_drawdown: float, sharpe: float) -> str:
    parts = []
    if risk_level == "高":
        parts.append("风险等级为高，说明它不是稳健低波动品种，观察权重和失效条件比方向判断更重要")
    elif risk_level == "中":
        parts.append("风险等级为中，波动可接受但仍需要避免过高关注权重")
    else:
        parts.append("风险等级为低，价格波动相对可控")

    if max_drawdown <= -0.35:
        parts.append("历史最大回撤很深，若观察位过高，收益回撤比会明显恶化")
    elif max_drawdown <= -0.25:
        parts.append("最大回撤偏大，需要用动态失效条件控制风险")
    else:
        parts.append("最大回撤压力相对温和")

    if sharpe >= 2.5:
        parts.append("夏普较高，说明单位波动带来的收益效率较好")
    elif sharpe >= 1.2:
        parts.append("夏普处于可用区间，但不是无风险信号")
    else:
        parts.append("夏普偏低，波动没有被收益充分补偿")
    return "；".join(parts) + "。"


def _trade_plan(score: float, signal: str, risk_level: str, rsi: float) -> dict[str, str]:
    normalized_signal = str(signal or "").lower()
    if score >= 78 or normalized_signal == "strong_buy":
        if risk_level == "高" or rsi >= 70:
            return {
                "action": "强势跟踪，等待回踩或放量突破确认",
                "position": "关注优先级：高，但需等待风险释放或二次确认",
                "stop": "跌破短期关键均线或回撤超过 7%-10%，视为趋势假设失效",
            }
        return {
            "action": "可作为重点候选，跟踪放量确认",
            "position": "关注优先级：高，确认后再提高跟踪权重",
            "stop": "用最近一轮震荡低点或 6%-8% 回撤作为失效线",
        }
    if score >= 72:
        return {
            "action": "偏积极，但观察位要挑剔",
            "position": "关注优先级：中高，突破或回踩承接确认后再提高",
            "stop": "若放量跌破近期支撑，降低跟踪权重",
        }
    if score >= 65:
        return {
            "action": "加入观察池，等待二次确认",
            "position": "关注优先级：中，只适合跟踪验证",
            "stop": "若趋势因子转弱或回撤扩大，应退出观察",
        }
    if score >= 58:
        return {
            "action": "观察为主，等待信号强化",
            "position": "关注优先级：低，等待量价改善",
            "stop": "没有量价改善前不提高跟踪权重",
        }
    return {
        "action": "暂不纳入重点跟踪",
        "position": "关注优先级：低",
        "stop": "只有评分重新回到 65 以上并出现成交确认时再评估",
    }


def _fmt_price(value: Any) -> str:
    number = _safe_number(value)
    return "-" if number is None else f"{number:.2f}"


def _fmt_pct(value: Any) -> str:
    number = _safe_number(value)
    if number is None:
        return "-"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.2f}%"


def _fmt_amount_cn(value: Any) -> str:
    number = _safe_number(value)
    if number is None or number <= 0:
        return "-"
    if number >= 100_000_000:
        return f"{number / 100_000_000:.2f}亿"
    if number >= 10_000:
        return f"{number / 10_000:.2f}万"
    return f"{number:.0f}"


def _professional_position_label(score: float, trend: float, momentum: float, risk_level: str) -> str:
    if score >= 82 and trend >= 78 and momentum >= 75 and risk_level != "高":
        return "强势候选"
    if score >= 72 and trend >= 65 and momentum >= 60:
        return "重点跟踪"
    if score >= 60:
        return "观察修复"
    return "暂不参与"


def _build_professional_single_stock_report(
    *,
    symbol: str,
    stock_name: str,
    result: dict[str, Any],
    quant_result: dict[str, Any],
    quote: dict[str, Any] | None,
    technical_snapshot: dict[str, Any],
) -> dict[str, str]:
    latest = quant_result.get("latest") or {}
    factors = quant_result.get("factors") or {}
    risk = quant_result.get("risk") or {}
    deep = result.get("deep_analysis") or {}
    quote = quote or {}

    price = quote.get("price") or quote.get("close") or result.get("current_price") or latest.get("close")
    pct = quote.get("change_percent")
    if pct is None:
        pct = quote.get("pct_chg") if quote else latest.get("pct_change")
    amount = quote.get("amount") or latest.get("amount")
    volume = quote.get("volume") or latest.get("volume")
    score = float(result.get("overall_score") or quant_result.get("score") or 0)
    signal = str(quant_result.get("signal") or result.get("signal") or "neutral")
    trend = float(factors.get("trend") or 0)
    momentum = float(factors.get("momentum") or 0)
    rsi = float(factors.get("rsi") or 0)
    liquidity = float(factors.get("liquidity") or 0)
    risk_control = float(factors.get("risk_control") or 0)
    volatility = float(risk.get("volatility") or 0)
    max_drawdown = float(risk.get("max_drawdown") or 0)
    sharpe = float(risk.get("sharpe") or 0)
    risk_level = _risk_level(volatility, max_drawdown)
    label = _professional_position_label(score, trend, momentum, risk_level)

    ma5 = technical_snapshot.get("ma5")
    ma10 = technical_snapshot.get("ma10")
    ma20 = technical_snapshot.get("ma20")
    ma30 = technical_snapshot.get("ma30")
    ma60 = technical_snapshot.get("ma60")
    high_60 = technical_snapshot.get("high_60")
    low_20 = technical_snapshot.get("low_20")
    prev_high = technical_snapshot.get("prev_high")
    last_close = _safe_number(price) or _safe_number(latest.get("close")) or 0
    level_values = [
        _safe_number(prev_high),
        _safe_number(ma5),
        _safe_number(ma10),
        _safe_number(ma20),
        _safe_number(low_20),
    ]
    usable_supports = [
        value for value in level_values
        if value is not None and last_close and last_close * 0.82 <= value <= last_close * 0.995
    ]
    support = max(usable_supports) if usable_supports else (last_close * 0.92 if last_close else 0)
    hard_stop = min(support * 0.985, last_close * 0.90) if last_close and support else support
    reclaim_candidates = [
        value for value in [_safe_number(prev_high), _safe_number(ma5), _safe_number(ma10)]
        if value is not None and last_close and last_close * 0.95 <= value <= last_close * 1.08
    ]
    reclaim = max(reclaim_candidates) if reclaim_candidates else (last_close * 1.01 if last_close else 0)
    breakout_candidates = [
        value for value in [_safe_number(high_60), reclaim]
        if value is not None and last_close and value >= last_close * 1.005
    ]
    breakout = min(breakout_candidates) if breakout_candidates else (last_close * 1.03 if last_close else reclaim)
    support_gap = ((last_close - support) / last_close * 100) if last_close and support else 0
    stop_gap = ((last_close - hard_stop) / last_close * 100) if last_close and hard_stop else 0
    reclaim_gap = ((reclaim - last_close) / last_close * 100) if last_close and reclaim else 0
    breakout_gap = ((breakout - last_close) / last_close * 100) if last_close and breakout else 0

    # 额外指标（净室实现的 ATR/KDJ/ADX，从量化引擎 latest 快照读取）
    atr_pct = float(latest.get("atr_pct") or 0)
    adx_value = float(latest.get("adx") or 0)
    kdj_j_value = latest.get("kdj_j")
    chandelier_stop = _safe_number(latest.get("chandelier_stop")) or 0
    kdj_state = ""
    if kdj_j_value is not None:
        jv = float(kdj_j_value)
        kdj_state = f"KDJ 的 J={jv:.0f}（{'超买区，注意回踩' if jv > 100 else '超卖区，关注反弹' if jv < 0 else '中性'}）。"
    adx_state = f"ADX {adx_value:.0f}（{'趋势明确' if adx_value >= 25 else '震荡为主' if adx_value < 20 else '趋势中等'}）。" if adx_value else ""
    atr_state = f"ATR 波动约 {atr_pct:.1f}%。" if atr_pct else ""
    mfi_value = float(latest.get("mfi") or 0)
    cmf_value = float(latest.get("cmf") or 0)
    mf_state = ""
    if mfi_value or cmf_value:
        flow = "资金净流入" if cmf_value > 0.05 else ("资金净流出" if cmf_value < -0.05 else "资金中性")
        mf_state = f"资金流 MFI {mfi_value:.0f}、CMF {cmf_value:+.2f}（{flow}）。"
    cci_v = float(latest.get("cci") or 0)
    wr_v = float(latest.get("williams_r") or 0)
    aroon_up_v = float(latest.get("aroon_up") or 0)
    aroon_down_v = float(latest.get("aroon_down") or 0)
    stochrsi_v = float(latest.get("stochrsi") or 0)
    obv_rising = bool(latest.get("obv_rising"))
    mom_bits = []
    if cci_v:
        mom_bits.append(f"CCI {cci_v:.0f}")
    if wr_v:
        mom_bits.append(f"威廉%R {wr_v:.0f}")
    if stochrsi_v:
        mom_bits.append(f"StochRSI {stochrsi_v:.0f}")
    if aroon_up_v or aroon_down_v:
        mom_bits.append(f"Aroon 上{aroon_up_v:.0f}/下{aroon_down_v:.0f}")
    mom_bits.append("OBV 上升" if obv_rising else "OBV 走平/下降")
    mom_state = ("动量补充：" + "、".join(mom_bits) + "。") if mom_bits else ""
    wyckoff = (quant_result.get("integrations") or {}).get("wyckoff") or {}
    wyckoff_line = ""
    if wyckoff:
        wyckoff_phase = str(wyckoff.get("phase") or "neutral-range")
        wyckoff_bias = str(wyckoff.get("bias") or "neutral")
        wyckoff_score = float(wyckoff.get("score") or 50)
        wyckoff_reasons = "；".join(str(item) for item in (wyckoff.get("reasons") or [])[:2])
        wyckoff_line = (
            f"Wyckoff/VSA：{wyckoff_phase}，bias={wyckoff_bias}，score={wyckoff_score:.0f}。"
            f"{wyckoff_reasons}。"
        )
    ml_features = (quant_result.get("integrations") or {}).get("ml_features") or {}
    ml_line = ""
    if ml_features:
        ml_line = (
            f"ML特征摘要：feature_score={float(ml_features.get('feature_score') or 50):.0f}，"
            f"趋势持续性={float(ml_features.get('trend_persistence') or 50):.0f}，"
            f"波动分位={float(ml_features.get('volatility_rank') or 50):.0f}。"
        )
    extra_ind_line = kdj_state + adx_state + atr_state + mf_state + mom_state + wyckoff_line + ml_line

    above_parts = []
    below_parts = []
    for name, value in [("5日线", ma5), ("10日线", ma10), ("20日线", ma20), ("30日线", ma30), ("60日线", ma60)]:
        number = _safe_number(value)
        if number is None or not last_close:
            continue
        (above_parts if last_close >= number else below_parts).append(f"{name}{number:.2f}")

    quality = deep.get("quality_score") or {}
    scenarios = deep.get("scenarios") or {}
    base_scenario = scenarios.get("base") or {}
    fundamental_points = []
    if quality:
        numeric_scores = [float(v) for v in quality.values() if isinstance(v, (int, float))]
        if numeric_scores:
            fundamental_points.append(f"Claude 深度质量均分约 {sum(numeric_scores) / len(numeric_scores):.1f}。")
        if quality.get("rationale"):
            fundamental_points.append(str(quality["rationale"]))
    if base_scenario.get("target_price") is not None:
        fundamental_points.append(f"财务情景测算的基准价格中枢约 {base_scenario.get('target_price')}，该值来自财报收入、利润率、EPS/股本和估值推导。")
    if not fundamental_points:
        fundamental_points.append("当前基本面层以量化和可用公开数据为主，若财报/公告数据不足，系统不会用固定假设伪造结论。")

    conclusion = (
        f"结论：{stock_name}（{symbol}）当前属于“{label}”。"
        f"量化综合评分 {score:.1f}，交易信号 {signal}。"
        f"它不是只看涨跌幅就能判断的票，关键要看趋势是否延续、短中期动量是否共振、成交额是否继续活跃，以及回撤风险有没有扩大。"
    )
    if label in {"强势候选", "重点跟踪"}:
        conclusion += f" 短线核心看 {support:.2f} 附近是否有承接、{reclaim:.2f} 能否站稳；这些价位都按当前价附近重新计算，不再拿远端均线当失效线。"
    else:
        conclusion += f" 现在更适合等确认，不能因为单日反弹就直接追；若跌破 {hard_stop:.2f}，短线风险就应该升级。"

    realtime_section = (
        f"截至 {quote.get('updated_at') or result.get('quote_updated_at') or result.get('updated_at') or '-'}：\n\n"
        f"- 股价：{_fmt_price(price)}\n"
        f"- 涨跌幅：{_fmt_pct(pct)}\n"
        f"- 成交额：{_fmt_amount_cn(amount)}\n"
        f"- 成交量：{_fmt_amount_cn(volume)}\n"
        f"- 数据源：{quote.get('quote_source') or result.get('quote_source') or 'quant/realtime'}"
    )

    vertical_section = (
        "一、纵向走势：\n\n"
        f"当前价格 {_fmt_price(price)}。"
        f"位于上方的均线：{'、'.join(above_parts) if above_parts else '暂无'}；"
        f"仍未站上的均线：{'、'.join(below_parts) if below_parts else '暂无'}。\n\n"
        f"趋势因子 {trend:.0f}，动量因子 {momentum:.0f}，RSI {rsi:.0f}，流动性 {liquidity:.0f}，风控 {risk_control:.0f}。"
        f"{extra_ind_line}"
        f"{_rsi_profile(rsi)} {_risk_profile(risk_level, volatility, max_drawdown, sharpe)}\n\n"
        f"关键价位：短线承接位 {support:.2f}（距当前约 {support_gap:.1f}%），战术失效位 {hard_stop:.2f}（距当前约 {stop_gap:.1f}%），站稳确认位 {reclaim:.2f}（距当前约 {reclaim_gap:+.1f}%），加速确认位 {breakout:.2f}（距当前约 {breakout_gap:+.1f}%）。"
    )

    fundamental_section = "二、基本面和深度框架：\n\n" + "\n".join(f"- {item}" for item in fundamental_points)

    execution_section = (
        "三、接下来怎么做：\n\n"
        f"- 已关注：重点看 {support:.2f} 附近的承接；跌破 {hard_stop:.2f} 就不是“继续看看”，应先降低关注权重。\n"
        f"- 未关注：不建议在涨幅已大时直接提高权重；更好的观察条件是回踩 {support:.2f} 附近有承接，或放量站稳 {reclaim:.2f} 后再确认。\n"
        f"- 如果突破 {breakout:.2f} 且成交额同步放大，才说明短中期趋势有继续走一段的概率。\n"
        "- 若量化评分跌破 70、动量转弱或风控因子明显下降，应把它从进攻候选降级为观察。"
        + (f"\n- ATR 自适应风险线：吊灯线约 {chandelier_stop:.2f}（22 日最高 − 3×ATR），跌破视为趋势假设失效，比固定百分比更贴合个股波动。" if chandelier_stop else "")
    )

    final_report = "\n\n".join([
        conclusion,
        realtime_section,
        fundamental_section,
        vertical_section,
        execution_section,
        "说明：以上是系统基于实时行情、历史量价、量化因子和深度分析框架生成的交易研究结果，不构成任何保证收益的承诺。",
    ])

    return {
        "summary": conclusion,
        "recommendation": execution_section,
        "professional_single_stock_analysis": final_report,
        "technical_analysis": vertical_section,
        "fundamental_analysis": fundamental_section,
        "final_trade_decision": final_report,
    }


async def enrich_lite_result_with_professional_analysis(
    symbol: str,
    result: dict[str, Any],
    quant_result: dict[str, Any],
    stock_meta: dict[str, Any] | None,
    quote: dict[str, Any] | None,
) -> dict[str, Any]:
    stock_name = (quote or {}).get("name") or (stock_meta or {}).get("name") or result.get("stock_name") or symbol
    technical_snapshot: dict[str, Any] = {}
    try:
        from quantcore.quant.data import default_start_date, fetch_stock_dataframe, normalize_ohlcv

        df = await asyncio.wait_for(
            asyncio.to_thread(fetch_stock_dataframe, symbol, default_start_date(260), None),
            timeout=20,
        )
        data = normalize_ohlcv(df)
        if not data.empty:
            technical_snapshot = {
                "ma5": float(data["close"].rolling(5).mean().iloc[-1]),
                "ma10": float(data["close"].rolling(10).mean().iloc[-1]),
                "ma20": float(data["close"].rolling(20).mean().iloc[-1]),
                "ma30": float(data["close"].rolling(30).mean().iloc[-1]),
                "ma60": float(data["close"].rolling(60).mean().iloc[-1]),
                "high_60": float(data["high"].rolling(60).max().iloc[-1]),
                "low_20": float(data["low"].rolling(20).min().iloc[-1]),
                "prev_high": float(data["high"].iloc[-2]) if len(data) >= 2 else float(data["high"].iloc[-1]),
            }
    except Exception as exc:
        result["professional_analysis_data_warning"] = str(exc)

    professional = _build_professional_single_stock_report(
        symbol=symbol,
        stock_name=stock_name,
        result=result,
        quant_result=quant_result,
        quote=quote,
        technical_snapshot=technical_snapshot,
    )
    reports = dict(result.get("reports") or {})
    reports["professional_single_stock_analysis"] = professional["professional_single_stock_analysis"]
    reports["technical_analysis"] = professional["technical_analysis"]
    reports["fundamental_analysis"] = professional["fundamental_analysis"]
    reports["final_trade_decision"] = professional["final_trade_decision"]
    result["reports"] = reports
    result["summary"] = professional["summary"]
    result["recommendation"] = professional["recommendation"]
    result["technical_analysis"] = professional["technical_analysis"]
    result["fundamental_analysis"] = professional["fundamental_analysis"]
    result["analysis_engine"] = f"{result.get('analysis_engine') or 'saas-lite-quant'} + professional-single-stock"
    result["professional_analysis"] = {
        "technical_snapshot": technical_snapshot,
    }
    return result


def build_lite_analysis_result(
    task_id: str,
    symbol: str,
    quant_result: dict[str, Any],
    parameters: dict[str, Any],
    now: str,
    stock_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    latest = quant_result.get("latest") or {}
    factors = quant_result.get("factors") or {}
    risk = quant_result.get("risk") or {}
    score = float(quant_result.get("score") or 0)
    signal = quant_result.get("signal") or "neutral"
    factor_items = [
        ("趋势", float(factors.get("trend") or 0)),
        ("动量", float(factors.get("momentum") or 0)),
        ("RSI", float(factors.get("rsi") or 0)),
        ("风控", float(factors.get("risk_control") or 0)),
        ("流动性", float(factors.get("liquidity") or 0)),
    ]
    strengths = [name for name, value in factor_items if value >= 70]
    weaknesses = [name for name, value in factor_items if value < 45]
    strength_text = "、".join(strengths) if strengths else "暂无特别突出的强项"
    weakness_text = "、".join(weaknesses) if weaknesses else "暂无明显短板"
    volatility = float(risk.get("volatility") or 0)
    max_drawdown = float(risk.get("max_drawdown") or 0)
    sharpe = float(risk.get("sharpe") or 0)
    trend_value = float(factors.get("trend") or 0)
    momentum_value = float(factors.get("momentum") or 0)
    rsi_value = float(factors.get("rsi") or 0)
    liquidity_value = float(factors.get("liquidity") or 0)
    risk_control_value = float(factors.get("risk_control") or 0)
    risk_level = _risk_level(volatility, max_drawdown)
    profile = _score_profile(score)
    rsi_text = _rsi_profile(rsi_value)
    risk_text = _risk_profile(risk_level, volatility, max_drawdown, sharpe)
    plan = _trade_plan(score, str(signal), risk_level, rsi_value)
    trend_view = "趋势强势" if trend_value >= 70 else "趋势中性" if trend_value >= 45 else "趋势偏弱"
    momentum_view = "短中期动量强" if momentum_value >= 70 else "动量一般" if momentum_value >= 45 else "动量偏弱"

    # 额外指标（ATR/KDJ/ADX，来自量化引擎的 latest 快照）
    atr_pct = float(latest.get("atr_pct") or 0)
    chandelier_stop = float(latest.get("chandelier_stop") or 0)
    adx_value = float(latest.get("adx") or 0)
    kdj_j_value = latest.get("kdj_j")
    kdj_text = ""
    if kdj_j_value is not None:
        jv = float(kdj_j_value)
        kdj_state = "超买区，注意回踩" if jv > 100 else ("超卖区，关注反弹" if jv < 0 else "中性区")
        kdj_text = f"KDJ 的 J 值 {jv:.0f}（{kdj_state}）。"
    adx_text = ""
    if adx_value:
        adx_state = "趋势明确" if adx_value >= 25 else ("趋势偏弱/震荡为主" if adx_value < 20 else "趋势中等")
        adx_text = f"ADX {adx_value:.0f}（{adx_state}）。"
    extra_ind_text = f"{kdj_text}{adx_text}" + (f"波动性 ATR≈{atr_pct:.1f}%。" if atr_pct else "")

    technical = (
        f"技术结构判断：{trend_view}，{momentum_view}。"
        f"趋势因子 {_fmt_score(trend_value)}，动量因子 {_fmt_score(momentum_value)}，"
        f"RSI 因子 {_fmt_score(rsi_value)}，流动性因子 {_fmt_score(liquidity_value)}。"
        f"{rsi_text} {extra_ind_text}"
    )
    stock_name = (stock_meta or {}).get("name") or latest.get("name") or symbol
    score_line = f"{profile['grade']} / {profile['label']}，综合评分 {score:.1f}"
    summary = (
        f"{symbol}（{stock_name}）完成 SaaS Lite 单股量化画像。"
        f"{score_line}，交易信号为 {signal}。"
        f"主要优势是{strength_text}，主要短板是{weakness_text}。"
        f"{profile['stance']} {rsi_text} {risk_text}"
    )
    fundamental = (
        "SaaS Lite 当前以行情、量价和本地量化因子为主，未接入完整利润表、资产负债表和估值数据库，"
        f"因此基本面结论只作为低置信度辅助。当前流动性因子为 {_fmt_score(liquidity_value)}，"
        "若后续接入完整财务源，应重点补充收入增速、ROE、毛利率、现金流、估值分位和行业景气度。"
    )
    sentiment = (
        "Lite 模式未启用新闻和社媒情绪队列，本段用价格行为替代情绪观察。"
        f"当前市场行为显示：{trend_view}、{momentum_view}；"
        "若RSI继续上行但成交不能放大，情绪可能从强势转为拥挤。"
    )
    news = (
        "当前 Lite 后端未连接新闻归档，暂不对公告、研报和舆情事件做结论。"
        "生产版建议接入公告、交易所问询、行业政策和主流财经新闻，避免只凭量价信号做决策。"
    )
    atr_stop_line = (
        f"\n\nATR 自适应风险线：吊灯线约 {chandelier_stop:.2f}（= 22 日最高价 − 3×ATR），"
        "跌破即视为趋势假设失效，比固定百分比更贴合个股波动。"
        if chandelier_stop > 0 else ""
    )
    investment_plan = (
        f"跟踪倾向：{plan['action']}。\n\n"
        f"关注权重：{plan['position']}。\n\n"
        f"失效规则：{plan['stop']}。{atr_stop_line}\n\n"
        "适用前提：趋势因子维持在当前水平附近，且最大回撤没有继续扩大。"
    )
    research_team_decision = (
        f"正向理由：{strength_text}支撑当前评分，说明价格结构里有可跟踪的一面。\n\n"
        f"反向理由：{weakness_text}和{risk_level}风险等级限制了关注权重，"
        "如果观察位过高，收益风险比会变差。\n\n"
        f"研究结论：{profile['bias']}。这不是只看强弱标签的机械信号，"
        "需要把评分区间和风险结构一起看。"
    )
    trader_plan = (
        f"跟踪计划：{plan['action']}。\n\n"
        "观察条件：优先看回踩不破、缩量企稳后重新放量，或突破前高并伴随成交确认。\n\n"
        f"跟踪权重：{plan['position']}。\n\n"
        f"失效条件：{plan['stop']}。"
    )
    risk_management = (
        f"风险评级：{risk_level}。最大回撤约 {max_drawdown:.2%}，年化波动率约 {volatility:.2%}，夏普约 {sharpe:.2f}。\n\n"
        f"{risk_text}\n\n"
        f"风控因子 {_fmt_score(risk_control_value)}，若该项低于 45，应把它视为限制关注权重的硬条件，而不是普通扣分项。"
    )
    final_decision = (
        f"最终结论：{profile['bias']}，但执行上按“{plan['action']}”处理。"
        f"{symbol} 当前不是一句简单的 {signal} 就能概括："
        f"评分 {score:.1f} 说明它处在“{profile['label']}”区间，"
        f"强项为{strength_text}，短板为{weakness_text}。"
        f"后续按跟踪条件观察，核心是控制关注权重和失效线：{plan['position']}；{plan['stop']}。"
    )

    return {
        "analysis_id": task_id,
        "task_id": task_id,
        "symbol": symbol,
        "stock_symbol": symbol,
        "stock_code": symbol,
        "stock_name": stock_name,
        "market_type": (parameters or {}).get("market_type", "A股"),
        "analysis_date": (parameters or {}).get("analysis_date") or now[:10],
        "analysis_type": "saas-lite-quant",
        "current_price": latest.get("close") or latest.get("price") or 0,
        "price_change": latest.get("change") or 0,
        "price_change_percent": latest.get("pct_change") or 0,
        "volume": latest.get("volume") or 0,
        "summary": summary,
        "technical_analysis": technical,
        "fundamental_analysis": fundamental,
        "sentiment_analysis": sentiment,
        "news_analysis": news,
        "recommendation": final_decision,
        "risk_assessment": risk_text,
        "technical_score": score,
        "fundamental_score": min(100, max(0, score * 0.8)),
        "sentiment_score": 50,
        "overall_score": score,
        "data_sources": ["akshare", "local-quant-engine"],
        "llm_provider": "saas-lite",
        "llm_model": "local-quant",
        "analysis_duration": 1,
        "reports": {
            "summary": summary,
            "technical_analysis": technical,
            "fundamental_analysis": fundamental,
            "sentiment_analysis": sentiment,
            "news_analysis": news,
            "risk_assessment": risk_management,
            "market_report": technical,
            "fundamentals_report": fundamental,
            "news_report": news,
            "sentiment_report": sentiment,
            "investment_plan": investment_plan,
            "research_team_decision": research_team_decision,
            "trader_investment_plan": trader_plan,
            "risk_management_decision": risk_management,
            "final_trade_decision": final_decision,
        },
        "state": {
            "quant_result": quant_result,
            "parameters": parameters,
        },
        "created_at": now,
        "updated_at": now,
    }


def _normalize_deep_rating(rating: str) -> str:
    rating_map = {
        "涔板叆": "积极关注",
        "鎸佹湁": "继续跟踪",
        "瑙傚療": "观察",
        "鍥為伩": "回避",
        "买入": "积极关注",
        "持有": "继续跟踪",
        "观察": "观察",
        "回避": "回避",
    }
    return rating_map.get(str(rating or "").strip(), "观察")


def _format_deep_chain(chain: dict[str, Any]) -> str:
    if not chain:
        return "暂无产业链结构化数据。"
    labels = {"upstream": "上游", "midstream": "中游/公司环节", "downstream": "下游"}
    sections: list[str] = []
    for key in ("upstream", "midstream", "downstream"):
        entries = chain.get(key) or []
        if not entries:
            continue
        lines = []
        for item in entries:
            name = item.get("name") if isinstance(item, dict) else str(item)
            companies = item.get("companies") if isinstance(item, dict) else []
            company_text = "、".join([str(company) for company in companies]) if companies else "暂无代表公司"
            lines.append(f"- {name}：{company_text}")
        sections.append(f"### {labels[key]}\n" + "\n".join(lines))
    return "\n\n".join(sections) if sections else "暂无产业链结构化数据。"


def _format_deep_quality(quality: dict[str, Any]) -> str:
    if not quality:
        return "暂无质量评分。"
    labels = {
        "fundamental": "基本面",
        "governance": "治理质量",
        "competitive": "竞争力",
        "growth": "成长性",
        "valuation": "估值合理性",
    }
    lines = [f"- {labels[key]}：{quality.get(key)}" for key in labels if quality.get(key) is not None]
    rationale = quality.get("rationale")
    if rationale:
        lines.append(f"\n结论：{rationale}")
    return "\n".join(lines)


def _format_deep_scenarios(scenarios: dict[str, Any]) -> str:
    if not scenarios:
        return "暂无情景测算数据。"
    labels = {"bear": "保守情景", "base": "中性情景", "bull": "乐观情景"}
    lines = []
    for key, label in labels.items():
        item = scenarios.get(key) or {}
        if item:
            lines.append(
                f"- {label}：收入 {item.get('revenue', '-')}，净利润 {item.get('net_profit', '-')}，价格中枢 {item.get('target_price', '-')}"
            )
    return "\n".join(lines) if lines else "暂无情景测算数据。"


def _format_deep_risks(risks: list[dict[str, Any]]) -> str:
    if not risks:
        return "暂无结构化风险清单。"
    return "\n".join([f"- {item.get('risk', '风险')}：{item.get('mitigation', '暂无应对策略')}" for item in risks])


def _format_deep_peers(peers: list[dict[str, Any]]) -> str:
    if not peers:
        return "暂无可比公司数据。"
    return "\n".join(
        [
            f"- {item.get('name', '-') or '-'}（{item.get('code', '-') or '-'}）：PE {item.get('pe', '-')}，ROE {item.get('roe', '-')}"
            for item in peers
        ]
    )


def _format_deep_tracking(plan: dict[str, Any]) -> str:
    metrics = plan.get("metrics") if isinstance(plan, dict) else []
    lines = [f"- {item.get('name', '指标')}：{item.get('threshold', '-')}" for item in metrics or []]
    next_review = plan.get("next_review") if isinstance(plan, dict) else ""
    if next_review:
        lines.append(f"\n复盘节奏：{next_review}")
    return "\n".join(lines) if lines else "暂无跟踪计划。"


def _deep_action_to_decision(rating: str, current_price: Any) -> dict[str, Any]:
    action_map = {
        "积极关注": "积极关注",
        "继续跟踪": "继续跟踪",
        "观察": "等待确认",
        "回避": "暂不纳入",
    }
    confidence_map = {"积极关注": 0.72, "继续跟踪": 0.62, "观察": 0.52, "回避": 0.42}
    return {
        "action": action_map.get(rating, "等待确认"),
        "reference_price": current_price or "-",
        "confidence": confidence_map.get(rating, 0.5),
        "risk_score": 0.45 if rating in {"积极关注", "继续跟踪"} else 0.62,
        "reasoning": f"Claude 深度分析给出“{rating}”倾向，SaaS Lite 量化层负责校验趋势、动量、RSI、流动性和回撤风险。",
    }


def _build_analysis_audit(result: dict[str, Any], deep_result: dict[str, Any] | None = None) -> dict[str, Any]:
    quant = (result.get("state") or {}).get("quant_result") or {}
    latest = quant.get("latest") or {}
    factors = quant.get("factors") or {}
    risk = quant.get("risk") or {}
    score = float(result.get("overall_score") or quant.get("score") or 0)
    evidence = [
        {"name": "量化评分", "value": round(score, 1), "source": "local-quant-engine"},
        {"name": "趋势因子", "value": round(float(factors.get("trend") or 0), 1), "source": "local-kline"},
        {"name": "动量因子", "value": round(float(factors.get("momentum") or 0), 1), "source": "local-kline"},
        {"name": "RSI", "value": round(float(factors.get("rsi") or 0), 1), "source": "local-kline"},
        {"name": "最大回撤", "value": round(float(risk.get("max_drawdown") or 0), 4), "source": "local-kline"},
    ]
    if latest.get("date"):
        evidence.append({"name": "行情日期", "value": latest.get("date"), "source": "local-store"})
    if deep_result:
        evidence.append({"name": "深研评级", "value": deep_result.get("overall_rating") or result.get("deep_rating"), "source": "deep-analysis-framework"})
        if deep_result.get("quality_score"):
            evidence.append({"name": "质量评分", "value": deep_result.get("quality_score"), "source": "deep-analysis-framework"})

    gaps: list[str] = []
    if not result.get("news_analysis") or "未连接新闻" in str(result.get("news_analysis")):
        gaps.append("新闻/公告/研报证据不足，舆情和催化结论需要降低权重。")
    if not deep_result:
        gaps.append("深度多智能体框架未返回结果，当前仅能使用量化画像。")
    elif not deep_result.get("peers"):
        gaps.append("可比公司样本不足，估值横向比较置信度偏低。")
    if not latest.get("date"):
        gaps.append("缺少最新行情日期，先检查本地数据同步。")

    risk_checks = []
    if float(factors.get("risk_control") or 0) < 45:
        risk_checks.append("风控因子低于 45，关注权重应明显降低。")
    if abs(float(risk.get("max_drawdown") or 0)) > 0.25:
        risk_checks.append("历史最大回撤超过 25%，不适合提高过高关注权重。")
    if float(factors.get("rsi") or 0) > 75:
        risk_checks.append("RSI 偏高，短线拥挤风险上升。")

    confidence = 0.72
    confidence -= min(0.25, len(gaps) * 0.08)
    confidence -= min(0.18, len(risk_checks) * 0.06)
    return {
        "confidence": round(max(0.35, confidence), 2),
        "evidence": evidence,
        "gaps": gaps,
        "risk_checks": risk_checks,
        "verdict": "证据较完整，可进入跟踪" if confidence >= 0.65 else "证据存在缺口，先观察或补数据",
    }


def _agent_stance(score: float, buy_line: float = 65, watch_line: float = 45) -> str:
    if score >= buy_line:
        return "支持跟踪"
    if score >= watch_line:
        return "等待确认"
    return "反对纳入"


def _build_agent_review(result: dict[str, Any], deep_result: dict[str, Any] | None = None) -> dict[str, Any]:
    quant = (result.get("state") or {}).get("quant_result") or {}
    factors = quant.get("factors") or {}
    risk = quant.get("risk") or {}
    score = float(result.get("overall_score") or quant.get("score") or 0)
    trend = float(factors.get("trend") or 0)
    momentum = float(factors.get("momentum") or 0)
    rsi = float(factors.get("rsi") or 0)
    risk_control = float(factors.get("risk_control") or 0)
    liquidity = float(factors.get("liquidity") or 0)
    max_drawdown = abs(float(risk.get("max_drawdown") or 0))

    quality = deep_result.get("quality_score") if isinstance(deep_result, dict) else {}
    if isinstance(quality, dict):
        quality_values = [float(v) for v in quality.values() if isinstance(v, (int, float))]
        fundamental_score = sum(quality_values) / len(quality_values) if quality_values else score * 0.75
    else:
        fundamental_score = float(quality or score * 0.75)

    has_news_gap = not result.get("news_analysis") or "未连接新闻" in str(result.get("news_analysis"))
    catalyst_score = 42 if has_news_gap else 58
    risk_score = max(0, min(100, risk_control - max_drawdown * 80 - (10 if rsi > 75 else 0)))

    agents = [
        {
            "role": "技术量化Agent",
            "stance": _agent_stance((trend + momentum + liquidity) / 3),
            "confidence": round(max(0.35, min(0.86, score / 100)), 2),
            "points": [
                f"趋势因子 {trend:.1f}，动量因子 {momentum:.1f}，流动性 {liquidity:.1f}",
                f"RSI {rsi:.1f}，用于识别拥挤或超卖区间",
            ],
        },
        {
            "role": "基本面Agent",
            "stance": _agent_stance(fundamental_score, 62, 45),
            "confidence": 0.66 if deep_result else 0.46,
            "points": [
                f"质量综合分约 {fundamental_score:.1f}",
                "深研框架已补充质量、估值、行业链和情景信息" if deep_result else "深研框架未返回，基本面只能降权处理",
            ],
        },
        {
            "role": "催化舆情Agent",
            "stance": _agent_stance(catalyst_score, 60, 45),
            "confidence": 0.42 if has_news_gap else 0.62,
            "points": [
                "新闻/公告/研报证据不足，催化项不作为核心依据" if has_news_gap else "新闻和催化信息已进入审查",
                "只把催化作为加分项，不能替代量化和风控确认",
            ],
        },
        {
            "role": "风险控制Agent",
            "stance": _agent_stance(risk_score, 60, 42),
            "confidence": 0.74,
            "points": [
                f"风控因子 {risk_control:.1f}，历史最大回撤 {max_drawdown:.2%}",
                "RSI 偏高，拥挤风险上升" if rsi > 75 else "未触发 RSI 过热拦截",
            ],
        },
        {
            "role": "反方审查Agent",
            "stance": "要求降权" if max_drawdown > 0.25 or has_news_gap else "暂无硬拦截",
            "confidence": 0.7,
            "points": [
                "如果观察位过高，收益回撤比会快速恶化",
                "缺少新闻/公告证据时，不能把题材叙事写成确定性结论" if has_news_gap else "需要继续跟踪是否出现放量滞涨或破位",
            ],
        },
    ]

    support = sum(1 for agent in agents if agent["stance"] in {"支持跟踪", "暂无硬拦截"})
    block = sum(1 for agent in agents if agent["stance"] in {"反对纳入", "要求降权"})
    if block >= 2 or risk_score < 42:
        final_action = "先观察，等待风险释放"
    elif support >= 3 and score >= 65:
        final_action = "进入重点跟踪池"
    else:
        final_action = "低权重跟踪，等待二次确认"

    return {
        "final_action": final_action,
        "consensus_score": round(max(0, min(100, score * 0.55 + fundamental_score * 0.2 + risk_score * 0.25)), 1),
        "agents": agents,
    }


def _format_agent_review(review: dict[str, Any]) -> str:
    lines = [
        f"最终结论：{review.get('final_action')}",
        f"共识评分：{review.get('consensus_score')}",
        "",
    ]
    for agent in review.get("agents", []):
        lines.append(f"### {agent.get('role')}：{agent.get('stance')}")
        for point in agent.get("points", []):
            lines.append(f"- {point}")
        lines.append("")
    return "\n".join(lines).strip()


def _format_analysis_audit(audit: dict[str, Any]) -> str:
    evidence = "\n".join([f"- {item['name']}：{item['value']}（{item['source']}）" for item in audit.get("evidence", [])])
    gaps = "\n".join([f"- {item}" for item in audit.get("gaps", [])]) or "- 暂无明显数据缺口"
    risks = "\n".join([f"- {item}" for item in audit.get("risk_checks", [])]) or "- 暂无硬性风控拦截"
    return (
        f"置信度：{float(audit.get('confidence') or 0):.0%}\n"
        f"结论：{audit.get('verdict')}\n\n"
        f"证据链：\n{evidence}\n\n"
        f"数据缺口：\n{gaps}\n\n"
        f"风控自检：\n{risks}"
    )


async def enrich_lite_result_with_deep_analysis(
    task_id: str,
    symbol: str,
    result: dict[str, Any],
    parameters: dict[str, Any],
    stock_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stock_name = (stock_meta or {}).get("name") or result.get("stock_name") or symbol
    try:
        import quantcore.analysis.deep_analysis.framework as deep_framework_module
        from quantcore.analysis.report.html_generator import HTMLReportGenerator

        deep_framework_module.CacheManager = LiteNoopCacheManager
        deep_llm = LiteDeepAnalysisLLM(symbol, stock_name, (parameters or {}).get("_llm_override"))
        framework = deep_framework_module.DeepAnalysisFramework(llm_client=deep_llm)
        deep_result = await asyncio.wait_for(
            asyncio.to_thread(framework.analyze, symbol, stock_name),
            timeout=90,
        )
        report_path = f"reports/{symbol}_deep_report.html"
        try:
            await asyncio.to_thread(HTMLReportGenerator().generate, deep_result, report_path)
            result["html_report_url"] = f"/reports/{symbol}_deep_report.html"
        except Exception as exc:
            result["html_report_error"] = str(exc)
    except Exception as exc:
        result["deep_analysis_error"] = str(exc)
        result["analysis_engine"] = "saas-lite-quant"
        return result

    rating = _normalize_deep_rating(deep_result.get("overall_rating", ""))
    quality = deep_result.get("quality_score") or {}
    risks = deep_result.get("risks") or []
    peers = deep_result.get("peers") or []
    tracking_plan = deep_result.get("tracking_plan") or {}
    industry_chain = deep_result.get("industry") or {}

    result["analysis_type"] = "saas-lite-quant+claude-deep-analysis"
    result["analysis_engine"] = "DeepAnalysisFramework + SaaS Lite QuantEngine"
    try:
        from quantcore.quant import llm as _qllm
        _llm_on = _qllm.available()
    except Exception:
        _llm_on = False
    result["llm_provider"] = "deepseek" if _llm_on else "deterministic-fallback"
    result["llm_model"] = parameters.get("deep_analysis_model") or ("deepseek-chat" if _llm_on else "lite-deterministic-adapter")
    result["model_info"] = "Claude 8步深度分析 + SaaS Lite量化画像"
    result["deep_rating"] = rating
    result["deep_analysis"] = deep_result
    result["decision"] = _deep_action_to_decision(rating, result.get("current_price"))
    result["analysis_audit"] = _build_analysis_audit(result, deep_result)
    result["agent_review"] = _build_agent_review(result, deep_result)

    original_summary = result.get("summary") or ""
    result["summary"] = (
        f"{symbol}（{stock_name}）已完成 Claude 8步深度分析与 SaaS Lite 量化画像。"
        f"深度评级为“{rating}”，量化综合评分为 {float(result.get('overall_score') or 0):.1f}。"
        f"{original_summary}"
    )
    result["recommendation"] = (
        f"综合结论：{rating}。执行上不要只看评级，应同时满足趋势、动量、成交额、RSI 和风险控制。"
        f"若量化信号转弱或风险指标恶化，应以风控优先。"
    )
    result["fundamental_analysis"] = _format_deep_quality(quality)
    result["risk_assessment"] = _format_deep_risks(risks)

    reports = dict(result.get("reports") or {})
    reports.update(
        {
            "deep_macro_positioning": deep_result.get("macro") or "暂无宏观定位。",
            "deep_industry_chain": _format_deep_chain(industry_chain),
            "deep_quality_score": _format_deep_quality(quality),
            "deep_scenario_analysis": _format_deep_scenarios(deep_result.get("scenarios") or {}),
            "deep_risk_checklist": _format_deep_risks(risks),
            "deep_tracking_plan": _format_deep_tracking(tracking_plan),
            "deep_self_check": _format_analysis_audit(result["analysis_audit"]),
            "deep_agent_review": _format_agent_review(result["agent_review"]),
            "deep_final_rating": f"Claude 深度评级：{rating}\n\n护城河判断：{deep_result.get('moat') or '暂无'}",
        }
    )
    result["reports"] = reports
    result["state"] = {
        **(result.get("state") or {}),
        "deep_analysis": deep_result,
        "deep_report_task_id": task_id,
    }
    return result
