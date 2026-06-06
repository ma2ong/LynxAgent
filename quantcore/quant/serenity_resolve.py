"""把 LLM 产出的受益公司名/概念落实成真实、可投资的 A 股代码。

LLM 只负责"谁受益"，代码一律来自本地库：
- 公司名 → stock_meta 名称匹配（精确 / 包含）
- 概念 → 反转 concept_lookup 的 {股名:概念} 缓存得到 {概念:[股名]}
找不到或不可投资的一律丢弃，绝不编造。
"""
from __future__ import annotations

from typing import Dict, List

from . import concept_lookup
from .local_store import get_local_store
from .universe import is_blocked_name, is_investable


def _load_meta() -> List[Dict[str, object]]:
    return get_local_store().load_meta()


def _name_to_symbol(meta: List[Dict[str, object]]) -> Dict[str, str]:
    return {str(m["name"]).strip(): str(m["symbol"]) for m in meta if m.get("name")}


def _concept_to_names() -> Dict[str, List[str]]:
    """反转 concept_lookup 缓存：{概念标签: [股票名,...]}。"""
    inv: Dict[str, List[str]] = {}
    for sname, concept in getattr(concept_lookup, "_cache", {}).items():
        inv.setdefault(str(concept), []).append(str(sname))
    return inv


def resolve_beneficiaries(names: List[str], concepts: List[str] | None = None,
                          max_out: int = 8) -> List[Dict[str, str]]:
    """names: LLM 给的受益公司名；concepts: 题材标签（可选，用于扩展）。
    返回去重后的可投资受益股 [{symbol,name}]。"""
    meta = _load_meta()
    name2sym = _name_to_symbol(meta)

    # 候选股票名集合
    candidates: List[str] = list(names or [])
    if concepts:
        inv = _concept_to_names()
        for c in concepts:
            candidates.extend(inv.get(str(c).strip(), []))

    seen: set = set()
    out: List[Dict[str, str]] = []
    for raw in candidates:
        cand = str(raw).strip()
        if not cand:
            continue
        # 精确匹配优先，否则包含匹配
        sym = name2sym.get(cand)
        full_name = cand
        if not sym:
            for nm, s in name2sym.items():
                if cand in nm or nm in cand:
                    sym, full_name = s, nm
                    break
        if not sym or sym in seen:
            continue
        if is_blocked_name(full_name) or not is_investable(sym, full_name):
            continue
        seen.add(sym)
        out.append({"symbol": sym, "name": full_name})
        if len(out) >= max_out:
            break
    return out
