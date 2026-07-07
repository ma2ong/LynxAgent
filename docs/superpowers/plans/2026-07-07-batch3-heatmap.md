# 批次 3：行业热力图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新页 `/heatmap`：ECharts treemap 行业热力图（面积=总市值/成交额兜底，颜色=当日涨跌幅，A股红涨绿跌），点行业下钻个股，点个股跳个股深研。纯本地数据 + 实时快照，零 LLM。

**Architecture:** 聚合逻辑放 `quantcore/quant/heatmap.py` 纯函数（可测），输入=实时快照 dict + 代码→行业映射 dict。端点放 `app/lite_main.py`（紧邻 macro-bar，复用 `_load_realtime_quotes_snapshot` / `_load_industry_map` / `_cache_get` 基建）。快照不可用时用本地日线兜底（新 store 方法 `latest_daily_stats`）。

**Tech Stack:** FastAPI、SQLite（daily_kline 45 天窗口 SQL）、ECharts treemap（echarts/core 按需注册 TreemapChart）、Vue3。

**调研结论（写计划时已验证）:**
- `stock_meta.industry` 全空（5525 只），**不可用**；行业映射用 lite_main 现成的 `_load_industry_map()`（东财 f100，磁盘缓存 `runtime/industry_map.json` 已有 5098 只，24h TTL）。
- 三条快照路径（腾讯/东财/akshare）都带 `pct_chg`、`amount`、`total_mv`；**市值单位不一**：腾讯是亿、东财/akshare 是元，用现有启发（>1e6 判定为元 ÷1e8，见 lite_main:6333）归一。
- 深研页支持 `/stock-analysis?symbol=600000`。
- `/api/lite/macro-bar` 无鉴权、60s `_cache_get/_cache_set` 缓存 + 失败短缓存，热力图端点照抄该模式。

**与 spec 的既定偏离（写计划时决定）:** spec 写的是 `GET /api/quant/heatmap`，实际用 `GET /api/lite/heatmap`——端点必须放 lite_main（快照/行业映射/缓存基建都在那里，routers/quant 反向 import 会循环），路径跟随 macro-bar 惯例。

**实施偏离记录（执行后回写）:**
（暂无）

**约定:** 测试 `python -m pytest`（仓库根）；后端改动需重启（无 --reload）；A股红涨绿跌；commit message 英文 + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`；每 Task 一个 commit。

---

### Task 1: LocalQuantStore.latest_daily_stats（日线兜底数据）

**Files:**
- Modify: `quantcore/quant/local_store.py`（`recent_returns` 方法之后）
- Test: `tests/test_heatmap.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_heatmap.py`：

```python
"""行业热力图（latest_daily_stats 兜底 + heatmap 聚合）回归测试。"""
from datetime import date, timedelta

import pandas as pd
import pytest

from quantcore.quant.local_store import LocalQuantStore


@pytest.fixture()
def store(tmp_path):
    return LocalQuantStore(str(tmp_path / "test.sqlite"))


def _kline(days_ago_closes):
    """{天数前: (close, amount)} -> DataFrame"""
    rows = []
    for days_ago, (close, amount) in days_ago_closes.items():
        d = (date.today() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        rows.append({"date": d, "open": close, "high": close, "low": close,
                     "close": close, "volume": 1000, "amount": amount})
    return pd.DataFrame(rows)


def test_latest_daily_stats_pct_and_amount(store):
    store.upsert_kline("600001", _kline({1: (11.0, 5e8), 2: (10.0, 4e8)}))
    stats = store.latest_daily_stats()
    assert stats["600001"]["pct"] == 10.0
    assert stats["600001"]["amount"] == 5e8


def test_latest_daily_stats_skips_placeholder_bars(store):
    # amount=0 的占位 bar 不参与（同 recent_returns 约定）
    store.upsert_kline("600002", _kline({1: (12.0, 0), 2: (11.0, 3e8), 3: (10.0, 3e8)}))
    stats = store.latest_daily_stats()
    assert stats["600002"]["pct"] == 10.0  # 11 vs 10，跳过 amount=0 的 12

def test_latest_daily_stats_needs_two_bars(store):
    store.upsert_kline("600003", _kline({1: (10.0, 1e8)}))
    assert "600003" not in store.latest_daily_stats()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_heatmap.py -v`
Expected: 3 FAIL（`AttributeError: ... 'latest_daily_stats'`）

- [ ] **Step 3: 实现**

`quantcore/quant/local_store.py`，`recent_returns` 方法结尾之后插入：

```python
    def latest_daily_stats(self) -> Dict[str, Dict[str, float]]:
        """每只股票最新真实 bar 的当日涨跌幅%/成交额/收盘价（快照不可用时热力图兜底）。

        与 recent_returns 同款 45 天窗口 + amount>0 过滤；涨跌幅 = 最新 bar vs 前一根。
        """
        from datetime import date as _date, timedelta as _td
        cutoff = (_date.today() - _td(days=45)).strftime("%Y-%m-%d")
        sql = """
        WITH ranked AS (
            SELECT symbol, close, amount,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
            FROM daily_kline
            WHERE amount > 0 AND date >= ?
        )
        SELECT cur.symbol, cur.close, cur.amount, prev.close
        FROM ranked cur
        JOIN ranked prev ON prev.symbol = cur.symbol AND prev.rn = 2
        WHERE cur.rn = 1
        """
        out: Dict[str, Dict[str, float]] = {}
        for symbol, close, amount, prev_close in self._conn().execute(sql, (cutoff,)).fetchall():
            c = _f(close)
            p = _f(prev_close)
            if p > 0:
                out[symbol] = {"pct": round((c - p) / p * 100, 2), "amount": _f(amount), "close": c}
        return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_heatmap.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add quantcore/quant/local_store.py tests/test_heatmap.py
git commit -m "feat(heatmap): latest_daily_stats fallback stats on LocalQuantStore"
```

---

### Task 2: 聚合模块 quantcore/quant/heatmap.py

**Files:**
- Create: `quantcore/quant/heatmap.py`
- Test: `tests/test_heatmap.py`（追加）

- [ ] **Step 1: 追加失败测试**

`tests/test_heatmap.py` 末尾追加：

```python
from quantcore.quant.heatmap import build_heatmap_industry, build_heatmap_stocks

SNAP = {
    # 腾讯口径：total_mv 单位亿
    "600001": {"name": "甲", "pct_chg": 5.0, "amount": 8e8, "total_mv": 200.0},
    "600002": {"name": "乙", "pct_chg": -1.0, "amount": 4e8, "total_mv": 100.0},
    # 东财口径：total_mv 单位元（>1e6 判定）
    "600003": {"name": "丙", "pct_chg": 2.0, "amount": 2e8, "total_mv": 5e10},
    # 无市值：用成交额（亿）兜底做面积
    "600004": {"name": "丁", "pct_chg": 0.5, "amount": 3e8, "total_mv": None},
    # 无涨跌幅：跳过
    "600005": {"name": "戊", "pct_chg": None, "amount": 1e8, "total_mv": 50.0},
}
IND = {"600001": "白酒", "600002": "白酒", "600003": "银行"}  # 600004 未映射 -> 其他


def test_build_heatmap_industry_aggregates_and_weights():
    items = build_heatmap_industry(SNAP, IND)
    by_name = {i["name"]: i for i in items}
    assert set(by_name) == {"白酒", "银行", "其他"}
    baijiu = by_name["白酒"]
    assert baijiu["count"] == 2
    assert baijiu["value"] == 300.0  # 200 + 100 亿
    assert baijiu["pct"] == 3.0      # (5*200 + -1*100) / 300 市值加权
    assert by_name["银行"]["value"] == 500.0   # 5e10 元 -> 500 亿
    assert by_name["其他"]["value"] == 3.0     # 3e8 成交额 -> 3 亿兜底
    # 面积降序
    assert [i["name"] for i in items] == ["银行", "白酒", "其他"]


def test_build_heatmap_stocks_filters_by_industry():
    items = build_heatmap_stocks(SNAP, IND, "白酒")
    assert [i["symbol"] for i in items] == ["600001", "600002"]  # 按面积降序
    assert items[0]["pct"] == 5.0 and items[0]["value"] == 200.0
    assert build_heatmap_stocks(SNAP, IND, "其他")[0]["symbol"] == "600004"
    assert build_heatmap_stocks(SNAP, IND, "不存在") == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_heatmap.py -v`
Expected: 新增 2 个 FAIL（ModuleNotFoundError heatmap），原 3 个 PASS

- [ ] **Step 3: 实现**

新建 `quantcore/quant/heatmap.py`：

```python
"""行业/个股热力图聚合（纯本地计算，零 LLM）。

输入 = 实时快照（或日线兜底伪快照）+ 代码->行业映射，输出 ECharts treemap 友好的
行业块/个股块列表。面积 = 总市值（亿，缺市值用成交额亿兜底），颜色 = 当日涨跌幅。
市值单位不一：腾讯快照是亿、东财/akshare 是元，>1e6 判定为元并 ÷1e8 归一。
"""
from __future__ import annotations

from typing import Dict, List, Optional

_UNMAPPED = "其他"


def _num(value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v


def _mv_yi(value) -> float:
    v = _num(value)
    if v <= 0:
        return 0.0
    return v / 1e8 if v > 1_000_000 else v


def _stock_item(symbol: str, q: Dict) -> Optional[Dict]:
    pct = q.get("pct_chg", q.get("change_percent"))
    if pct is None:
        return None
    mv = _mv_yi(q.get("total_mv"))
    amount_yi = max(0.0, _num(q.get("amount"))) / 1e8
    value = mv if mv > 0 else amount_yi
    if value <= 0:
        return None
    return {"symbol": symbol, "name": str(q.get("name") or symbol),
            "pct": round(float(pct), 2), "value": round(value, 2),
            "mv_yi": round(mv, 2), "amount_yi": round(amount_yi, 2)}


def build_heatmap_industry(snapshot: Dict[str, Dict], industry_map: Dict[str, str]) -> List[Dict]:
    """行业块列表：面积=行业总市值（亿），颜色=市值加权当日涨跌幅，按面积降序。"""
    groups: Dict[str, List[Dict]] = {}
    for symbol, q in snapshot.items():
        item = _stock_item(symbol, q)
        if item is None:
            continue
        groups.setdefault(industry_map.get(symbol) or _UNMAPPED, []).append(item)
    out: List[Dict] = []
    for name, items in groups.items():
        total = sum(i["value"] for i in items)
        if total <= 0:
            continue
        pct = sum(i["pct"] * i["value"] for i in items) / total
        out.append({"name": name, "count": len(items),
                    "value": round(total, 2), "pct": round(pct, 2),
                    "amount_yi": round(sum(i["amount_yi"] for i in items), 2)})
    out.sort(key=lambda x: x["value"], reverse=True)
    return out


def build_heatmap_stocks(snapshot: Dict[str, Dict], industry_map: Dict[str, str], industry: str) -> List[Dict]:
    """指定行业的个股块列表，按面积降序。"""
    out = [item for symbol, q in snapshot.items()
           if (industry_map.get(symbol) or _UNMAPPED) == industry
           and (item := _stock_item(symbol, q)) is not None]
    out.sort(key=lambda x: x["value"], reverse=True)
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_heatmap.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add quantcore/quant/heatmap.py tests/test_heatmap.py
git commit -m "feat(heatmap): industry/stock treemap aggregation module"
```

---

### Task 3: API 端点 GET /api/lite/heatmap

**Files:**
- Modify: `app/lite_main.py`（`lite_macro_bar` 端点之后插入）

- [ ] **Step 1: 实现端点**

`app/lite_main.py`，`lite_macro_bar` 函数体结束后（`@app.get("/api/system/config/validate")` 之前）插入：

```python
@app.get("/api/lite/heatmap")
async def lite_heatmap(level: str = "industry", industry: str = ""):
    """行业/个股热力图：面积=总市值（亿，成交额兜底），颜色=当日涨跌幅。60s 缓存。

    快照不可用（收盘后/断网）时退回本地日线最新 bar（同收盘快照教训：不读未同步的当日）。
    """
    from quantcore.quant.heatmap import build_heatmap_industry, build_heatmap_stocks

    if level not in ("industry", "stock"):
        raise HTTPException(status_code=400, detail="level 必须是 industry/stock")
    if level == "stock" and not industry.strip():
        raise HTTPException(status_code=400, detail="level=stock 需要 industry 参数")
    cache_key = f"heatmap:{level}:{industry}"
    cached = _cache_get(cache_key, 60)
    if cached:
        return cached

    industry_map = await _run_data_task(_load_industry_map, timeout=15.0)
    snapshot: dict[str, dict[str, Any]] = {}
    source = "realtime"
    try:
        snapshot = await _run_data_task(_load_realtime_quotes_snapshot, 60, timeout=8.0) or {}
    except Exception:
        snapshot = {}
    if not snapshot:
        # 日线兜底：伪快照（无市值 -> 面积用成交额）
        source = "daily-kline"
        from quantcore.quant.local_store import get_local_store

        def _fallback() -> dict[str, dict[str, Any]]:
            store = get_local_store()
            names = {str(m.get("symbol")): str(m.get("name") or "") for m in store.load_meta()}
            return {sym: {"name": names.get(sym) or sym, "pct_chg": st["pct"], "amount": st["amount"]}
                    for sym, st in store.latest_daily_stats().items()}

        try:
            snapshot = await _run_data_task(_fallback, timeout=20.0)
        except Exception:
            snapshot = {}

    if level == "industry":
        items = build_heatmap_industry(snapshot, industry_map)
    else:
        items = build_heatmap_stocks(snapshot, industry_map, industry.strip())
    payload = {
        "success": True,
        "data": {"level": level, "industry": industry.strip() or None, "items": items,
                 "source": source, "mapped": len(industry_map),
                 "updated_at": datetime.now().astimezone().strftime("%Y/%m/%d %H:%M:%S")},
    }
    if items:
        _cache_set(cache_key, payload)
    return payload
```

- [ ] **Step 2: 验证**

`python -c "import app.lite_main"` 无错误。重启后端（杀 8001 上的 python，`.\scripts\start_lite.ps1 -NoOpen -NoFrontend`），然后：

```powershell
Invoke-RestMethod "http://127.0.0.1:8001/api/lite/heatmap?level=industry" | ConvertTo-Json -Depth 3
Invoke-RestMethod "http://127.0.0.1:8001/api/lite/heatmap?level=stock&industry=银行"
```

Expected: industry 返回几十个行业块（value 降序、pct 合理）；stock 返回该行业成分；`level=bogus` → 400；`level=stock` 无 industry → 400。二次调用应命中缓存（毫秒级）。

- [ ] **Step 3: 全量回归**

Run: `python -m pytest tests/ -q`
Expected: 68 passed（63+5）

- [ ] **Step 4: Commit**

```bash
git add app/lite_main.py
git commit -m "feat(heatmap): /api/lite/heatmap endpoint with realtime snapshot and kline fallback"
```

---

### Task 4: 前端 /heatmap 页面（ECharts treemap + 下钻）

**Files:**
- Modify: `frontend/src/utils/echarts.ts`（注册 TreemapChart）
- Modify: `frontend/src/api/quant.ts`（末尾追加 heatmapApi）
- Create: `frontend/src/views/Heatmap/Index.vue`
- Modify: `frontend/src/router/index.ts`（`limit-up` 路由行后加 `/heatmap`）
- Modify: `frontend/src/components/Layout/AppLayout.vue`（涨停热点菜单项后加入口）

- [ ] **Step 1: echarts.ts 注册 treemap**

`import { BarChart, CandlestickChart, GaugeChart, LineChart } from 'echarts/charts'` 改为：

```typescript
import { BarChart, CandlestickChart, GaugeChart, LineChart, TreemapChart } from 'echarts/charts'
```

`echarts.use([` 数组里 `LineChart,` 后加一行 `TreemapChart,`。

- [ ] **Step 2: quant.ts 末尾追加**

```typescript
export interface HeatmapItem {
  name: string
  pct: number
  value: number
  amount_yi?: number
  count?: number
  symbol?: string
  mv_yi?: number
}

export interface HeatmapData {
  level: 'industry' | 'stock'
  industry: string | null
  items: HeatmapItem[]
  source: string
  updated_at: string
}

export const heatmapApi = {
  fetch: async (level: 'industry' | 'stock', industry?: string) => {
    const raw = await ApiClient.get<any>('/api/lite/heatmap', { level, industry: industry || '' }, { timeout: 30000 })
    return (raw as any)?.data as HeatmapData | null
  },
}
```

- [ ] **Step 3: 新建 `frontend/src/views/Heatmap/Index.vue`**

```vue
<template>
  <div class="heatmap-page">
    <div class="page-head">
      <div>
        <h2>行业热力图</h2>
        <p class="sub">
          面积=总市值 · 颜色=当日涨跌幅（红涨绿跌）
          <template v-if="data"> · {{ data.source === 'realtime' ? '实时行情' : '本地日线' }} · {{ data.updated_at }}</template>
        </p>
      </div>
      <div class="actions">
        <el-button v-if="currentIndustry" size="small" @click="backToIndustry">← 全部行业</el-button>
        <el-button size="small" :loading="loading" @click="load(currentIndustry)">刷新</el-button>
      </div>
    </div>
    <div v-loading="loading" class="chart-wrap">
      <div ref="chartEl" class="chart" />
      <el-empty v-if="!loading && !items.length" description="暂无数据：请先在数据中心同步行情" />
    </div>
    <p class="hint">{{ currentIndustry ? '点击个股跳转个股深研' : '点击行业下钻查看成分股' }}</p>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { echarts, type ECharts } from '@/utils/echarts'
import { heatmapApi, type HeatmapData, type HeatmapItem } from '@/api/quant'

const router = useRouter()
const chartEl = ref<HTMLDivElement>()
const loading = ref(false)
const data = ref<HeatmapData | null>(null)
const items = ref<HeatmapItem[]>([])
const currentIndustry = ref('')
let chart: ECharts | null = null

// A股红涨绿跌：±4% 封顶线性插值
const pctColor = (pct: number) => {
  const t = Math.max(-1, Math.min(1, pct / 4))
  const mix = (a: number[], b: number[], k: number) =>
    `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * k)).join(',')})`
  const flat = [58, 63, 75]
  return t >= 0 ? mix(flat, [224, 64, 44], t) : mix(flat, [30, 158, 99], -t)
}

const render = () => {
  if (!chartEl.value) return
  if (!chart) {
    chart = echarts.init(chartEl.value)
    chart.on('click', (params: any) => {
      const it = params?.data as HeatmapItem | undefined
      if (!it) return
      if (!currentIndustry.value && it.name) load(it.name)
      else if (it.symbol) router.push({ path: '/stock-analysis', query: { symbol: it.symbol } })
    })
  }
  chart.setOption({
    tooltip: {
      formatter: (p: any) => {
        const it = p?.data as HeatmapItem
        if (!it) return ''
        const head = it.symbol ? `${it.name} ${it.symbol}` : `${it.name}（${it.count} 只）`
        return `${head}<br/>涨跌幅 ${it.pct > 0 ? '+' : ''}${it.pct}%<br/>市值/面积 ${it.value} 亿`
      },
    },
    series: [{
      type: 'treemap',
      roam: false,
      nodeClick: false,
      breadcrumb: { show: false },
      width: '100%',
      height: '100%',
      label: {
        show: true,
        formatter: (p: any) => `${p.data.name}\n${p.data.pct > 0 ? '+' : ''}${p.data.pct}%`,
        fontSize: 12,
      },
      itemStyle: { borderColor: '#1a1c22', borderWidth: 1, gapWidth: 1 },
      data: items.value.map(it => ({ ...it, itemStyle: { color: pctColor(it.pct) } })),
    }],
  }, true)
}

const load = async (industry = '') => {
  loading.value = true
  try {
    const res = await heatmapApi.fetch(industry ? 'stock' : 'industry', industry || undefined)
    if (!res) return
    data.value = res
    items.value = res.items || []
    currentIndustry.value = industry
    render()
  } finally {
    loading.value = false
  }
}

const backToIndustry = () => load('')

const onResize = () => chart?.resize()

onMounted(() => {
  load()
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  chart?.dispose()
  chart = null
})
</script>

<style scoped lang="scss">
.heatmap-page { display: flex; flex-direction: column; height: 100%; }
.page-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px;
  h2 { margin: 0; font-size: 20px; }
  .sub { margin: 4px 0 0; font-size: 12px; color: var(--el-text-color-secondary); }
}
.chart-wrap { position: relative; flex: 1; min-height: 520px; }
.chart { width: 100%; height: 100%; min-height: 520px; }
.hint { margin: 8px 0 0; font-size: 12px; color: var(--el-text-color-placeholder); }
</style>
```

- [ ] **Step 4: 路由 + 侧边栏**

`frontend/src/router/index.ts`，`limit-up` 行后插入：

```typescript
      { path: 'heatmap', name: 'heatmap', component: () => import('@/views/Heatmap/Index.vue') },
```

`frontend/src/components/Layout/AppLayout.vue`，涨停热点菜单项后插入（`Histogram` 图标需并入现有 `@element-plus/icons-vue` import）：

```html
        <el-menu-item index="/heatmap"><el-icon><Histogram /></el-icon><span>行业热力</span></el-menu-item>
```

- [ ] **Step 5: 验证 + Commit**

```bash
cd frontend && npx vue-tsc --noEmit && npm run build
```

Expected: 通过。

```bash
git add frontend/src/utils/echarts.ts frontend/src/api/quant.ts frontend/src/views/Heatmap/Index.vue frontend/src/router/index.ts frontend/src/components/Layout/AppLayout.vue
git commit -m "feat(heatmap): treemap page with industry drilldown and stock deep-dive link"
```

---

### Task 5: 端到端验证 + README

- [ ] **Step 1: 全量回归**

```bash
python -m pytest tests/ -q     # 68 passed
```

- [ ] **Step 2: 实机巡检（headless Playwright）**

复用批次 2 巡检方式：API 登录（looptest / loop-test-1234）拿 token 注入 localStorage `auth-token`，访问 `http://[::1]:5173/heatmap`（vite dev 只监听 IPv6）。验证：
1. treemap 渲染出行业块（canvas 存在且 items>10）
2. 点击最大行业块 → 出现「← 全部行业」按钮且块变成个股
3. 点击个股块 → 跳转 `/stock-analysis?symbol=`
4. 截图确认颜色红涨绿跌

- [ ] **Step 3: README 功能清单**

「✨ 核心功能」中「五方判读」条目后追加：

```markdown
- **行业热力图** — 全市场行业 treemap（面积=市值、颜色=当日涨跌幅），点行业下钻成分股、点个股直达深研；实时行情断档时自动退回本地日线。
```

- [ ] **Step 4: Commit + push**

```bash
git add README.md docs/superpowers/plans/2026-07-07-batch3-heatmap.md
git commit -m "docs: add industry heatmap to feature list"
git push origin main
```
