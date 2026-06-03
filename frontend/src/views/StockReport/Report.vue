<template>
  <div class="report-page">
    <div class="page-head">
      <div>
        <h1>个股 AI 研报</h1>
        <p>一页纸个股研报：评级、目标价区间、技术与基本面评分、AI 投资观点。仅供研究分析，不构成投资建议。</p>
      </div>
      <div class="search">
        <el-input v-model="symbol" placeholder="输入股票代码，如 600519" style="width: 220px"
          @keyup.enter="load" clearable />
        <el-button type="primary" :loading="loading" @click="load">生成研报</el-button>
      </div>
    </div>

    <el-alert v-if="report && !report.available" title="未获取到该股票数据，请检查代码" type="warning"
      :closable="false" show-icon />

    <template v-if="report && report.available">
      <!-- 顶部：股票信息 + 评级 -->
      <section class="top-band">
        <div class="ident">
          <div class="code">{{ report.header.symbol }}</div>
          <div class="name">{{ report.header.name }}</div>
          <el-tag size="small" effect="plain">{{ report.header.industry }}</el-tag>
        </div>
        <div class="price">
          <span class="label">最新价</span>
          <strong>{{ report.header.last_price ?? '-' }}</strong>
        </div>
        <div class="rating" :class="ratingClass(report.rating.signal)">
          <span class="label">投资评级</span>
          <strong>{{ report.rating.label }}</strong>
          <small v-if="report.rating.position_note">{{ report.rating.position_note }}</small>
          <div v-if="report.rating.rating_bands" class="bands">
            买入 &lt; {{ report.rating.rating_bands.buy_below }} ｜
            观察 {{ report.rating.rating_bands.watch_low }}~{{ report.rating.rating_bands.watch_high }} ｜
            风险 &gt; {{ report.rating.rating_bands.risk_above }}
          </div>
        </div>
      </section>

      <!-- 核心摘要 -->
      <section class="card">
        <div class="ct">核心摘要</div>
        <p class="summary">{{ report.core_summary }}</p>
      </section>

      <div class="grid-2">
        <!-- 目标价森林图 -->
        <section class="card">
          <div class="ct">目标价区间与估值区间</div>
          <div v-if="report.valuation_forest.available" ref="forestEl" class="chart"></div>
          <div v-else class="empty">暂无</div>
        </section>

        <!-- 技术与基本面评分 -->
        <section class="card">
          <div class="ct">技术与基本面综合评分</div>
          <div class="score-row">
            <div class="score-card">
              <span class="score-label">技术面评分</span>
              <strong :style="{ color: scoreColor(report.scores.technical) }">{{ report.scores.technical }}</strong>
              <small>/ 100</small>
            </div>
            <div class="score-card">
              <span class="score-label">基本面评分</span>
              <strong :style="{ color: scoreColor(report.scores.fundamental) }">{{ report.scores.fundamental }}</strong>
              <small>/ 100</small>
            </div>
          </div>
        </section>
      </div>

      <!-- 关键指标(当日) -->
      <section class="card">
        <div class="ct">关键指标（当日）</div>
        <el-table :data="report.key_indicators" size="small" stripe>
          <el-table-column prop="name" label="指标" />
          <el-table-column prop="value" label="数值" />
          <el-table-column label="较昨">
            <template #default="{ row }"><span :class="pnClass(row.vs_yesterday)">{{ fmtPct(row.vs_yesterday) }}</span></template>
          </el-table-column>
          <el-table-column label="较上周">
            <template #default="{ row }"><span :class="pnClass(row.vs_week)">{{ fmtPct(row.vs_week) }}</span></template>
          </el-table-column>
          <el-table-column label="较上月">
            <template #default="{ row }"><span :class="pnClass(row.vs_month)">{{ fmtPct(row.vs_month) }}</span></template>
          </el-table-column>
        </el-table>
      </section>

      <!-- AI 投资观点 -->
      <section class="card">
        <div class="ct">AI 投资观点<small class="src">（{{ report.ai_view.source === 'llm' ? 'AI 生成' : '规则推断' }}）</small></div>
        <div class="grid-3">
          <div class="view-col">
            <div class="view-h bull">看多逻辑</div>
            <ul><li v-for="(x, i) in report.ai_view.bull" :key="i">{{ x }}</li></ul>
          </div>
          <div class="view-col">
            <div class="view-h risk">风险提示</div>
            <ul><li v-for="(x, i) in report.ai_view.risk" :key="i">{{ x }}</li></ul>
          </div>
          <div class="view-col">
            <div class="view-h cata">关键催化</div>
            <ul><li v-for="(x, i) in report.ai_view.catalyst" :key="i">{{ x }}</li></ul>
          </div>
        </div>
      </section>

      <div class="grid-2">
        <!-- 核心财务摘要 -->
        <section class="card">
          <div class="ct">核心财务摘要</div>
          <el-table v-if="report.financial_summary.available" :data="report.financial_summary.rows" size="small" stripe>
            <el-table-column prop="name" label="科目" />
            <el-table-column label="数值">
              <template #default="{ row }">{{ row.value ?? '暂无' }}</template>
            </el-table-column>
            <el-table-column label="同比">
              <template #default="{ row }"><span :class="pnClass(row.yoy)">{{ fmtPct(row.yoy) }}</span></template>
            </el-table-column>
          </el-table>
          <div v-else class="empty">暂无</div>
        </section>

        <!-- 市场表现 -->
        <section class="card">
          <div class="ct">市场表现 %</div>
          <div class="perf-grid">
            <div v-for="p in perfRows" :key="p.key" class="perf-item">
              <span class="perf-label">{{ p.label }}</span>
              <strong :class="pnClass(report.market_performance[p.key])">{{ fmtPct(report.market_performance[p.key]) }}</strong>
            </div>
          </div>
        </section>
      </div>

      <div class="grid-2">
        <!-- 机构持仓变动 -->
        <section class="card">
          <div class="ct">机构持仓变动</div>
          <div class="empty">{{ report.institution_holdings.note || '暂无' }}</div>
        </section>

        <!-- 相关新闻 -->
        <section class="card">
          <div class="ct">相关新闻（近 7 天）</div>
          <el-table v-if="report.news && report.news.length" :data="report.news" size="small">
            <el-table-column prop="time" label="时间" width="150" />
            <el-table-column prop="title" label="标题">
              <template #default="{ row }">
                <a v-if="row.url" :href="row.url" target="_blank" rel="noopener">{{ row.title }}</a>
                <span v-else>{{ row.title }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="source" label="来源" width="120" />
          </el-table>
          <div v-else class="empty">暂无</div>
        </section>
      </div>

      <p class="disclaimer">{{ report.disclaimer }}</p>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, onUnmounted } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { stockReportApi } from '@/api/stockReport'

const UP = '#ef232a'
const DOWN = '#14b143'

const symbol = ref('600519')
const loading = ref(false)
const report = ref<any>(null)

const forestEl = ref<HTMLDivElement>()
let forestChart: echarts.ECharts | null = null

const perfRows = [
  { key: 'd1', label: '1日' },
  { key: 'd5', label: '5日' },
  { key: 'm1', label: '1月' },
  { key: 'm3', label: '3月' },
  { key: 'ytd', label: '年初至今' },
  { key: 'y1', label: '1年' },
]

const fmtPct = (v: number | null | undefined) => (v == null ? '-' : `${v > 0 ? '+' : ''}${v}%`)
const pnClass = (v: number | null | undefined) => (v == null ? '' : v >= 0 ? 'up' : 'down')
const scoreColor = (s: number) => (s >= 62 ? UP : s >= 45 ? '#e6a23c' : DOWN)
const ratingClass = (sig: string) =>
  sig === 'strong_buy' || sig === 'buy' ? 'r-buy' : sig === 'hold' ? 'r-hold' : 'r-avoid'

const renderForest = () => {
  const v = report.value?.valuation_forest
  if (!v || !v.available || !forestEl.value) return
  forestChart?.dispose()
  forestChart = echarts.init(forestEl.value)
  const rows = v.rows as Array<{ label: string; low: number; high: number }>
  const labels = rows.map((r) => r.label)
  // 区间条用 floating bar：base = low，bar 长度 = high - low；当前价行画一个点
  const base = rows.map((r) => r.low)
  const span = rows.map((r) => Math.max(r.high - r.low, 0))
  const points = rows.map((r) => (r.high - r.low <= 0 ? r.low : (r.low + r.high) / 2))
  forestChart.setOption({
    grid: { left: 80, right: 24, top: 16, bottom: 28 },
    tooltip: {
      trigger: 'axis',
      formatter: (ps: any) => {
        const i = ps[0].dataIndex
        const r = rows[i]
        return `${r.label}<br/>区间 ${r.low} ~ ${r.high}`
      },
    },
    xAxis: { type: 'value', scale: true, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'category', data: labels, inverse: true, axisLabel: { fontSize: 12 } },
    series: [
      { type: 'bar', stack: 'f', itemStyle: { color: 'transparent' }, data: base, barWidth: 14, silent: true },
      {
        type: 'bar', stack: 'f', barWidth: 14, data: span,
        itemStyle: {
          color: (p: any) => ['#ef9a9a', '#5b8ff9', '#a0cfa0', '#3b3f8c'][p.dataIndex] || '#5b8ff9',
        },
      },
      {
        type: 'scatter', symbol: 'diamond', symbolSize: 14,
        itemStyle: { color: '#3b3f8c' },
        data: points.map((x, i) => [x, i]),
      },
    ],
  })
}

const load = async () => {
  if (!symbol.value.trim()) {
    ElMessage.warning('请输入股票代码')
    return
  }
  loading.value = true
  try {
    const res: any = await stockReportApi.get(symbol.value.trim())
    report.value = res || { available: false }
    await nextTick()
    renderForest()
  } catch (error: any) {
    ElMessage.error(error?.message || '生成研报失败')
  } finally {
    loading.value = false
  }
}

onUnmounted(() => forestChart?.dispose())
</script>

<style scoped lang="scss">
.page-head { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 14px; flex-wrap: wrap; gap: 12px;
  h1 { margin: 0 0 4px; font-size: 22px; } p { margin: 0; color: var(--el-text-color-secondary); font-size: 13px; }
  .search { display: flex; gap: 10px; } }
.top-band { display: grid; grid-template-columns: 1.4fr 1fr 2fr; gap: 12px; margin-bottom: 12px;
  background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: 10px; padding: 16px; }
.ident { display: flex; flex-direction: column; gap: 6px;
  .code { font-size: 13px; color: var(--el-text-color-secondary); } .name { font-size: 22px; font-weight: 800; } }
.price { display: flex; flex-direction: column; gap: 4px; .label { font-size: 12px; color: var(--el-text-color-secondary); }
  strong { font-size: 26px; font-weight: 800; } }
.rating { display: flex; flex-direction: column; gap: 4px; border-radius: 8px; padding: 10px 12px;
  .label { font-size: 12px; color: var(--el-text-color-secondary); } strong { font-size: 22px; font-weight: 800; }
  small { font-size: 12px; color: var(--el-text-color-secondary); } .bands { font-size: 12px; margin-top: 2px; } }
.r-buy { background: #fdeaea; strong { color: #ef232a; } }
.r-hold { background: #fdf6ec; strong { color: #e6a23c; } }
.r-avoid { background: #eef9f0; strong { color: #14b143; } }
.card { background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
.ct { font-weight: 700; margin-bottom: 10px; .src { font-weight: 400; font-size: 12px; color: var(--el-text-color-secondary); } }
.summary { margin: 0; font-size: 14px; line-height: 1.7; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
.chart { width: 100%; height: 240px; }
.empty { color: var(--el-text-color-secondary); padding: 24px 0; text-align: center; }
.score-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.score-card { text-align: center; padding: 18px 0; background: var(--el-fill-color-light); border-radius: 10px;
  .score-label { display: block; font-size: 13px; color: var(--el-text-color-secondary); margin-bottom: 6px; }
  strong { font-size: 44px; font-weight: 800; } small { font-size: 14px; color: var(--el-text-color-secondary); } }
.view-col ul { margin: 6px 0 0; padding-left: 18px; line-height: 1.8; font-size: 13px; }
.view-h { font-weight: 700; padding: 4px 0; border-bottom: 2px solid; }
.view-h.bull { color: #ef232a; border-color: #ef232a; }
.view-h.risk { color: #e6a23c; border-color: #e6a23c; }
.view-h.cata { color: #5b8ff9; border-color: #5b8ff9; }
.perf-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.perf-item { text-align: center; padding: 12px 0; background: var(--el-fill-color-light); border-radius: 8px;
  .perf-label { display: block; font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 4px; }
  strong { font-size: 18px; font-weight: 700; } }
.disclaimer { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 8px; }
.up { color: #ef232a; } .down { color: #14b143; }
@media (max-width: 900px) { .top-band, .grid-2, .grid-3, .perf-grid { grid-template-columns: 1fr; } }
</style>
