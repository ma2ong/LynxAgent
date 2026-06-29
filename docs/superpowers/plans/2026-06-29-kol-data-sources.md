# KOL 真实数据源接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `scripts/build_kol_digest.py` 从 X(推特) / 雪球 / 微博三源采集真实 KOL 内容，归一后交现有 DeepSeek 聚合，落盘 `runtime/kol_digest.json`，并新增 `--discover` 模式发现真实 handle。

**Architecture:** 单脚本重构。把现有 X-only 采集拆成 `_opencli_json` 通用调用器 + 三个独立采集器，统一经 `_norm` 归一、`_dedup_rank` 去重排序；把聚合的"JSON→digest 组装"抽成纯函数 `_assemble`（注入 resolver）以便离线单测；修 `_sources` 的平台硬编码 bug。前端与 `get_digest()` 契约零改动。

**Tech Stack:** Python 3.14 / opencli（浏览器桥）/ DeepSeek（现有 `quantcore.quant.llm`）/ pytest。

**约定：** 每个 commit 结尾加一行 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`（仓库惯例）。所有命令在 `C:/Users/Administrator/lynxagent` 仓库根执行；pytest 从根跑。当前分支 `feat/kol-data-sources`。

---

## 参考：spec

`docs/superpowers/specs/2026-06-29-kol-data-sources-design.md`

## 文件结构

- **Modify:** `scripts/build_kol_digest.py` — 全部采集/归一/聚合/discover 逻辑。
- **Create:** `tests/test_kol_digest.py` — 离线单测（importlib 按路径加载脚本，不联网、不调 LLM）。

## 现有代码锚点（改前）

`scripts/build_kol_digest.py`：配置块 26-31（`SEARCH_TERMS`/`KOL_HANDLES`/`SEARCH_LIMIT`/`MAX_TWEETS`）；`_search` 56-72；`collect` 75-97；`_agg_prompt` 103-121；`aggregate` 124-216（内含 `_sources` 闭包 139-150、组装 152-216）；`main` 219-235。

---

## Task 0：测试加载器（建测试文件骨架）

**Files:**
- Create: `tests/test_kol_digest.py`

- [ ] **Step 1: 写测试文件头（按路径加载脚本模块）**

```python
"""KOL 采集器离线单测：按路径加载 scripts/build_kol_digest.py，不联网、不调 LLM。"""
import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_kol_digest.py"


def _load_bkd():
    spec = importlib.util.spec_from_file_location("build_kol_digest", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bkd = _load_bkd()


def test_module_loads():
    assert hasattr(bkd, "collect")
```

- [ ] **Step 2: 跑测试，确认能加载现有脚本**

Run: `python -m pytest tests/test_kol_digest.py::test_module_loads -v`
Expected: PASS（现有脚本 `if __name__=="__main__"` 守卫，import 无副作用）。

- [ ] **Step 3: Commit**

```bash
git add tests/test_kol_digest.py
git commit -m "test(kol): add offline test loader for build_kol_digest"
```

---

## Task 1：`_as_int` + `_norm` 归一（TDD）

**Files:**
- Modify: `scripts/build_kol_digest.py`
- Test: `tests/test_kol_digest.py`

- [ ] **Step 1: 写失败测试**

```python
def test_norm_weibo_search_uses_title_and_zero_likes():
    raw = {"title": "微博财经标题一二三四五", "author": "wb1", "url": "http://wb/1", "time": "x"}
    it = bkd._norm("微博", raw)
    assert it["platform"] == "微博"
    assert it["text"] == "微博财经标题一二三四五"
    assert it["likes"] == 0
    assert it["url"] == "http://wb/1"


def test_norm_twitter_carries_text_and_likes():
    raw = {"text": "X 财经观点一二三四", "author": "x1", "url": "http://x/1", "likes": "12"}
    it = bkd._norm("X", raw)
    assert it["platform"] == "X" and it["author"] == "x1"
    assert it["text"] == "X 财经观点一二三四" and it["likes"] == 12
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_kol_digest.py -k norm -v`
Expected: FAIL（`_norm` 不存在 / `_as_int` 不存在）。

- [ ] **Step 3: 在 `scripts/build_kol_digest.py` 加 `_as_int` 与 `_norm`（放在 `_opencli_path` 之后）**

```python
def _as_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _norm(platform: str, raw: dict) -> dict:
    """把任一站点的一条原始记录归一为统一结构。
    text 优先取 text，缺则取 title（微博 search 用 title）；likes 缺则 0；url 缺则空。"""
    text = str(raw.get("text") or raw.get("title") or "").strip()
    return {
        "platform": platform,
        "author": str(raw.get("author") or "").strip(),
        "text": text[:180],
        "url": str(raw.get("url") or ""),
        "id": str(raw.get("id") or ""),
        "likes": _as_int(raw.get("likes")),
    }
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_kol_digest.py -k norm -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add scripts/build_kol_digest.py tests/test_kol_digest.py
git commit -m "feat(kol): add _norm to unify per-platform records"
```

---

## Task 2：`_dedup_rank` 跨源去重+粗排（TDD）

**Files:**
- Modify: `scripts/build_kol_digest.py`
- Test: `tests/test_kol_digest.py`

- [ ] **Step 1: 写失败测试**

```python
def test_dedup_rank_cross_platform_kept_same_platform_deduped():
    items = [
        {"platform": "X", "author": "a", "text": "同链接不同平台一二三", "url": "u1", "likes": 1},
        {"platform": "微博", "author": "b", "text": "同链接不同平台一二三", "url": "u1", "likes": 9},
        {"platform": "X", "author": "a", "text": "同链接不同平台一二三", "url": "u1", "likes": 1},  # 与第1条重复
        {"platform": "X", "author": "c", "text": "短", "url": "u2", "likes": 99},  # 文本太短被丢
    ]
    out = bkd._dedup_rank(items, 10)
    assert len(out) == 2                      # 跨平台保留、同平台同链接去重、短文本丢弃
    assert out[0]["platform"] == "微博"        # likes 高在前
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_kol_digest.py -k dedup -v`
Expected: FAIL（`_dedup_rank` 不存在）。

- [ ] **Step 3: 在 `_norm` 之后加 `_dedup_rank`**

```python
def _dedup_rank(items: list[dict], max_items: int) -> list[dict]:
    """跨源去重（platform + url/id/文本哈希），丢弃过短文本，按 likes 粗排取前 max_items。"""
    seen: set = set()
    out: list[dict] = []
    for it in items:
        if len(it.get("text") or "") < 8:
            continue
        key = (it.get("platform"),
               it.get("url") or it.get("id")
               or hash((it.get("author"), (it.get("text") or "")[:40])))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    out.sort(key=lambda x: _as_int(x.get("likes")), reverse=True)
    return out[:max_items]
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_kol_digest.py -k dedup -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add scripts/build_kol_digest.py tests/test_kol_digest.py
git commit -m "feat(kol): add _dedup_rank for cross-source dedup and ranking"
```

---

## Task 3：抽出 `_sources` 到模块级并修平台硬编码 bug（TDD）

**Files:**
- Modify: `scripts/build_kol_digest.py`（把 `aggregate` 内的 `_sources` 闭包 139-150 删除，改为模块级函数）
- Test: `tests/test_kol_digest.py`

- [ ] **Step 1: 写失败测试**

```python
def test_sources_carries_real_platform_not_hardcoded_x():
    items = [
        {"platform": "X", "author": "x1", "url": "http://x/1"},
        {"platform": "雪球", "author": "xq1", "url": "http://xq/1"},
        {"platform": "微博", "author": "wb1", "url": ""},   # 无 url → is_placeholder
    ]
    srcs = bkd._sources([0, 1, 2], items)
    plats = [s["platform"] for s in srcs]
    assert plats == ["X", "雪球", "微博"]            # 不再全部 "X"
    assert srcs[2]["is_placeholder"] is True        # 无 url 标占位
    assert srcs[0]["is_placeholder"] is False
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_kol_digest.py -k sources -v`
Expected: FAIL（`_sources` 还是 `aggregate` 内的闭包，模块级取不到）。

- [ ] **Step 3: 在 `_dedup_rank` 之后新增模块级 `_sources`，并删除 `aggregate` 里的同名闭包（旧 139-150 行）**

```python
def _sources(idxs, items: list[dict]) -> list[dict]:
    """把 LLM 输出的条目索引还原成真实来源；platform 取每条 item 真实平台（修硬编码 X bug）。"""
    out, seen = [], set()
    for i in idxs or []:
        if not isinstance(i, int) or i < 0 or i >= len(items):
            continue
        it = items[i]
        key = it.get("url") or it.get("author")
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "platform": it.get("platform") or "未知",
            "author": it.get("author") or "",
            "url": it.get("url") or "",
            "is_placeholder": not it.get("url"),
        })
    return out
```

> 删除 `aggregate` 内 `def _sources(idxs) -> list[dict]: ...` 整段闭包；下一 Task 会把组装逻辑也搬出去并改调用为 `_sources(b.get("tweets"), items)`。本 Task 结束时 `aggregate` 仍可临时引用模块级 `_sources`（签名变了，下一 Task 修调用）。

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_kol_digest.py -k sources -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add scripts/build_kol_digest.py tests/test_kol_digest.py
git commit -m "fix(kol): hoist _sources to module level, carry real platform"
```

---

## Task 4：抽出 `_assemble` 纯函数 + 契约 schema 测试（TDD）

**Files:**
- Modify: `scripts/build_kol_digest.py`（把 `aggregate` 组装段 152-216 搬进 `_assemble`，`aggregate` 改为调用它）
- Test: `tests/test_kol_digest.py`

- [ ] **Step 1: 写失败测试**

```python
def test_assemble_matches_get_digest_contract():
    items = [
        {"platform": "X", "author": "x1", "url": "http://x/1", "text": "看多光模块一二三"},
        {"platform": "雪球", "author": "xq1", "url": "http://xq/1", "text": "估值偏高一二三"},
    ]
    agg = {
        "stocks": [{
            "name": "中际旭创", "group": "多头共识", "tag": "热议", "stance": "看多",
            "summary": "需求未见顶",
            "blocks": [
                {"kind": "买入逻辑", "content": "1.6T 放量", "tweets": [0]},
                {"kind": "卖出 / 风险逻辑", "content": "估值高", "tweets": [1]},
            ],
        }],
        "other_topics": [{"title": "算力 capex", "tag": "赛道", "content": "偏乐观", "tweets": [0]}],
    }

    def fake_resolve(names):
        return [{"symbol": "300308", "name": "中际旭创"}] if names and names[0] == "中际旭创" else []

    d = bkd._assemble(agg, items, fake_resolve)
    assert d["is_mock"] is False
    assert {"stats", "hottest", "attention_rank", "stocks", "other_topics", "sources_platform"} <= set(d)
    s = d["stocks"][0]
    assert s["code"] == "300308" and s["name"] == "中际旭创"
    plats = {src["platform"] for b in s["view_blocks"] for src in b["sources"]}
    assert plats == {"X", "雪球"}                 # 两个平台来源都在
    assert d["sources_platform"] == ["X", "雪球"]  # 并集（排序后）


def test_assemble_returns_none_when_no_resolvable_stock():
    items = [{"platform": "X", "author": "x1", "url": "u", "text": "随便一二三四五"}]
    agg = {"stocks": [{"name": "查无此股", "blocks": [{"kind": "x", "content": "y", "tweets": [0]}]}],
           "other_topics": []}
    assert bkd._assemble(agg, items, lambda names: []) is None
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_kol_digest.py -k assemble -v`
Expected: FAIL（`_assemble` 不存在）。

- [ ] **Step 3: 新增 `_assemble`（放在 `_sources` 之后），并把 `aggregate` 改为调用它**

新增函数：

```python
def _assemble(agg: dict, items: list[dict], resolve) -> dict | None:
    """把 LLM 解析出的 agg(dict) + 采集 items 组装成 digest；resolve 为名称→代码解析器（注入便于测试）。"""
    stocks: list[dict] = []
    for s in agg.get("stocks") or []:
        name = str(s.get("name") or "").strip()
        resolved = resolve([name])
        if not resolved:
            continue  # 解析不到真实代码的丢弃，绝不编造
        code, full = resolved[0]["symbol"], resolved[0]["name"]
        view_blocks, authors, tids = [], set(), set()
        for b in s.get("blocks") or []:
            srcs = _sources(b.get("tweets"), items)
            if not srcs:
                continue
            for src in srcs:
                authors.add(src["author"])
            tids.update(b.get("tweets") or [])
            view_blocks.append({
                "kind": str(b.get("kind") or "多空综合分析"),
                "count": len(b.get("tweets") or []),
                "handles": [f"@{a}" for a in {src['author'] for src in srcs}][:4],
                "content": str(b.get("content") or "").strip(),
                "sources": srcs,
            })
        if not view_blocks:
            continue
        stocks.append({
            "code": code, "name": full,
            "group": str(s.get("group") or "热议"),
            "tag": str(s.get("tag") or "热议"),
            "stance": str(s.get("stance") or "中性"),
            "kol_count": len(authors), "post_count": len(tids),
            "summary": str(s.get("summary") or "").strip(),
            "view_blocks": view_blocks,
        })

    if not stocks:
        return None

    other_topics = []
    for t in agg.get("other_topics") or []:
        other_topics.append({
            "title": str(t.get("title") or "").strip(),
            "tag": str(t.get("tag") or "宏观"),
            "content": str(t.get("content") or "").strip(),
            "sources": _sources(t.get("tweets"), items),
        })

    stocks.sort(key=lambda x: (x["kol_count"], x["post_count"]), reverse=True)
    attention = [{"code": s["code"], "name": s["name"],
                  "kol_count": s["kol_count"], "post_count": s["post_count"]} for s in stocks[:6]]
    all_authors = {src["author"] for s in stocks for b in s["view_blocks"] for src in b["sources"]}
    platforms = sorted({src["platform"] for s in stocks for b in s["view_blocks"] for src in b["sources"]})
    hottest = attention[0] if attention else None
    return {
        "is_mock": False,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().strftime("%Y/%m/%d %H:%M"),
        "stats": {"kol_total": len(all_authors), "post_total": len(items), "hours": 24},
        "hottest": hottest,
        "attention_rank": attention,
        "stocks": stocks,
        "other_topics": other_topics,
        "sources_platform": platforms,
    }
```

把 `aggregate` 函数体替换为（保留前半 LLM 调用，组装委托给 `_assemble`）：

```python
def aggregate(items: list[dict]) -> dict | None:
    """调 DeepSeek 聚合，再把个股名解析成代码、把索引还原成真实来源。"""
    from quantcore.quant import llm
    from quantcore.quant.serenity_resolve import resolve_beneficiaries

    if not llm.available():
        print("  [error] LLM 不可用（缺 DEEPSEEK_API_KEY）", flush=True)
        return None
    sys_json = _AGG_SYS + "\n你必须只输出合法 JSON，不要任何解释或 markdown 代码块标记。"
    raw = llm.chat(_agg_prompt(items), sys_json, deep=True, max_tokens=4000)
    agg = llm._extract_json(raw) if raw else None
    if not isinstance(agg, dict):
        print(f"  [error] LLM 聚合返回非 JSON（raw {len(raw)} 字）: {raw[:160]!r}", flush=True)
        return None
    digest = _assemble(agg, items, resolve_beneficiaries)
    if not digest:
        print("  [error] 聚合后无可用个股（信号不足）", flush=True)
    return digest
```

> `aggregate` 参数名由 `tweets` 改为 `items`，与新语义一致。

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_kol_digest.py -k assemble -v`
Expected: PASS（两条都过）。

- [ ] **Step 5: Commit**

```bash
git add scripts/build_kol_digest.py tests/test_kol_digest.py
git commit -m "refactor(kol): extract _assemble pure fn; union sources_platform"
```

---

## Task 5：`_opencli_json` 通用调用器（替换 `_search`）（TDD）

**Files:**
- Modify: `scripts/build_kol_digest.py`（删除 `_search` 56-72，新增 `_opencli_json`）
- Test: `tests/test_kol_digest.py`

- [ ] **Step 1: 写失败测试**

```python
def test_opencli_json_parses_subprocess_output(monkeypatch):
    def fake_run(cmd, stdout, **kw):
        stdout.write('[{"author":"a","text":"hello world test","url":"u","likes":3}]')
        return None
    monkeypatch.setattr(bkd.subprocess, "run", fake_run)
    out = bkd._opencli_json(["twitter", "search", "x"])
    assert out and out[0]["author"] == "a"


def test_opencli_json_returns_empty_on_bad_json(monkeypatch):
    def fake_run(cmd, stdout, **kw):
        stdout.write("not json")
        return None
    monkeypatch.setattr(bkd.subprocess, "run", fake_run)
    assert bkd._opencli_json(["weibo", "search", "x"]) == []
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_kol_digest.py -k opencli_json -v`
Expected: FAIL（`_opencli_json` 不存在）。

- [ ] **Step 3: 删除 `_search`，新增 `_opencli_json`（放在 `_opencli_path` 之后、`_as_int` 之前）**

```python
def _opencli_json(args: list[str]) -> list[dict]:
    """跑 `opencli <args> -f json`，返回解析后的 list（任何失败返回 []）。
    写临时文件再读，避免 stdin/管道破坏 JSON。"""
    tmp = ROOT / "runtime" / "_kol_fetch.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            subprocess.run(
                [_opencli_path(), *args, "-f", "json"],
                stdout=f, stderr=subprocess.DEVNULL, timeout=90, check=False,
            )
        data = json.loads(tmp.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as exc:
        print(f"  [warn] opencli {' '.join(args)} failed: {exc}", flush=True)
        return []
    finally:
        tmp.unlink(missing_ok=True)
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_kol_digest.py -k opencli_json -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add scripts/build_kol_digest.py tests/test_kol_digest.py
git commit -m "refactor(kol): generic _opencli_json runner (replaces _search)"
```

---

## Task 6：配置块 + 三源采集器 + 重写 `collect`（TDD）

**Files:**
- Modify: `scripts/build_kol_digest.py`（改配置块 26-31；删除旧 `collect` 75-97；新增三采集器与新 `collect`）
- Test: `tests/test_kol_digest.py`

- [ ] **Step 1: 写失败测试**

```python
def test_collect_merges_three_sources_deduped(monkeypatch):
    def fake_oj(args):
        if args[:2] == ["twitter", "search"]:
            return [{"author": "x1", "text": "X 财经观点一二三四", "url": "http://x/1", "likes": 10}]
        if args[:2] == ["xueqiu", "hot"]:
            return [{"author": "xq1", "text": "雪球热门观点一二三", "url": "http://xq/1", "likes": 5}]
        if args[:2] == ["weibo", "search"]:
            return [{"author": "wb1", "title": "微博财经标题一二三", "url": "http://wb/1"}]
        return []  # feed / tweets / user-posts 空（无名单）
    monkeypatch.setattr(bkd, "_opencli_json", fake_oj)
    monkeypatch.setattr(bkd, "X_KOLS", [])
    monkeypatch.setattr(bkd, "WEIBO_KOLS", [])
    out = bkd.collect()
    assert {it["platform"] for it in out} == {"X", "雪球", "微博"}
    assert len(out) == 3   # 多关键词重复抓同一 url 被去重
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_kol_digest.py -k collect_merges -v`
Expected: FAIL（采集器/新 `collect` 未就位）。

- [ ] **Step 3a: 替换配置块（原 26-31）**

```python
# ── 跟踪对象（可编辑）────────────────────────────────────────────────
# 按个股名/关键词搜索是免名单兜底；X_KOLS/WEIBO_KOLS 填 handle 做名单增强。
SEARCH_TERMS = [
    "中际旭创", "寒武纪 算力", "宁德时代", "贵州茅台", "比亚迪",
    "A股 复盘", "A股 龙头 涨停",
]
FINANCE_KEYWORDS = SEARCH_TERMS          # 微博 search 用，默认复用
X_KOLS: list[str] = []                   # 推特 handle（twitter tweets <handle>）
WEIBO_KOLS: list[str] = []               # 微博 @博主（weibo user-posts <id>）
SEARCH_LIMIT = 12
MAX_ITEMS = 36                           # 跨源去重后保留上限
```

> 雪球名单靠网页关注后走 `feed`，脚本侧无 handle 配置。

- [ ] **Step 3b: 新增三采集器与新 `collect`（放在 `_dedup_rank` 之后、`_sources` 之前）**

```python
def _collect_x() -> list[dict]:
    items = []
    for term in SEARCH_TERMS:
        for raw in _opencli_json(["twitter", "search", term, "--filter", "live", "--limit", str(SEARCH_LIMIT)]):
            items.append(_norm("X", raw))
    for h in X_KOLS:
        for raw in _opencli_json(["twitter", "tweets", h, "--limit", str(SEARCH_LIMIT)]):
            items.append(_norm("X", raw))
    return items


def _collect_xueqiu() -> list[dict]:
    items = []
    for raw in _opencli_json(["xueqiu", "hot", "--limit", str(SEARCH_LIMIT)]):
        items.append(_norm("雪球", raw))
    for raw in _opencli_json(["xueqiu", "feed", "--page", "1", "--limit", str(SEARCH_LIMIT)]):
        items.append(_norm("雪球", raw))
    return items


def _collect_weibo() -> list[dict]:
    items = []
    for term in FINANCE_KEYWORDS:
        for raw in _opencli_json(["weibo", "search", term, "--limit", str(SEARCH_LIMIT)]):
            items.append(_norm("微博", raw))
    for h in WEIBO_KOLS:
        for raw in _opencli_json(["weibo", "user-posts", h, "--limit", str(SEARCH_LIMIT)]):
            items.append(_norm("微博", raw))
    return items


def collect() -> list[dict]:
    """三源采集 → 跨源去重粗排。每源独立 try/except，一个挂不影响其他。"""
    items: list[dict] = []
    for name, fn in (("X", _collect_x), ("雪球", _collect_xueqiu), ("微博", _collect_weibo)):
        try:
            got = fn()
            print(f"  {name}: {len(got)} 条", flush=True)
            items.extend(got)
        except Exception as exc:
            print(f"  [warn] {name} 采集失败: {exc}", flush=True)
    return _dedup_rank(items, MAX_ITEMS)
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_kol_digest.py -k collect_merges -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add scripts/build_kol_digest.py tests/test_kol_digest.py
git commit -m "feat(kol): three-source collectors (X/xueqiu/weibo) + hybrid collect"
```

---

## Task 7：`--discover` 候选发现模式（TDD）

**Files:**
- Modify: `scripts/build_kol_digest.py`（新增 `CAND_PATH` 常量、`discover()`；`import argparse`）
- Test: `tests/test_kol_digest.py`

- [ ] **Step 1: 写失败测试**

```python
def test_discover_groups_authors_and_writes_file(monkeypatch, tmp_path):
    def fake_oj(args):
        if args[:2] == ["twitter", "search"]:
            return [{"author": "tw_kol", "text": "财经观点一二三四五", "url": "http://x/1", "likes": 7}]
        if args[:2] == ["weibo", "search"]:
            return [{"author": "wb_kol", "title": "微博财经一二三四", "url": "http://wb/1"}]
        return []
    monkeypatch.setattr(bkd, "_opencli_json", fake_oj)
    monkeypatch.setattr(bkd, "SEARCH_TERMS", ["t1", "t2"])
    monkeypatch.setattr(bkd, "FINANCE_KEYWORDS", ["t1"])
    monkeypatch.setattr(bkd, "CAND_PATH", tmp_path / "kol_candidates.json")
    cands = bkd.discover()
    by_author = {c["author"]: c for c in cands}
    assert by_author["tw_kol"]["platform"] == "X"
    assert by_author["tw_kol"]["count"] == 2      # 两个关键词各命中一次
    assert by_author["tw_kol"]["likes"] == 14
    assert by_author["wb_kol"]["count"] == 1
    assert (tmp_path / "kol_candidates.json").exists()
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_kol_digest.py -k discover -v`
Expected: FAIL（`discover`/`CAND_PATH` 不存在）。

- [ ] **Step 3a: 顶部 `import` 段加 `argparse`，并在 `OUT_PATH` 旁加 `CAND_PATH`**

```python
import argparse
```

```python
CAND_PATH = ROOT / "runtime" / "kol_candidates.json"   # --discover 候选输出
```

- [ ] **Step 3b: 新增 `discover()`（放在 `aggregate` 之后）**

```python
def discover() -> list[dict]:
    """按关键词搜 X/微博，按作者聚合候选 KOL（不调 LLM、不写 digest）。供人工挑 handle。"""
    from collections import defaultdict
    agg: dict = defaultdict(lambda: {"count": 0, "likes": 0, "sample": ""})

    def _tally(platform: str, raws: list[dict]) -> None:
        for raw in raws:
            it = _norm(platform, raw)
            if not it["author"]:
                continue
            slot = agg[(platform, it["author"])]
            slot["count"] += 1
            slot["likes"] += it["likes"]
            if not slot["sample"]:
                slot["sample"] = it["text"][:60]

    for term in SEARCH_TERMS:
        _tally("X", _opencli_json(["twitter", "search", term, "--filter", "live", "--limit", str(SEARCH_LIMIT)]))
    for term in FINANCE_KEYWORDS:
        _tally("微博", _opencli_json(["weibo", "search", term, "--limit", str(SEARCH_LIMIT)]))

    cands = [{"platform": p, "author": a, **v} for (p, a), v in agg.items()]
    cands.sort(key=lambda x: (x["count"], x["likes"]), reverse=True)
    CAND_PATH.parent.mkdir(parents=True, exist_ok=True)
    CAND_PATH.write_text(json.dumps(cands, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n候选 KOL（共 {len(cands)}，已写 {CAND_PATH.name}）：", flush=True)
    print(f"{'平台':<5}{'作者':<22}{'次数':>5}{'累计赞':>8}  样例")
    for c in cands[:30]:
        print(f"{c['platform']:<5}{c['author']:<22}{c['count']:>5}{c['likes']:>8}  {c['sample']}")
    return cands
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_kol_digest.py -k discover -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add scripts/build_kol_digest.py tests/test_kol_digest.py
git commit -m "feat(kol): --discover mode to surface real KOL handle candidates"
```

---

## Task 8：`main` 接 `--discover` 路由 + prompt/header 措辞（无独立测试，grep 验证）

**Files:**
- Modify: `scripts/build_kol_digest.py`（`main` 219-235；`_agg_prompt` 引文行；文件头 docstring）

- [ ] **Step 1: 替换 `main`**

```python
def main() -> int:
    ap = argparse.ArgumentParser(description="KOL 日报采集器（X 优先 + 雪球/微博补充）")
    ap.add_argument("--discover", action="store_true", help="只列候选 KOL（不生成 digest）")
    args = ap.parse_args()

    _load_env()
    sys.path.insert(0, str(ROOT))

    if args.discover:
        discover()
        return 0

    print("采集 X / 雪球 / 微博 KOL 内容…", flush=True)
    items = collect()
    print(f"采集到 {len(items)} 条去重内容", flush=True)
    if len(items) < 5:
        print("内容太少，放弃生成（保留上次/占位）。", flush=True)
        return 1
    print("DeepSeek 聚合中…", flush=True)
    digest = aggregate(items)
    if not digest:
        return 1
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 已写入 {OUT_PATH}：{len(digest['stocks'])} 只个股 / {len(digest['other_topics'])} 条热议", flush=True)
    return 0
```

- [ ] **Step 2: `_agg_prompt` 改两处措辞（引文行 + 变量名）**

把开头一行：
```python
"下面是从推特(X)采集的中文财经推文（已编号）。请：\n"
```
改为：
```python
"下面是从 X / 雪球 / 微博 采集的中文财经内容（已编号）。请：\n"
```
并把函数签名 `def _agg_prompt(tweets: list[dict]) -> str:` 改为 `def _agg_prompt(items: list[dict]) -> str:`，函数体内 `for i, t in enumerate(tweets)` 改为 `enumerate(items)`。（`tweets` 这个 JSON 字段名是 LLM 输出的索引数组键，**保持不变**，不要改 prompt 里 `"tweets":[编号,...]` 的 schema。）

- [ ] **Step 3: 更新文件头 docstring 顶部说明（1-11 行附近）**

把模块 docstring 第一行与"用法"段更新为三源 + 登录前置：
```python
"""KOL 日报真实采集器：opencli 读 X(推特)/雪球/微博 → DeepSeek 按个股聚合 → 写 runtime/kol_digest.json。

X 优先 + 雪球/微博补充；混合策略=热门兜底(search/hot)+名单增强(X_KOLS/WEIBO_KOLS/雪球feed)。
后端 quantcore.quant.kol_rooms.get_digest() 读该文件（新鲜则用真实数据，否则降级占位）。

前置：Chrome 已登录 x.com / weibo.com / xueqiu.com + Browser Bridge 扩展（未登录的源静默返回空，由兜底/降级吸收）。
用法（本地）：
    python scripts/build_kol_digest.py            # 三源采集 → 生成 digest
    python scripts/build_kol_digest.py --discover # 只列候选 KOL，挑 handle 填进 X_KOLS/WEIBO_KOLS
"""
```

- [ ] **Step 4: 验证措辞与路由**

Run: `python -m pytest tests/test_kol_digest.py -v`
Expected: 全部 PASS（重构未破坏）。

Run: `grep -n "推特(X)采集" scripts/build_kol_digest.py`
Expected: 无输出（旧措辞已清除）。

Run: `python scripts/build_kol_digest.py --discover` （无 Chrome 登录时）
Expected: 不报错；打印"候选 KOL（共 0 …）"并写出空 `runtime/kol_candidates.json`（各源返回空、优雅降级）。

- [ ] **Step 5: Commit**

```bash
git add scripts/build_kol_digest.py
git commit -m "feat(kol): wire --discover into main; generalize prompt/header to 3 sources"
```

---

## Task 9：全量离线测试 + 自检收尾

**Files:**
- Test: `tests/test_kol_digest.py`

- [ ] **Step 1: 跑该功能全部离线单测**

Run: `python -m pytest tests/test_kol_digest.py -v`
Expected: 全部 PASS（norm×2 / dedup / sources / assemble×2 / opencli_json×2 / collect_merges / discover / module_loads）。

- [ ] **Step 2: 跑全仓回归，确认未破坏现有**

Run: `python -m pytest -q`
Expected: 现有 29 + 新增用例全 PASS。

- [ ] **Step 3: 静态检查残留旧符号**

Run: `grep -n "_search\|MAX_TWEETS\|KOL_HANDLES" scripts/build_kol_digest.py`
Expected: 无输出（旧符号已全部替换为 `_opencli_json`/`MAX_ITEMS`/`X_KOLS`）。

- [ ] **Step 4: Commit（如有遗留清理）**

```bash
git add -A
git commit -m "test(kol): full offline suite green; cleanup legacy symbols"
```

---

## 联网冒烟（人工，需 Chrome 登录 x.com/weibo.com/xueqiu.com）

非自动步骤，交付后由用户本地执行：

1. 逐源单跑确认字段未漂移：
   - `opencli twitter search "中际旭创" --filter live --limit 5 -f json`
   - `opencli xueqiu hot --limit 5 -f json`
   - `opencli weibo search "中际旭创" --limit 5 -f json`
2. `python scripts/build_kol_digest.py --discover` → 看候选表，挑真实 handle 填进 `X_KOLS`/`WEIBO_KOLS`。
3. `python scripts/build_kol_digest.py` → 生成 `runtime/kol_digest.json`；起前端 KOL 日报页核对：来源平台/作者/链接正确，多源混排。
4. 任一源未登录 → 该源空、其余照常出结果（优雅降级）。

## 自检（spec 覆盖）

- 混合采集（热门兜底+名单增强）→ Task 6 三采集器 ✓
- X 名单升级 `twitter tweets` → Task 6 `_collect_x` ✓
- `--discover` 发现真实 handle → Task 7 ✓
- 归一统一结构 → Task 1 `_norm` ✓
- 跨源去重 → Task 2 `_dedup_rank` ✓
- 修来源平台硬编码 bug → Task 3 `_sources` ✓
- 输出契约不变 → Task 4 `_assemble` schema 测试 ✓
- prompt/header 措辞 → Task 8 ✓
- 离线单测三断言（platform 透传/去重/schema）→ Task 3/2/4 ✓
- 前端 & `get_digest()` 零改动 → 全程未触碰 `kol_rooms.py` 既有契约 / 前端 ✓
