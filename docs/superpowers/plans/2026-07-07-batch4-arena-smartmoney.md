# 批次 4：Arena 虚拟盘 + 聪明钱 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ① Arena：5 个 AI 人格（复用 investor_panel 五风格）各管 100 万虚拟资金，每交易日收盘后 LLM 生成调仓指令按收盘价成交（含 A 股交易成本），新页 `/arena` 排行榜+人格详情。② 聪明钱：龙虎榜活跃席位榜 + 席位胜率排行 + 基金共识重仓，新页 `/smart-money`。

**Architecture:** Arena 核心逻辑 `quantcore/quant/arena.py` 纯函数化（prices/candidates 由调用方传入，LLM 可 mock），仓储表挂 LocalQuantStore（arena_state/arena_positions/arena_trades/arena_nav）；cron 15:40（收盘报 15:35 之后）挂现有 `_ml_factor_scheduler`，交易日守卫 `_is_trading_day_now`，结算价用实时快照（15:40 快照价=收盘价，本地日线此时未同步——批次 1 教训）。聪明钱 `quantcore/quant/smart_money.py`：akshare 三接口薄封装 + 可测的纯 DataFrame 变换，端点挂 routers/quant.py（同 dragon_tiger 模式，6h 缓存）。

**Tech Stack:** SQLite、FastAPI、APScheduler（现有实例）、akshare、`llm.chat_json`、Vue3 + Element Plus + ECharts LineChart（已注册）。

**数据质量验证结论（2026-07-07 实测，spec 要求实施前先验）:**
- `ak.stock_lhb_hyyyb_em(start,end)` 活跃营业部 ✅（近 30 天 6404 行：营业部名称/买卖总额/买入股票）
- `ak.stock_lhb_yybph_em(symbol="近一月")` 营业部排行 ✅（1481 行：上榜后 1/2/3/5/10 天平均涨幅+上涨概率）
- `ak.stock_report_fund_hold(symbol="基金持仓", date=季度末)` 基金重仓 ✅（2026Q1 3914 行：持有基金家数/持股市值/增减仓）
- ~~北向持股~~ ❌ `stock_hsgt_hold_stock_em` 返回 None——港交所 2024-08 起停止披露每日北向持股，**砍掉**（spec 预留了此决策）
- 机构买卖统计 `stock_lhb_jgmmtj_em` ✅ 可用但与活跃席位榜信息重叠，YAGNI 不做

**Arena 关键决策:**
- 每人格每日一次 LLM 调用（5 次/日，独立失败互不影响）；LLM 不可用/失败 → 该人格持仓不动，NAV 照常结算
- 候选池 = 当日四池留痕并集（每池 rank 前 10，去重）；人格只能买候选池或已持仓股票
- 交易规则：先卖后买、收盘价成交、A 股 100 股整手向下取整、成本复用 backtest（买 0.03%/卖 0.08%）、每人格最多 5 只持仓
- 幂等：(date, persona) 已有 NAV 记录则跳过该人格——cron 重复触发/手动重跑不会双重交易
- 手动触发 `POST /api/lite/arena/run`（不设交易日守卫，便于验证；cron 才有守卫）

**实施偏离记录（执行后回写）:**
（暂无）

**约定:** 测试 `python -m pytest`（仓库根）；后端改动需重启（无 --reload）；A股红涨绿跌；commit message 英文 + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`；每 Task 一个 commit。

---

### Task 1: Arena 仓储层（LocalQuantStore）

**Files:**
- Modify: `quantcore/quant/local_store.py`（_SCHEMA 末尾 + panel 方法后新增方法组）
- Test: `tests/test_arena.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_arena.py`：

```python
"""Arena 虚拟盘（仓储 + 交易执行 + 结算）回归测试。"""
import pytest

from quantcore.quant.local_store import LocalQuantStore


@pytest.fixture()
def store(tmp_path):
    return LocalQuantStore(str(tmp_path / "test.sqlite"))


def test_arena_cash_defaults_to_one_million(store):
    assert store.arena_cash("价值派") == 1_000_000.0
    store.set_arena_cash("价值派", 500_000.0)
    assert store.arena_cash("价值派") == 500_000.0
    assert store.arena_cash("趋势派") == 1_000_000.0  # 互不影响


def test_arena_positions_roundtrip(store):
    assert store.arena_positions("价值派") == []
    store.upsert_arena_position("价值派", "600001", 1000, 10.5)
    store.upsert_arena_position("价值派", "600001", 1500, 11.0)  # 覆盖
    pos = store.arena_positions("价值派")
    assert pos == [{"symbol": "600001", "shares": 1500, "avg_cost": 11.0}]
    store.delete_arena_position("价值派", "600001")
    assert store.arena_positions("价值派") == []


def test_arena_trades_log(store):
    store.insert_arena_trade("2026-07-07", "价值派", "600001", "buy", 10.0, 1000, "低估买入")
    store.insert_arena_trade("2026-07-07", "价值派", "600001", "sell", 11.0, 1000, "止盈")
    trades = store.load_arena_trades("价值派", limit=10)
    assert len(trades) == 2
    assert trades[0]["side"] == "sell"  # 最新在前
    assert trades[0]["reason"] == "止盈"


def test_arena_nav_roundtrip_and_series(store):
    assert not store.arena_nav_exists("2026-07-07", "价值派")
    store.save_arena_nav("2026-07-07", "价值派", 1_010_000.0, 200_000.0, "看多科技")
    store.save_arena_nav("2026-07-08", "价值派", 1_020_000.0, 150_000.0, "")
    store.save_arena_nav("2026-07-07", "趋势派", 990_000.0, 990_000.0, "")
    assert store.arena_nav_exists("2026-07-07", "价值派")
    series = store.load_arena_nav_series()
    assert series["价值派"] == [
        {"date": "2026-07-07", "nav": 1_010_000.0, "comment": "看多科技"},
        {"date": "2026-07-08", "nav": 1_020_000.0, "comment": ""},
    ]
    assert len(series["趋势派"]) == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_arena.py -v`
Expected: 4 FAIL（AttributeError arena_cash 等）

- [ ] **Step 3: 实现**

`_SCHEMA` 末尾（panel_scores 建表后、闭合 `"""` 前）追加：

```sql
CREATE TABLE IF NOT EXISTS arena_state (
    persona TEXT PRIMARY KEY,
    cash REAL
);
CREATE TABLE IF NOT EXISTS arena_positions (
    persona TEXT,
    symbol TEXT,
    shares INTEGER,
    avg_cost REAL,
    PRIMARY KEY (persona, symbol)
);
CREATE TABLE IF NOT EXISTS arena_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    persona TEXT,
    symbol TEXT,
    side TEXT,
    price REAL,
    shares INTEGER,
    reason TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS arena_nav (
    date TEXT,
    persona TEXT,
    nav REAL,
    cash REAL,
    comment TEXT,
    PRIMARY KEY (date, persona)
);
```

`LocalQuantStore` 类中（`load_picks_symbols` 方法之后）新增：

```python
    # ---- Arena 虚拟盘 ----
    ARENA_INITIAL_CASH = 1_000_000.0

    def arena_cash(self, persona: str) -> float:
        row = self._conn().execute("SELECT cash FROM arena_state WHERE persona=?", (persona,)).fetchone()
        return float(row[0]) if row else self.ARENA_INITIAL_CASH

    def set_arena_cash(self, persona: str, cash: float) -> None:
        conn = self._conn()
        conn.execute("INSERT OR REPLACE INTO arena_state(persona, cash) VALUES(?,?)", (persona, float(cash)))
        conn.commit()

    def arena_positions(self, persona: str) -> List[Dict[str, object]]:
        rows = self._conn().execute(
            "SELECT symbol, shares, avg_cost FROM arena_positions WHERE persona=? ORDER BY symbol",
            (persona,)).fetchall()
        return [{"symbol": r[0], "shares": int(r[1]), "avg_cost": float(r[2])} for r in rows]

    def upsert_arena_position(self, persona: str, symbol: str, shares: int, avg_cost: float) -> None:
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO arena_positions(persona, symbol, shares, avg_cost) VALUES(?,?,?,?)",
            (persona, symbol, int(shares), float(avg_cost)))
        conn.commit()

    def delete_arena_position(self, persona: str, symbol: str) -> None:
        conn = self._conn()
        conn.execute("DELETE FROM arena_positions WHERE persona=? AND symbol=?", (persona, symbol))
        conn.commit()

    def insert_arena_trade(self, date: str, persona: str, symbol: str, side: str,
                           price: float, shares: int, reason: str) -> None:
        from datetime import datetime
        conn = self._conn()
        conn.execute(
            "INSERT INTO arena_trades(date, persona, symbol, side, price, shares, reason, created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (date, persona, symbol, side, float(price), int(shares), str(reason or ""),
             datetime.now().isoformat(timespec="seconds")))
        conn.commit()

    def load_arena_trades(self, persona: str, limit: int = 50) -> List[Dict[str, object]]:
        rows = self._conn().execute(
            "SELECT date, symbol, side, price, shares, reason FROM arena_trades"
            " WHERE persona=? ORDER BY id DESC LIMIT ?", (persona, int(limit))).fetchall()
        return [{"date": r[0], "symbol": r[1], "side": r[2], "price": float(r[3]),
                 "shares": int(r[4]), "reason": r[5]} for r in rows]

    def arena_nav_exists(self, date: str, persona: str) -> bool:
        return self._conn().execute(
            "SELECT 1 FROM arena_nav WHERE date=? AND persona=?", (date, persona)).fetchone() is not None

    def save_arena_nav(self, date: str, persona: str, nav: float, cash: float, comment: str = "") -> None:
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO arena_nav(date, persona, nav, cash, comment) VALUES(?,?,?,?,?)",
            (date, persona, float(nav), float(cash), str(comment or "")))
        conn.commit()

    def load_arena_nav_series(self) -> Dict[str, List[Dict[str, object]]]:
        out: Dict[str, List[Dict[str, object]]] = {}
        for date, persona, nav, comment in self._conn().execute(
                "SELECT date, persona, nav, comment FROM arena_nav ORDER BY date").fetchall():
            out.setdefault(persona, []).append({"date": date, "nav": float(nav), "comment": comment or ""})
        return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_arena.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add quantcore/quant/local_store.py tests/test_arena.py
git commit -m "feat(arena): portfolio state, trades and nav storage on LocalQuantStore"
```

---

### Task 2: Arena 核心逻辑（quantcore/quant/arena.py）

**Files:**
- Create: `quantcore/quant/arena.py`
- Test: `tests/test_arena.py`（追加）

- [ ] **Step 1: 追加失败测试**

`tests/test_arena.py` 末尾追加：

```python
from quantcore.quant import arena


PRICES = {"600001": 10.0, "600002": 20.0, "600003": 40.0}
NAMES = {"600001": "甲", "600002": "乙", "600003": "丙"}


def _run(store, monkeypatch, orders_by_persona, llm_ok=True):
    monkeypatch.setattr(arena, "get_local_store", lambda: store)
    monkeypatch.setattr(arena.llm, "available", lambda: llm_ok)
    monkeypatch.setattr(arena, "_ask_persona",
                        lambda persona, *a, **k: orders_by_persona.get(persona["persona"]))
    return arena.run_arena_daily("2026-07-07", ["600001", "600002", "600003"], PRICES, NAMES)


def test_arena_buy_executes_with_cost_and_lot(store, monkeypatch):
    orders = {"价值派": {"sells": [], "buys": [{"symbol": "600001", "weight_pct": 50, "reason": "低估"}],
                        "comment": "买入甲"}}
    result = _run(store, monkeypatch, orders)
    pos = store.arena_positions("价值派")
    # 预算 50 万，成本价 10*1.0003=10.003，可买 49985 股 -> 整手 49900 股
    assert pos[0]["symbol"] == "600001" and pos[0]["shares"] == 49900
    cash = store.arena_cash("价值派")
    assert cash == pytest.approx(1_000_000 - 49900 * 10.0 * 1.0003, abs=1)
    nav = [p for p in result["personas"] if p["persona"] == "价值派"][0]["nav"]
    assert nav == pytest.approx(cash + 49900 * 10.0, abs=1)
    # 其余 4 人格无指令 -> 空仓 NAV=100 万
    assert store.arena_cash("趋势派") == 1_000_000.0


def test_arena_sell_then_buy_and_max_positions(store, monkeypatch):
    store.upsert_arena_position("游资派", "600003", 1000, 30.0)
    store.set_arena_cash("游资派", 100_000.0)
    orders = {"游资派": {"sells": [{"symbol": "600003", "reason": "止盈"}],
                        "buys": [{"symbol": "600002", "weight_pct": 30, "reason": "热点"}],
                        "comment": "换仓"}}
    _run(store, monkeypatch, orders)
    pos = {p["symbol"]: p for p in store.arena_positions("游资派")}
    assert "600003" not in pos          # 已卖出
    assert pos["600002"]["shares"] > 0  # 已买入
    trades = store.load_arena_trades("游资派")
    assert [t["side"] for t in trades] == ["buy", "sell"]  # 最新在前：先卖后买


def test_arena_idempotent_same_day(store, monkeypatch):
    orders = {"价值派": {"sells": [], "buys": [{"symbol": "600001", "weight_pct": 50, "reason": "x"}],
                        "comment": ""}}
    _run(store, monkeypatch, orders)
    shares1 = store.arena_positions("价值派")[0]["shares"]
    _run(store, monkeypatch, orders)  # 同日重跑：全员已有 NAV，直接跳过
    assert store.arena_positions("价值派")[0]["shares"] == shares1
    assert len(store.load_arena_trades("价值派")) == 1


def test_arena_llm_unavailable_settles_nav_without_trading(store, monkeypatch):
    store.upsert_arena_position("价值派", "600001", 1000, 9.0)
    store.set_arena_cash("价值派", 500_000.0)
    result = _run(store, monkeypatch, {}, llm_ok=False)
    assert store.load_arena_trades("价值派") == []
    nav = [p for p in result["personas"] if p["persona"] == "价值派"][0]["nav"]
    assert nav == pytest.approx(500_000 + 1000 * 10.0)
    assert store.arena_nav_exists("2026-07-07", "价值派")


def test_arena_ignores_symbols_outside_candidates_and_holdings(store, monkeypatch):
    orders = {"价值派": {"sells": [], "buys": [{"symbol": "999999", "weight_pct": 50, "reason": "幻觉"}],
                        "comment": ""}}
    _run(store, monkeypatch, orders)
    assert store.arena_positions("价值派") == []  # 幻觉代码被过滤
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_arena.py -v`
Expected: 新增 5 个 FAIL（ModuleNotFoundError arena），原 4 个 PASS

- [ ] **Step 3: 实现**

新建 `quantcore/quant/arena.py`：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_arena.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add quantcore/quant/arena.py tests/test_arena.py
git commit -m "feat(arena): daily rebalance engine with LLM orders, lot rounding and costs"
```

---

### Task 3: Arena API + 15:40 cron（lite_main）

**Files:**
- Modify: `app/lite_main.py`（heatmap 端点后加 3 个端点；`_start_ml_factor_scheduler` 里加 cron）

- [ ] **Step 1: 实现端点与 cron**

`app/lite_main.py`，`lite_heatmap` 端点之后插入：

```python
def _arena_prices_and_names() -> tuple[dict[str, float], dict[str, str]]:
    """Arena 结算价：优先实时快照（15:40 快照价=收盘价），失败退本地日线最新收盘。"""
    prices: dict[str, float] = {}
    names: dict[str, str] = {}
    try:
        snapshot = _load_realtime_quotes_snapshot(60)
    except Exception:
        snapshot = {}
    if snapshot:
        for sym, q in snapshot.items():
            price = q.get("price") or q.get("close")
            if price:
                prices[sym] = float(price)
                names[sym] = str(q.get("name") or sym)
        return prices, names
    from quantcore.quant.local_store import get_local_store
    store = get_local_store()
    metas = {str(m.get("symbol")): str(m.get("name") or "") for m in store.load_meta()}
    for sym, st in store.latest_daily_stats().items():
        prices[sym] = float(st["close"])
        names[sym] = metas.get(sym, sym)
    return prices, names


def _arena_candidates(today: str) -> list[str]:
    """当日四池留痕并集（每池 rank 前 10，去重保序）。"""
    from quantcore.quant.local_store import get_local_store
    store = get_local_store()
    seen: list[str] = []
    for pool in ("smart", "pattern", "swing", "auction"):
        for sym in store.load_picks_symbols(today, pool, limit=10):
            if sym not in seen:
                seen.append(sym)
    return seen


@app.post("/api/lite/arena/run")
async def lite_arena_run():
    """手动触发一次 Arena 调仓+结算（幂等：当日已结算的人格跳过）。无交易日守卫，便于验证。"""
    from quantcore.quant.arena import run_arena_daily
    today = datetime.now().strftime("%Y-%m-%d")

    def _run():
        prices, names = _arena_prices_and_names()
        return run_arena_daily(today, _arena_candidates(today), prices, names)

    result = await asyncio.to_thread(_run)
    return {"success": True, "data": result}


@app.get("/api/lite/arena")
async def lite_arena_board():
    """排行榜：各人格最新 NAV/收益率/持仓数 + NAV 序列。"""
    from quantcore.quant.arena import _PERSONAS
    from quantcore.quant.local_store import get_local_store

    def _load():
        store = get_local_store()
        series = store.load_arena_nav_series()
        board = []
        for p in _PERSONAS:
            pname = p["persona"]
            navs = series.get(pname) or []
            latest = navs[-1]["nav"] if navs else store.ARENA_INITIAL_CASH
            board.append({
                "persona": pname, "style": p["style"], "desc": p["desc"],
                "nav": round(latest, 2),
                "return_pct": round((latest / store.ARENA_INITIAL_CASH - 1) * 100, 2),
                "positions": len(store.arena_positions(pname)),
                "comment": navs[-1]["comment"] if navs else "",
                "days": len(navs),
            })
        board.sort(key=lambda x: x["nav"], reverse=True)
        return {"board": board, "series": series}

    return {"success": True, "data": await asyncio.to_thread(_load)}


@app.get("/api/lite/arena/detail")
async def lite_arena_detail(persona: str):
    """人格详情：持仓明细（现价/浮盈）+ 交易历史。"""
    from quantcore.quant.arena import _PERSONAS
    from quantcore.quant.local_store import get_local_store
    if persona not in {p["persona"] for p in _PERSONAS}:
        raise HTTPException(status_code=400, detail="未知人格")

    def _load():
        store = get_local_store()
        prices, names = _arena_prices_and_names()
        positions = []
        for pos in store.arena_positions(persona):
            price = prices.get(pos["symbol"]) or pos["avg_cost"]
            positions.append({
                **pos, "name": names.get(pos["symbol"], ""), "price": round(price, 2),
                "pnl_pct": round((price / pos["avg_cost"] - 1) * 100, 2) if pos["avg_cost"] else 0.0,
            })
        return {"persona": persona, "cash": round(store.arena_cash(persona), 2),
                "positions": positions, "trades": store.load_arena_trades(persona, limit=50)}

    return {"success": True, "data": await asyncio.to_thread(_load)}
```

`_start_ml_factor_scheduler` 内（收盘盘报 add_job 之后、`_ml_factor_scheduler.start()` 之前）插入：

```python
    # Arena 虚拟盘：交易日 15:40 调仓+结算（收盘盘报 15:35 之后，快照价=收盘价）
    async def _job_arena_daily() -> None:
        try:
            if not await _is_trading_day_now():
                return
            from quantcore.quant.arena import run_arena_daily
            today = datetime.now().strftime("%Y-%m-%d")

            def _run():
                prices, names = _arena_prices_and_names()
                return run_arena_daily(today, _arena_candidates(today), prices, names)

            await asyncio.to_thread(_run)
        except Exception as exc:  # noqa: BLE001
            import warnings
            warnings.warn(f"arena daily run failed: {exc}", RuntimeWarning, stacklevel=1)

    _ml_factor_scheduler.add_job(
        _job_arena_daily,
        CronTrigger.from_crontab(os.getenv("ARENA_CRON", "40 15 * * 1-5"), timezone=tz),
        id="arena_daily", name="Arena虚拟盘每日调仓",
        replace_existing=True, misfire_grace_time=3600,
    )
```

- [ ] **Step 2: 验证**

`python -c "import app.lite_main"` 无错误。重启后端后：

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8001/api/lite/arena/run" -TimeoutSec 120
Invoke-RestMethod "http://127.0.0.1:8001/api/lite/arena"
Invoke-RestMethod "http://127.0.0.1:8001/api/lite/arena/detail?persona=价值派"
```

Expected: run 返回 5 人格摘要（LLM 可用时有 comment/交易；候选池有当日留痕）；再跑一次 run → personas 为空数组（幂等）；board 有 5 行；detail 返回持仓+交易；`persona=瞎写` → 400。

- [ ] **Step 3: 全量回归**

Run: `python -m pytest tests/ -q`
Expected: 77 passed（68+9）

- [ ] **Step 4: Commit**

```bash
git add app/lite_main.py
git commit -m "feat(arena): run/board/detail endpoints and 15:40 trading-day cron"
```

---

### Task 4: 聪明钱模块（smart_money.py + 端点）

**Files:**
- Create: `quantcore/quant/smart_money.py`
- Modify: `app/routers/quant.py`（dragon-tiger 端点后追加 3 个端点）
- Test: `tests/test_smart_money.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_smart_money.py`：

```python
"""聪明钱（活跃席位聚合 / 席位胜率 / 基金重仓 纯变换）回归测试。"""
import pandas as pd

from quantcore.quant.smart_money import _agg_active_seats, _shape_seat_winrate, _shape_fund_hold


def test_agg_active_seats_groups_and_ranks():
    df = pd.DataFrame([
        {"营业部名称": "席位A", "上榜日": "2026-07-01", "买入总金额": 2e8, "卖出总金额": 1e8, "买入股票": "甲 乙"},
        {"营业部名称": "席位A", "上榜日": "2026-07-03", "买入总金额": 3e8, "卖出总金额": 0.0, "买入股票": "丙"},
        {"营业部名称": "席位B", "上榜日": "2026-07-02", "买入总金额": 1e8, "卖出总金额": 5e8, "买入股票": ""},
    ])
    rows = _agg_active_seats(df, top=10)
    assert rows[0]["seat"] == "席位A"          # 净买额降序
    assert rows[0]["count"] == 2
    assert rows[0]["net_yi"] == 4.0            # (2+3-1)e8 -> 亿
    assert rows[0]["last_date"] == "2026-07-03"
    assert "丙" in rows[0]["stocks"]
    assert rows[1]["net_yi"] == -4.0


def test_shape_seat_winrate_picks_5d_metrics():
    df = pd.DataFrame([
        {"营业部名称": "游资甲", "上榜后5天-买入次数": 30, "上榜后5天-平均涨幅": 2.5, "上榜后5天-上涨概率": 60.0,
         "上榜后1天-买入次数": 33, "上榜后1天-平均涨幅": 1.0, "上榜后1天-上涨概率": 55.0},
        {"营业部名称": "散户乙", "上榜后5天-买入次数": 2, "上榜后5天-平均涨幅": 9.9, "上涨概率": 0},
    ])
    rows = _shape_seat_winrate(df, min_trades=5, top=10)
    assert len(rows) == 1                       # 样本<5 的过滤
    assert rows[0] == {"seat": "游资甲", "trades_5d": 30, "avg_chg_5d": 2.5, "win_rate_5d": 60.0,
                       "avg_chg_1d": 1.0, "win_rate_1d": 55.0}


def test_shape_fund_hold_top_by_mv():
    df = pd.DataFrame([
        {"股票代码": "300750", "股票简称": "宁德时代", "持有基金家数": 2338, "持股市值": 1.6e11,
         "持股变化": "减仓", "持股变动比例": -22.87},
        {"股票代码": "600519", "股票简称": "贵州茅台", "持有基金家数": 2000, "持股市值": 2.0e11,
         "持股变化": "增仓", "持股变动比例": 1.5},
    ])
    rows = _shape_fund_hold(df, top=10)
    assert rows[0]["symbol"] == "600519"        # 持股市值降序
    assert rows[0]["mv_yi"] == 2000.0
    assert rows[0]["funds"] == 2000
    assert rows[1]["change"] == "减仓" and rows[1]["change_pct"] == -22.87
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_smart_money.py -v`
Expected: 3 FAIL（ModuleNotFoundError smart_money）

- [ ] **Step 3: 实现**

新建 `quantcore/quant/smart_money.py`：

```python
"""聪明钱（A股语境）：龙虎榜活跃席位榜 / 席位胜率排行 / 基金共识重仓。

数据源 akshare（东财 datacenter），实测可达（2026-07-07 验证）；北向持股 2024-08 起
停止每日披露，砍掉。取数失败统一降级 {empty, message}；纯 DataFrame 变换拆成
_agg/_shape 函数便于测试。6h 缓存（每日收盘后才更新）。
"""
from __future__ import annotations

from datetime import date as _date, timedelta
from typing import Dict, List

import pandas as pd

from ._cache import cached

_EMPTY = {"empty": True, "message": "聪明钱数据拉取失败或为空"}
_TTL = 6 * 3600


def _f(v, default=0.0) -> float:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return default


def _agg_active_seats(df: pd.DataFrame, top: int = 50) -> List[Dict]:
    """活跃营业部按席位聚合：上榜次数/买卖总额/净买额/最近上榜/买过的票。"""
    rows: Dict[str, Dict] = {}
    for _, r in df.iterrows():
        seat = str(r.get("营业部名称") or "").strip()
        if not seat:
            continue
        item = rows.setdefault(seat, {"seat": seat, "count": 0, "buy_yi": 0.0,
                                      "sell_yi": 0.0, "net_yi": 0.0, "last_date": "", "stocks": []})
        item["count"] += 1
        buy, sell = _f(r.get("买入总金额")), _f(r.get("卖出总金额"))
        item["buy_yi"] += buy / 1e8
        item["sell_yi"] += sell / 1e8
        item["net_yi"] += (buy - sell) / 1e8
        d = str(r.get("上榜日") or "")[:10]
        if d > item["last_date"]:
            item["last_date"] = d
        for s in str(r.get("买入股票") or "").split():
            if s and s not in item["stocks"]:
                item["stocks"].append(s)
    out = []
    for item in rows.values():
        out.append({**item, "buy_yi": round(item["buy_yi"], 2), "sell_yi": round(item["sell_yi"], 2),
                    "net_yi": round(item["net_yi"], 2), "stocks": " ".join(item["stocks"][:12])})
    out.sort(key=lambda x: x["net_yi"], reverse=True)
    return out[:top]


def _shape_seat_winrate(df: pd.DataFrame, min_trades: int = 5, top: int = 50) -> List[Dict]:
    """营业部排行：取 5 日口径的平均涨幅/上涨概率（外加 1 日参考），样本太少的过滤。"""
    out = []
    for _, r in df.iterrows():
        seat = str(r.get("营业部名称") or "").strip()
        trades = int(_f(r.get("上榜后5天-买入次数")))
        if not seat or trades < min_trades:
            continue
        out.append({"seat": seat, "trades_5d": trades,
                    "avg_chg_5d": _f(r.get("上榜后5天-平均涨幅")),
                    "win_rate_5d": _f(r.get("上榜后5天-上涨概率")),
                    "avg_chg_1d": _f(r.get("上榜后1天-平均涨幅")),
                    "win_rate_1d": _f(r.get("上榜后1天-上涨概率"))})
    out.sort(key=lambda x: (x["win_rate_5d"], x["trades_5d"]), reverse=True)
    return out[:top]


def _shape_fund_hold(df: pd.DataFrame, top: int = 100) -> List[Dict]:
    """基金重仓：按持股市值降序。"""
    out = []
    for _, r in df.iterrows():
        out.append({"symbol": str(r.get("股票代码") or "").zfill(6),
                    "name": str(r.get("股票简称") or ""),
                    "funds": int(_f(r.get("持有基金家数"))),
                    "mv_yi": round(_f(r.get("持股市值")) / 1e8, 2),
                    "change": str(r.get("持股变化") or ""),
                    "change_pct": _f(r.get("持股变动比例"))})
    out.sort(key=lambda x: x["mv_yi"], reverse=True)
    return out[:top]


def active_seats(days: int = 30) -> Dict[str, object]:
    def _compute():
        import akshare as ak
        end = _date.today()
        start = end - timedelta(days=days)
        try:
            df = ak.stock_lhb_hyyyb_em(start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))
        except Exception:
            return {**_EMPTY, "rows": []}
        if df is None or df.empty:
            return {**_EMPTY, "rows": []}
        return {"empty": False, "days": days, "rows": _agg_active_seats(df)}
    return cached(f"sm:seats:{days}", _TTL, _compute)


def seat_winrate() -> Dict[str, object]:
    def _compute():
        import akshare as ak
        try:
            df = ak.stock_lhb_yybph_em(symbol="近一月")
        except Exception:
            return {**_EMPTY, "rows": []}
        if df is None or df.empty:
            return {**_EMPTY, "rows": []}
        return {"empty": False, "window": "近一月", "rows": _shape_seat_winrate(df)}
    return cached("sm:winrate", _TTL, _compute)


def _recent_quarter_ends(n: int = 4) -> List[str]:
    today = _date.today()
    ends, y = [], today.year
    while len(ends) < n:
        for m, d in ((12, 31), (9, 30), (6, 30), (3, 31)):
            q = _date(y, m, d)
            if q < today:
                ends.append(q.strftime("%Y%m%d"))
                if len(ends) >= n:
                    break
        y -= 1
    return ends


def fund_consensus() -> Dict[str, object]:
    def _compute():
        import akshare as ak
        for quarter in _recent_quarter_ends():
            try:
                df = ak.stock_report_fund_hold(symbol="基金持仓", date=quarter)
            except Exception:
                continue
            if df is not None and not df.empty:
                return {"empty": False, "quarter": quarter, "rows": _shape_fund_hold(df)}
        return {**_EMPTY, "rows": []}
    return cached("sm:fund", _TTL, _compute)
```

- [ ] **Step 4: 跑测试确认通过 + 端点**

Run: `python -m pytest tests/test_smart_money.py -v`
Expected: 3 passed

`app/routers/quant.py`，`quant_dragon_tiger_seats` 端点之后追加：

```python
# ---- 聪明钱：活跃席位 / 席位胜率 / 基金重仓（akshare，6h 缓存，不计费）----
@router.get("/smart-money/seats")
async def quant_smart_money_seats(days: int = 30):
    from quantcore.quant.smart_money import active_seats
    return await asyncio.to_thread(active_seats, max(7, min(days, 90)))


@router.get("/smart-money/seat-winrate")
async def quant_smart_money_winrate():
    from quantcore.quant.smart_money import seat_winrate
    return await asyncio.to_thread(seat_winrate)


@router.get("/smart-money/fund-consensus")
async def quant_smart_money_fund():
    from quantcore.quant.smart_money import fund_consensus
    return await asyncio.to_thread(fund_consensus)
```

- [ ] **Step 5: 验证 + Commit**

重启后端，登录拿 token 后逐个调 3 个端点，Expected: empty:false + rows 非空（席位榜有「沪股通专用」等，基金重仓有宁德时代等）。

```bash
python -m pytest tests/ -q     # 80 passed（77+3）
git add quantcore/quant/smart_money.py app/routers/quant.py tests/test_smart_money.py
git commit -m "feat(smart-money): active seats, seat winrate and fund consensus endpoints"
```

---

### Task 5: 前端 /arena 页

**Files:**
- Modify: `frontend/src/api/quant.ts`（末尾追加 arenaApi）
- Create: `frontend/src/views/Arena/Index.vue`
- Modify: `frontend/src/router/index.ts`（heatmap 路由后加）
- Modify: `frontend/src/components/Layout/AppLayout.vue`（行业热力菜单后加，图标 Trophy）

- [ ] **Step 1: quant.ts 末尾追加**

```typescript
export interface ArenaBoardRow {
  persona: string
  style: string
  desc: string
  nav: number
  return_pct: number
  positions: number
  comment: string
  days: number
}

export interface ArenaNavPoint { date: string; nav: number; comment: string }

export interface ArenaPosition {
  symbol: string
  name: string
  shares: number
  avg_cost: number
  price: number
  pnl_pct: number
}

export interface ArenaTrade {
  date: string
  symbol: string
  side: string
  price: number
  shares: number
  reason: string
}

export const arenaApi = {
  board: async () => {
    const raw = await ApiClient.get<any>('/api/lite/arena')
    return (raw as any)?.data as { board: ArenaBoardRow[]; series: Record<string, ArenaNavPoint[]> } | null
  },
  detail: async (persona: string) => {
    const raw = await ApiClient.get<any>('/api/lite/arena/detail', { persona })
    return (raw as any)?.data as { persona: string; cash: number; positions: ArenaPosition[]; trades: ArenaTrade[] } | null
  },
  run: async () => {
    const raw = await ApiClient.post<any>('/api/lite/arena/run', undefined, { timeout: 180000 })
    return (raw as any)?.data
  },
}
```

- [ ] **Step 2: 新建 `frontend/src/views/Arena/Index.vue`**

```vue
<template>
  <div class="arena-page">
    <div class="page-head">
      <div>
        <h2>AI 擂台</h2>
        <p class="sub">5 个 AI 人格各管 100 万虚拟盘，每交易日 15:40 自动调仓结算 · 虚拟资金，仅供观察风格差异</p>
      </div>
      <el-button size="small" :loading="running" @click="runNow">手动结算一次</el-button>
    </div>

    <el-table v-loading="loading" :data="board" size="small" @row-click="openDetail">
      <el-table-column label="#" type="index" width="46" />
      <el-table-column prop="persona" label="人格" width="100" />
      <el-table-column label="净值" width="130">
        <template #default="{ row }"><b>{{ (row.nav / 10000).toFixed(2) }} 万</b></template>
      </el-table-column>
      <el-table-column label="总收益" width="110">
        <template #default="{ row }">
          <span :class="row.return_pct > 0 ? 'up' : row.return_pct < 0 ? 'down' : ''">
            {{ row.return_pct > 0 ? '+' : '' }}{{ row.return_pct }}%
          </span>
        </template>
      </el-table-column>
      <el-table-column prop="positions" label="持仓数" width="80" />
      <el-table-column prop="days" label="结算天数" width="90" />
      <el-table-column prop="comment" label="今日判词" min-width="320" show-overflow-tooltip />
    </el-table>

    <div ref="chartEl" class="nav-chart" />

    <el-drawer v-model="detailVisible" :title="`${detailPersona} · 持仓与交易`" size="46%">
      <template v-if="detail">
        <p class="cash-line">现金 {{ (detail.cash / 10000).toFixed(2) }} 万</p>
        <h4>持仓</h4>
        <el-table :data="detail.positions" size="small">
          <el-table-column prop="symbol" label="代码" width="90" />
          <el-table-column prop="name" label="名称" width="110" />
          <el-table-column prop="shares" label="股数" width="90" />
          <el-table-column label="成本/现价" width="130">
            <template #default="{ row }">{{ row.avg_cost.toFixed(2) }} / {{ row.price.toFixed(2) }}</template>
          </el-table-column>
          <el-table-column label="浮盈" width="90">
            <template #default="{ row }">
              <span :class="row.pnl_pct > 0 ? 'up' : row.pnl_pct < 0 ? 'down' : ''">{{ row.pnl_pct > 0 ? '+' : '' }}{{ row.pnl_pct }}%</span>
            </template>
          </el-table-column>
        </el-table>
        <h4>交易历史</h4>
        <el-table :data="detail.trades" size="small" max-height="360">
          <el-table-column prop="date" label="日期" width="100" />
          <el-table-column label="方向" width="70">
            <template #default="{ row }">
              <el-tag size="small" :type="row.side === 'buy' ? 'danger' : 'success'">{{ row.side === 'buy' ? '买入' : '卖出' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="symbol" label="代码" width="90" />
          <el-table-column label="价格/股数" width="120">
            <template #default="{ row }">{{ row.price.toFixed(2) }} × {{ row.shares }}</template>
          </el-table-column>
          <el-table-column prop="reason" label="理由" min-width="200" show-overflow-tooltip />
        </el-table>
      </template>
    </el-drawer>
    <p class="hint">AI 模拟盘，非投资建议；点击行查看持仓与判词。</p>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { echarts, type ECharts } from '@/utils/echarts'
import { arenaApi, type ArenaBoardRow, type ArenaNavPoint } from '@/api/quant'

const loading = ref(false)
const running = ref(false)
const board = ref<ArenaBoardRow[]>([])
const series = ref<Record<string, ArenaNavPoint[]>>({})
const detailVisible = ref(false)
const detailPersona = ref('')
const detail = ref<Awaited<ReturnType<typeof arenaApi.detail>>>(null)
const chartEl = ref<HTMLDivElement>()
let chart: ECharts | null = null

const renderChart = () => {
  if (!chartEl.value) return
  const names = Object.keys(series.value)
  if (!names.length) return
  if (!chart) chart = echarts.init(chartEl.value)
  const dates = [...new Set(names.flatMap(n => series.value[n].map(p => p.date)))].sort()
  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { top: 0 },
    grid: { left: 60, right: 20, top: 30, bottom: 24 },
    xAxis: { type: 'category', data: dates },
    yAxis: { type: 'value', scale: true, axisLabel: { formatter: (v: number) => `${(v / 10000).toFixed(0)}万` } },
    series: names.map(n => ({
      name: n, type: 'line', showSymbol: false,
      data: dates.map(d => series.value[n].find(p => p.date === d)?.nav ?? null),
      connectNulls: true,
    })),
  }, true)
}

const load = async () => {
  loading.value = true
  try {
    const res = await arenaApi.board()
    if (!res) return
    board.value = res.board || []
    series.value = res.series || {}
    renderChart()
  } finally {
    loading.value = false
  }
}

const runNow = async () => {
  running.value = true
  try {
    await arenaApi.run()
    ElMessage.success('已触发结算（当日已结算的人格自动跳过）')
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '触发失败')
  } finally {
    running.value = false
  }
}

const openDetail = async (row: ArenaBoardRow) => {
  detailPersona.value = row.persona
  detail.value = await arenaApi.detail(row.persona)
  detailVisible.value = true
}

const onResize = () => chart?.resize()
onMounted(() => { load(); window.addEventListener('resize', onResize) })
onBeforeUnmount(() => { window.removeEventListener('resize', onResize); chart?.dispose(); chart = null })
</script>

<style scoped lang="scss">
.page-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 10px;
  h2 { margin: 0; font-size: 20px; }
  .sub { margin: 4px 0 0; font-size: 12px; color: var(--el-text-color-secondary); }
}
.up { color: #e0402c; }
.down { color: #1e9e63; }
.nav-chart { height: 320px; margin-top: 14px; }
.cash-line { margin: 0 0 8px; font-size: 13px; }
h4 { margin: 12px 0 6px; }
.hint { margin: 8px 0 0; font-size: 12px; color: var(--el-text-color-placeholder); }
</style>
```

- [ ] **Step 3: 路由 + 侧边栏 + 验证 + Commit**

`router/index.ts` heatmap 行后：

```typescript
      { path: 'arena', name: 'arena', component: () => import('@/views/Arena/Index.vue') },
```

`AppLayout.vue` 行业热力菜单项后（`Trophy` 并入 icons import）：

```html
        <el-menu-item index="/arena"><el-icon><Trophy /></el-icon><span>AI擂台</span></el-menu-item>
```

```bash
cd frontend && npx vue-tsc --noEmit && npm run build
git add frontend/src/api/quant.ts frontend/src/views/Arena/Index.vue frontend/src/router/index.ts frontend/src/components/Layout/AppLayout.vue
git commit -m "feat(arena): leaderboard page with nav chart and persona detail drawer"
```

---

### Task 6: 前端 /smart-money 页

**Files:**
- Modify: `frontend/src/api/quant.ts`（末尾追加 smartMoneyApi）
- Create: `frontend/src/views/SmartMoney/Index.vue`
- Modify: `frontend/src/router/index.ts`（arena 路由后加）
- Modify: `frontend/src/components/Layout/AppLayout.vue`（AI擂台菜单后加，图标 Wallet）

- [ ] **Step 1: quant.ts 末尾追加**

```typescript
export interface SmartSeatRow {
  seat: string
  count: number
  buy_yi: number
  sell_yi: number
  net_yi: number
  last_date: string
  stocks: string
}

export interface SeatWinrateRow {
  seat: string
  trades_5d: number
  avg_chg_5d: number
  win_rate_5d: number
  avg_chg_1d: number
  win_rate_1d: number
}

export interface FundHoldRow {
  symbol: string
  name: string
  funds: number
  mv_yi: number
  change: string
  change_pct: number
}

export const smartMoneyApi = {
  seats: async (days = 30) => {
    const raw = await ApiClient.get<any>('/api/quant/smart-money/seats', { days }, { timeout: 60000 })
    return raw as { empty: boolean; message?: string; rows: SmartSeatRow[] }
  },
  winrate: async () => {
    const raw = await ApiClient.get<any>('/api/quant/smart-money/seat-winrate', undefined, { timeout: 60000 })
    return raw as { empty: boolean; message?: string; rows: SeatWinrateRow[] }
  },
  fund: async () => {
    const raw = await ApiClient.get<any>('/api/quant/smart-money/fund-consensus', undefined, { timeout: 60000 })
    return raw as { empty: boolean; message?: string; quarter?: string; rows: FundHoldRow[] }
  },
}
```

- [ ] **Step 2: 新建 `frontend/src/views/SmartMoney/Index.vue`**

```vue
<template>
  <div class="smart-money-page">
    <div class="page-head">
      <h2>聪明钱</h2>
      <p class="sub">龙虎榜席位与基金持仓视角的资金追踪（东财数据，收盘后更新；北向持股已停止披露，不提供）</p>
    </div>
    <el-tabs v-model="tab">
      <el-tab-pane label="活跃席位（近30天）" name="seats">
        <el-table v-loading="loadingSeats" :data="seats" size="small" max-height="620">
          <el-table-column label="#" type="index" width="46" />
          <el-table-column prop="seat" label="营业部/席位" min-width="240" show-overflow-tooltip />
          <el-table-column prop="count" label="上榜次数" width="90" sortable />
          <el-table-column label="净买额" width="110" sortable :sort-by="'net_yi'">
            <template #default="{ row }">
              <span :class="row.net_yi > 0 ? 'up' : 'down'">{{ row.net_yi > 0 ? '+' : '' }}{{ row.net_yi }} 亿</span>
            </template>
          </el-table-column>
          <el-table-column label="买入/卖出" width="140">
            <template #default="{ row }">{{ row.buy_yi }} / {{ row.sell_yi }} 亿</template>
          </el-table-column>
          <el-table-column prop="last_date" label="最近上榜" width="110" />
          <el-table-column prop="stocks" label="买过的票" min-width="260" show-overflow-tooltip />
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="席位胜率（近一月）" name="winrate">
        <el-table v-loading="loadingWinrate" :data="winrate" size="small" max-height="620">
          <el-table-column label="#" type="index" width="46" />
          <el-table-column prop="seat" label="营业部/席位" min-width="240" show-overflow-tooltip />
          <el-table-column prop="trades_5d" label="样本数" width="90" sortable />
          <el-table-column label="5日胜率" width="100" sortable :sort-by="'win_rate_5d'">
            <template #default="{ row }"><b>{{ row.win_rate_5d }}%</b></template>
          </el-table-column>
          <el-table-column label="5日平均涨幅" width="120">
            <template #default="{ row }">
              <span :class="row.avg_chg_5d > 0 ? 'up' : 'down'">{{ row.avg_chg_5d > 0 ? '+' : '' }}{{ row.avg_chg_5d }}%</span>
            </template>
          </el-table-column>
          <el-table-column label="1日胜率/涨幅" width="140">
            <template #default="{ row }">{{ row.win_rate_1d }}% / {{ row.avg_chg_1d }}%</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
      <el-tab-pane :label="`基金重仓${fundQuarter ? '（' + fundQuarter + '）' : ''}`" name="fund">
        <el-table v-loading="loadingFund" :data="fund" size="small" max-height="620" @row-click="goStock">
          <el-table-column label="#" type="index" width="46" />
          <el-table-column prop="symbol" label="代码" width="90" />
          <el-table-column prop="name" label="名称" width="120" />
          <el-table-column prop="funds" label="持有基金数" width="110" sortable />
          <el-table-column label="持股市值" width="120" sortable :sort-by="'mv_yi'">
            <template #default="{ row }"><b>{{ row.mv_yi }} 亿</b></template>
          </el-table-column>
          <el-table-column label="较上期" width="140">
            <template #default="{ row }">
              <el-tag size="small" :type="row.change === '增仓' ? 'danger' : row.change === '减仓' ? 'success' : 'info'">
                {{ row.change || '-' }} {{ row.change_pct > 0 ? '+' : '' }}{{ row.change_pct }}%
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
        <p class="hint">点击行跳转个股深研；基金持仓为季报口径，滞后于当前。</p>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { smartMoneyApi, type SmartSeatRow, type SeatWinrateRow, type FundHoldRow } from '@/api/quant'

const router = useRouter()
const tab = ref('seats')
const seats = ref<SmartSeatRow[]>([])
const winrate = ref<SeatWinrateRow[]>([])
const fund = ref<FundHoldRow[]>([])
const fundQuarter = ref('')
const loadingSeats = ref(false)
const loadingWinrate = ref(false)
const loadingFund = ref(false)

const loadSeats = async () => {
  if (seats.value.length) return
  loadingSeats.value = true
  try { seats.value = (await smartMoneyApi.seats()).rows || [] } finally { loadingSeats.value = false }
}
const loadWinrate = async () => {
  if (winrate.value.length) return
  loadingWinrate.value = true
  try { winrate.value = (await smartMoneyApi.winrate()).rows || [] } finally { loadingWinrate.value = false }
}
const loadFund = async () => {
  if (fund.value.length) return
  loadingFund.value = true
  try {
    const res = await smartMoneyApi.fund()
    fund.value = res.rows || []
    fundQuarter.value = res.quarter || ''
  } finally { loadingFund.value = false }
}

const goStock = (row: FundHoldRow) => router.push({ path: '/stock-analysis', query: { symbol: row.symbol } })

watch(tab, (t) => { if (t === 'winrate') loadWinrate(); if (t === 'fund') loadFund() })
onMounted(loadSeats)
</script>

<style scoped lang="scss">
.page-head { margin-bottom: 6px;
  h2 { margin: 0; font-size: 20px; }
  .sub { margin: 4px 0 0; font-size: 12px; color: var(--el-text-color-secondary); }
}
.up { color: #e0402c; }
.down { color: #1e9e63; }
.hint { margin: 8px 0 0; font-size: 12px; color: var(--el-text-color-placeholder); }
</style>
```

- [ ] **Step 3: 路由 + 侧边栏 + 验证 + Commit**

`router/index.ts` arena 行后：

```typescript
      { path: 'smart-money', name: 'smart-money', component: () => import('@/views/SmartMoney/Index.vue') },
```

`AppLayout.vue` AI擂台菜单项后（`Wallet` 并入 icons import）：

```html
        <el-menu-item index="/smart-money"><el-icon><Wallet /></el-icon><span>聪明钱</span></el-menu-item>
```

```bash
cd frontend && npx vue-tsc --noEmit && npm run build
git add frontend/src/api/quant.ts frontend/src/views/SmartMoney/Index.vue frontend/src/router/index.ts frontend/src/components/Layout/AppLayout.vue
git commit -m "feat(smart-money): seats, winrate and fund consensus tabs page"
```

---

### Task 7: 端到端验证 + README + push

- [ ] **Step 1: 全量回归**

```bash
python -m pytest tests/ -q     # 80 passed
cd frontend && npx vue-tsc --noEmit && npm run build
```

- [ ] **Step 2: 实机巡检（headless Playwright，复用既有方式）**

登录 token 注入 localStorage → 依次访问 `http://[::1]:5173/arena` 与 `/smart-money`：
1. `/arena`：先 POST `/api/lite/arena/run` 触发一次结算 → 排行榜 5 行、NAV 图有线、点行开抽屉见持仓/交易
2. `/smart-money`：活跃席位表非空；切换席位胜率/基金重仓 tab 各自加载出行
3. 截图确认

- [ ] **Step 3: README 功能清单**

「行业热力图」条目后追加：

```markdown
- **AI 擂台** — 5 个 AI 人格各管 100 万虚拟盘，每交易日收盘后按各自方法论自动调仓（含 A 股交易成本），排行榜看净值曲线与每日判词；LLM 断档时持仓不动照常结算。
- **聪明钱** — 龙虎榜活跃席位榜、席位胜率排行（上榜后 5 日跟踪）、基金共识重仓（季报口径）；北向持股因披露停止不提供。
```

- [ ] **Step 4: Commit + push**

```bash
git add README.md docs/superpowers/plans/2026-07-07-batch4-arena-smartmoney.md
git commit -m "docs: add arena and smart money to feature list"
git push origin main
```
