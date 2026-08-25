<template>
  <div class="heatmap-page">
    <div class="page-head">
      <div>
        <h2>行业热力图</h2>
        <p class="sub">
          面积=A股市值 · 颜色={{ PERIODS.find(p => p.key === period)?.label }}涨跌幅（红涨绿跌）
          <template v-if="data"> · {{ data.source === 'realtime' ? '实时行情' : '本地日线' }} · {{ data.updated_at }}</template>
          <template v-if="!currentIndustry && data?.coverage?.classified">
            · 已归类 {{ data.coverage.classified }} 只<span
              v-if="(data.coverage.unclassified || 0) > 0"
              class="cov-note">（未归类 {{ data.coverage.unclassified }} 只，行业源不全暂不计入）</span>
          </template>
        </p>
      </div>
      <div class="actions">
        <!-- 单日颜色回答不了「这个板块是不是在持续走强」：一根大阳线和连涨二十天
             在当日口径下同样是红的。周期切换只换颜色，不重新取数。 -->
        <el-radio-group v-model="period" size="small" :disabled="data?.periods_ready === false" @change="render">
          <el-radio-button v-for="p in PERIODS" :key="p.key" :value="p.key">{{ p.label }}</el-radio-button>
        </el-radio-group>
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
import { nextTick, onActivated, onBeforeUnmount, onMounted, ref } from 'vue'
defineOptions({ name: 'HeatmapPage' })  // keep-alive 保活标识，勿改
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

// 每档周期的饱和刻度必须各不相同：20 日涨跌若也用 ±4% 封顶，
// 但凡走出趋势的板块全是纯红，热力图退化成一块红布，区分不出强弱。
const PERIODS = [
  { key: 'pct' as const, label: '当日', cap: 4 },
  { key: 'pct5' as const, label: '5日', cap: 8 },
  { key: 'pct20' as const, label: '20日', cap: 15 },
]
const period = ref<'pct' | 'pct5' | 'pct20'>('pct')
const periodCap = () => PERIODS.find(p => p.key === period.value)?.cap || 4
const valOf = (it: HeatmapItem) => it[period.value] as number | null | undefined

// A股红涨绿跌：按当前周期的刻度线性插值；无数据给中性灰，不拿 0 冒充平盘
const flat = [58, 63, 75]
const pctColor = (pct: number | null | undefined) => {
  if (pct == null) return 'rgb(72,76,86)'
  const t = Math.max(-1, Math.min(1, pct / periodCap()))
  const mix = (a: number[], b: number[], k: number) =>
    `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * k)).join(',')})`
  return t >= 0 ? mix(flat, [224, 64, 44], t) : mix(flat, [30, 158, 99], -t)
}
const fmtPct = (v: number | null | undefined) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v}%`)

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
        // 口径必须写清楚：行情源给的是 A股股本×现价，不含 H 股。建设银行 96% 的股本在
        // 港股，这里只有 949 亿而真实总市值约 2.5 万亿，不标注会被当成数据错误。
        // 三档一起给：切到 20 日看到一块红时，得能立刻看出它今天是不是正在回落
        return `${head}<br/>当日 ${fmtPct(it.pct)} · 5日 ${fmtPct(it.pct5)} · 20日 ${fmtPct(it.pct20)}<br/>` +
          `A股市值 ${it.value} 亿<br/>` +
          `<span style="opacity:.65">A+H 公司只计 A 股部分</span>`
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
        formatter: (p: any) => `${p.data.name}\n${fmtPct(valOf(p.data))}`,
        fontSize: 12,
      },
      itemStyle: { borderColor: '#1a1c22', borderWidth: 1, gapWidth: 1 },
      data: items.value.map(it => ({ ...it, itemStyle: { color: pctColor(valOf(it)) } })),
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
// keep-alive：从详情页返回时组件被重新激活，treemap 若在隐藏期间容器尺寸变过需重算
onActivated(() => nextTick(() => chart?.resize()))
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
.cov-note { color: var(--el-text-color-placeholder); }
</style>
