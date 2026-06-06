"""serenity 叙事→受益股推理引擎（A 股适配）。

5 步法：①过滤真实需求 ②财务翻译 ③受益标的 ④验证链 ⑤证伪点。
LLM 只推理，不产代码；代码由 serenity_resolve 从本地库落实。
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from .llm import chat_json
from .serenity_resolve import resolve_beneficiaries

_SCAN_SYS = (
    "你是 A 股事件驱动研究助手，使用 serenity 框架：从新闻里区分'纯叙事炒作'与"
    "'可观察的真实需求变化'，只对后者输出受益假设。你只做研究假设，不构成投资建议。"
)

_SCAN_PROMPT = """对下面这条新闻做 serenity 轻量分析，只输出 JSON：
新闻标题：{title}
新闻摘要：{summary}

JSON 字段：
- is_real_demand: bool（是否代表可观察的真实需求/供给变化，纯情绪炒作为 false）
- theme: 题材标签（简短）
- thesis: 受益财务逻辑一句话（需求变化→营收/利润路径）
- beneficiary_names: A股受益公司中文名数组（你已知的真实公司名，最多6个，宁缺毋滥）
- concepts: 相关概念标签数组（最多3个）
- validation: 1-4季度可验证的观测点
- falsification: 证伪点（出现什么则推翻该假设）
若 is_real_demand 为 false，其余字段可留空。"""


def scan_event(news: Dict[str, str], deep: bool = False) -> Optional[Dict[str, object]]:
    """对一条新闻做轻量 serenity 分析，返回事件卡或 None（纯叙事/失败）。"""
    title = str(news.get("title") or "").strip()
    summary = str(news.get("summary") or news.get("content") or title).strip()
    if not title:
        return None
    data = chat_json(_SCAN_PROMPT.format(title=title, summary=summary[:800]),
                     _SCAN_SYS, deep=deep, max_tokens=900)
    if not data or not data.get("is_real_demand"):
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

JSON 字段：
- demand_shift: 需求/供给变化的具体描述
- financial_translation: 对营收/毛利/现金流/资产负债的影响（分点）
- ranked_beneficiaries: 数组[{{name, why, elasticity}}]，按受益弹性排序
- validation_chain: 数组，1-4 季度逐季验证里程碑
- falsification_points: 数组，证伪信号
- risks: 主要风险
- position_note: 仓位/跟踪建议（研究性，非投顾）"""


def deep_report(theme: str, event: str, beneficiaries: List[Dict[str, str]]) -> Optional[Dict[str, object]]:
    """对一个题材跑完整 5 步深度报告。"""
    names = "、".join(b.get("name", "") for b in beneficiaries)
    data = chat_json(_DEEP_PROMPT.format(theme=theme, event=event, beneficiaries=names),
                     _DEEP_SYS, deep=True, max_tokens=2048)
    if not data:
        return None
    data["theme"] = theme
    data["ts"] = int(time.time())
    return data
