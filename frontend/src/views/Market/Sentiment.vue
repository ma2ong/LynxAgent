<template>
  <div class="sentiment-page">
    <div class="page-head">
      <div>
        <h1>市场情绪分析</h1>
        <p>基于本地日线与实时行情的市场资金流向与指标研究。仅供研究分析，不构成投资建议。</p>
        <small v-if="data?.as_of_time" class="as-of">
          更新至 {{ data.as_of }} {{ data.realtime ? '实时行情' : '本地日线' }} ｜ {{ data.as_of_time }}
        </small>
      </div>
    </div>

    <!-- KPI 卡 -->
    <section class="kpi-band" v-loading="loading">
      <div class="kpi-card">
        <span class="kpi-title">情绪温度</span>
        <div class="kpi-temp">
          <strong :style="{ color: tempColor }">{{ kpi.sentiment_temperature ?? '-' }}%</strong>
          <div ref="gaugeEl" class="gauge"></div>
        </div>
      </div>
      <div class="kpi-card">
        <span class="kpi-title">成交额</span>
        <strong class="kpi-num">{{ fmtTurnover(kpi.turnover_yi) }}</strong>
        <small :class="(kpi.turnover_change_yi ?? 0) >= 0 ? 'up' : 'down'">
          {{ (kpi.turnover_change_yi ?? 0) >= 0 ? '↑' : '↓' }}{{ Math.abs(kpi.turnover_change_yi ?? 0).toFixed(0) }}亿 vs 上一日
        </small>
      </div>
      <div class="kpi-card">
        <span class="kpi-title">连板高度</span>
        <strong class="kpi-num">{{ kpi.max_board_height ?? '-' }}板</strong>
        <small>
          <el-tag size="small" type="danger" effect="plain">涨停 {{ kpi.limit_up ?? 0 }}</el-tag>
          <el-tag size="small" type="success" effect="plain">跌停 {{ kpi.limit_down ?? 0 }}</el-tag>
          <span class="ladder">3板:{{ kpi.boards3 ?? 0 }} 4板:{{ kpi.boards4 ?? 0 }} 5板+:{{ kpi.boards5plus ?? 0 }}</span>
        </small>
      </div>
      <div class="kpi-card">
        <span class="kpi-title">涨跌比</span>
        <strong class="kpi-num" :style="{ color: (kpi.adv_dec_ratio ?? 1) >= 1 ? '#ef232a' : '#14b143' }">
          {{ kpi.adv_dec_ratio ?? '-' }}
        </strong>
        <small>涨:{{ kpi.advancers ?? 0 }} 跌:{{ kpi.decliners ?? 0 }} ｜ 5日线占比 {{ kpi.above_ma5_pct ?? 0 }}%</small>
      </div>
    </section>

    <section class="controls">
      <span class="title">大盘情绪</span>
      <div class="right">
        <el-date-picker v-model="range" type="daterange" value-format="YYYY-MM-DD"
          start-placeholder="开始" end-placeholder="结束" size="default" />
        <el-button type="primary" :loading="loading" @click="load">加载复盘数据</el-button>
      </div>
    </section>

    <el-alert v-if="data && data.empty" :title="data.message" type="warning" :closable="false" show-icon />

    <template v-if="data && !data.empty">
      <div class="chart-row">
        <div class="chart-card"><div class="ct">大盘成交额趋势</div><div ref="turnoverEl" class="chart"></div></div>
        <div class="chart-card"><div class="ct">大盘情绪（5日线占比）趋势</div><div ref="ma5El" class="chart"></div></div>
      </div>
      <div class="chart-card full"><div class="ct">涨跌停板分布对比</div><div ref="limitEl" class="chart wide"></div></div>
      <div class="chart-row">
        <div class="chart-card"><div class="ct">连板梯队结构</div><div ref="ladderEl" class="chart"></div></div>
        <div class="chart-card"><div class="ct">板块涨跌幅（区间累计）</div><div ref="indexEl" class="chart"></div></div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { marketSentimentApi } from '@/api/marketSentiment'

const UP = '#ef232a', DOWN = '#14b143'
const loading = ref(false)
const data = ref<any>(null)
const kpi = reactive<Record<string, any>>({})
const range = ref<[string, string] | null>(null)

const gaugeEl = ref<HTMLDivElement>()
const turnoverEl = ref<HTMLDivElement>()
const ma5El = ref<HTMLDivElement>()
const limitEl = ref<HTMLDivElement>()
const ladderEl = ref<HTMLDivElement>()
const indexEl = ref<HTMLDivElement>()
const charts: echarts.ECharts[] = []

const tempColor = computed(() => {
  const t = kpi.sentiment_temperature ?? 0
  return t >= 60 ? UP : t >= 40 ? '#e6a23c' : DOWN
})

const fmtTurnover = (yi?: number) => {
  if (yi == null) return '-'
  return yi >= 10000 ? `${(yi / 10000).toFixed(2)}万亿` : `${yi.toFixed(0)}亿`
}

const defaultRange = (): [string, string] => {
  const end = new Date()
  const start = new Date(); start.setDate(start.getDate() - 30)
  const f = (d: Date) => d.toISOString().slice(0, 10)
  return [f(start), f(end)]
}

const mk = (el?: HTMLDivElement) => { if (!el) return null; const c = echarts.init(el); charts.push(c); return c }

const renderAll = () => {
  const d = data.value
  if (!d || d.empty) return
  const dates = d.dates || []

  // 温度仪表盘
  if (gaugeEl.value) {
    const g = mk(gaugeEl.value)
    g?.setOption({
      series: [{
        type: 'gauge', radius: '100%', center: ['50%', '60%'], startAngle: 200, endAngle: -20,
        min: 0, max: 100, splitNumber: 5, pointer: { width: 4, length: '60%' },
        progress: { show: true, width: 8 }, axisLine: { lineStyle: { width: 8 } },
        axisLabel: { show: false }, axisTick: { show: false }, splitLine: { show: false },
        detail: { show: false }, data: [{ value: kpi.sentiment_temperature ?? 0 }],
        itemStyle: { color: tempColor.value },
      }],
    })
  }

  mk(turnoverEl.value)?.setOption({
    tooltip: { trigger: 'axis' }, grid: { left: 56, right: 16, top: 16, bottom: 28 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', name: '亿', scale: true },
    series: [
      { type: 'bar', data: d.turnover_trend.amount_yi, itemStyle: { color: '#5b8ff9' }, barMaxWidth: 18 },
      { type: 'line', data: d.turnover_trend.amount_yi, smooth: true, showSymbol: false, lineStyle: { color: '#e6a23c' } },
    ],
  })

  const m = d.above_ma5_trend
  mk(ma5El.value)?.setOption({
    tooltip: { trigger: 'axis' }, legend: { top: 0, itemHeight: 8, textStyle: { fontSize: 10 } },
    grid: { left: 44, right: 16, top: 28, bottom: 28 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', name: '%', max: 100 },
    series: [
      { name: '全市场', type: 'line', data: m.all, smooth: true, showSymbol: false, lineStyle: { width: 2, color: UP } },
      { name: '大盘股', type: 'line', data: m['大盘股'], smooth: true, showSymbol: false, lineStyle: { color: '#5b8ff9' } },
      { name: '中盘股', type: 'line', data: m['中盘股'], smooth: true, showSymbol: false, lineStyle: { color: '#e6a23c' } },
      { name: '小盘股', type: 'line', data: m['小盘股'], smooth: true, showSymbol: false, lineStyle: { color: '#67c23a' } },
    ],
  })

  const ld = d.limit_distribution
  mk(limitEl.value)?.setOption({
    tooltip: { trigger: 'axis' }, legend: { top: 0, data: ['涨停', '跌停'], itemHeight: 8, textStyle: { fontSize: 10 } },
    grid: { left: 48, right: 16, top: 28, bottom: 28 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value' },
    series: [
      { name: '涨停', type: 'bar', stack: 'x', data: ld.up, itemStyle: { color: UP }, barMaxWidth: 22,
        label: { show: true, position: 'top', fontSize: 9, color: UP } },
      { name: '跌停', type: 'bar', stack: 'x', data: ld.down.map((v: number) => -v), itemStyle: { color: DOWN }, barMaxWidth: 22 },
      { name: '涨停趋势', type: 'line', data: ld.up, smooth: true, showSymbol: false, lineStyle: { color: UP, width: 1 } },
    ],
  })

  const lad = d.board_ladder

  mk(ladderEl.value)?.setOption({
    tooltip: { trigger: 'axis' }, legend: { top: 0, data: ['3板', '4板', '5板+', '最高板'], itemHeight: 8, textStyle: { fontSize: 10 } },
    grid: { left: 40, right: 44, top: 28, bottom: 28 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10 } },
    yAxis: [{ type: 'value', name: '家数' }, { type: 'value', name: '高度', max: 8 }],
    series: [
      { name: '3板', type: 'bar', stack: 'b', data: lad.b3, itemStyle: { color: '#67c23a' }, barMaxWidth: 22 },
      { name: '4板', type: 'bar', stack: 'b', data: lad.b4, itemStyle: { color: '#e6a23c' } },
      { name: '5板+', type: 'bar', stack: 'b', data: lad.b5plus, itemStyle: { color: UP } },
      { name: '最高板', type: 'line', yAxisIndex: 1, data: lad.max_height, lineStyle: { color: '#3b3f8c' }, symbolSize: 5 },
    ],
  })

  const idx = d.index_returns
  const idxColors: Record<string, string> = { '全A': '#3b3f8c', '沪主板': '#5b8ff9', '深主板': '#e6a23c', '创业板': '#ef232a', '科创板': '#9b59b6' }
  mk(indexEl.value)?.setOption({
    tooltip: { trigger: 'axis' }, legend: { top: 0, itemHeight: 8, textStyle: { fontSize: 10 } },
    grid: { left: 48, right: 16, top: 28, bottom: 28 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', name: '%', axisLabel: { formatter: '{value}%' } },
    series: ['全A', '沪主板', '深主板', '创业板', '科创板'].map((k) => ({
      name: k, type: 'line', data: idx[k], smooth: true, showSymbol: false, lineStyle: { color: idxColors[k] },
    })),
  })
}

const disposeAll = () => { charts.forEach((c) => c.dispose()); charts.length = 0 }

const load = async () => {
  loading.value = true
  try {
    const [s, e] = range.value || []
    const res: any = await marketSentimentApi.get(s, e)
    data.value = res?.data || { empty: true, message: '无数据' }
    Object.assign(kpi, data.value?.kpi || {})
    await nextTick()
    disposeAll()
    renderAll()
  } catch (error: any) {
    ElMessage.error(error?.message || '加载市场情绪失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => { range.value = defaultRange(); load() })
onUnmounted(disposeAll)
</script>

<style scoped lang="scss">
.page-head { margin-bottom: 12px; h1 { margin: 0 0 4px; font-size: 22px; } p { margin: 0; color: var(--el-text-color-secondary); font-size: 13px; } }
.as-of { display: inline-block; margin-top: 5px; color: var(--el-color-primary); font-size: 12px; }
.kpi-band { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 14px; }
.kpi-card {
  background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: 10px;
  padding: 14px 16px; display: flex; flex-direction: column; gap: 6px;
}
.kpi-title { font-size: 13px; color: var(--el-text-color-secondary); }
.kpi-num { font-size: 26px; font-weight: 700; }
.kpi-temp { display: flex; align-items: center; justify-content: space-between; }
.kpi-temp strong { font-size: 30px; font-weight: 800; }
.gauge { width: 84px; height: 56px; }
.kpi-card small { color: var(--el-text-color-secondary); display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.ladder { font-size: 12px; }
.up { color: #ef232a; } .down { color: #14b143; }
.controls { display: flex; align-items: center; justify-content: space-between; margin: 6px 0 12px;
  .title { font-size: 18px; font-weight: 700; } .right { display: flex; gap: 10px; } }
.chart-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
.chart-card { background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: 10px; padding: 12px 14px; }
.chart-card.full { margin-bottom: 12px; }
.ct { font-weight: 700; text-align: center; margin-bottom: 8px; }
.chart { width: 100%; height: 280px; }
.chart.wide { height: 320px; }
@media (max-width: 900px) { .kpi-band, .chart-row { grid-template-columns: 1fr; } }
</style>
