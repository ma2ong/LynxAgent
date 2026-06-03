"""critic：对汇总去重后的候选逐只打分 0-10 + 拒绝理由。

- 有 LLM：把候选的量化因子 + 命中来源 + 指标快照投喂 chat_json，让模型打 0-10 分并给理由，
  同时参考 feedback_curator 的 avoid_tags（命中则倾向拒绝）。
- 无 LLM：用 composite_score（0-100）/10 折算为 0-10 分，按规则模板生成拒绝理由
  （RSI 极端 / 流动性枯竭 / 趋势走坏），并叠加 feedback factor_bias 对因子分加权。

去重：同一 symbol 多来源在汇总阶段已合并 sources，这里只对 (symbol) 唯一打分。
通过线：score >= keep_threshold 入 kept，否则 rejected。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .. import llm
from ..factors import compute_factor_scores

KEEP_THRESHOLD = 6.0  # 0-10


def _rule_score(factors: Dict[str, float], factor_bias: Dict[str, float]) -> float:
    """规则降级打分：加权因子均值（0-100）折算 0-10，叠加 feedback 因子乘数。"""
    if not factors:
        return 5.0
    biased = {k: v * float(factor_bias.get(k, 1.0)) for k, v in factors.items()}
    avg = sum(biased.values()) / max(1, len(biased))
    return round(max(0.0, min(10.0, avg / 10.0)), 2)


def _rule_reject_reason(factors: Dict[str, float], snapshot: Dict[str, float]) -> Optional[str]:
    """规则降级拒绝理由：命中即返回首条，否则 None（通过）。"""
    rsi = float(snapshot.get("rsi", 50.0)) if snapshot else 50.0
    if rsi >= 80:
        return "RSI极度超买，存在顶背离风险"
    if rsi <= 15:
        return "RSI极度超卖，人气极端低迷、流动性枯竭"
    if factors.get("liquidity", 50.0) < 30:
        return "流动性枯竭，成交不足以支撑建仓"
    if factors.get("trend", 50.0) < 35 and factors.get("momentum", 50.0) < 35:
        return "趋势与动量同时走坏，无右侧买点"
    return None


def _critique_one_rule(cand: Dict[str, object], factor_bias: Dict[str, float], avoid_tags: List[str]) -> Dict[str, object]:
    factors = cand.get("factors") or {}
    snapshot = cand.get("snapshot") or {}
    score = _rule_score(factors, factor_bias)
    reason = _rule_reject_reason(factors, snapshot)
    # feedback avoid_tags：候选理由命中即直接降级
    blob = str(cand.get("reason", "")) + " " + " ".join(cand.get("source_reasons", []))
    for tag in avoid_tags:
        if tag and tag in blob:
            reason = reason or f"命中历史优汰标签「{tag}」"
            score = min(score, KEEP_THRESHOLD - 0.5)
    keep = score >= KEEP_THRESHOLD and reason is None
    return {
        "symbol": cand["symbol"],
        "name": cand.get("name", ""),
        "score": score,
        "keep": keep,
        "reject_reason": None if keep else (reason or "综合评分不足"),
        "sources": cand.get("sources", []),
        "factors": factors,
        "llm_comment": "",
    }


def _critique_batch_llm(cands: List[Dict[str, object]], avoid_tags: List[str]) -> Optional[Dict[str, Dict[str, object]]]:
    """一次性把全部候选投喂 LLM 打分，返回 symbol -> {score, reason, comment}。失败 None。"""
    lines = []
    for c in cands:
        f = c.get("factors") or {}
        lines.append(
            f"{c['symbol']} {c.get('name','')} 来源={','.join(c.get('sources', []))} "
            f"因子={ {k: round(v,0) for k,v in f.items()} } 理由={c.get('reason','')}"
        )
    avoid = ("，应警惕这些信号: " + "、".join(avoid_tags)) if avoid_tags else ""
    prompt = (
        "你是严格的 A 股选股评审。下面每行是一只候选股的量化因子与入选来源。"
        f"请给每只打 0-10 分（越高越值得买），并给一句中文理由{avoid}。\n"
        '输出 JSON：{"picks":[{"symbol":"600519","score":7.5,"reason":"...","keep":true}]}。'
        "keep 表示是否建议保留（分>=6 且无重大风险）。\n\n" + "\n".join(lines)
    )
    data = llm.chat_json(prompt, system="你是 A 股量化选股评审，只输出 JSON。", max_tokens=3000)
    if not isinstance(data, dict):
        return None
    out: Dict[str, Dict[str, object]] = {}
    for p in data.get("picks", []) or []:
        if not isinstance(p, dict) or not p.get("symbol"):
            continue
        sym = str(p["symbol"]).zfill(6)
        out[sym] = {
            "score": max(0.0, min(10.0, float(p.get("score") or 0))),
            "reason": str(p.get("reason") or ""),
            "keep": bool(p.get("keep", True)),
        }
    return out or None


def run_critic(
    candidates: List[Dict[str, object]],
    factor_bias: Optional[Dict[str, float]] = None,
    avoid_tags: Optional[List[str]] = None,
) -> Dict[str, object]:
    """对去重候选打分。candidates 每项需含 symbol/name/sources/factors/snapshot/reason。

    返回 {stage, llm_used, kept:[...], rejected:[...]}，按 score 降序。
    """
    factor_bias = factor_bias or {}
    avoid_tags = avoid_tags or []
    llm_scores = _critique_batch_llm(candidates, avoid_tags) if (candidates and llm.available()) else None

    scored: List[Dict[str, object]] = []
    for cand in candidates:
        if llm_scores is not None and cand["symbol"] in llm_scores:
            ls = llm_scores[cand["symbol"]]
            score = round(float(ls["score"]), 2)
            keep = bool(ls["keep"]) and score >= KEEP_THRESHOLD
            scored.append({
                "symbol": cand["symbol"],
                "name": cand.get("name", ""),
                "score": score,
                "keep": keep,
                "reject_reason": None if keep else (ls["reason"] or "评审未通过"),
                "sources": cand.get("sources", []),
                "factors": cand.get("factors") or {},
                "llm_comment": ls["reason"],
            })
        else:
            scored.append(_critique_one_rule(cand, factor_bias, avoid_tags))

    scored.sort(key=lambda x: x["score"], reverse=True)
    return {
        "stage": "critic",
        "llm_used": llm_scores is not None,
        "kept": [s for s in scored if s["keep"]],
        "rejected": [s for s in scored if not s["keep"]],
    }


def attach_factors(symbol: str, df) -> Dict[str, float]:
    """辅助：给候选算因子分（critic 输入）。失败返回空 dict。"""
    try:
        return compute_factor_scores(df)
    except Exception:
        return {}
