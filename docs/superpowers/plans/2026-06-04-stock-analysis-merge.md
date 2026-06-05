# 个股分析重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复财务/新闻数据空白、改善涨停热点概念分类、将「个股研报」+「个股深研」合并为一个令人眼前一亮的「个股分析」页面。

**Architecture:** 三条独立改进线：(A) 修 `fundamentals()` 的列选 bug → 财务数据立即可用；(B) 用 AKShare 概念板块 API 替换静态 `CONCEPT_BY_NAME` 字典，本地 SQLite 日缓存；(C) 新增 `StockAnalysis.vue` 合并两个入口，移除旧两个页面，侧边栏替换为「个股分析」。

**Tech Stack:** Python/FastAPI (quantcore)、Vue 3 + Element Plus + ECharts、AKShare、SQLite (已有 local_store)

---

## 文件清单

| 操作 | 路径 | 说明 |
|------|------|------|
| 修改 | `quantcore/quant/fundamentals.py` | 修 idx_col 列选 bug |
| 修改 | `quantcore/quant/report_service.py` | 修 news 调用，删 institution 空占位 |
| 修改 | `quantcore/quant/market_sentiment.py` | 移除 CONCEPT_BY_NAME（改用 lookup） |
| 新建 | `quantcore/quant/concept_lookup.py` | AKShare 概念板块缓存查询 |
| 修改 | `quantcore/quant/limit_up.py` | 改用 concept_lookup 分类 |
| 修改 | `app/lite_main.py` | 新增 `/api/quant/stock-analysis` 合并端点 |
| 新建 | `frontend/src/views/StockAnalysis/index.vue` | 合并后的新页面 |
| 删除 | `frontend/src/views/StockReport/Report.vue` | 替换为新页面 |
| 删除 | `frontend/src/views/Analysis/SingleAnalysis.vue` | 替换为新页面 |
| 修改 | `frontend/src/router/index.ts` | 换路由 |
| 修改 | `frontend/src/components/Layout/AppLayout.vue` | 侧边栏改「个股分析」 |

---

## Task 1: 修复 fundamentals() 列选 bug（财务数据全 None 根因）

**Files:**
- Modify: `quantcore/quant/fundamentals.py`

**背景：** `ak.stock_financial_abstract` 返回两个非数值列：`'选项'`（类别，如"盈利指标"）和 `'指标'`（指标名，如"营业总收入"）。现有代码的 `idx_col` 选择逻辑会优先命中 `'选项'`（因为条件 `str(c) in ("选项", "")` 先满足），导致 `metric()` 在类别列里搜索指标名，永远匹配不到，全返回 None。

- [ ] **Step 1: 定位并修复 `fundamentals()` 中的 idx_col 选择**

打开 `quantcore/quant/fundamentals.py`，找到这段（约第 30 行）：

```python
idx_col = next((c for c in df.columns if "指标" in str(c) or str(c) in ("选项", "")), df.columns[0])
period_cols = [c for c in df.columns if c != idx_col]
```

改为：

```python
# 指标名列：精确匹配 "指标"，或第二列（stock_financial_abstract 固定格式）
idx_col = next(
    (c for c in df.columns if str(c) == "指标"),
    df.columns[1] if len(df.columns) > 1 else df.columns[0],
)
# 期间列：排除类别列（选项）和指标列
period_cols = [c for c in df.columns if c not in (df.columns[0], idx_col) and str(c)[0].isdigit()]
```

- [ ] **Step 2: 验证修复（命令行快速测试）**

```bash
cd C:\Users\Administrator\lynxagent
python -c "
from quantcore.quant.fundamentals import fundamentals
r = fundamentals('600519')
print('revenue:', r.get('revenue'))
print('roe:', r.get('roe'))
print('eps:', r.get('eps'))
"
```

预期输出：revenue/roe/eps 均为非 None 数值（如 revenue ≈ 5.47e10，roe ≈ 30+）。

- [ ] **Step 3: 同时验证 news 是否正常返回**

```bash
python -c "
from quantcore.quant.news import stock_news
r = stock_news('600519', days=7, limit=5)
print('news count:', len(r))
if r: print('first title:', r[0].get('title','')[:40])
"
```

预期：news count >= 1，有标题文字。如果 news count = 0，后续 Task 2 会处理。

- [ ] **Step 4: Commit**

```bash
cd C:\Users\Administrator\lynxagent
git add quantcore/quant/fundamentals.py
git commit -m "fix(fundamentals): select '指标' column not '选项' — financial data was all None"
```

---

## Task 2: 涨停热点概念分类 — 用 AKShare 概念板块替换静态字典

**Files:**
- Create: `quantcore/quant/concept_lookup.py`
- Modify: `quantcore/quant/limit_up.py`
- Modify: `quantcore/quant/market_sentiment.py`（移除 CONCEPT_BY_NAME，改 export）

**背景：** 现有 `CONCEPT_BY_NAME` 只有约 55 条手动维护的映射，大量涨停股落入"其他"。用 AKShare `ak.stock_board_concept_cons_em(symbol=concept)` 可拿到每个概念板块的成分股列表，建成 `name→concept` 的完整映射，每天缓存一次到内存（进程重启才重建，够用）。

**目标概念优先级顺序：** AI硬件、光通信/CPO、机器人、国产芯片、煤炭、电力、大消费、航天军工、新能源车、医药、有色金属、公告重组、其他

- [ ] **Step 1: 新建 `quantcore/quant/concept_lookup.py`**

```python
"""按需从 AKShare 拉取概念板块成分，缓存到进程内存（日级刷新）。"""
from __future__ import annotations

import logging
import threading
from datetime import date
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# 要抓取的概念板块（名称需与 AKShare em 概念名匹配）及对应的展示标签
CONCEPT_TARGETS: list[tuple[str, str]] = [
    ("光模块", "光通信/CPO"),
    ("CPO概念", "光通信/CPO"),
    ("AI算力", "AI硬件"),
    ("人工智能", "AI硬件"),
    ("机器人概念", "机器人"),
    ("工业母机", "机器人"),
    ("半导体", "国产芯片"),
    ("芯片概念", "国产芯片"),
    ("煤炭", "煤炭"),
    ("电力", "电力"),
    ("储能", "电力"),
    ("消费电子", "大消费"),
    ("白酒", "大消费"),
    ("航天军工", "航天军工"),
    ("低空经济", "航天军工"),
    ("新能源车", "新能源车"),
    ("医疗器械", "医药"),
    ("创新药", "医药"),
    ("黄金", "有色金属"),
    ("铜", "有色金属"),
]

_cache: Dict[str, str] = {}       # name → concept label
_cache_date: Optional[date] = None
_lock = threading.Lock()


def _build_cache() -> Dict[str, str]:
    """拉取所有目标概念板块的成分股，返回 name→label 映射。失败的概念跳过。"""
    try:
        import akshare as ak
    except ImportError:
        return {}

    mapping: Dict[str, str] = {}
    # 先拿所有概念板块列表，用于模糊匹配 CONCEPT_TARGETS
    try:
        board_df = ak.stock_board_concept_name_em()
        board_names = board_df["板块名称"].tolist() if "板块名称" in board_df.columns else []
    except Exception:
        board_names = []

    seen_labels: set[str] = set()
    for keyword, label in CONCEPT_TARGETS:
        if label in seen_labels:
            continue  # 同一 label 只抓一次（避免重复覆盖）
        # 在板块名称里找最接近的匹配
        matched = next((n for n in board_names if keyword in n), None)
        if not matched:
            continue
        try:
            cons_df = ak.stock_board_concept_cons_em(symbol=matched)
            name_col = next((c for c in cons_df.columns if "名称" in str(c) or "name" in str(c).lower()), None)
            if name_col is None:
                continue
            for name in cons_df[name_col].dropna().astype(str):
                if name and name not in mapping:  # 先写入的概念优先级更高
                    mapping[name] = label
            seen_labels.add(label)
        except Exception as exc:
            logger.debug("concept_lookup: skip %s — %s", matched, exc)

    return mapping


def get_concept(name: str) -> Optional[str]:
    """返回股票名称对应的概念标签，没有映射时返回 None（由调用方决定降级）。"""
    global _cache, _cache_date
    today = date.today()
    with _lock:
        if _cache_date != today or not _cache:
            try:
                _cache = _build_cache()
                _cache_date = today
                logger.info("concept_lookup: built cache with %d entries", len(_cache))
            except Exception as exc:
                logger.warning("concept_lookup: cache build failed — %s", exc)
    return _cache.get(str(name))
```

- [ ] **Step 2: 修改 `quantcore/quant/limit_up.py` 使用 concept_lookup**

找到 `limit_up.py` 中的 import 和分类调用，替换：

```python
# 原来：
from .market_sentiment import CONCEPT_BY_NAME, _limit_cause, _limit_threshold, _segment

# 改为：
from .concept_lookup import get_concept
from .market_sentiment import _limit_cause, _limit_threshold, _segment
```

在 `compute_limit_up_distribution()` 内找到对 `_limit_cause()` 的调用，在其前面优先尝试 concept_lookup：

```python
# 找到这段（约在 compute_limit_up_distribution 末尾 items 构建处）：
#   "cause": r["cause"],
# 改为在 items 构建之前预先生成 cause：

# 在 for r in rows: 循环内，替换 cause 生成逻辑：
cause = get_concept(str(r["name"] or "")) or _limit_cause(
    str(r["name"] or ""), str(r["industry"] or ""), str(r["seg"] or "")
)
# 然后在 item dict 里用 cause（而不是重新调用 _limit_cause）
```

具体找到 `compute_limit_up_distribution` 内构建 item dict 的代码，将：
```python
"cause": r["cause"],
```
改为（假设你已在循环里提前算好 cause）：
```python
"cause": cause,
```

如果当前代码是用 lambda 或 apply 生成 cause 的（如 `df["cause"] = df.apply(lambda row: _limit_cause(...), axis=1)`），则改为：

```python
from .concept_lookup import get_concept

df["cause"] = df.apply(
    lambda row: get_concept(str(row.get("name") or "")) or _limit_cause(
        str(row.get("name") or ""), str(row.get("industry") or ""), str(row.get("seg") or "")
    ),
    axis=1,
)
```

- [ ] **Step 3: 移除 market_sentiment.py 中的 CONCEPT_BY_NAME（改用 concept_lookup）**

打开 `quantcore/quant/market_sentiment.py`，找到 `CONCEPT_BY_NAME = { ... }` 字典（约 50+ 行），以及 `_limit_cause()` 中对它的引用：

```python
# 在 _limit_cause 开头把这段：
if name in CONCEPT_BY_NAME:
    return CONCEPT_BY_NAME[name]
# 改为：
from .concept_lookup import get_concept
_cached = get_concept(name)
if _cached:
    return _cached
```

然后删除 `CONCEPT_BY_NAME` 整个字典定义。

- [ ] **Step 4: 验证分类改善**

```bash
python -c "
from quantcore.quant.concept_lookup import get_concept, _build_cache
cache = _build_cache()
print('cache size:', len(cache))
# 抽查几个常见涨停股
for name in ['亨通光电', '大有能源', '中重科技', '通富微电', '波顿股份']:
    print(f'{name}: {cache.get(name, \"未映射\")}')
"
```

预期：cache size > 200，亨通光电→光通信/CPO，大有能源→煤炭。

- [ ] **Step 5: Commit**

```bash
git add quantcore/quant/concept_lookup.py quantcore/quant/limit_up.py quantcore/quant/market_sentiment.py
git commit -m "feat(limitup): AKShare concept board lookup replaces static CONCEPT_BY_NAME dict"
```

---

## Task 3: 新建合并「个股分析」页面后端端点

**Files:**
- Modify: `app/lite_main.py`

**设计：** 新端点 `GET /api/quant/stock-analysis/{symbol}` 返回所有可用数据，前端渲染。快速报告（rule-based）立即返回；多智能体深度分析保留原来 `/api/analysis/single` 异步入口不变（前端按需触发）。

- [ ] **Step 1: 在 `app/lite_main.py` 末尾新增 stock-analysis 端点**

```python
@app.get("/api/quant/stock-analysis/{symbol}")
async def stock_analysis(symbol: str, _: str = Depends(get_current_lite_user)):
    """合并个股研报 + 快速技术分析，供新版个股分析页使用。"""
    from quantcore.quant.report_service import build_stock_report
    try:
        report = await asyncio.to_thread(build_stock_report, symbol)
    except Exception as exc:
        report = {"available": False, "error": str(exc)}
    return {"success": True, "data": report}
```

（注意：`build_stock_report` 已包含 kline、技术评分、财务、AI投资观点、新闻。深度分析仍用原有 `/api/analysis/single` POST 端点。）

- [ ] **Step 2: 验证端点**

启动后访问（已有 backend 直接测试）：

```bash
python -c "
import requests, json
r = requests.post('http://localhost:8000/api/auth/login', json={'username':'admin','password':'admin123'})
token = r.json()['data']['access_token']
r2 = requests.get('http://localhost:8000/api/quant/stock-analysis/600519', headers={'Authorization': f'Bearer {token}'})
d = r2.json()['data']
print('available:', d.get('available'))
print('financial_summary available:', d.get('financial_summary', {}).get('available'))
print('news count:', len(d.get('news', [])))
print('kpi keys:', list(d.get('header', {}).keys())[:6])
"
```

预期：`financial_summary.available = True`（Task 1 修复后），`news count >= 1`。

- [ ] **Step 3: Commit**

```bash
git add app/lite_main.py
git commit -m "feat(api): add /api/quant/stock-analysis/{symbol} unified endpoint"
```

---

## Task 4: 新建「个股分析」前端页面

**Files:**
- Create: `frontend/src/views/StockAnalysis/index.vue`

**页面结构（从上到下）：**

```
┌─ 搜索栏 ─────────────────────────────────────────────────┐
│  [输入框 股票代码/名称]  [分析] 按钮                        │
└───────────────────────────────────────────────────────────┘
┌─ Hero 卡片 ────────────────────────────────────────────────┐
│  股票名称  代码  [信号badge]  现价  涨跌幅  量化评分(圆形)    │
│  板块 · PE · ROE · 市值                                    │
└───────────────────────────────────────────────────────────┘
┌─ K线图 (ECharts, 120天) ──────────────────────────────────┐
└───────────────────────────────────────────────────────────┘
┌─ 三列: 看多逻辑 | 风险提示 | 关键催化 ────────────────────┐
└───────────────────────────────────────────────────────────┘
┌─ 操作建议 ──────────┐  ┌─ 技术指标 ────────────────────────┐
│ 信号 / 入场 / 止损   │  │ 趋势 动量 RSI KDJ ADX 资金流 等   │
│ / 目标价             │  │ 彩色进度条展示评分                 │
└─────────────────────┘  └──────────────────────────────────┘
┌─ 财务速览 (条件显示) ─────────────────────────────────────┐
│  营收/净利润/ROE/EPS 卡片，YoY箭头                         │
└───────────────────────────────────────────────────────────┘
┌─ 相关新闻 (近7天) ─────────────────────────────────────────┐
└───────────────────────────────────────────────────────────┘
┌─ 深度分析 (折叠，按需触发) ────────────────────────────────┐
│  [启动深度分析] 按钮 → 展开多智能体输出                     │
└───────────────────────────────────────────────────────────┘
```

- [ ] **Step 1: 创建 `frontend/src/views/StockAnalysis/index.vue`**

```vue
<template>
  <div class="stock-analysis">
    <!-- 搜索栏 -->
    <div class="search-bar">
      <el-input
        v-model="symbolInput"
        placeholder="输入股票代码或名称，如 600519 / 贵州茅台"
        clearable
        size="large"
        style="max-width:360px"
        @keyup.enter="analyze"
      />
      <el-button type="primary" size="large" :loading="loading" @click="analyze">分析</el-button>
    </div>

    <template v-if="data">
      <!-- Hero -->
      <div class="hero-card" v-if="data.header">
        <div class="hero-left">
          <span class="stock-name">{{ data.header.name }}</span>
          <el-tag size="small" effect="plain" class="code-tag">{{ data.header.symbol }}</el-tag>
          <el-tag :type="signalType" size="default" effect="dark" class="signal-tag">
            {{ data.rating?.label || '-' }}
          </el-tag>
        </div>
        <div class="hero-right">
          <div class="price-block">
            <span class="price">{{ fmt(data.header.last_price) }}</span>
            <span :class="['chg', pctClass(data.header.pct_chg)]">
              {{ signedPct(data.header.pct_chg) }}
            </span>
          </div>
          <div class="score-ring">
            <svg viewBox="0 0 36 36" class="ring-svg">
              <path class="ring-bg" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
              <path class="ring-fill" :stroke-dasharray="`${data.rating?.tech_score ?? 0}, 100`"
                d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
            </svg>
            <span class="ring-label">{{ Math.round(data.rating?.tech_score ?? 0) }}</span>
          </div>
        </div>
        <div class="hero-meta" v-if="data.header.sector || data.header.pe || data.header.roe">
          <span v-if="data.header.sector">{{ data.header.sector }}</span>
          <span v-if="data.header.pe">PE {{ fmt(data.header.pe) }}</span>
          <span v-if="data.header.market_cap_yi">市值 {{ fmt(data.header.market_cap_yi) }}亿</span>
        </div>
      </div>

      <!-- K线图 -->
      <div class="card chart-card">
        <div class="card-title">价格走势（近120日）</div>
        <div ref="klineEl" class="kline-chart"></div>
      </div>

      <!-- AI投资观点 三栏 -->
      <div class="card ai-card" v-if="data.ai_view">
        <div class="ai-col bull-col">
          <div class="ai-col-title">📈 看多逻辑</div>
          <ul><li v-for="(t, i) in data.ai_view.bull" :key="i">{{ t }}</li></ul>
        </div>
        <div class="ai-col risk-col">
          <div class="ai-col-title">⚠️ 风险提示</div>
          <ul><li v-for="(t, i) in data.ai_view.risk" :key="i">{{ t }}</li></ul>
        </div>
        <div class="ai-col cat-col">
          <div class="ai-col-title">⚡ 关键催化</div>
          <ul><li v-for="(t, i) in data.ai_view.catalyst" :key="i">{{ t }}</li></ul>
        </div>
      </div>

      <!-- 操作建议 + 技术指标 -->
      <div class="two-col">
        <div class="card action-card" v-if="data.rating">
          <div class="card-title">操作建议</div>
          <div class="action-table">
            <div class="action-row"><span class="ak">交易信号</span><span class="av signal-text" :class="signalClass">{{ data.rating.label || '-' }}</span></div>
            <div class="action-row"><span class="ak">入场区间</span><span class="av">{{ data.rating.entry_low ? `${fmt(data.rating.entry_low)} – ${fmt(data.rating.entry_high)}` : '-' }}</span></div>
            <div class="action-row"><span class="ak">止损位</span><span class="av loss">{{ fmt(data.rating.stop_loss) || '-' }}</span></div>
            <div class="action-row"><span class="ak">目标价</span><span class="av gain">{{ fmt(data.rating.target) || '-' }}</span></div>
            <div class="action-row" v-if="data.rating.position_note"><span class="ak">仓位建议</span><span class="av">{{ data.rating.position_note }}</span></div>
          </div>
          <div class="core-summary" v-if="data.core_summary">{{ data.core_summary }}</div>
        </div>

        <div class="card factor-card">
          <div class="card-title">技术因子</div>
          <div class="factor-grid">
            <div class="factor-item" v-for="f in factorItems" :key="f.key">
              <div class="fi-label">{{ f.label }}</div>
              <div class="fi-bar">
                <div class="fi-fill" :style="{ width: f.pct + '%', background: f.color }"></div>
              </div>
              <div class="fi-val">{{ Math.round(f.val) }}</div>
            </div>
          </div>
        </div>
      </div>

      <!-- 财务速览 -->
      <div class="card fin-card" v-if="data.financial_summary?.available">
        <div class="card-title">财务速览</div>
        <div class="fin-grid">
          <div class="fin-item" v-for="row in data.financial_summary.rows.filter(r => r.value != null)" :key="row.name">
            <div class="fi-name">{{ row.name }}</div>
            <div class="fi-value">{{ fmtFin(row.value) }}</div>
            <div class="fi-yoy" v-if="row.yoy != null" :class="row.yoy >= 0 ? 'up' : 'down'">
              {{ row.yoy >= 0 ? '↑' : '↓' }}{{ Math.abs(row.yoy).toFixed(1) }}%
            </div>
          </div>
        </div>
      </div>

      <!-- 市场表现 -->
      <div class="card perf-card" v-if="data.market_performance">
        <div class="card-title">市场表现</div>
        <div class="perf-grid">
          <div class="perf-item" v-for="(label, key) in perfLabels" :key="key">
            <div class="perf-label">{{ label }}</div>
            <div class="perf-val" :class="pctClass(data.market_performance[key])">
              {{ signedPct(data.market_performance[key]) }}
            </div>
          </div>
        </div>
      </div>

      <!-- 相关新闻 -->
      <div class="card news-card" v-if="data.news?.length">
        <div class="card-title">相关新闻（近7天）</div>
        <div class="news-list">
          <a class="news-item" v-for="n in data.news" :key="n.url || n.title" :href="n.url" target="_blank">
            <span class="news-title">{{ n.title }}</span>
            <span class="news-time">{{ n.published }}</span>
          </a>
        </div>
      </div>

      <!-- 深度分析（折叠） -->
      <div class="card deep-card">
        <div class="card-title">
          深度分析
          <el-tag size="small" type="info" effect="plain">多智能体 · 约30秒</el-tag>
        </div>
        <div v-if="!deepStarted">
          <p class="deep-hint">点击下方按钮启动多智能体深度分析（行业/估值/情景），生成结构化研究结论。</p>
          <el-button type="primary" plain :loading="deepLoading" @click="startDeep">启动深度分析</el-button>
        </div>
        <div v-else-if="deepLoading" class="deep-loading">
          <el-icon class="spin"><Loading /></el-icon> 多智能体分析中，请稍候…
        </div>
        <div v-else-if="deepResult" class="deep-result">
          <div v-for="(section, i) in deepSections" :key="i" class="deep-section">
            <div class="ds-title">{{ section.title }}</div>
            <div class="ds-body">{{ section.body }}</div>
          </div>
        </div>
        <el-alert v-else-if="deepError" :title="deepError" type="error" :closable="false" />
      </div>
    </template>

    <el-empty v-else-if="!loading" description="输入股票代码开始分析" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, onUnmounted } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import { ApiClient } from '@/api/request'

const symbolInput = ref('600519')
const loading = ref(false)
const data = ref<any>(null)
const klineEl = ref<HTMLDivElement>()
let klineChart: echarts.ECharts | null = null

const deepStarted = ref(false)
const deepLoading = ref(false)
const deepResult = ref<any>(null)
const deepError = ref('')

const perfLabels: Record<string, string> = { d1: '1日', d5: '5日', m1: '1月', m3: '3月', ytd: '年初至今', y1: '1年' }

const signalType = computed(() => {
  const s = data.value?.rating?.signal
  return s === 'strong_buy' ? 'danger' : s === 'buy' ? 'warning' : s === 'sell' ? 'info' : ''
})
const signalClass = computed(() => {
  const s = data.value?.rating?.signal
  return s?.includes('buy') ? 'sig-buy' : s?.includes('sell') ? 'sig-sell' : ''
})

const factorItems = computed(() => {
  const factors = data.value?.rating?.factors || {}
  const map: Record<string, string> = {
    trend: '趋势', momentum: '动量', rsi: 'RSI', risk_control: '风控',
    liquidity: '流动性', macd: 'MACD', bollinger: '布林', capital_flow: '资金流',
  }
  return Object.entries(map)
    .filter(([k]) => factors[k] != null)
    .map(([k, label]) => {
      const val = Number(factors[k])
      const color = val >= 70 ? '#ef232a' : val >= 45 ? '#e6a23c' : '#14b143'
      return { key: k, label, val, pct: Math.min(100, val), color }
    })
})

const deepSections = computed(() => {
  if (!deepResult.value) return []
  const TITLES: Record<string, string> = {
    overall_conclusion: '综合结论', operation_advice: '操作建议',
    technical_analysis: '技术面分析', industry_analysis: '行业分析',
    valuation_analysis: '估值分析', risk_assessment: '风险评估',
    tracking_plan: '跟踪计划',
  }
  return Object.entries(TITLES)
    .filter(([k]) => deepResult.value[k])
    .map(([k, title]) => ({ title, body: deepResult.value[k] }))
})

const fmt = (v?: number | null) => v == null ? '-' : v >= 1e8 ? `${(v / 1e8).toFixed(2)}亿` : v.toFixed(2)
const fmtFin = (v?: number | null) => {
  if (v == null) return '-'
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(2)}万`
  return v.toFixed(2)
}
const signedPct = (v?: number | null) => v == null ? '-' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
const pctClass = (v?: number | null) => v == null ? '' : v >= 0 ? 'up' : 'down'

const renderKline = () => {
  if (!klineEl.value || !data.value?.kline) return
  if (!klineChart) klineChart = echarts.init(klineEl.value)
  const k = data.value.kline
  klineChart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    grid: { left: 56, right: 16, top: 16, bottom: 28 },
    xAxis: { type: 'category', data: k.dates, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', scale: true },
    series: [{
      type: 'candlestick',
      data: k.dates.map((_: string, i: number) => [k.open[i], k.close[i], k.low[i], k.high[i]]),
      itemStyle: { color: '#ef232a', color0: '#14b143', borderColor: '#ef232a', borderColor0: '#14b143' },
    }],
  })
}

const analyze = async () => {
  const sym = symbolInput.value.trim()
  if (!sym) return
  loading.value = true
  deepStarted.value = false; deepResult.value = null; deepError.value = ''
  try {
    const res: any = await ApiClient.get(`/api/quant/stock-analysis/${sym}`)
    data.value = res?.data || null
    if (!data.value?.available) {
      ElMessage.warning('未找到该股票数据，请检查代码')
      return
    }
    await nextTick()
    renderKline()
  } catch (e: any) {
    ElMessage.error(e?.message || '分析失败')
  } finally {
    loading.value = false
  }
}

let deepPollTimer: ReturnType<typeof setTimeout> | null = null

const startDeep = async () => {
  deepStarted.value = true
  deepLoading.value = true
  deepError.value = ''
  const sym = symbolInput.value.trim()
  try {
    const res: any = await ApiClient.post('/api/analysis/single', {
      symbol: sym, depth: 3, use_llm: true,
    })
    const taskId = res?.data?.task_id
    if (!taskId) throw new Error('未获取到任务ID')
    pollDeep(taskId)
  } catch (e: any) {
    deepLoading.value = false
    deepError.value = e?.message || '启动深度分析失败'
  }
}

const pollDeep = (taskId: string) => {
  deepPollTimer = setTimeout(async () => {
    try {
      const res: any = await ApiClient.get(`/api/analysis/tasks/${taskId}/status`)
      const status = res?.data?.status
      if (status === 'completed') {
        const r: any = await ApiClient.get(`/api/analysis/tasks/${taskId}/result`)
        deepResult.value = r?.data?.deep_analysis || r?.data || null
        deepLoading.value = false
      } else if (status === 'failed') {
        deepError.value = res?.data?.error || '深度分析失败'
        deepLoading.value = false
      } else {
        pollDeep(taskId)
      }
    } catch {
      pollDeep(taskId)
    }
  }, 3000)
}

onUnmounted(() => {
  if (deepPollTimer) clearTimeout(deepPollTimer)
  klineChart?.dispose()
})
</script>

<style scoped lang="scss">
.stock-analysis { display: flex; flex-direction: column; gap: 16px; }

.search-bar { display: flex; gap: 10px; align-items: center; }

.card { background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: 8px; padding: 16px; }
.card-title { font-size: 14px; font-weight: 600; color: var(--el-text-color-secondary); margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }

/* Hero */
.hero-card { background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: 8px; padding: 16px 20px; display: flex; flex-wrap: wrap; align-items: center; gap: 16px; }
.hero-left { display: flex; align-items: center; gap: 10px; flex: 1; }
.stock-name { font-size: 22px; font-weight: 700; }
.code-tag { font-size: 12px; }
.signal-tag { font-size: 13px; padding: 4px 10px; }
.hero-right { display: flex; align-items: center; gap: 16px; }
.price { font-size: 24px; font-weight: 700; }
.chg { font-size: 16px; font-weight: 600; margin-left: 6px; }
.hero-meta { width: 100%; display: flex; gap: 16px; font-size: 13px; color: var(--el-text-color-secondary); margin-top: 4px; }

/* Score ring */
.score-ring { position: relative; width: 52px; height: 52px; }
.ring-svg { transform: rotate(-90deg); }
.ring-bg { fill: none; stroke: var(--el-border-color-light); stroke-width: 3; }
.ring-fill { fill: none; stroke: #ef232a; stroke-width: 3; stroke-linecap: round; transition: stroke-dasharray .4s; }
.ring-label { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-size: 14px; font-weight: 700; }

/* Kline */
.chart-card .kline-chart { height: 260px; }

/* AI view */
.ai-card { display: flex; gap: 0; padding: 0; overflow: hidden; }
.ai-col { flex: 1; padding: 14px 16px; }
.ai-col + .ai-col { border-left: 1px solid var(--el-border-color-lighter); }
.ai-col-title { font-size: 13px; font-weight: 600; margin-bottom: 8px; }
.bull-col .ai-col-title { color: #ef232a; }
.risk-col .ai-col-title { color: #e6a23c; }
.cat-col .ai-col-title { color: #409eff; }
.ai-col ul { margin: 0; padding-left: 16px; font-size: 13px; line-height: 1.7; }

/* Two-col */
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }

/* Action */
.action-table { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; }
.action-row { display: flex; justify-content: space-between; font-size: 14px; }
.ak { color: var(--el-text-color-secondary); }
.av { font-weight: 600; }
.loss { color: #14b143; }
.gain { color: #ef232a; }
.sig-buy { color: #ef232a; }
.sig-sell { color: #14b143; }
.core-summary { font-size: 13px; line-height: 1.7; color: var(--el-text-color-secondary); border-top: 1px solid var(--el-border-color-lighter); padding-top: 10px; }

/* Factors */
.factor-grid { display: flex; flex-direction: column; gap: 6px; }
.factor-item { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.fi-label { width: 52px; color: var(--el-text-color-secondary); flex-shrink: 0; }
.fi-bar { flex: 1; height: 6px; background: var(--el-fill-color); border-radius: 3px; overflow: hidden; }
.fi-fill { height: 100%; border-radius: 3px; transition: width .3s; }
.fi-val { width: 28px; text-align: right; font-weight: 600; }

/* Financial */
.fin-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; }
.fin-item { background: var(--el-fill-color-lighter); border-radius: 6px; padding: 10px 12px; }
.fi-name { font-size: 12px; color: var(--el-text-color-secondary); }
.fi-value { font-size: 16px; font-weight: 700; margin: 4px 0 2px; }
.fi-yoy { font-size: 12px; }

/* Perf */
.perf-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; }
.perf-item { text-align: center; background: var(--el-fill-color-lighter); border-radius: 6px; padding: 8px 4px; }
.perf-label { font-size: 11px; color: var(--el-text-color-secondary); margin-bottom: 4px; }
.perf-val { font-size: 14px; font-weight: 600; }

/* News */
.news-list { display: flex; flex-direction: column; gap: 8px; }
.news-item { display: flex; justify-content: space-between; align-items: baseline; text-decoration: none; color: inherit; padding: 6px 0; border-bottom: 1px solid var(--el-border-color-lighter); font-size: 13px; }
.news-item:last-child { border-bottom: none; }
.news-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #409eff; }
.news-time { flex-shrink: 0; font-size: 11px; color: var(--el-text-color-secondary); margin-left: 12px; }

/* Deep */
.deep-hint { font-size: 13px; color: var(--el-text-color-secondary); margin-bottom: 12px; }
.deep-loading { display: flex; align-items: center; gap: 8px; color: var(--el-text-color-secondary); padding: 16px 0; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.deep-section { margin-bottom: 16px; }
.ds-title { font-size: 14px; font-weight: 600; margin-bottom: 6px; color: var(--el-text-color-primary); }
.ds-body { font-size: 13px; line-height: 1.8; color: var(--el-text-color-regular); white-space: pre-wrap; }

/* 涨跌色 */
.up { color: #ef232a; }
.down { color: #14b143; }
</style>
```

- [ ] **Step 2: Commit（先不接路由，让这一步独立）**

```bash
git add frontend/src/views/StockAnalysis/index.vue
git commit -m "feat(ui): new StockAnalysis page — hero/kline/factors/finance/news/deep-analysis"
```

---

## Task 5: 接路由 + 更新侧边栏 + 删除旧页面

**Files:**
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/components/Layout/AppLayout.vue`
- Delete: `frontend/src/views/StockReport/Report.vue`（可保留文件但路由不再指向）
- Delete: `frontend/src/views/Analysis/SingleAnalysis.vue`（同上）

- [ ] **Step 1: 更新 router**

打开 `frontend/src/router/index.ts`，找到：

```ts
{ path: 'stock-report', name: 'stock-report', component: () => import('@/views/StockReport/Report.vue') },
// ... 其他
{ path: 'analysis/single', name: 'single-analysis', component: () => import('@/views/Analysis/SingleAnalysis.vue') },
```

替换为（删除这两行，新增一行）：

```ts
{ path: 'stock-analysis', name: 'stock-analysis', component: () => import('@/views/StockAnalysis/index.vue') },
```

同时把默认重定向从 `'/market/sentiment'` 保持不变（无需改）。

- [ ] **Step 2: 更新侧边栏**

打开 `frontend/src/components/Layout/AppLayout.vue`，找到：

```html
<el-menu-item index="/stock-report">
  <el-icon><Tickets /></el-icon><span>个股研报</span>
</el-menu-item>
<!-- ... -->
<el-menu-item index="/analysis/single">
  <el-icon><Search /></el-icon><span>单股深研</span>
</el-menu-item>
```

**删除**上面两个 `el-menu-item`，在同一位置插入一个：

```html
<el-menu-item index="/stock-analysis">
  <el-icon><DocumentChecked /></el-icon><span>个股分析</span>
</el-menu-item>
```

并在 `<script setup>` 的 icon import 里补充 `DocumentChecked`（删除不再使用的 `Tickets`，保留 `Search` 如其他地方还用）：

```ts
import {
  Odometer, TrendCharts, Histogram, DataLine, Search, Star, Wallet, SwitchButton,
  DocumentChecked,
} from '@element-plus/icons-vue'
```

- [ ] **Step 3: 验证页面在浏览器里可访问**

确保 dev server 仍在运行，打开 `http://localhost:5173/stock-analysis`，应看到搜索栏 + 空状态提示"输入股票代码开始分析"。

- [ ] **Step 4: 端到端功能验证**

1. 输入 `600519`，点「分析」
2. 等待约 3-8 秒，应看到：Hero（贵州茅台 · 持有/买入 · 当前价 · 评分圆环）
3. K线图渲染
4. 三栏 AI投资观点（看多/风险/催化各有内容）
5. 操作建议（有止损/目标价）
6. 财务速览（有营收/ROE 数据，非"暂无"）
7. 相关新闻（有 1+ 条）
8. 点「启动深度分析」，3-30 秒后出现深度分析展开区

- [ ] **Step 5: Commit + Push**

```bash
git add frontend/src/router/index.ts frontend/src/components/Layout/AppLayout.vue
git commit -m "feat(nav): replace 个股研报+个股深研 with unified 个股分析 page"
git push
```

---

## Task 6: report_service.py 补全 kline 数据 + rating.factors

**Files:**
- Modify: `quantcore/quant/report_service.py`

**背景：** `StockAnalysis.vue` 期望 `data.kline`（含 `dates/open/high/low/close`）和 `data.rating.factors`（因子评分字典）。现有 `build_stock_report()` 已有这些字段，但需要确认格式匹配。

- [ ] **Step 1: 检查 build_stock_report 返回值是否包含 kline 和 factors**

```bash
python -c "
from quantcore.quant.report_service import build_stock_report
r = build_stock_report('600519')
print('keys:', list(r.keys()))
print('rating keys:', list(r.get('rating', {}).keys()))
print('kline type:', type(r.get('kline')))
"
```

- [ ] **Step 2a: 如果 kline 缺失 — 在 build_stock_report 中补充**

找到 `build_stock_report()` 的 return dict，在末尾加：

```python
# kline 供前端图表用（最近120日）
kline_payload = {}
if has_data:
    df120 = data.tail(120)
    kline_payload = {
        "dates": df120.index.strftime("%Y-%m-%d").tolist() if hasattr(df120.index, "strftime") else df120["date"].astype(str).tolist(),
        "open": df120["open"].round(2).tolist(),
        "high": df120["high"].round(2).tolist(),
        "low": df120["low"].round(2).tolist(),
        "close": df120["close"].round(2).tolist(),
    }
# 在 return dict 里加：
"kline": kline_payload,
```

- [ ] **Step 2b: 如果 rating.factors 缺失 — 在 rating 字典中补充**

找到 return dict 里的 `"rating"` 字典，确保包含 `"factors": factors`（factors 是 compute_factor_scores 的结果）：

```python
"rating": {
    "signal": signal,
    "label": _SIGNAL_LABEL.get(signal, signal),
    "tech_score": tech_score,
    "factors": factors,          # ← 确保这行存在
    ...
},
```

- [ ] **Step 3: Commit**

```bash
git add quantcore/quant/report_service.py
git commit -m "fix(report): ensure kline and factors fields in build_stock_report response"
```

---

## Self-Review

**Spec coverage check:**
- ✅ 涨停热点概念分类 → Task 2（AKShare concept_lookup）
- ✅ 个股研报财务暂无 → Task 1（fundamentals bug）+ Task 6（kline/factors）
- ✅ 新闻暂无 → Task 1 Step 3 验证（已有实现，确认可用）
- ✅ 合并两页面 → Task 3（后端端点）+ Task 4（前端 Vue）+ Task 5（路由/导航）
- ✅ 令人眼前一亮 → Task 4 的 Hero/三栏AI观点/因子进度条/折叠深度分析设计

**Placeholder scan:** 无 TBD/TODO。

**Type consistency:** `data.rating.factors`（Task 4 用）对应 Task 6 中 `build_stock_report` 的 `factors` 字段；`data.kline.dates/open/high/low/close` 对应 Task 6 的 kline_payload 结构。`data.financial_summary.rows[].{name, value, yoy}` 来自现有 `_financial_summary()`，与 Task 1 修复后一致。
