"""serenity 叙事→受益股推理引擎（A 股适配）。

5 步法：①过滤真实需求 ②财务翻译 ③受益标的 ④验证链 ⑤证伪点。
LLM 只推理，不产代码；代码由 serenity_resolve 从本地库落实。
"""
from __future__ import annotations

import re
import time
from typing import Dict, List, Optional

from .llm import chat_json
from .serenity_resolve import resolve_beneficiaries

# 显著性门槛：只保留 significance>=该值的事件（0-10）。
# 设 6 与证据阶梯封顶配合：传闻级封顶 5.0 恒低于门槛，确保传闻必被淘汰。
MIN_SIGNIFICANCE = 6

# 确定性预过滤：命中即判噪音，不再花 LLM token。
_NOISE_PATTERNS = re.compile(
    "|".join([
        # 市场面/资金面/指数点评
        "大盘", "沪指", "深成指", "创业板指", "上证指数", "科创50", "北证50",
        "三大指数", "两市成交", "北向资金", "南向资金", "主力资金", "资金净流",
        "资金流向", "板块轮动", "午评", "早评", "收评", "盘前", "盘后", "复盘",
        "涨停潮", "跌停潮", "涨停板分析", "涨停揭秘", "龙虎榜", "个股异动", "异常波动",
        # 境外指数/汇率播报
        "综合指数", "日经", "恒生指数", "道琼斯", "纳斯达克", "标普", "富时",
        "兑美元", "兑人民币", "汇率中间价",
        # 例行公司公告
        "回购股份", "拟回购", "股份回购", "现金分红", "派息", "拟派", "10派", "10转",
        "拟减持", "股东减持", "高管减持", "减持计划", "增持计划", "股权质押", "股份质押",
        "股份解禁", "限售股", "股东户数", "变更证券简称", "公司更名", "证券简称变更",
        # 卖方观点
        "维持买入评级", "维持增持评级", "给予买入评级", "上调评级", "下调评级", "目标价",
    ])
)


def is_noise_title(title: str) -> bool:
    """确定性预判：标题是否为无歧义的市场面/例行公告噪音。"""
    return bool(_NOISE_PATTERNS.search(title or ""))


# 七维加权评分
_SCORE_WEIGHTS = {
    "demand_certainty": 3,      # 需求确定性
    "transmission_clarity": 3,  # 传导清晰度
    "business_purity": 3,       # 业务纯度
    "cap_elasticity": 3,        # 市值弹性
    "market_neglect": 2,        # 市场忽视度
    "verification_speed": 2,    # 验证速度
    "downside_safety": 2,       # 下行安全度
}
# 证据阶梯封顶：传闻级压到门槛下淘汰
_TIER_CAP = {"一手": 10.0, "可核验": 10.0, "管理层": 9.0, "推断": 7.5, "传闻": 5.0}


def composite_significance(scores: Dict[str, object], evidence_tier: str = "") -> float:
    """七维加权 → 0-10 综合显著性，再按证据阶梯封顶。"""
    total = 0
    for key, weight in _SCORE_WEIGHTS.items():
        try:
            v = float(scores.get(key))  # type: ignore[union-attr]
        except (TypeError, ValueError, AttributeError):
            v = 0.0
        total += max(0.0, min(5.0, v)) * weight
    score = total / (5 * sum(_SCORE_WEIGHTS.values())) * 10
    return round(min(score, _TIER_CAP.get(evidence_tier, 10.0)), 1)


_SCAN_SYS = (
    "你是 A 股事件驱动研究员，用 serenity 框架做严格收录。【默认 qualified=false】："
    "只有新闻同时满足具体产业变化、清晰传导链、可映射 A 股受益标的、1-4 季度可验证，才 qualified=true。"
    "纯市场面/资金面、宏观泛谈、海外孤立事故、例行公告、卖方观点、没有A股业绩传导的新闻一律剔除。"
    "无论 qualified 真假，都必须照实给出 7 个维度的 scores。你只做研究假设，不构成投资建议。"
)

_SCAN_PROMPT = """判断下面这条快讯是否值得作为事件跟踪。【默认不合格 qualified=false】，必须满足全部收录条件才判 true。只输出 JSON。

新闻标题：{title}
新闻摘要：{summary}

【硬否决——命中任一条则 qualified=false】：
1. 大盘/指数涨跌、点位、北向资金流向等纯市场面描述
2. 纯宏观数据发布（CPI/PMI/社融等），且没有任何指向具体产业的抓手
3. 笼统的情绪面/资金面/板块轮动描述（"XX板块活跃""资金流入XX"）
4. 纯例行公司公告：分红、回购、股东增减持、人事变动、更名
5. 既无产业指向、又讲不出"→具体环节→具体公司业绩"传导链的纯口水内容
6. 海外事故/灾害/政治新闻，若不能清楚落到 A 股具体供需或订单链条
7. 单一公司小额、低确定性、难验证的普通经营动态

【收录条件——必须全部满足才 qualified=true】：
1. 催化具体：政策落地/订单中标/供需涨价/技术量产/产能认证/龙头资本动作/产业链瓶颈，至少命中一类
2. 传导清楚：能写出“变化 → 产业环节 → A股公司收入/毛利/订单/估值重估”的路径
3. 标的可信：beneficiary_names 中每家公司必须真实存在，且能说明为什么受益；宁缺毋滥
4. 可验证：1-4 个季度内能通过订单、价格、出货、财报、产能、政策文件等观察
5. 机会性：不是已经被广泛交易透支的泛题材；若已充分炒作，market_neglect 必须低分

【证据阶梯 evidence_tier】：一手（公告/政策文件/中标/财报/认证）> 可核验（行业标准/可核验数据）> 管理层（管理层口径）> 推断（媒体报道/分析）> 传闻（小作文/据说）。传闻级会被压到门槛下淘汰；其余等级凭七维评分高低决定去留。

JSON 字段：
- qualified: bool（是否通过上面全部审查）
- evidence_tier: 一手|可核验|管理层|推断|传闻（本条快讯的最强信源等级）
- scores: 对象，以下 7 个维度各打 1-5 整数分（无论 qualified 真假都必须照实填，主筛选靠分数而非二元判断）：
    - demand_certainty 需求确定性（真实可观察=5，纯想象/预期=1）
    - transmission_clarity 传导清晰度（变化→具体公司业绩链条越清晰越高）
    - business_purity 业务纯度（受益股主业纯正暴露于该需求=5；只占大集团零头=1）
    - cap_elasticity 市值弹性（小市值遇大需求冲击=5；大白马被稀释=1）
    - market_neglect 市场忽视度（尚未被普遍认知/炒作=5；已充分炒作=1）
    - verification_speed 验证速度（1-2季度可财务证实=5；要等数年=1）
    - downside_safety 下行安全度（风险小=5；高商誉/质押/单一客户/已大涨透支=1）
- stage: 观察|试探|验证中|降预期|证伪（当前所处阶段）
- theme: 题材标签（简短具体，如"固态电池量产"，不要泛泛的"新能源"）
- thesis: 传导链一句话（需求/供给变化 → 环节 → 公司业绩路径）
- evidence: 信源/证据一句话（公告/政策/订单/财报；若仅媒体转述要注明）
- beneficiary_names: A股受益公司全称数组（你确信真实存在的公司名，最多5个，宁缺毋滥；说不清为何受益的不要列）
- concepts: 相关概念标签数组（最多3个）
- validation: 1-4季度可验证的观测点
- falsification: 证伪点（出现什么则推翻该假设）
若 qualified=false，theme/thesis 等文字字段可留空，但 scores 仍需照实填。"""


def scan_event(news: Dict[str, str], deep: bool = False,
               min_significance: int = MIN_SIGNIFICANCE) -> Optional[Dict[str, object]]:
    """对一条新闻做严格 serenity 审查，返回事件卡或 None（噪音/不达标/失败）。"""
    title = str(news.get("title") or "").strip()
    summary = str(news.get("summary") or news.get("content") or title).strip()
    if not title or is_noise_title(title):
        return None
    data = chat_json(_SCAN_PROMPT.format(title=title, summary=summary[:800]),
                     _SCAN_SYS, deep=deep, max_tokens=1000)
    if not data or not data.get("qualified"):
        return None
    evidence_tier = str(data.get("evidence_tier") or "").strip()
    scores = data.get("scores") if isinstance(data.get("scores"), dict) else {}
    if scores:
        significance = composite_significance(scores, evidence_tier)
    else:
        try:
            significance = float(data.get("significance") or 0)
        except (TypeError, ValueError):
            significance = 0.0
    if significance < min_significance:
        return None
    beneficiaries = resolve_beneficiaries(
        list(data.get("beneficiary_names") or []),
        concepts=list(data.get("concepts") or []),
    )
    if not beneficiaries:
        return None
    return {
        "event": title,
        "theme": str(data.get("theme") or "").strip(),
        "thesis": str(data.get("thesis") or "").strip(),
        "evidence": str(data.get("evidence") or "").strip(),
        "evidence_tier": evidence_tier,
        "stage": str(data.get("stage") or "").strip(),
        "significance": significance,
        "scores": scores,
        "beneficiaries": beneficiaries,
        "validation": str(data.get("validation") or "").strip(),
        "falsification": str(data.get("falsification") or "").strip(),
        "source_url": str(news.get("url") or ""),
        "ts": int(time.time()),
    }


_DEEP_SYS = _SCAN_SYS + " 现在做完整 5 步深度分析。"

_DEEP_PROMPT = """对题材『{theme}』做完整 serenity 深度分析，只输出 JSON：
背景事件：{event}
已知受益股：{beneficiaries}

【纪律】
- 逆向逐跳显形传导链：下游需求 → L1 → L2 → … → 受益公司，每跳标证据等级（已证实/推断/推测）。
- 精度降级：凡关键假设属"推断/推测"级，弹性与影响只给【方向或数量级】，不要给看似精确的百分比。
- A股红旗扫描：单一大客户依赖、商誉/股权质押过高、近期已大幅炒作透支、纯概念无业绩兑现、限售解禁压力——命中要在 risks 点明。

JSON 字段：
- demand_shift: 需求/供给变化的具体描述
- transmission_chain: 数组，逆向逐跳[{{layer, role, evidence_level}}]，evidence_level∈已证实|推断|推测
- financial_translation: 对营收/毛利/现金流/资产负债的影响（分点；推断级只给方向/数量级）
- ranked_beneficiaries: 数组[{{name, why, elasticity, evidence_level}}]，按受益弹性排序
- validation_chain: 数组，1-4 季度逐季验证里程碑
- falsification_points: 数组，证伪信号
- red_flags: 数组，命中的 A股红旗（无则空数组）
- risks: 主要风险
- tracking_note: 跟踪备注（只写观察点、验证节奏和证伪条件，不给仓位或交易动作）"""

_REVIEW_SYS = (
    "你是独立复核员，立场独立、只挑刺不背书。审查下面这份 serenity 深度分析，"
    "找出：编造或夸大的数字/受益关系、被当成已证实的推断、传导链断点、迎合式结论、"
    "精度未按证据降级之处。只输出 JSON。"
)
_REVIEW_PROMPT = """待复核报告（JSON）：
{report}

只输出 JSON：
- verdict: pass|revise（有 load-bearing 错误则 revise）
- issues: 数组，发现的问题（每条具体指出哪个字段/哪个结论）
- corrections: 数组，建议的纠正（可空）"""


def _review_report(report: Dict[str, object]) -> Optional[Dict[str, object]]:
    """独立复核 sub-agent：二次 LLM 唱反调，失败返回 None。"""
    import json
    try:
        payload = json.dumps(report, ensure_ascii=False)[:3000]
        data = chat_json(_REVIEW_PROMPT.format(report=payload), _REVIEW_SYS,
                         deep=True, max_tokens=1200)
    except Exception:
        return None
    if not data or not data.get("verdict"):
        return None
    return {
        "verdict": str(data.get("verdict") or "").strip(),
        "issues": list(data.get("issues") or []),
        "corrections": list(data.get("corrections") or []),
    }


def deep_report(theme: str, event: str, beneficiaries: List[Dict[str, str]],
                review: bool = True) -> Optional[Dict[str, object]]:
    """对一个题材跑完整 5 步深度报告，默认附独立复核。"""
    names = "、".join(b.get("name", "") for b in beneficiaries)
    data = chat_json(_DEEP_PROMPT.format(theme=theme, event=event, beneficiaries=names),
                     _DEEP_SYS, deep=True, max_tokens=2048)
    if not data:
        return None
    data["theme"] = theme
    data["ts"] = int(time.time())
    if review:
        rev = _review_report(data)
        if rev:
            data["review"] = rev
    return data
