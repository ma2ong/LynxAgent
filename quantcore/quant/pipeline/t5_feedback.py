"""t5_feedback：反向优汰闭环。

读历史某轮 picks（kept），取每只 T+5（自选出日起 5 个交易日后）涨跌幅，
投喂 LLM 分析"之前选的为什么跌"，反向总结因子优汰规则，写回 feedback 存储，
供下一轮 feedback_curator 读取。无 LLM 时用规则：跌的多的来源/因子下调权重。

入口：run_t5_review(run_dir=None, picks=None) -> dict
- run_dir：某次 pipeline run 目录（读其 critic.json 的 kept）。
- picks：直接传 kept 列表（用于测试）。每项需含 symbol、name、可选 pick_date、factors、sources。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Dict, List, Optional

from .. import llm
from .feedback_curator import load_feedback_rules, save_feedback_rules
from .sources import get_ohlcv


def _t5_return(symbol: str, pick_date: Optional[str]) -> Optional[float]:
    """从 pick_date 起第 5 个交易日相对买入日的涨跌幅（小数）。无足够数据返回 None。"""
    df = get_ohlcv(symbol, days=120)
    if df is None or df.empty or "date" not in df.columns:
        return None
    df = df.sort_values("date").reset_index(drop=True)
    if pick_date:
        try:
            ts = datetime.strptime(pick_date[:10], "%Y-%m-%d")
            mask = df["date"] >= ts
            idx = int(df.index[mask][0]) if mask.any() else len(df) - 6
        except Exception:
            idx = len(df) - 6
    else:
        idx = len(df) - 6  # 无买入日：用倒数第6根当买入日
    if idx < 0 or idx + 5 >= len(df):
        return None
    base = float(df["close"].iloc[idx])
    after = float(df["close"].iloc[idx + 5])
    return round(after / base - 1, 4) if base else None


def _load_picks_from_run(run_dir: str) -> List[Dict[str, object]]:
    path = os.path.join(run_dir, "steps", "critic.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("kept", [])
    except Exception:
        return []


def run_t5_review(run_dir: Optional[str] = None, picks: Optional[List[Dict[str, object]]] = None) -> Dict[str, object]:
    """评估历史 picks 的 T+5 表现，产出并写回因子优汰规则。"""
    if picks is None:
        picks = _load_picks_from_run(run_dir) if run_dir else []
    if not picks:
        return {"stage": "t5_feedback", "reviewed": 0, "message": "无历史 picks 可复盘", "rules_written": False}

    reviewed: List[Dict[str, object]] = []
    for p in picks:
        ret = _t5_return(str(p.get("symbol")).zfill(6), p.get("pick_date"))
        reviewed.append({
            "symbol": str(p.get("symbol")).zfill(6),
            "name": p.get("name", ""),
            "sources": p.get("sources", []),
            "factors": p.get("factors", {}),
            "t5_return": ret,
        })

    losers = [r for r in reviewed if r["t5_return"] is not None and r["t5_return"] < 0]
    valid = [r for r in reviewed if r["t5_return"] is not None]
    win_rate = round(len([r for r in valid if r["t5_return"] >= 0]) / len(valid), 3) if valid else None

    rules = _rules_via_llm(reviewed, losers) if llm.available() else None
    if not rules:
        rules = _rules_via_heuristic(losers)

    # 写回 feedback 存储（供下一轮 feedback_curator）
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "rules": rules.get("factor_rules", []),
        "avoid_tags": rules.get("avoid_tags", []),
        "last_review": {"reviewed": len(reviewed), "win_rate": win_rate, "losers": len(losers)},
    }
    save_feedback_rules(payload)

    return {
        "stage": "t5_feedback",
        "reviewed": len(reviewed),
        "evaluated": len(valid),
        "win_rate": win_rate,
        "losers": len(losers),
        "llm_used": bool(llm.available() and rules.get("_llm")),
        "rules_written": True,
        "rules": payload["rules"],
        "avoid_tags": payload["avoid_tags"],
        "detail": reviewed,
    }


def _rules_via_llm(reviewed: List[Dict[str, object]], losers: List[Dict[str, object]]) -> Optional[Dict[str, object]]:
    lines = [
        f"{r['symbol']} {r.get('name','')} T+5={r['t5_return']} 来源={','.join(r.get('sources', []))} "
        f"因子={ {k: round(float(v),0) for k,v in (r.get('factors') or {}).items()} }"
        for r in reviewed if r["t5_return"] is not None
    ]
    if not lines:
        return None
    prompt = (
        "下面是上一轮选出的个股及其 T+5 涨跌幅。请分析为什么部分股票选出后下跌，"
        "反向总结需要优汰/降权的量化因子，以及应回避的特征标签。\n"
        '输出 JSON：{"factor_rules":[{"factor":"rsi","direction":"down","weight":0.85,"note":"..."}],'
        '"avoid_tags":["人气极端低迷","RSI极度超买"]}\n'
        "weight<1 表示该因子下调权重。\n\n" + "\n".join(lines)
    )
    data = llm.chat_json(prompt, system="你是量化因子复盘分析师，只输出 JSON。")
    if not isinstance(data, dict):
        return None
    data["_llm"] = True
    data.setdefault("factor_rules", [])
    data.setdefault("avoid_tags", [])
    return data


def _rules_via_heuristic(losers: List[Dict[str, object]]) -> Dict[str, object]:
    """降级：统计下跌股的高分因子，对其下调权重；下跌占比高的来源加 avoid_tag。"""
    if not losers:
        return {"factor_rules": [], "avoid_tags": [], "_llm": False}
    factor_sum: Dict[str, float] = {}
    for r in losers:
        for k, v in (r.get("factors") or {}).items():
            factor_sum[k] = factor_sum.get(k, 0.0) + float(v)
    rules = []
    for factor, total in sorted(factor_sum.items(), key=lambda x: x[1], reverse=True)[:2]:
        avg = total / len(losers)
        if avg >= 55:  # 下跌股该因子普遍偏高 -> 之前据此选的多跌，下调
            rules.append({
                "factor": factor,
                "direction": "down",
                "weight": 0.85,
                "note": f"T+5 下跌股的「{factor}」因子普遍偏高({avg:.0f})，下调其权重以反向优汰。",
            })
    return {"factor_rules": rules, "avoid_tags": [], "_llm": False}
