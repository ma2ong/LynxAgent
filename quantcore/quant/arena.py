"""Arena：5 个 AI 人格各管 100 万虚拟盘，每交易日收盘后调仓一次（A股 T+1，盘中无意义）。

- 每人格一次 LLM 调用生成调仓指令；LLM 不可用/失败 → 持仓不动，NAV 照常结算；
- 先卖后买、收盘价成交、100 股整手、成本复用 backtest（买 0.03%/卖 0.08%）、最多 5 只持仓；
- 幂等：(date, persona) 已有 NAV 则跳过，重复触发不会双重交易；
- prices/names 由调用方传入（15:40 实时快照价=收盘价；本地日线此时未同步——批次 1 教训）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import llm
from .backtest import BUY_COST, SELL_COST
from .investor_panel import _PERSONAS
from .local_store import get_local_store

MAX_POSITIONS = 5


def _ask_persona(persona: Dict[str, str], holdings: List[Dict], candidates: List[Dict],
                 cash: float, nav: float) -> Optional[Dict]:
    """一次 LLM 调用生成该人格的调仓指令。失败返回 None（持仓不动）。"""
    hold_lines = "\n".join(
        f"  - {h['symbol']} {h.get('name') or ''} {h['shares']}股 成本{h['avg_cost']:.2f} 现价{h.get('price') or '?'}"
        for h in holdings) or "  （空仓）"
    cand_lines = "\n".join(
        f"  - {c['symbol']} {c.get('name') or ''} 现价{c.get('price') or '?'}"
        for c in candidates) or "  （今日无候选）"
    prompt = (
        f"你是 A 股虚拟盘投资人「{persona['persona']}」：{persona['desc']}。\n"
        f"组合现状：现金 {cash:,.0f} 元，净值 {nav:,.0f} 元。\n"
        f"当前持仓：\n{hold_lines}\n"
        f"今日候选池（只能从候选池或当前持仓中操作）：\n{cand_lines}\n"
        "按你的方法论决定今日收盘调仓。规则：最多持有 5 只；buys 的 weight_pct 是目标金额占净值百分比，"
        "合计不得超过卖出后可用现金；不想动就都给空数组。\n"
        '输出 JSON：{"sells":[{"symbol":"600000","reason":"一句话"}],'
        '"buys":[{"symbol":"600001","weight_pct":20,"reason":"一句话"}],"comment":"今日一句话判词"}'
    )
    try:
        result = llm.chat_json(prompt)
    except Exception:
        return None
    return result if isinstance(result, dict) else None


def _settle_nav(positions: List[Dict], cash: float, prices: Dict[str, float]) -> float:
    value = sum(p["shares"] * float(prices.get(p["symbol"]) or p["avg_cost"]) for p in positions)
    return cash + value


def run_arena_daily(date: str, candidates: List[str], prices: Dict[str, float],
                    names: Dict[str, str]) -> Dict[str, object]:
    """全体人格跑一天：调仓（LLM 可用时）+ NAV 结算落库。返回摘要。"""
    store = get_local_store()
    summary: List[Dict[str, object]] = []
    cand_items = [{"symbol": s, "name": names.get(s, ""), "price": prices.get(s)}
                  for s in candidates if prices.get(s)]
    for persona in _PERSONAS:
        pname = persona["persona"]
        if store.arena_nav_exists(date, pname):
            continue
        cash = store.arena_cash(pname)
        positions = store.arena_positions(pname)
        for p in positions:
            p["name"] = names.get(p["symbol"], "")
            p["price"] = prices.get(p["symbol"])
        nav_before = _settle_nav(positions, cash, prices)
        comment = ""
        orders = None
        if llm.available():
            orders = _ask_persona(persona, positions, cand_items, cash, nav_before)
        if isinstance(orders, dict):
            comment = str(orders.get("comment") or "")
            allowed = {c["symbol"] for c in cand_items} | {p["symbol"] for p in positions}
            held = {p["symbol"]: p for p in positions}
            # 先卖
            for o in (orders.get("sells") or []):
                sym = str(o.get("symbol") or "").zfill(6)
                pos = held.get(sym)
                price = prices.get(sym)
                if not pos or not price:
                    continue
                proceeds = pos["shares"] * price * (1 - SELL_COST)
                cash += proceeds
                store.delete_arena_position(pname, sym)
                store.insert_arena_trade(date, pname, sym, "sell", price, pos["shares"],
                                         str(o.get("reason") or ""))
                del held[sym]
            # 后买
            for o in (orders.get("buys") or []):
                sym = str(o.get("symbol") or "").zfill(6)
                price = prices.get(sym)
                if sym not in allowed or not price or sym in held or len(held) >= MAX_POSITIONS:
                    continue
                try:
                    weight = max(0.0, min(100.0, float(o.get("weight_pct") or 0)))
                except (TypeError, ValueError):
                    continue
                budget = min(nav_before * weight / 100.0, cash)
                shares = int(budget / (price * (1 + BUY_COST)) / 100) * 100
                if shares <= 0:
                    continue
                cost = shares * price * (1 + BUY_COST)
                cash -= cost
                store.upsert_arena_position(pname, sym, shares, round(cost / shares, 4))
                store.insert_arena_trade(date, pname, sym, "buy", price, shares,
                                         str(o.get("reason") or ""))
                held[sym] = {"symbol": sym, "shares": shares}
            store.set_arena_cash(pname, cash)
        nav = _settle_nav(store.arena_positions(pname), cash, prices)
        store.save_arena_nav(date, pname, nav, cash, comment)
        summary.append({"persona": pname, "style": persona["style"], "nav": round(nav, 2),
                        "cash": round(cash, 2), "comment": comment,
                        "positions": len(store.arena_positions(pname))})
    return {"date": date, "personas": summary}
