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

    <section v-if="data?.capital_flow" class="flow-panel">
      <div class="flow-head">
        <div>
          <span>资金面板</span>
          <strong>{{ data.capital_flow.state }}</strong>
        </div>
        <el-tag :type="data.capital_flow.score >= 65 ? 'danger' : data.capital_flow.score <= 40 ? 'success' : 'warning'" effect="plain">
          {{ Math.round(data.capital_flow.score) }}
        </el-tag>
      </div>
      <div class="flow-metrics">
        <div><span>成交额</span><b>{{ fmtTurnover(data.capital_flow.turnover_yi) }}</b></div>
        <div><span>较上一日</span><b :class="data.capital_flow.turnover_change_yi >= 0 ? 'up' : 'down'">{{ data.capital_flow.turnover_change_yi >= 0 ? '+' : '' }}{{ data.capital_flow.turnover_change_yi.toFixed(0) }}亿</b></div>
        <div><span>上涨占比</span><b>{{ data.capital_flow.breadth_pct.toFixed(1) }}%</b></div>
        <div><span>涨跌停差</span><b :class="data.capital_flow.limit_balance >= 0 ? 'up' : 'down'">{{ data.capital_flow.limit_balance }}</b></div>
      </div>
      <ul>
        <li v-for="note in data.capital_flow.notes" :key="note">{{ note }}</li>
      </ul>
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

    <!-- 多源加权情绪 -->
    <div class="chart-row">
      <div class="chart-card">
        <div class="ct">大盘加权情绪<small class="sub">（多源加权，区别于上方宽度情绪温度）</small></div>
        <div v-if="ws.marketLoading" class="ws-empty">情绪计算中…</div>
        <template v-else-if="ws.market && !ws.market.empty">
          <div class="ws-head">
            <div class="ws-score" :style="{ color: wsColor(ws.market.score) }">
              <strong>{{ ws.market.score }}</strong><small>/ 100</small>
            </div>
            <el-tag size="small" :type="wsLevelType(ws.market.level)" effect="dark">{{ ws.market.level }}</el-tag>
            <span v-if="ws.market.base_temperature != null" class="ws-cmp">宽度温度 {{ ws.market.base_temperature }}</span>
          </div>
          <div class="ws-bars">
            <div v-for="(b, i) in ws.market.breakdown" :key="i" class="ws-bar">
              <el-tooltip :content="dimHint(b.name)" placement="top" :disabled="!dimHint(b.name)">
                <span class="wb-name">{{ b.name }}</span>
              </el-tooltip>
              <div class="wb-track"><div class="wb-fill" :style="{ width: b.score + '%', background: wsColor(b.score) }"></div></div>
              <span class="wb-val">{{ b.score }}</span>
              <span class="wb-w">{{ Math.round(b.weight * 100) }}%</span>
            </div>
          </div>
        </template>
        <div v-else class="ws-empty">{{ ws.market?.message || '暂无' }}</div>
      </div>

      <div class="chart-card">
        <div class="ct">板块情绪排行<small class="sub">（资金流 / 涨跌幅 / 新闻加权）</small></div>
        <div v-if="ws.sectorLoading" class="ws-empty">加载中…</div>
        <el-table v-else-if="ws.sector && ws.sector.length" :data="ws.sector" size="small" height="300" stripe>
          <el-table-column type="index" label="#" width="44" />
          <el-table-column prop="name" label="板块" />
          <el-table-column label="情绪分" width="120">
            <template #default="{ row }">
              <el-tag size="small" :type="wsLevelType(row.level)" effect="plain">{{ row.score }} {{ row.level }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="资金净额(亿)" width="120">
            <template #default="{ row }"><span :class="row.net_yi >= 0 ? 'up' : 'down'">{{ row.net_yi }}</span></template>
          </el-table-column>
          <el-table-column label="涨跌幅" width="100">
            <template #default="{ row }"><span :class="row.pct >= 0 ? 'up' : 'down'">{{ row.pct }}%</span></template>
          </el-table-column>
        </el-table>
        <div v-else class="ws-empty">暂无</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { echarts, type ECharts } from '@/utils/echarts'
import { ElMessage } from 'element-plus'
import { marketSentimentApi } from '@/api/marketSentiment'
import { quantApi } from '@/api/quant'

const UP = '#ef232a', DOWN = '#14b143'
const loading = ref(false)
const data = ref<any>(null)

// 多源加权情绪
const ws = reactive<{ market: any; sector: any[]; marketLoading: boolean; sectorLoading: boolean }>({
  market: null, sector: [], marketLoading: false, sectorLoading: false,
})
const wsColor = (s: number) => (s >= 62 ? UP : s >= 45 ? '#e6a23c' : DOWN)
const wsLevelType = (lv: string) =>
  lv === '高涨' ? 'danger' : lv === '偏暖' ? 'warning' : lv === '中性' ? 'info' : 'success'
const DIM_HINTS: Record<string, string> = {
  '宽度(>MA5)': '站上 5 日均线的个股占比，衡量多头宽度',
  '涨跌比': '上涨家数 ÷ 下跌家数，大于 1 偏强',
  '涨跌停强弱': '涨停数占涨跌停总数比例，反映情绪极端度',
  '行业资金合计': '各行业主力资金净流入合计（亿），正值偏暖',
  '市场新闻热度': '市场新闻标题的利好/利空词净值',
}
const dimHint = (name: string): string => DIM_HINTS[name] || ''
const loadWeightedSentiment = () => {
  ws.marketLoading = true
  quantApi.marketWeightedSentiment()
    .then((res) => { ws.market = res })
    .catch(() => { ws.market = { empty: true, message: '加载失败' } })
    .finally(() => { ws.marketLoading = false })
  ws.sectorLoading = true
  quantApi.sectorSentimentRank(20)
    .then((res) => { ws.sector = res?.rows || [] })
    .catch(() => { ws.sector = [] })
    .finally(() => { ws.sectorLoading = false })
}

const kpi = reactive<Record<string, any>>({})
const range = ref<[string, string] | null>(null)

const gaugeEl = ref<HTMLDivElement>()
const turnoverEl = ref<HTMLDivElement>()
const ma5El = ref<HTMLDivElement>()
const limitEl = ref<HTMLDivElement>()
const ladderEl = ref<HTMLDivElement>()
const indexEl = ref<HTMLDivElement>()
const charts: ECharts[] = []

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

onMounted(() => { range.value = defaultRange(); load(); loadWeightedSentiment() })
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
.flow-panel {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 10px;
  padding: 14px 16px;
  margin-bottom: 14px;
}
.flow-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.flow-head span { display: block; color: var(--el-text-color-secondary); font-size: 12px; margin-bottom: 2px; }
.flow-head strong { font-size: 20px; }
.flow-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-bottom: 10px; }
.flow-metrics div { background: var(--el-fill-color-lighter); border-radius: 6px; padding: 8px 10px; }
.flow-metrics span { display: block; color: var(--el-text-color-secondary); font-size: 11px; margin-bottom: 2px; }
.flow-metrics b { font-size: 14px; }
.flow-panel ul { margin: 0; padding-left: 18px; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.7; }
.controls { display: flex; align-items: center; justify-content: space-between; margin: 6px 0 12px;
  .title { font-size: 18px; font-weight: 700; } .right { display: flex; gap: 10px; } }
.chart-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
.chart-card { background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: 10px; padding: 12px 14px; }
.chart-card.full { margin-bottom: 12px; }
.ct { font-weight: 700; text-align: center; margin-bottom: 8px; }
.chart { width: 100%; height: 280px; }
.chart.wide { height: 320px; }
@media (max-width: 900px) { .kpi-band, .chart-row, .flow-metrics { grid-template-columns: 1fr; } }
/* 多源加权情绪 */
.ct .sub { font-weight: 400; font-size: 11px; color: var(--el-text-color-secondary); }
.ws-empty { color: var(--el-text-color-secondary); padding: 40px 0; text-align: center; }
.ws-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.ws-score { display: flex; align-items: baseline; gap: 3px; strong { font-size: 30px; font-weight: 800; }
  small { font-size: 12px; color: var(--el-text-color-secondary); } }
.ws-cmp { font-size: 12px; color: var(--el-text-color-secondary); }
.ws-bars { display: flex; flex-direction: column; gap: 8px; padding: 4px 0; }
.ws-bar { display: flex; align-items: center; gap: 10px; font-size: 13px;
  .wb-name { width: 88px; flex: none; color: var(--el-text-color-regular); }
  .wb-track { flex: 1; height: 10px; border-radius: 5px; background: var(--el-fill-color); overflow: hidden; }
  .wb-fill { height: 100%; border-radius: 5px; transition: width .3s; }
  .wb-val { width: 36px; text-align: right; font-weight: 600; }
  .wb-w { width: 40px; text-align: right; font-size: 11px; color: var(--el-text-color-secondary); } }
</style>
