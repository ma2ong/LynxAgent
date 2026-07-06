"""每日盘报：盘前看点 + 收盘复盘（对标 stockgod /reports，A股适配）。

- facts 来自现有模块：环境标签(engine.market_context)、市场情绪、涨停分布、留痕胜率；
  竞价/催化剂属 app 层数据，由调用方经 extra 传入。
- LLM 可用 → chat_json 生成结构化 sections；不可用/输出非法 → 纯数据版 sections 降级。
- 结果存 daily_reports 表，date+kind 唯一，重复生成覆盖。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from . import llm
from .local_store import get_local_store

_CLOSE_TITLES = ["一句话定调", "主线分析", "热门追踪", "明日看点", "核心结论"]
_PRE_TITLES = ["一句话定调", "竞价看点", "催化剂雷达", "今日策略"]

_SYSTEM = (
    "你是A股每日盘报撰稿人。基于给定事实客观撰写，克制、不夸张、不承诺收益，"
    "结尾不用加免责声明。每节 2-4 句话，「一句话定调」只写一句。"
)


def _trim(value, max_list: int = 8, depth: int = 0):
    """递归裁剪嵌套结构里的长列表，控制进 LLM 的 token 量。"""
    if depth > 4:
        return None
    if isinstance(value, list):
        return [_trim(v, max_list, depth + 1) for v in value[:max_list]]
    if isinstance(value, dict):
        return {k: _trim(v, max_list, depth + 1) for k, v in value.items()}
    return value


def _gather_close_facts() -> Dict[str, object]:
    from .engine import market_context
    today = datetime.now().strftime("%Y-%m-%d")
    facts: Dict[str, object] = {"date": today}
    try:
        facts["market_context"] = market_context()
    except Exception:
        facts["market_context"] = {}
    try:
        from .market_sentiment import compute_market_sentiment
        start = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
        facts["sentiment"] = _trim(compute_market_sentiment(start, today, 24, None))
    except Exception:
        facts["sentiment"] = {}
    try:
        from .limit_up import compute_limit_up_distribution
        facts["limit_up"] = _trim(compute_limit_up_distribution(today, None))
    except Exception:
        facts["limit_up"] = {}
    try:
        facts["picks_stats"] = _trim(get_local_store().evaluate_picks(days=30))
    except Exception:
        facts["picks_stats"] = {}
    return facts


def _gather_premarket_facts(extra: Optional[Dict[str, object]]) -> Dict[str, object]:
    from .engine import market_context
    facts: Dict[str, object] = {"date": datetime.now().strftime("%Y-%m-%d")}
    try:
        facts["market_context"] = market_context()
    except Exception:
        facts["market_context"] = {}
    extra = extra or {}
    facts["auction"] = _trim(extra.get("auction")) or {}
    facts["catalysts"] = _trim(extra.get("catalysts")) or {}
    return facts


def _llm_sections(kind: str, facts: Dict[str, object]) -> Optional[List[Dict[str, str]]]:
    titles = _CLOSE_TITLES if kind == "close" else _PRE_TITLES
    prompt = (
        f"以下是今日A股{'收盘' if kind == 'close' else '盘前'}事实数据(JSON)：\n"
        f"{json.dumps(facts, ensure_ascii=False)}\n\n"
        f"请输出 JSON：{{\"sections\": [{{\"title\": 标题, \"body\": 正文}}, ...]}}，"
        f"标题依次为：{'、'.join(titles)}。缺数据的小节如实说明「今日数据不足」，不要编造。"
    )
    data = llm.chat_json(prompt, system=_SYSTEM, max_tokens=1800)
    if not isinstance(data, dict):
        return None
    sections = data.get("sections")
    if not isinstance(sections, list) or not sections:
        return None
    out: List[Dict[str, str]] = []
    for s in sections:
        if isinstance(s, dict) and s.get("title") and s.get("body"):
            out.append({"title": str(s["title"]), "body": str(s["body"])})
    return out or None


def _fallback_sections(kind: str, facts: Dict[str, object]) -> List[Dict[str, str]]:
    """无 LLM 时的纯数据版：把关键事实写成可读文本，页面照常渲染。"""
    ctx = facts.get("market_context") or {}
    tone = f"大盘环境「{ctx.get('state', '未知')}」。{ctx.get('advice', '')}".strip()
    sections = [{"title": "一句话定调", "body": tone or "今日数据不足。"}]
    if kind == "close":
        lu = facts.get("limit_up") or {}
        sections.append({"title": "热门追踪",
                         "body": f"涨停分布原始数据：{json.dumps(_trim(lu, 5), ensure_ascii=False)[:500]}"})
        ps = facts.get("picks_stats") or {}
        sections.append({"title": "核心结论",
                         "body": f"近30日选股池留痕统计：{json.dumps(_trim(ps, 5), ensure_ascii=False)[:500]}。"
                                 f"未配置 LLM 密钥，本报告为纯数据版。"})
    else:
        au = facts.get("auction") or {}
        sections.append({"title": "竞价看点",
                         "body": f"竞价原始数据：{json.dumps(_trim(au, 5), ensure_ascii=False)[:500]}"})
        sections.append({"title": "今日策略", "body": "未配置 LLM 密钥，本报告为纯数据版。"})
    return sections


def generate_report(kind: str, extra: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    """生成并落库一份盘报，返回 content dict。kind: premarket | close。"""
    if kind not in ("premarket", "close"):
        raise ValueError(f"unknown report kind: {kind}")
    facts = _gather_close_facts() if kind == "close" else _gather_premarket_facts(extra)
    sections = _llm_sections(kind, facts)
    used_llm = sections is not None
    if sections is None:
        sections = _fallback_sections(kind, facts)
    content: Dict[str, object] = {
        "kind": kind,
        "date": str(facts.get("date")),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "llm": used_llm,
        "sections": sections,
        "facts": facts,
    }
    get_local_store().save_daily_report(content["date"], kind, content)
    return content
