<template>
  <div class="stock-analysis">
    <!-- 搜索栏 -->
    <div class="search-bar">
      <el-input
        v-model="symbolInput"
        placeholder="输入股票代码，如 600519"
        clearable
        size="large"
        style="max-width:320px"
        @keyup.enter="analyze"
      />
      <el-button type="primary" size="large" :loading="loading" @click="analyze">
        <el-icon><Search /></el-icon> 分析
      </el-button>
    </div>

    <div v-if="loading" class="loading-hint">正在分析，请稍候（约5-10秒）…</div>

    <template v-if="data && data.available">
      <!-- Hero 卡片 -->
      <div class="hero-card">
        <div class="hero-main">
          <span class="stock-name">{{ data.header?.name || symbolInput }}</span>
          <el-tag size="small" effect="plain" class="code-tag">{{ data.header?.symbol }}</el-tag>
          <el-tag :type="signalTagType" size="default" effect="dark" class="signal-tag">
            {{ data.rating?.label || '-' }}
          </el-tag>
        </div>
        <div class="hero-prices">
          <span class="price">{{ fmt(data.header?.last_price) }}</span>
          <span :class="['chg', pctClass(data.header?.pct_chg)]">
            {{ signedPct(data.header?.pct_chg) }}
          </span>
          <span class="score-pill">量化 {{ Math.round(data.rating?.tech_score ?? 0) }}</span>
        </div>
        <div class="hero-meta" v-if="data.header?.sector || data.header?.industry">
          <span>{{ data.header?.sector || data.header?.industry }}</span>
          <span v-if="data.header?.pe">PE {{ fmt(data.header.pe, 1) }}</span>
          <span v-if="data.header?.market_cap_yi">市值 {{ fmt(data.header.market_cap_yi) }}亿</span>
        </div>
      </div>

      <!-- K线图 -->
      <div class="card" v-if="data.kline?.dates?.length">
        <div class="card-title">价格走势（近120日）</div>
        <div ref="klineEl" class="kline-chart"></div>
      </div>

      <!-- AI投资三栏 -->
      <div class="ai-card" v-if="data.ai_view">
        <div class="ai-col bull">
          <div class="ai-col-head">📈 看多逻辑</div>
          <ul><li v-for="(t, i) in (data.ai_view.bull || [])" :key="i">{{ t }}</li></ul>
        </div>
        <div class="ai-col risk">
          <div class="ai-col-head">⚠️ 风险提示</div>
          <ul><li v-for="(t, i) in (data.ai_view.risk || [])" :key="i">{{ t }}</li></ul>
        </div>
        <div class="ai-col cat">
          <div class="ai-col-head">⚡ 关键催化</div>
          <ul><li v-for="(t, i) in (data.ai_view.catalyst || [])" :key="i">{{ t }}</li></ul>
        </div>
      </div>

      <!-- 操作建议 + 技术因子 -->
      <div class="two-col">
        <!-- 操作建议 -->
        <div class="card">
          <div class="card-title">操作建议</div>
          <div class="action-rows">
            <div class="action-row">
              <span class="ak">交易信号</span>
              <span class="av" :class="signalClass">{{ data.rating?.label || '-' }}</span>
            </div>
            <div class="action-row" v-if="data.rating?.entry_low">
              <span class="ak">入场区间</span>
              <span class="av">{{ fmt(data.rating.entry_low) }} – {{ fmt(data.rating.entry_high) }}</span>
            </div>
            <div class="action-row" v-if="data.rating?.stop_loss">
              <span class="ak">止损参考</span>
              <span class="av loss">{{ fmt(data.rating.stop_loss) }}</span>
            </div>
            <div class="action-row" v-if="data.rating?.target">
              <span class="ak">目标参考</span>
              <span class="av gain">{{ fmt(data.rating.target) }}</span>
            </div>
            <div class="action-row" v-if="data.rating?.position_note">
              <span class="ak">仓位建议</span>
              <span class="av">{{ data.rating.position_note }}</span>
            </div>
          </div>
          <div class="core-summary" v-if="data.core_summary">{{ data.core_summary }}</div>
        </div>

        <!-- 技术因子 -->
        <div class="card">
          <div class="card-title">技术因子</div>
          <div class="factor-list">
            <div v-for="f in factorItems" :key="f.key" class="factor-row">
              <span class="fl">{{ f.label }}</span>
              <div class="fbar">
                <div class="ffill" :style="{ width: f.pct + '%', background: f.color }" />
              </div>
              <span class="fval">{{ Math.round(f.val) }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 财务速览 -->
      <div class="card" v-if="data.financial_summary?.available">
        <div class="card-title">财务速览</div>
        <div class="fin-grid">
          <div v-for="row in visibleFinRows" :key="row.name" class="fin-item">
            <div class="fi-name">{{ row.name }}</div>
            <div class="fi-val">{{ fmtFin(row.value) }}</div>
            <div v-if="row.yoy != null" class="fi-yoy" :class="row.yoy >= 0 ? 'up' : 'down'">
              {{ row.yoy >= 0 ? '↑' : '↓' }}{{ Math.abs(row.yoy).toFixed(1) }}%
            </div>
          </div>
        </div>
      </div>

      <!-- 市场表现 -->
      <div class="card" v-if="data.market_performance && hasPerfData">
        <div class="card-title">市场表现</div>
        <div class="perf-grid">
          <div v-for="(label, key) in PERF_LABELS" :key="key" class="perf-item">
            <div class="pl">{{ label }}</div>
            <div class="pv" :class="pctClass(data.market_performance[key])">
              {{ signedPct(data.market_performance[key]) }}
            </div>
          </div>
        </div>
      </div>

      <!-- 相关新闻 -->
      <div class="card" v-if="data.news?.length">
        <div class="card-title">相关新闻（近7天）</div>
        <div class="news-list">
          <a v-for="n in data.news" :key="n.url || n.title" class="news-item"
             :href="n.url || '#'" target="_blank">
            <span class="nt">{{ n.title }}</span>
            <span class="nd">{{ (n.time || n.published || '').slice(0, 10) }}</span>
          </a>
        </div>
      </div>

      <!-- 深度分析（按需触发） -->
      <div class="card deep-card">
        <div class="card-title">
          深度多智能体分析
          <el-tag size="small" type="info" effect="plain">约30-60秒</el-tag>
        </div>
        <template v-if="!deepStarted">
          <p class="deep-hint">多智能体分析行业/估值/风险/跟踪计划，生成结构化研究结论。</p>
          <el-button type="primary" plain @click="startDeep">启动深度分析</el-button>
        </template>
        <div v-else-if="deepLoading" class="deep-spin">
          <el-icon class="spin"><Loading /></el-icon> 多智能体分析中，请稍候…
        </div>
        <div v-else-if="deepResult" class="deep-result">
          <div v-for="s in deepSections" :key="s.title" class="deep-section">
            <div class="ds-title">{{ s.title }}</div>
            <div class="ds-body">{{ s.body }}</div>
          </div>
        </div>
        <el-alert v-else-if="deepError" :title="deepError" type="error" :closable="false" />
      </div>
    </template>

    <el-alert
      v-else-if="data && !data.available"
      title="未找到该股票数据，请检查代码是否正确"
      type="warning"
      :closable="false"
      show-icon
    />

    <el-empty v-else-if="!loading" description="输入股票代码开始分析" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, onUnmounted } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { Loading, Search } from '@element-plus/icons-vue'
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
let deepTimer: ReturnType<typeof setTimeout> | null = null

const PERF_LABELS: Record<string, string> = {
  d1: '1日', d5: '5日', m1: '1月', m3: '3月', ytd: '年初至今', y1: '1年',
}

const FACTOR_LABELS: Record<string, string> = {
  trend: '趋势', momentum: '动量', rsi: 'RSI', risk_control: '风控',
  liquidity: '流动性', macd: 'MACD', bollinger: '布林', capital_flow: '资金流',
}

const DEEP_TITLES: Record<string, string> = {
  overall_conclusion: '综合结论', operation_advice: '操作建议',
  technical_analysis: '技术面分析', industry_analysis: '行业分析',
  valuation_analysis: '估值分析', risk_assessment: '风险评估', tracking_plan: '跟踪计划',
}

const signalTagType = computed(() => {
  const s = data.value?.rating?.signal
  return s === 'strong_buy' ? 'danger' : s === 'buy' ? 'warning' : s === 'sell' ? 'info' : ''
})

const signalClass = computed(() => {
  const s = data.value?.rating?.signal
  return s?.includes('buy') ? 'sig-buy' : s?.includes('sell') ? 'sig-sell' : ''
})

const factorItems = computed(() => {
  const factors = data.value?.rating?.factors || data.value?.scores?.factors || {}
  return Object.entries(FACTOR_LABELS)
    .filter(([k]) => factors[k] != null)
    .map(([k, label]) => {
      const val = Number(factors[k])
      const color = val >= 70 ? '#ef232a' : val >= 45 ? '#e6a23c' : '#14b143'
      return { key: k, label, val, pct: Math.min(100, val), color }
    })
})

const visibleFinRows = computed(() =>
  (data.value?.financial_summary?.rows || []).filter((r: any) => r.value != null)
)

const hasPerfData = computed(() => {
  const p = data.value?.market_performance
  return p && Object.values(p).some((v) => v != null)
})

const deepSections = computed(() => {
  if (!deepResult.value) return []
  return Object.entries(DEEP_TITLES)
    .filter(([k]) => deepResult.value[k])
    .map(([k, title]) => ({ title, body: deepResult.value[k] }))
})

const fmt = (v?: number | null, dp = 2) =>
  v == null ? '-' : v >= 1e8 ? `${(v / 1e8).toFixed(dp)}亿` : v.toFixed(dp)

const fmtFin = (v?: number | null) => {
  if (v == null) return '-'
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(2)}万`
  return v.toFixed(2)
}

const signedPct = (v?: number | null) =>
  v == null ? '-' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`

const pctClass = (v?: number | null) => (v == null ? '' : v >= 0 ? 'up' : 'down')

const renderKline = () => {
  if (!klineEl.value || !data.value?.kline?.dates?.length) return
  if (!klineChart) klineChart = echarts.init(klineEl.value)
  const k = data.value.kline
  klineChart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    grid: { left: 60, right: 16, top: 16, bottom: 28 },
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
  data.value = null
  deepStarted.value = false
  deepResult.value = null
  deepError.value = ''
  try {
    const res: any = await ApiClient.get(`/api/quant/stock-analysis/${sym}`)
    data.value = res?.data || null
    if (data.value?.available) {
      await nextTick()
      renderKline()
    } else {
      ElMessage.warning('未找到该股票数据')
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '分析失败')
  } finally {
    loading.value = false
  }
}

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
    deepError.value = e?.message || '启动失败'
  }
}

const pollDeep = (taskId: string) => {
  deepTimer = setTimeout(async () => {
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
  if (deepTimer) clearTimeout(deepTimer)
  klineChart?.dispose()
})
</script>

<style scoped lang="scss">
.stock-analysis { display: flex; flex-direction: column; gap: 16px; }

.search-bar { display: flex; gap: 10px; align-items: center; }
.loading-hint { color: var(--el-text-color-secondary); font-size: 13px; }

/* Hero */
.hero-card {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.hero-main { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.stock-name { font-size: 22px; font-weight: 700; }
.code-tag { font-size: 12px; }
.signal-tag { font-size: 13px; padding: 4px 12px; }
.hero-prices { display: flex; align-items: baseline; gap: 12px; }
.price { font-size: 26px; font-weight: 700; }
.chg { font-size: 16px; font-weight: 600; }
.score-pill {
  background: var(--el-fill-color);
  border-radius: 20px;
  padding: 2px 10px;
  font-size: 13px;
  font-weight: 600;
}
.hero-meta { display: flex; gap: 16px; font-size: 13px; color: var(--el-text-color-secondary); }

/* Card base */
.card {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 16px;
}
.card-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}

/* K线图 */
.kline-chart { height: 260px; }

/* AI三栏 */
.ai-card {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  overflow: hidden;
}
.ai-col { padding: 14px 16px; }
.ai-col + .ai-col { border-left: 1px solid var(--el-border-color-lighter); }
.ai-col-head { font-size: 13px; font-weight: 600; margin-bottom: 8px; }
.bull .ai-col-head { color: #ef232a; }
.risk .ai-col-head { color: #e6a23c; }
.cat .ai-col-head { color: #409eff; }
.ai-col ul { margin: 0; padding-left: 16px; font-size: 13px; line-height: 1.8; color: var(--el-text-color-regular); }

/* Two col */
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }

/* Action */
.action-rows { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; }
.action-row { display: flex; justify-content: space-between; font-size: 14px; }
.ak { color: var(--el-text-color-secondary); }
.av { font-weight: 600; }
.loss { color: #14b143; }
.gain { color: #ef232a; }
.sig-buy { color: #ef232a; }
.sig-sell { color: #67c23a; }
.core-summary {
  font-size: 13px;
  line-height: 1.8;
  color: var(--el-text-color-regular);
  border-top: 1px solid var(--el-border-color-lighter);
  padding-top: 10px;
}

/* Factors */
.factor-list { display: flex; flex-direction: column; gap: 7px; }
.factor-row { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.fl { width: 52px; color: var(--el-text-color-secondary); flex-shrink: 0; }
.fbar { flex: 1; height: 6px; background: var(--el-fill-color); border-radius: 3px; overflow: hidden; }
.ffill { height: 100%; border-radius: 3px; transition: width .3s; }
.fval { width: 28px; text-align: right; font-weight: 600; }

/* Finance */
.fin-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 10px; }
.fin-item { background: var(--el-fill-color-lighter); border-radius: 6px; padding: 10px 12px; }
.fi-name { font-size: 11px; color: var(--el-text-color-secondary); }
.fi-val { font-size: 16px; font-weight: 700; margin: 4px 0 2px; }
.fi-yoy { font-size: 12px; }

/* Perf */
.perf-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; }
.perf-item { text-align: center; background: var(--el-fill-color-lighter); border-radius: 6px; padding: 8px 4px; }
.pl { font-size: 11px; color: var(--el-text-color-secondary); margin-bottom: 4px; }
.pv { font-size: 14px; font-weight: 600; }

/* News */
.news-list { display: flex; flex-direction: column; gap: 6px; }
.news-item {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  text-decoration: none;
  color: inherit;
  padding: 6px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
  font-size: 13px;
  &:last-child { border-bottom: none; }
}
.nt { flex: 1; color: #409eff; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nd { flex-shrink: 0; font-size: 11px; color: var(--el-text-color-secondary); margin-left: 12px; }

/* Deep */
.deep-hint { font-size: 13px; color: var(--el-text-color-secondary); margin-bottom: 12px; }
.deep-spin { display: flex; align-items: center; gap: 8px; color: var(--el-text-color-secondary); padding: 12px 0; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.deep-section { margin-bottom: 16px; &:last-child { margin-bottom: 0; } }
.ds-title { font-size: 14px; font-weight: 600; margin-bottom: 6px; }
.ds-body { font-size: 13px; line-height: 1.8; white-space: pre-wrap; color: var(--el-text-color-regular); }

/* Colors */
.up { color: #ef232a; }
.down { color: #14b143; }
</style>
