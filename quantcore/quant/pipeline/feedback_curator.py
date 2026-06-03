"""feedback_curator：流水线第一步。

职责：读取历史反向优汰沉淀下来的"因子调整规则"（由 t5_feedback 写入的一个 JSON），
转成本轮选股/打分要用的偏好（factor_bias）与提示语，回喂给后续 stage。

存储：runtime/pipeline_runs/feedback_rules.json —— 一个简单 JSON，结构：
    {"updated_at": "...", "rules": [{"factor": "rsi", "direction": "down",
      "note": "RSI极端超买的之前选了多跌，下调权重", "weight": 0.8}], "avoid_tags": ["人气极端低迷"]}

无文件时返回空规则（首轮冷启动），不抛栈。
"""
from __future__ import annotations

import json
import os
from typing import Dict, List

FEEDBACK_STORE = os.path.join("runtime", "pipeline_runs", "feedback_rules.json")


def _store_path() -> str:
    os.makedirs(os.path.dirname(FEEDBACK_STORE), exist_ok=True)
    return FEEDBACK_STORE


def load_feedback_rules() -> Dict[str, object]:
    """读取历史反馈规则；无文件/损坏返回空结构。"""
    path = _store_path()
    if not os.path.exists(path):
        return {"updated_at": None, "rules": [], "avoid_tags": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("rules", [])
        data.setdefault("avoid_tags", [])
        return data
    except Exception:
        return {"updated_at": None, "rules": [], "avoid_tags": []}


def save_feedback_rules(rules: Dict[str, object]) -> None:
    """供 t5_feedback 写回。"""
    path = _store_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)


def run_feedback_curator() -> Dict[str, object]:
    """流水线 stage：把历史规则整理成本轮可用的偏好。

    输出：
      - factor_bias: {factor: multiplier}  critic 打分时对该因子分数的乘数（<1 降权 / >1 加权）
      - avoid_tags: [str]  命中这些拒绝标签的候选直接降级
      - notes: [str]  人类可读的本轮选股指导
    """
    raw = load_feedback_rules()
    factor_bias: Dict[str, float] = {}
    notes: List[str] = []
    for rule in raw.get("rules", []):
        factor = str(rule.get("factor") or "").strip()
        if not factor:
            continue
        weight = float(rule.get("weight") or 1.0)
        factor_bias[factor] = round(weight, 3)
        if rule.get("note"):
            notes.append(str(rule["note"]))

    avoid_tags = [str(t) for t in raw.get("avoid_tags", []) if str(t).strip()]
    if not raw.get("rules"):
        notes.append("冷启动：暂无历史反向优汰规则，本轮按默认因子选股。")

    return {
        "stage": "feedback_curator",
        "source_updated_at": raw.get("updated_at"),
        "factor_bias": factor_bias,
        "avoid_tags": avoid_tags,
        "notes": notes,
    }
