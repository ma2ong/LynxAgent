"""theme_agent：选股线①（新闻 -> 热点板块/行业 + 热度打分 -> 板块内个股）。

- 输入：market_news_flow(limit) 的财经快讯流。
- 有 LLM：chat_json 让模型从快讯里提炼"热点主题/板块 + 热度0-100 + 相关行业关键词"。
- 无 LLM：降级为按快讯标题做关键词词频统计，取高频词当热点主题。

theme_agent 只产出"热点主题 + 关键词"，把哪些个股属于该主题的匹配交给 strategy/sources 那层做
（本仓库 universe 仅有 symbol/name，无现成的行业-主题映射，故用 name 关键词粗匹配作降级）。
失败时返回空主题列表，不抛栈。
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List

from .. import llm
from ..news import market_news_flow

# 词频降级时过滤的停用词/无信息高频词
_STOP = set("的 了 在 是 和 与 及 等 将 已 月 日 年 公司 股份 集团 国家 中国 市场 行业 板块 概念 涨 跌 亿 万 元".split())


def run_theme_agent(news_limit: int = 300, top_themes: int = 8) -> Dict[str, object]:
    """提炼热点主题；返回 {stage, llm_used, themes:[{name, heat, keywords}]}。"""
    news = market_news_flow(limit=news_limit)
    if not news:
        return {"stage": "theme_agent", "llm_used": False, "themes": [], "note": "无可用财经快讯流"}

    if llm.available():
        themes = _themes_via_llm(news, top_themes)
        if themes:
            return {"stage": "theme_agent", "llm_used": True, "themes": themes}

    return {"stage": "theme_agent", "llm_used": False, "themes": _themes_via_wordfreq(news, top_themes)}


def _themes_via_llm(news: List[Dict[str, str]], top_themes: int) -> List[Dict[str, object]]:
    digest = "\n".join(f"- {n.get('title','')} {n.get('content','')[:60]}" for n in news[:120])
    prompt = (
        "下面是今日 A 股财经快讯。请提炼出当前最热的投资主题/板块，"
        f"最多 {top_themes} 个，按热度从高到低。\n"
        '输出 JSON：{"themes":[{"name":"主题名","heat":0-100,"keywords":["关键词1","关键词2"]}]}\n'
        "keywords 用于在股票名称里粗匹配相关个股。\n\n" + digest
    )
    data = llm.chat_json(prompt, system="你是 A 股题材投资分析师，只输出 JSON。")
    if not isinstance(data, dict):
        return []
    out: List[Dict[str, object]] = []
    for t in (data.get("themes") or [])[:top_themes]:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        out.append({
            "name": str(t["name"]),
            "heat": max(0, min(100, int(t.get("heat") or 50))),
            "keywords": [str(k) for k in (t.get("keywords") or []) if str(k).strip()][:6],
        })
    return out


def _themes_via_wordfreq(news: List[Dict[str, str]], top_themes: int) -> List[Dict[str, object]]:
    """降级：从标题里抽中文 2-4 字词做词频，高频词当热点主题（热度=归一化词频）。"""
    counter: Counter = Counter()
    for n in news:
        for token in re.findall(r"[一-龥]{2,4}", n.get("title", "")):
            if token in _STOP or len(token) < 2:
                continue
            counter[token] += 1
    common = counter.most_common(top_themes)
    if not common:
        return []
    top_freq = common[0][1] or 1
    return [
        {"name": word, "heat": int(round(freq / top_freq * 100)), "keywords": [word]}
        for word, freq in common
    ]
