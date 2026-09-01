<template>
  <div class="breadth-page">
    <div class="page-head">
      <h2>市场宽度</h2>
      <p class="sub">
        全部按个股等权统计，不用指数——指数被权重股主导，看不见「指数横盘但七成票破位」
      </p>
      <div class="actions">
        <FreshnessChip :f="data?.freshness" />
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <div v-if="data && !data.ready" class="empty">{{ data.message || '暂不可用' }}</div>

    <template v-else-if="l">
      <!-- 温度分放在最前面并标明同源：顶部横幅和这一页说的必须是同一件事，
           否则用户会以为系统内部两套口径打架。 -->
      <div class="regime" :class="regimeClass">
        <b>{{ data?.regime }}</b>
        <span>温度 {{ data?.temp }}</span>
        <span class="muted">与顶部横幅、回放分层同一口径</span>
      </div>

      <div class="tiles">
        <div class="tile">
          <div class="k">上涨占比</div>
          <div class="v" :class="l.pct_up >= 0.5 ? 'up' : 'down'">{{ pct(l.pct_up) }}</div>
          <div class="s">涨 {{ l.up }} · 跌 {{ l.down }} · 平 {{ l.flat }}</div>
        </div>
        <div class="tile">
          <div class="k">站上 20 日线</div>
          <div class="v" :class="(l.above_ma20 ?? 0) >= 0.5 ? 'up' : 'down'">{{ pct(l.above_ma20) }}</div>
          <div class="s">短期结构</div>
        </div>
        <div class="tile">
          <div class="k">站上 60 日线</div>
          <div class="v" :class="(l.above_ma60 ?? 0) >= 0.5 ? 'up' : 'down'">{{ pct(l.above_ma60) }}</div>
          <div class="s">中期结构</div>
        </div>
        <div class="tile">
          <div class="k">20 日新高 / 新低</div>
          <div class="v"><span class="up">{{ l.new_high20 }}</span> / <span class="down">{{ l.new_low20 }}</span></div>
          <div class="s">极值扩散，见顶时新高先收缩</div>
        </div>
        <div class="tile">
          <div class="k">涨停 / 跌停</div>
          <div class="v"><span class="up">{{ l.limit_up }}</span> / <span class="down">{{ l.limit_down }}</span></div>
          <div class="s">情绪温度（±9.8% 粗口径）</div>
        </div>
        <div class="tile">
          <div class="k">中位涨幅</div>
          <div class="v" :class="l.median_ret >= 0 ? 'up' : 'down'">
            {{ l.median_ret > 0 ? '+' : '' }}{{ l.median_ret }}%
          </div>
          <div class="s">全市场个股中位，也是超额的基准</div>
        </div>
      </div>

      <div ref="chartEl" v-loading="loading" class="chart" />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { echarts, type ECharts } from '@/utils/echarts'
import { ApiClient } from '@/api/request'
import FreshnessChip, { type Freshness } from '@/components/FreshnessChip.vue'

interface Row {
  date: string; total: number; up: number; down: number; flat: number
  pct_up: number; above_ma20: number | null; above_ma60: number | null
  new_high20: number; new_low20: number; limit_up: number; limit_down: number
  median_ret: number
}
interface Breadth {
  ready: boolean; message?: string; computing?: boolean; as_of: string
  temp: number; regime: string; latest: Row; series: Row[]
  freshness: Freshness
}

const data = ref<Breadth | null>(null)
const loading = ref(false)
// 后端首次算全市场聚合要十几秒，期间返回 computing。自动重试一次而不是让用户
// 盯着「稍后刷新」自己猜时机——这类等待应该由系统承担，不是交给用户。
let retryTimer: ReturnType<typeof setTimeout> | undefined

const chartEl = ref<HTMLDivElement>()
let chart: ECharts | null = null

const l = computed(() => data.value?.latest)
const regimeClass = computed(() =>
  data.value?.regime === '偏暖' ? 'warm' : data.value?.regime === '偏冷' ? 'cold' : 'mid')
const pct = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)

const load = async () => {
  loading.value = true
  try {
    data.value = await ApiClient.get('/api/lite/breadth')
    if (data.value?.computing) {
      clearTimeout(retryTimer)
      retryTimer = setTimeout(load, 20000)
    }
    await nextTick()
    render()
  } finally {
    loading.value = false
  }
}

const render = () => {
  const d = data.value
  if (!chartEl.value || !d?.ready) return
  if (!chart) chart = echarts.init(chartEl.value)
  const s = d.series
  chart.setOption({
    grid: { left: 46, right: 52, top: 30, bottom: 46 },
    legend: { top: 0, textStyle: { color: '#8b93a3' } },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: s.map(r => r.date), axisLabel: { color: '#8b93a3' } },
    yAxis: [
      // 均线占比固定 0-100：不让 ECharts 自适应，否则 55%→60% 的窄幅波动会被拉成
      // 满屏大起大落，看着像市场天翻地覆，实际什么也没发生。
      { min: 0, max: 100, axisLabel: { color: '#8b93a3', formatter: '{value}%' },
        splitLine: { lineStyle: { color: '#23262e' } } },
      // 新高/新低是发散型柱（新低画成负值），右轴必须对零对称。让 ECharts 自适应会
      // 得到 +2000/−4000 这种范围，零线跑到上三分之一，视觉上「新低比新高多得多」
      // 的错觉全来自坐标轴而不是数据。
      {
        axisLabel: { color: '#8b93a3' },
        splitLine: { show: false },
        min: (v: { min: number; max: number }) => -Math.max(Math.abs(v.min), Math.abs(v.max)),
        max: (v: { min: number; max: number }) => Math.max(Math.abs(v.min), Math.abs(v.max)),
      },
    ],
    series: [
      {
        name: '站上 20 日线', type: 'line', showSymbol: false, smooth: true,
        lineStyle: { color: '#e0902c' },
        itemStyle: { color: '#e0902c' },
        data: s.map(r => (r.above_ma20 == null ? null : +(r.above_ma20 * 100).toFixed(1))),
      },
      {
        name: '站上 60 日线', type: 'line', showSymbol: false, smooth: true,
        lineStyle: { color: '#5b8fb5' },
        itemStyle: { color: '#5b8fb5' },
        data: s.map(r => (r.above_ma60 == null ? null : +(r.above_ma60 * 100).toFixed(1))),
      },
      {
        name: '20 日新高', type: 'bar', yAxisIndex: 1,
        itemStyle: { color: 'rgba(224,64,44,.45)' },
        data: s.map(r => r.new_high20),
      },
      {
        name: '20 日新低', type: 'bar', yAxisIndex: 1,
        itemStyle: { color: 'rgba(30,158,99,.45)' },
        data: s.map(r => -r.new_low20),
      },
    ],
  }, true)
}

const onResize = () => chart?.resize()
onMounted(() => { load(); window.addEventListener('resize', onResize) })
onBeforeUnmount(() => {
  clearTimeout(retryTimer)
  window.removeEventListener('resize', onResize)
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
/* flex:1 而不是 height:100%：外层 .content 是列式弹性容器且上面还有 MacroBar，
   写 100% 会把 MacroBar 的高度重复占一遍，多出一截滚动条、把坐标轴名裁掉。 */
.breadth-page { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.page-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.page-head h2 { margin: 0; font-size: 18px; }
.sub { margin: 0; color: #8b93a3; font-size: 12px; }
.actions { margin-left: auto; display: flex; gap: 10px; align-items: center; }
.empty { padding: 40px; text-align: center; color: #8b93a3; }
.regime { display: flex; align-items: baseline; gap: 10px; margin: 10px 0; padding: 6px 12px; border-radius: 6px; background: #12141a; font-size: 13px; }
.regime b { font-size: 15px; }
.regime.warm b { color: #e0402c; }
.regime.cold b { color: #1e9e63; }
.regime.mid b { color: #c7cede; }
.regime span { color: #8b93a3; font-size: 12px; }
.regime .muted { margin-left: auto; color: #6f7889; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; }
.tile { background: #12141a; border-radius: 6px; padding: 10px 12px; }
.k { font-size: 12px; color: #8b93a3; }
.v { font-size: 20px; font-weight: 600; margin: 2px 0; color: #dfe5f0; }
.s { font-size: 11px; color: #6f7889; }
.chart { flex: 1; min-height: 260px; margin-top: 10px; background: #12141a; border-radius: 6px; }
.up { color: #e0402c; }
.down { color: #1e9e63; }
</style>
