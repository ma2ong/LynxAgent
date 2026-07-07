# 批次 2：五方判读批量化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有单股「五评委打分」（`investor_panel.py`，价值/趋势/游资/逆向/量化 5 人格 LLM 评分）批量化：对当日选股池候选自动打分入库，选股列表显示「五方共识分 + 分歧度」列，点开看 5 人格评语——对标 stockgod 扫描页的五方判读列。

**Architecture:** 评分按 (date, symbol) 存 `panel_scores` 表（同一股票当日只打一次，跨池复用，控 LLM 成本）。API `GET /api/quant/panel/batch?pool=` 读当日该池候选（picks_history）→ 返回已有评分 + 后台单线程补打缺失的（不阻塞请求）。前端拿到 pending>0 就轮询。**上限每池每日 20 只**。

**Tech Stack:** 复用 `investor_panel(symbol)`（一次 LLM 调用出 5 人格），SQLite（LocalQuantStore），FastAPI 后台 ThreadPoolExecutor(1)，Vue3 + Element Plus。

**实施偏离记录（执行后回写）:**
（暂无）

**约定:** 测试 `python -m pytest`（仓库根）；后端改动需重启（无 --reload）；A股红涨绿跌；commit message 英文 + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`；每 Task 一个 commit。

---

### Task 1: panel_scores 存储层（LocalQuantStore）

**Files:**
- Modify: `quantcore/quant/local_store.py`（_SCHEMA + 类尾部新方法）
- Test: `tests/test_panel_batch.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_panel_batch.py`：

```python
"""五方判读批量化（panel_scores 表 + panel_batch 逻辑）回归测试。"""
import pytest

from quantcore.quant.local_store import LocalQuantStore


@pytest.fixture()
def store(tmp_path):
    return LocalQuantStore(str(tmp_path / "test.sqlite"))


def _payload(consensus=60.0, divergence=20.0):
    return {
        "consensus_score": consensus, "divergence": divergence,
        "bull_count": 3, "bear_count": 1, "summary": "共识偏多",
        "verdicts": [{"persona": "价值派", "style": "value", "score": 70,
                      "stance": "看多", "reason": "低估"}],
    }


def test_panel_score_roundtrip(store):
    store.save_panel_score("2026-07-07", "600001", _payload())
    scores = store.load_panel_scores("2026-07-07", ["600001", "600002"])
    assert set(scores.keys()) == {"600001"}
    assert scores["600001"]["consensus_score"] == 60.0
    assert scores["600001"]["verdicts"][0]["persona"] == "价值派"
    # 同日同股重存覆盖
    store.save_panel_score("2026-07-07", "600001", _payload(consensus=80.0))
    assert store.load_panel_scores("2026-07-07", ["600001"])["600001"]["consensus_score"] == 80.0


def test_panel_scores_scoped_by_date(store):
    store.save_panel_score("2026-07-04", "600001", _payload())
    assert store.load_panel_scores("2026-07-07", ["600001"]) == {}


def test_load_panel_scores_no_symbols_returns_all_of_day(store):
    store.save_panel_score("2026-07-07", "600001", _payload())
    store.save_panel_score("2026-07-07", "600002", _payload(consensus=40.0))
    scores = store.load_panel_scores("2026-07-07")
    assert len(scores) == 2


def test_load_picks_symbols(store):
    store.record_picks("smart", [
        {"symbol": "600001", "name": "甲", "score": 90, "close": 10.0},
        {"symbol": "600002", "name": "乙", "score": 80, "close": 20.0},
    ])
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")
    assert store.load_picks_symbols(today, "smart", limit=10) == ["600001", "600002"]
    assert store.load_picks_symbols(today, "pattern") == []
    assert store.load_picks_symbols(today, "smart", limit=1) == ["600001"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_panel_batch.py -v`
Expected: FAIL，`AttributeError: ... 'save_panel_score'`

- [ ] **Step 3: 实现**

`_SCHEMA` 末尾（daily_reports 建表语句后、闭合 `"""` 前）追加：

```sql
CREATE TABLE IF NOT EXISTS panel_scores (
    date TEXT,
    symbol TEXT,
    consensus REAL,
    divergence REAL,
    bull INTEGER,
    bear INTEGER,
    verdicts_json TEXT,
    summary TEXT,
    created_at TEXT,
    PRIMARY KEY (date, symbol)
);
```

`LocalQuantStore` 类中（`list_report_dates` 方法之后）新增：

```python
    # ---- 五方判读批量评分 ----
    def save_panel_score(self, date: str, symbol: str, payload: Dict[str, object]) -> None:
        import json
        from datetime import datetime
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO panel_scores"
            "(date, symbol, consensus, divergence, bull, bear, verdicts_json, summary, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (date, symbol,
             float(payload.get("consensus_score") or 0.0),
             float(payload.get("divergence") or 0.0),
             int(payload.get("bull_count") or 0),
             int(payload.get("bear_count") or 0),
             json.dumps(payload.get("verdicts") or [], ensure_ascii=False),
             str(payload.get("summary") or ""),
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()

    def load_panel_scores(self, date: str, symbols: Optional[List[str]] = None) -> Dict[str, Dict[str, object]]:
        import json
        if symbols is not None and not symbols:
            return {}
        sql = ("SELECT symbol, consensus, divergence, bull, bear, verdicts_json, summary"
               " FROM panel_scores WHERE date=?")
        params: List[object] = [date]
        if symbols:
            sql += f" AND symbol IN ({','.join('?' * len(symbols))})"
            params.extend(symbols)
        out: Dict[str, Dict[str, object]] = {}
        for row in self._conn().execute(sql, params).fetchall():
            try:
                verdicts = json.loads(row[5] or "[]")
            except ValueError:
                verdicts = []
            out[row[0]] = {
                "consensus_score": row[1], "divergence": row[2],
                "bull_count": row[3], "bear_count": row[4],
                "verdicts": verdicts, "summary": row[6],
            }
        return out

    def load_picks_symbols(self, date: str, pool: str, limit: int = 20) -> List[str]:
        rows = self._conn().execute(
            "SELECT symbol FROM picks_history WHERE pick_date=? AND pool=?"
            " ORDER BY COALESCE(rank, 999), symbol LIMIT ?",
            (date, pool, limit),
        ).fetchall()
        return [r[0] for r in rows]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_panel_batch.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add quantcore/quant/local_store.py tests/test_panel_batch.py
git commit -m "feat(panel): panel_scores table and picks symbol lookup on LocalQuantStore"
```

---

### Task 2: 批量评分逻辑（investor_panel.py 追加）

**Files:**
- Modify: `quantcore/quant/investor_panel.py`（文件末尾追加）
- Test: `tests/test_panel_batch.py`（追加）

- [ ] **Step 1: 追加失败测试**

`tests/test_panel_batch.py` 末尾追加：

```python
from quantcore.quant import investor_panel as ip


def test_run_panel_batch_scores_missing_and_skips_scored(store, monkeypatch):
    calls: list[str] = []

    def fake_panel(symbol):
        calls.append(symbol)
        return {"empty": False, "symbol": symbol, "consensus_score": 66.0,
                "divergence": 10.0, "bull_count": 4, "bear_count": 0,
                "verdicts": [], "summary": "ok"}

    monkeypatch.setattr(ip, "investor_panel", fake_panel)
    monkeypatch.setattr(ip, "get_local_store", lambda: store)
    store.save_panel_score("2026-07-07", "600001", {"consensus_score": 50})

    n = ip.run_panel_batch("2026-07-07", ["600001", "600002", "600003"])
    assert n == 2  # 600001 已有评分被跳过
    assert calls == ["600002", "600003"]
    assert set(store.load_panel_scores("2026-07-07").keys()) == {"600001", "600002", "600003"}


def test_run_panel_batch_skips_failed_scores(store, monkeypatch):
    monkeypatch.setattr(ip, "investor_panel",
                        lambda s: {"empty": True, "message": "no llm"})
    monkeypatch.setattr(ip, "get_local_store", lambda: store)
    assert ip.run_panel_batch("2026-07-07", ["600001"]) == 0
    assert store.load_panel_scores("2026-07-07") == {}


def test_run_panel_batch_inflight_dedupe(store, monkeypatch):
    """同一 symbol 正在评分时（inflight），重复批次不会再打。"""
    monkeypatch.setattr(ip, "get_local_store", lambda: store)
    ip._PANEL_INFLIGHT.add("600009")
    try:
        called = []
        monkeypatch.setattr(ip, "investor_panel", lambda s: called.append(s) or {"empty": True})
        ip.run_panel_batch("2026-07-07", ["600009"])
        assert called == []
    finally:
        ip._PANEL_INFLIGHT.discard("600009")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_panel_batch.py -v`
Expected: 新增 3 个 FAIL（AttributeError: run_panel_batch），原 4 个 PASS

- [ ] **Step 3: 实现**

`quantcore/quant/investor_panel.py` 顶部 import 区确认已有 `from typing import Dict, List, Optional`（缺则补），并在文件顶部 import 区加：

```python
import threading
```

再在文件末尾追加：

```python
from .local_store import get_local_store

# 批量评分：跨请求去重（同一 symbol 只允许一个在途评分）
_PANEL_LOCK = threading.Lock()
_PANEL_INFLIGHT: set = set()


def run_panel_batch(date: str, symbols: List[str]) -> int:
    """顺序为缺评分的 symbol 打分并落库，返回新打分数量。

    - 已有当日评分/正在评分中的跳过（跨池复用，控 LLM 成本）；
    - 单线程顺序调用（每只一次 LLM），失败的静默跳过下次再补；
    - 供 API 层丢进后台线程执行，勿在请求路径同步调用。
    """
    store = get_local_store()
    scored = set(store.load_panel_scores(date, symbols).keys())
    with _PANEL_LOCK:
        todo = [s for s in symbols if s not in scored and s not in _PANEL_INFLIGHT]
        _PANEL_INFLIGHT.update(todo)
    done = 0
    try:
        for symbol in todo:
            try:
                result = investor_panel(symbol)
                if isinstance(result, dict) and not result.get("empty"):
                    store.save_panel_score(date, symbol, result)
                    done += 1
            except Exception:
                continue
    finally:
        with _PANEL_LOCK:
            _PANEL_INFLIGHT.difference_update(todo)
    return done
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_panel_batch.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add quantcore/quant/investor_panel.py tests/test_panel_batch.py
git commit -m "feat(panel): run_panel_batch scores pool candidates once per day with inflight dedupe"
```

---

### Task 3: API 端点 GET /api/quant/panel/batch

**Files:**
- Modify: `app/routers/quant.py`

- [ ] **Step 1: 实现端点**

`app/routers/quant.py`：

顶部 import 区（`from quantcore.quant.investor_panel import investor_panel` 一行）改为：

```python
from quantcore.quant.investor_panel import investor_panel, run_panel_batch
```

`_light_executor` 定义之后加一个专用单线程池（LLM 批量评分互相排队，不占轻量池）：

```python
# 五方判读批量评分专用（LLM 顺序调用，单线程防限流），与轻量读接口隔离
_panel_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="panel-batch")
```

在 `quant_investor_panel` 端点（`@router.get("/stock/investor-panel")`）之后追加：

```python
_PANEL_POOLS = ("smart", "pattern", "swing", "auction")


@router.get("/panel/batch")
async def quant_panel_batch(pool: str = "smart", limit: int = 20):
    """当日选股池候选的五方判读批量评分：返回已有评分，缺的丢后台补打（不阻塞）。"""
    from datetime import datetime
    from quantcore.quant import llm
    from quantcore.quant.local_store import get_local_store

    if pool not in _PANEL_POOLS:
        raise HTTPException(status_code=400, detail=f"pool 必须是 {'/'.join(_PANEL_POOLS)}")
    limit = max(1, min(limit, 20))
    today = datetime.now().strftime("%Y-%m-%d")
    store = get_local_store()
    symbols = await _run_light(store.load_picks_symbols, today, pool, limit)
    if not symbols:
        return {"success": True, "data": {"date": today, "pool": pool, "items": {},
                                          "pending": 0, "llm": llm.available(),
                                          "message": "今日该池暂无留痕候选，先跑一次选股"}}
    scores = await _run_light(store.load_panel_scores, today, symbols)
    pending = [s for s in symbols if s not in scores]
    if pending and llm.available():
        _panel_executor.submit(run_panel_batch, today, pending)
    return {"success": True, "data": {
        "date": today, "pool": pool, "items": scores,
        "pending": len(pending) if llm.available() else 0,
        "llm": llm.available(),
    }}
```

- [ ] **Step 2: 验证**

`python -c "import app.routers.quant"` 无错误。重启后端（先杀 8001 再 `.\scripts\start_lite.ps1 -NoOpen -NoFrontend`），登录（looptest / loop-test-1234，token 在 `.data.access_token`）后：

```powershell
$tok = (Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8001/api/auth/login -ContentType application/json -Body '{"username":"looptest","password":"loop-test-1234"}').data.access_token
Invoke-RestMethod "http://127.0.0.1:8001/api/quant/panel/batch?pool=smart" -Headers @{Authorization="Bearer $tok"}
```

Expected: success:true。若今日无留痕返回引导 message；若有候选则 pending>0，等 1-2 分钟再调 items 逐渐变多（llm 可用时）。`pool=bogus` 应 400。

- [ ] **Step 3: 全量回归**

Run: `python -m pytest tests/ -q`
Expected: 63 passed（56+7）

- [ ] **Step 4: Commit**

```bash
git add app/routers/quant.py
git commit -m "feat(panel): batch scoring endpoint with background fill for pool candidates"
```

---

### Task 4: 前端五方判读列 + 评委详情弹层

**Files:**
- Modify: `frontend/src/api/quant.ts`（末尾追加）
- Modify: `frontend/src/views/Quant/index.vue`（智能推荐表格 + 形态选股表格加列；新增弹层与轮询逻辑）

- [ ] **Step 1: quant.ts 末尾追加**

```typescript
export interface PanelVerdict {
  persona: string
  style: string
  score: number
  stance: string
  reason: string
}

export interface PanelScore {
  consensus_score: number
  divergence: number
  bull_count: number
  bear_count: number
  verdicts: PanelVerdict[]
  summary: string
}

export interface PanelBatchData {
  date: string
  pool: string
  items: Record<string, PanelScore>
  pending: number
  llm: boolean
  message?: string
}

export const panelApi = {
  batch: async (pool: string) => {
    const raw = await ApiClient.get<any>('/api/quant/panel/batch', { pool }, { timeout: 30000 })
    return (raw as any)?.data as PanelBatchData | null
  },
}
```

- [ ] **Step 2: Quant/index.vue 加状态与加载逻辑**

该文件很大（约 2000 行），外科手术式插入。`<script setup>` 里（其他 ref 定义附近）加：

```typescript
// 五方判读批量评分：pool -> symbol -> score；后台补打时轮询
const panelScores = ref<Record<string, Record<string, PanelScore>>>({ smart: {}, pattern: {} })
const panelDialogVisible = ref(false)
const panelDialogData = ref<PanelScore | null>(null)
const panelDialogTitle = ref('')
const panelTimers: Record<string, number> = {}

const loadPanelScores = async (pool: 'smart' | 'pattern', attempt = 0) => {
  try {
    const data = await panelApi.batch(pool)
    if (!data) return
    panelScores.value[pool] = data.items || {}
    if (data.pending > 0 && attempt < 10) {
      if (panelTimers[pool]) window.clearTimeout(panelTimers[pool])
      panelTimers[pool] = window.setTimeout(() => loadPanelScores(pool, attempt + 1), 20000)
    }
  } catch { /* 判读是增强信息，失败静默 */ }
}

const openPanelDialog = (row: { symbol: string; name?: string }, pool: 'smart' | 'pattern') => {
  const score = panelScores.value[pool]?.[row.symbol]
  if (!score) return
  panelDialogData.value = score
  panelDialogTitle.value = `${row.name || row.symbol} · 五方判读`
  panelDialogVisible.value = true
}

const stanceType = (stance: string) => (stance === '看多' ? 'danger' : stance === '看空' ? 'success' : 'info')
```

import 区补 `panelApi, type PanelScore`（并入现有 `@/api/quant` import）。组件卸载时清理定时器：若已有 `onUnmounted` 则并入，没有则新增 `onUnmounted(() => Object.values(panelTimers).forEach(t => window.clearTimeout(t)))`（记得 import onUnmounted）。

在智能池结果就绪处（后台任务完成给 `smartPoolResult.value` 赋值的地方，grep `smartPoolResult.value =` 找到所有赋值点，在成功赋值后）调用 `loadPanelScores('smart')`；形态池同理（`patternPoolResult.value =` 赋值成功后调 `loadPanelScores('pattern')`）。

- [ ] **Step 3: 两个表格加「五方判读」列**

智能推荐表格（`AI因子` 列之后）插入：

```html
              <el-table-column label="五方判读" width="110">
                <template #default="{ row }">
                  <el-tooltip v-if="panelScores.smart[row.symbol]" :content="panelScores.smart[row.symbol].summary" placement="top">
                    <el-button text size="small" @click.stop="openPanelDialog(row, 'smart')">
                      <b>{{ panelScores.smart[row.symbol].consensus_score.toFixed(0) }}</b>
                      <span class="panel-div">±{{ panelScores.smart[row.symbol].divergence.toFixed(0) }}</span>
                    </el-button>
                  </el-tooltip>
                  <span v-else class="panel-pending">—</span>
                </template>
              </el-table-column>
```

形态选股表格加同结构列（`panelScores.pattern[row.symbol]` / `openPanelDialog(row, 'pattern')`）。

页面模板底部（其他 el-dialog 旁）加评委弹层：

```html
    <el-dialog v-model="panelDialogVisible" :title="panelDialogTitle" width="560px">
      <template v-if="panelDialogData">
        <p class="panel-summary">{{ panelDialogData.summary }}</p>
        <el-table :data="panelDialogData.verdicts" size="small">
          <el-table-column prop="persona" label="评委" width="90" />
          <el-table-column prop="score" label="评分" width="70" />
          <el-table-column label="立场" width="80">
            <template #default="{ row }">
              <el-tag size="small" :type="stanceType(row.stance)">{{ row.stance }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="reason" label="一句话理由" min-width="220" />
        </el-table>
        <p class="panel-note">AI 模拟多风格视角生成，仅供参考，非投资建议。</p>
      </template>
    </el-dialog>
```

`<style scoped>` 加：

```css
.panel-div { margin-left: 2px; font-size: 11px; color: var(--el-text-color-secondary); }
.panel-pending { color: var(--el-text-color-placeholder); }
.panel-summary { margin: 0 0 10px; font-size: 13px; }
.panel-note { margin: 10px 0 0; font-size: 12px; color: var(--el-text-color-placeholder); }
```

注意：形态表格若列很挤，列宽保持 110 即可（表格自身横向滚动）。立场配色红多绿空（A股习惯，stanceType 已按此写）。

- [ ] **Step 4: 验证**

```bash
cd frontend && npx vue-tsc --noEmit && npm run build
```

Expected: 通过。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/quant.ts frontend/src/views/Quant/index.vue
git commit -m "feat(panel): consensus/divergence column with judge detail dialog on pool tables"
```

---

### Task 5: 端到端验证 + README

- [ ] **Step 1: 全量回归**

```bash
python -m pytest tests/ -q          # 63 passed
cd frontend && npx vue-tsc --noEmit && npm run build
```

- [ ] **Step 2: 实机巡检**

后端 + 前端都在跑（`.\scripts\start_lite.ps1 -NoOpen`）。浏览器登录 → 智能选股页跑一次「一键智能推荐」→ 确认：
1. 表格出现「五方判读」列，初始 `—`，随后台评分完成逐渐出现「66 ±18」样式分数（llm 可用时；每只约 5-15 秒）
2. 点分数弹出评委弹层：5 行评分/立场/理由 + 摘要 + 免责
3. 刷新页面重进：已评分的立即显示（当日缓存，无重复 LLM 调用，可从后端日志/耗时确认）

- [ ] **Step 3: README 功能清单**

「✨ 核心功能」中「多智能体深度分析」条目后追加：

```markdown
- **五方判读** — 价值/趋势/游资/逆向/量化 5 个 AI 人格对选股池候选批量打分，列表直接看共识分与分歧度，点开看各家立场与理由；同一股票当日只打一次。
```

- [ ] **Step 4: Commit + push**

```bash
git add README.md
git commit -m "docs: add investor panel batch scoring to feature list"
git push origin main
```
