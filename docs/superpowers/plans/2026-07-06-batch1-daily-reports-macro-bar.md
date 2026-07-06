# 批次 1：每日盘报 + 宏观条 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增每日盘报（盘前看点 + 收盘 AI 复盘，定时生成、历史可翻）与全站顶部宏观指标条，对标 stockgod.xyz 的 /reports 与顶部指标条，适配 A 股。

**Architecture:** 盘报生成器放 `quantcore/quant/report_daily.py`（复用环境标签/市场情绪/涨停分布/留痕胜率；竞价与催化剂由 app 层作为 extra 传入），结果存 `daily_reports` 表（LocalQuantStore / runtime/quant_data.sqlite）。定时任务挂 `app/lite_main.py` 现有 APScheduler。宏观条指数拉取放 `quantcore/quant/macro_bar.py`，聚合端点在 lite_main。前端新增 `/reports` 页与 `MacroBar.vue` 组件。

**Tech Stack:** FastAPI + APScheduler + SQLite（后端）；Vue 3 + Element Plus（前端）；LLM 走 `quantcore/quant/llm.py` 统一入口（`chat_json`，无密钥自动降级）。

**约定（全项目通用，执行时必须遵守）:**
- 运行测试：`python -m pytest tests/test_report_daily.py -v`（项目根 `C:\Users\Administrator\lynxagent`）。
- 后端改动需重启后端才生效（uvicorn 无 --reload）；前端 vite 有 HMR。
- A股红涨绿跌。所有面向用户的文案中文。
- commit message 英文，结尾加 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。

---

### Task 1: daily_reports 表与存取方法（LocalQuantStore）

**Files:**
- Modify: `quantcore/quant/local_store.py`（_SCHEMA 末尾 + 类尾部新方法）
- Test: `tests/test_report_daily.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_report_daily.py`：

```python
"""每日盘报（daily_reports 表 + report_daily 生成器）回归测试。"""
import pytest

from quantcore.quant.local_store import LocalQuantStore


@pytest.fixture()
def store(tmp_path):
    return LocalQuantStore(str(tmp_path / "test.sqlite"))


def test_daily_report_roundtrip(store):
    content = {"kind": "close", "date": "2026-07-06", "llm": False,
               "sections": [{"title": "一句话定调", "body": "市场偏冷"}]}
    store.save_daily_report("2026-07-06", "close", content)
    loaded = store.load_daily_report("2026-07-06", "close")
    assert loaded == content
    # 同日同 kind 重存覆盖，不重复
    store.save_daily_report("2026-07-06", "close", {**content, "llm": True})
    assert store.load_daily_report("2026-07-06", "close")["llm"] is True


def test_daily_report_latest_and_list(store):
    store.save_daily_report("2026-07-03", "close", {"d": 1})
    store.save_daily_report("2026-07-06", "close", {"d": 2})
    store.save_daily_report("2026-07-06", "premarket", {"d": 3})
    assert store.latest_daily_report("close") == {"d": 2}
    assert store.latest_daily_report("premarket") == {"d": 3}
    assert store.latest_daily_report("nope") is None
    dates = store.list_report_dates(30)
    assert {"date": "2026-07-06", "kind": "premarket"} in dates
    assert len(dates) == 3


def test_load_daily_report_missing_returns_none(store):
    assert store.load_daily_report("2026-01-01", "close") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_report_daily.py -v`
Expected: FAIL，`AttributeError: 'LocalQuantStore' object has no attribute 'save_daily_report'`

- [ ] **Step 3: 实现**

`quantcore/quant/local_store.py` 的 `_SCHEMA` 字符串末尾（`picks_history` 建表语句后、闭合 `"""` 前）追加：

```sql
CREATE TABLE IF NOT EXISTS daily_reports (
    date TEXT,
    kind TEXT,
    content_json TEXT,
    created_at TEXT,
    PRIMARY KEY (date, kind)
);
```

在 `LocalQuantStore` 类中（`fundamental_flag_count` 方法之后）新增：

```python
    # ---- 每日盘报 ----
    def save_daily_report(self, date: str, kind: str, content: Dict[str, object]) -> None:
        import json
        from datetime import datetime
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO daily_reports(date, kind, content_json, created_at) VALUES (?,?,?,?)",
            (date, kind, json.dumps(content, ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()

    def load_daily_report(self, date: str, kind: str) -> Optional[Dict[str, object]]:
        import json
        row = self._conn().execute(
            "SELECT content_json FROM daily_reports WHERE date=? AND kind=?", (date, kind)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def latest_daily_report(self, kind: str) -> Optional[Dict[str, object]]:
        import json
        row = self._conn().execute(
            "SELECT content_json FROM daily_reports WHERE kind=? ORDER BY date DESC LIMIT 1", (kind,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def list_report_dates(self, limit: int = 30) -> List[Dict[str, str]]:
        rows = self._conn().execute(
            "SELECT date, kind FROM daily_reports ORDER BY date DESC, kind LIMIT ?", (limit,)
        ).fetchall()
        return [{"date": r[0], "kind": r[1]} for r in rows]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_report_daily.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add quantcore/quant/local_store.py tests/test_report_daily.py
git commit -m "feat(reports): daily_reports table with save/load/latest/list on LocalQuantStore"
```

---

### Task 2: 宏观条指数模块 macro_bar.py

**Files:**
- Create: `quantcore/quant/macro_bar.py`
- Test: `tests/test_macro_bar.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_macro_bar.py`：

```python
"""宏观条：腾讯 s_ 简版指数行情解析。"""
from quantcore.quant.macro_bar import parse_index_payload

SAMPLE = (
    'v_s_sh000001="1~上证指数~000001~3391.88~10.14~0.30~319129749~416024730~~~1";\n'
    'v_s_sz399001="51~深证成指~399001~10318.36~-25.31~-0.24~412345678~523456789~~~2";\n'
)


def test_parse_index_payload():
    rows = parse_index_payload(SAMPLE)
    assert len(rows) == 2
    sh = rows[0]
    assert sh["code"] == "sh000001"
    assert sh["name"] == "上证指数"
    assert sh["price"] == 3391.88
    assert sh["change"] == 10.14
    assert sh["change_percent"] == 0.30
    assert rows[1]["change_percent"] == -0.24


def test_parse_index_payload_garbage_returns_empty():
    assert parse_index_payload("") == []
    assert parse_index_payload('v_s_sh000001="broken";') == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_macro_bar.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'quantcore.quant.macro_bar'`

- [ ] **Step 3: 实现**

新建 `quantcore/quant/macro_bar.py`：

```python
"""顶部宏观指标条：三大指数实时快照（腾讯 s_ 简版行情）。

字段序（~ 分隔）：0 市场 1 名称 2 代码 3 现价 4 涨跌 5 涨跌幅% 6 成交量(手) 7 成交额(万)。
解析与网络分离：parse_index_payload 纯函数可测，fetch_index_quotes 负责请求。
"""
from __future__ import annotations

import re
from typing import Dict, List

import requests

INDEX_CODES = [("sh000001", "上证指数"), ("sz399001", "深证成指"), ("sz399006", "创业板指")]


def parse_index_payload(text: str) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for m in re.finditer(r'v_s_(sh|sz)(\d{6})="([^"]*)"', text):
        fields = m.group(3).split("~")
        if len(fields) < 6:
            continue

        def _f(idx: int):
            try:
                return float(fields[idx])
            except (ValueError, IndexError):
                return None

        price = _f(3)
        if price is None:
            continue
        out.append({
            "code": m.group(1) + m.group(2),
            "name": fields[1],
            "price": price,
            "change": _f(4),
            "change_percent": _f(5),
            "amount_wan": _f(7),
        })
    return out


def fetch_index_quotes() -> List[Dict[str, object]]:
    """拉取三大指数简版行情；失败抛异常由调用方兜底。"""
    query = ",".join(f"s_{code}" for code, _ in INDEX_CODES)
    session = requests.Session()
    session.trust_env = False
    resp = session.get(
        f"https://qt.gtimg.cn/q={query}",
        headers={"User-Agent": "Mozilla/5.0"}, timeout=8,
    )
    resp.encoding = "gbk"
    resp.raise_for_status()
    return parse_index_payload(resp.text)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_macro_bar.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add quantcore/quant/macro_bar.py tests/test_macro_bar.py
git commit -m "feat(macro-bar): tencent index quotes fetch with testable parser"
```

---

### Task 3: 盘报生成器 report_daily.py

**Files:**
- Create: `quantcore/quant/report_daily.py`
- Test: `tests/test_report_daily.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_report_daily.py` 末尾追加（注意 import 添加 `report_daily`）：

```python
from quantcore.quant import report_daily


def _stub_facts(monkeypatch, store):
    """隔离外部数据：facts 收集全部打桩，测试只验证组装/降级/落库逻辑。"""
    monkeypatch.setattr(report_daily, "_gather_close_facts", lambda: {
        "date": "2026-07-06",
        "market_context": {"state": "偏冷", "advice": "建议降低仓位", "as_of": "2026-07-06"},
        "limit_up": {"total": 35},
        "sentiment": {"median_chg": -0.8},
        "picks_stats": {"pools": []},
    })
    monkeypatch.setattr(report_daily, "get_local_store", lambda: store)


def test_generate_close_report_without_llm(monkeypatch, store):
    _stub_facts(monkeypatch, store)
    monkeypatch.setattr(report_daily.llm, "chat_json", lambda *a, **k: None)
    content = report_daily.generate_report("close")
    assert content["kind"] == "close"
    assert content["llm"] is False
    titles = [s["title"] for s in content["sections"]]
    assert "一句话定调" in titles
    # 已落库
    assert store.latest_daily_report("close")["kind"] == "close"


def test_generate_close_report_with_llm(monkeypatch, store):
    _stub_facts(monkeypatch, store)
    fake = {"sections": [
        {"title": "一句话定调", "body": "缩量普跌，防守为主。"},
        {"title": "主线分析", "body": "无明显主线。"},
        {"title": "热门追踪", "body": "涨停 35 家。"},
        {"title": "明日看点", "body": "关注量能。"},
        {"title": "核心结论", "body": "轻仓观望。"},
    ]}
    monkeypatch.setattr(report_daily.llm, "chat_json", lambda *a, **k: fake)
    content = report_daily.generate_report("close")
    assert content["llm"] is True
    assert content["sections"][0]["body"] == "缩量普跌，防守为主。"


def test_generate_premarket_uses_extra(monkeypatch, store):
    monkeypatch.setattr(report_daily, "get_local_store", lambda: store)
    monkeypatch.setattr(report_daily, "_gather_premarket_facts", lambda extra: {
        "date": "2026-07-06", "market_context": {"state": "中性"},
        "auction": (extra or {}).get("auction") or {},
        "catalysts": (extra or {}).get("catalysts") or {},
    })
    monkeypatch.setattr(report_daily.llm, "chat_json", lambda *a, **k: None)
    content = report_daily.generate_report("premarket", {"auction": {"summary": "高开"}})
    assert content["kind"] == "premarket"
    assert content["facts"]["auction"] == {"summary": "高开"}


def test_generate_report_rejects_bad_kind(store):
    with pytest.raises(ValueError):
        report_daily.generate_report("weekly")


def test_llm_result_missing_sections_falls_back(monkeypatch, store):
    _stub_facts(monkeypatch, store)
    monkeypatch.setattr(report_daily.llm, "chat_json", lambda *a, **k: {"foo": 1})
    content = report_daily.generate_report("close")
    assert content["llm"] is False  # 非法 LLM 输出走降级
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_report_daily.py -v`
Expected: 新增用例 FAIL（`ImportError`/`AttributeError`），Task 1 的 3 个用例仍 PASS

- [ ] **Step 3: 实现**

新建 `quantcore/quant/report_daily.py`：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_report_daily.py tests/test_macro_bar.py -v`
Expected: 全部 passed（8+2 用例）

- [ ] **Step 5: Commit**

```bash
git add quantcore/quant/report_daily.py tests/test_report_daily.py
git commit -m "feat(reports): daily report generator with LLM sections and data-only fallback"
```

---

### Task 4: lite_main 端点 + 定时任务

**Files:**
- Modify: `app/lite_main.py`（三处：端点、生成协程、调度注册）

- [ ] **Step 1: 添加生成协程与 API 端点**

在 `app/lite_main.py` 中 `@app.get("/api/lite/limit-up")` 端点函数结束之后添加：

```python
# ---- 每日盘报 + 宏观条 ----
async def _generate_daily_report(kind: str) -> dict[str, Any]:
    """组装 app 层数据（竞价/催化剂）后调用生成器。收盘版无需 extra。"""
    from quantcore.quant.report_daily import generate_report
    extra: dict[str, Any] = {}
    if kind == "premarket":
        try:
            auction = await lite_call_auction()
            if isinstance(auction, dict) and auction.get("success"):
                extra["auction"] = auction.get("data")
        except Exception:
            pass
        try:
            cats = await lite_catalysts()
            if isinstance(cats, dict) and cats.get("success"):
                extra["catalysts"] = cats.get("data")
        except Exception:
            pass
    return await asyncio.to_thread(generate_report, kind, extra)


@app.get("/api/lite/reports")
async def lite_reports(date: str = "", kind: str = ""):
    """盘报查询：给 date+kind 返回单篇；否则返回可用日期列表。"""
    from quantcore.quant.local_store import get_local_store
    store_q = get_local_store()
    if date and kind:
        content = await asyncio.to_thread(store_q.load_daily_report, date, kind)
        if content is None:
            return {"success": False, "data": None, "message": "该日期暂无此类盘报"}
        return {"success": True, "data": content}
    dates = await asyncio.to_thread(store_q.list_report_dates, 60)
    return {"success": True, "data": {"available": dates}}


@app.get("/api/lite/reports/latest")
async def lite_reports_latest(kind: str = "close"):
    from quantcore.quant.local_store import get_local_store
    store_q = get_local_store()
    content = await asyncio.to_thread(store_q.latest_daily_report, kind)
    return {"success": True, "data": content}


@app.post("/api/lite/reports/generate")
async def lite_reports_generate(kind: str = "close",
                                user: dict = Depends(get_current_lite_user)):
    """手动触发生成（验证/补数用），不等定时任务。"""
    if kind not in ("premarket", "close"):
        raise HTTPException(status_code=400, detail="kind 必须是 premarket 或 close")
    content = await _generate_daily_report(kind)
    return {"success": True, "data": content}


@app.get("/api/lite/macro-bar")
async def lite_macro_bar():
    """顶部宏观条：三大指数 + 全市场涨跌家数/两市成交额。60s 缓存。"""
    cached = _cache_get("macro-bar", 60)
    if cached:
        return cached
    from quantcore.quant.macro_bar import fetch_index_quotes
    try:
        indices = await _run_data_task(fetch_index_quotes, timeout=10.0)
    except Exception:
        indices = []
    breadth: dict[str, Any] | None = None
    try:
        snapshot = await _run_data_task(_load_realtime_quotes_snapshot, 60, timeout=8.0)
        if snapshot:
            ups = downs = flats = 0
            total_amount = 0.0
            for q in snapshot.values():
                pct = q.get("change_percent")
                if pct is None:
                    continue
                if pct > 0:
                    ups += 1
                elif pct < 0:
                    downs += 1
                else:
                    flats += 1
                total_amount += float(q.get("amount") or 0)
            breadth = {"up": ups, "down": downs, "flat": flats,
                       "amount_yi": round(total_amount / 1e8)}
    except Exception:
        breadth = None
    payload = {"success": True, "data": {
        "indices": indices, "breadth": breadth,
        "updated_at": datetime.now().strftime("%H:%M:%S"),
    }}
    if indices or breadth:
        _cache_set("macro-bar", payload)
    return payload
```

- [ ] **Step 2: 注册定时任务**

在 `_start_ml_factor_scheduler`（startup 钩子）内，`_ml_factor_scheduler.start()` 之前添加：

```python
    # 每日盘报：盘前版 9:26（竞价结束后）、收盘版 15:35
    async def _job_daily_report_premarket() -> None:
        try:
            await _generate_daily_report("premarket")
        except Exception as exc:  # noqa: BLE001
            import warnings
            warnings.warn(f"premarket report failed: {exc}", RuntimeWarning, stacklevel=1)

    async def _job_daily_report_close() -> None:
        try:
            await _generate_daily_report("close")
        except Exception as exc:  # noqa: BLE001
            import warnings
            warnings.warn(f"close report failed: {exc}", RuntimeWarning, stacklevel=1)

    _ml_factor_scheduler.add_job(
        _job_daily_report_premarket,
        CronTrigger.from_crontab(os.getenv("REPORT_PREMARKET_CRON", "26 9 * * 1-5"), timezone=tz),
        id="daily_report_premarket", name="盘前盘报生成",
        replace_existing=True, misfire_grace_time=1800,
    )
    _ml_factor_scheduler.add_job(
        _job_daily_report_close,
        CronTrigger.from_crontab(os.getenv("REPORT_CLOSE_CRON", "35 15 * * 1-5"), timezone=tz),
        id="daily_report_close", name="收盘盘报生成",
        replace_existing=True, misfire_grace_time=3600,
    )
```

- [ ] **Step 3: 验证后端可启动、端点可用**

```powershell
# 重启后端（scripts/start_lite.ps1 幂等，但已占用端口会跳过——先杀旧进程再起）
Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
.\scripts\start_lite.ps1 -NoOpen -NoFrontend
```

然后（需先登录拿 token，测试账号 looptest / loop-test-1234）：

```powershell
$tok = (Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8001/api/auth/login -ContentType application/json -Body '{"username":"looptest","password":"loop-test-1234"}').data.access_token
Invoke-RestMethod http://127.0.0.1:8001/api/lite/macro-bar -Headers @{Authorization="Bearer $tok"}
Invoke-RestMethod -Method Post "http://127.0.0.1:8001/api/lite/reports/generate?kind=close" -Headers @{Authorization="Bearer $tok"}
Invoke-RestMethod "http://127.0.0.1:8001/api/lite/reports/latest?kind=close" -Headers @{Authorization="Bearer $tok"}
```

登录响应为 `{success, data: {access_token, ...}}`（见 `app/lite_auth.py` login）。macro-bar 应返回 3 条指数；generate 应返回含 `sections` 的 content（无 LLM 密钥时 `llm: false` 也算成功）。

Expected: 三个请求均 `success: true`

- [ ] **Step 4: 跑全量回归**

Run: `python -m pytest tests/ -v`
Expected: 全部 passed（原 46 + 新增 10 上下）

- [ ] **Step 5: Commit**

```bash
git add app/lite_main.py
git commit -m "feat(reports): report/macro-bar endpoints and daily cron jobs (9:26 premarket, 15:35 close)"
```

---

### Task 5: 前端宏观条 MacroBar.vue

**Files:**
- Create: `frontend/src/components/MacroBar.vue`
- Modify: `frontend/src/components/Layout/AppLayout.vue`（挂载）
- Modify: `frontend/src/api/quant.ts`（末尾追加 API）

- [ ] **Step 1: 在 quant.ts 末尾追加类型与 API**

```typescript
export interface MacroIndexQuote {
  code: string
  name: string
  price: number
  change: number | null
  change_percent: number | null
  amount_wan: number | null
}

export interface MacroBarData {
  indices: MacroIndexQuote[]
  breadth: { up: number; down: number; flat: number; amount_yi: number } | null
  updated_at: string
}

export const macroBarApi = {
  fetch: async () => {
    const raw = await ApiClient.get<{ success: boolean; data: MacroBarData }>('/api/lite/macro-bar', undefined, { timeout: 15000 })
    return (raw as any)?.data as MacroBarData | null
  },
}
```

- [ ] **Step 2: 新建 MacroBar.vue**

`frontend/src/components/MacroBar.vue`：

```vue
<template>
  <div v-if="data && data.indices.length" class="macro-bar">
    <span v-for="idx in data.indices" :key="idx.code" class="item">
      <span class="label">{{ idx.name }}</span>
      <span :class="colorClass(idx.change_percent)">
        {{ idx.price?.toFixed(2) }}
        <template v-if="idx.change_percent != null">
          {{ idx.change_percent > 0 ? '+' : '' }}{{ idx.change_percent.toFixed(2) }}%
        </template>
      </span>
    </span>
    <span v-if="data.breadth" class="item">
      <span class="label">涨跌</span>
      <span class="up">{{ data.breadth.up }}</span>/<span class="down">{{ data.breadth.down }}</span>
    </span>
    <span v-if="data.breadth" class="item">
      <span class="label">两市成交</span>
      <span>{{ data.breadth.amount_yi }}亿</span>
    </span>
    <span class="item updated">{{ data.updated_at }}</span>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { macroBarApi, type MacroBarData } from '@/api/quant'

const data = ref<MacroBarData | null>(null)
let timer: number | undefined

// A股红涨绿跌
const colorClass = (pct: number | null) => (pct == null ? '' : pct > 0 ? 'up' : pct < 0 ? 'down' : '')

const load = async () => {
  try {
    data.value = await macroBarApi.fetch()
  } catch {
    /* 静默：宏观条是辅助信息，失败不打扰用户 */
  }
}

onMounted(() => {
  load()
  timer = window.setInterval(load, 60000)
})
onUnmounted(() => { if (timer) window.clearInterval(timer) })
</script>

<style scoped>
.macro-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 4px 16px;
  font-size: 12px;
  overflow-x: auto;
  white-space: nowrap;
  border-bottom: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color);
}
.item { display: inline-flex; align-items: center; gap: 4px; }
.label { color: var(--el-text-color-secondary); }
.up { color: #f56c6c; }
.down { color: #67c23a; }
.updated { margin-left: auto; color: var(--el-text-color-placeholder); }
</style>
```

- [ ] **Step 3: 挂载到 AppLayout**

`frontend/src/components/Layout/AppLayout.vue`：在 `<router-view />` 的正上方（主内容区顶部）插入 `<MacroBar />`，并在 `<script setup>` 中加 `import MacroBar from '@/components/MacroBar.vue'`。若 `<router-view />` 外层有主内容容器（如 `<el-main>` 或 `.main` div），放进该容器第一行。

- [ ] **Step 4: 验证**

```bash
cd frontend && npx vue-tsc --noEmit && npm run build
```

Expected: 类型检查与构建均通过。再开 http://localhost:5173 登录后确认每页顶部出现指数条（后端在跑时）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/MacroBar.vue frontend/src/components/Layout/AppLayout.vue frontend/src/api/quant.ts
git commit -m "feat(macro-bar): site-wide index strip with breadth and turnover"
```

---

### Task 6: 前端盘报页 /reports

**Files:**
- Create: `frontend/src/views/Reports/Index.vue`
- Modify: `frontend/src/router/index.ts`（加路由）
- Modify: `frontend/src/components/Layout/AppLayout.vue`（侧边栏菜单项）
- Modify: `frontend/src/api/quant.ts`（末尾追加 API）

- [ ] **Step 1: 在 quant.ts 末尾追加盘报 API**

```typescript
export interface DailyReportSection { title: string; body: string }
export interface DailyReport {
  kind: 'premarket' | 'close'
  date: string
  generated_at: string
  llm: boolean
  sections: DailyReportSection[]
}

export const reportsApi = {
  latest: async (kind: 'premarket' | 'close') => {
    const raw = await ApiClient.get<any>('/api/lite/reports/latest', { kind })
    return (raw as any)?.data as DailyReport | null
  },
  byDate: async (date: string, kind: 'premarket' | 'close') => {
    const raw = await ApiClient.get<any>('/api/lite/reports', { date, kind })
    return (raw as any)?.data as DailyReport | null
  },
  available: async () => {
    const raw = await ApiClient.get<any>('/api/lite/reports')
    return ((raw as any)?.data?.available ?? []) as { date: string; kind: string }[]
  },
  generate: async (kind: 'premarket' | 'close') => {
    const raw = await ApiClient.post<any>(`/api/lite/reports/generate?kind=${kind}`, undefined, { timeout: 120000 })
    return (raw as any)?.data as DailyReport | null
  },
}
```

- [ ] **Step 2: 新建 Reports/Index.vue**

`frontend/src/views/Reports/Index.vue`：

```vue
<template>
  <div class="reports-page">
    <div class="header">
      <h1>每日盘报</h1>
      <div class="controls">
        <el-radio-group v-model="kind" size="small" @change="loadByDate">
          <el-radio-button value="premarket">盘前看点</el-radio-button>
          <el-radio-button value="close">收盘复盘</el-radio-button>
        </el-radio-group>
        <el-select v-model="selectedDate" size="small" style="width: 140px" @change="loadByDate">
          <el-option v-for="d in datesForKind" :key="d" :label="d" :value="d" />
        </el-select>
        <el-button size="small" :loading="generating" @click="regenerate">立即生成</el-button>
      </div>
    </div>

    <el-alert v-if="report && !report.llm" type="info" :closable="false" show-icon
              title="当前为纯数据版盘报（未配置 LLM 密钥），配置后可获得 AI 解读。" />

    <template v-if="report">
      <el-card v-for="s in report.sections" :key="s.title" class="section" shadow="never">
        <h2>{{ s.title }}</h2>
        <p>{{ s.body }}</p>
      </el-card>
      <p class="meta">生成于 {{ report.generated_at }} · 信息整理，非投资建议</p>
    </template>
    <el-empty v-else-if="!loading" description="该日期暂无盘报，交易日 9:26 / 15:35 自动生成，也可点「立即生成」" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { reportsApi, type DailyReport } from '@/api/quant'

const kind = ref<'premarket' | 'close'>('close')
const report = ref<DailyReport | null>(null)
const available = ref<{ date: string; kind: string }[]>([])
const selectedDate = ref('')
const loading = ref(false)
const generating = ref(false)

const datesForKind = computed(() =>
  [...new Set(available.value.filter(d => d.kind === kind.value).map(d => d.date))])

const loadByDate = async () => {
  loading.value = true
  try {
    if (!datesForKind.value.includes(selectedDate.value)) selectedDate.value = datesForKind.value[0] ?? ''
    report.value = selectedDate.value
      ? await reportsApi.byDate(selectedDate.value, kind.value)
      : await reportsApi.latest(kind.value)
  } finally {
    loading.value = false
  }
}

const regenerate = async () => {
  generating.value = true
  try {
    report.value = await reportsApi.generate(kind.value)
    available.value = await reportsApi.available()
    if (report.value) selectedDate.value = report.value.date
    ElMessage.success('盘报已生成')
  } catch {
    ElMessage.error('生成失败，请稍后重试')
  } finally {
    generating.value = false
  }
}

onMounted(async () => {
  available.value = await reportsApi.available()
  await loadByDate()
})
</script>

<style scoped>
.reports-page { max-width: 860px; margin: 0 auto; padding: 16px; }
.header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }
.controls { display: flex; gap: 8px; align-items: center; }
.section { margin-top: 12px; }
.section h2 { margin: 0 0 8px; font-size: 15px; }
.section p { margin: 0; line-height: 1.8; white-space: pre-wrap; }
.meta { margin-top: 12px; font-size: 12px; color: var(--el-text-color-placeholder); text-align: center; }
</style>
```

- [ ] **Step 3: 路由与侧边栏**

`frontend/src/router/index.ts` children 里 `today` 之后加：

```typescript
      { path: 'reports', name: 'daily-reports', component: () => import('@/views/Reports/Index.vue') },
```

`AppLayout.vue` 菜单「今日」项之后加（Notebook 图标需在现有 `@element-plus/icons-vue` import 列表中补上）：

```html
        <el-menu-item index="/reports"><el-icon><Notebook /></el-icon><span>每日盘报</span></el-menu-item>
```

- [ ] **Step 4: 验证**

```bash
cd frontend && npx vue-tsc --noEmit && npm run build
```

Expected: 通过。开发模式打开 http://localhost:5173/reports：切 kind、选日期、点「立即生成」后 sections 渲染。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/Reports/Index.vue frontend/src/router/index.ts frontend/src/components/Layout/AppLayout.vue frontend/src/api/quant.ts
git commit -m "feat(reports): daily reports page with premarket/close tabs and history"
```

---

### Task 7: 端到端验证与收尾

**Files:** 无新文件（验证 + README 更新）

- [ ] **Step 1: 全量回归**

```bash
python -m pytest tests/ -v
cd frontend && npx vue-tsc --noEmit && npm run build
```

Expected: 后端全部 passed，前端构建通过。

- [ ] **Step 2: 实机巡检（后端 + 前端都在跑）**

用 Playwright 或手动浏览器确认：
1. 登录后任意页面顶部有宏观条（3 指数 + 涨跌家数；盘后时段指数仍显示收盘值）。
2. `/reports` 页：点「立即生成」→ 收盘版 sections 出现；无 LLM 密钥时显示纯数据版提示条。
3. 侧边栏「每日盘报」入口可达；移动端宽度下宏观条可横向滚动不破版。

- [ ] **Step 3: 更新 README 功能清单**

`README.md` 「✨ 核心功能」列表中「大盘环境标签」条目后追加：

```markdown
- **每日盘报** — 交易日自动生成盘前看点（9:26，竞价后）与收盘 AI 复盘（15:35），历史可翻；未配置 LLM 时输出纯数据版。
- **宏观指标条** — 全站顶部实时显示三大指数、涨跌家数与两市成交额。
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add daily reports and macro bar to feature list"
```
