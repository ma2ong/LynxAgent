<template>
  <div class="heatmap-page">
    <!-- 标题与口径说明同一行：这页的价值全在那张图上，每多占一行文字，
         就少一行瓦片、多一次滚动。 -->
    <div class="page-head">
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
      <div class="actions">
        <!-- 单日颜色回答不了「这个板块是不是在持续走强」：一根大阳线和连涨二十天
             在当日口径下同样是红的。周期切换只换颜色，不重新取数。 -->
        <el-radio-group v-model="period" size="small" :disabled="data?.periods_ready === false" @change="render">
          <el-radio-button v-for="p in PERIODS" :key="p.key" :value="p.key">{{ p.label }}</el-radio-button>
        </el-radio-group>
        <!-- 板块直达：图上点标题条也能进，但那是一条窄边，找得到才怪。
             这个下拉还兼做「在板块之间横跳」——否则每换一个板块都要先退回全市场。
             不开 filterable：那会变成输入框，用户得先想「这个板块叫什么」再打字；
             板块名是固定的一百多个，展开直接挑才对。 -->
        <el-select
          v-model="currentIndustry"
          class="industry-jump"
          size="small"
          clearable
          placeholder="进入板块热力图"
          @change="(v: string) => load(v || '')"
        >
          <el-option
            v-for="opt in industryOptions"
            :key="opt.name"
            :label="`${opt.name}  ${fmtPct(opt.val)}`"
            :value="opt.name"
          >
            <span>{{ opt.name }}</span>
            <span class="opt-pct" :class="opt.val != null && opt.val > 0 ? 'up' : 'down'">
              {{ fmtPct(opt.val) }}
            </span>
            <span class="opt-count">{{ opt.count }} 只</span>
          </el-option>
        </el-select>
        <el-button v-if="currentIndustry" size="small" @click="backToIndustry">← 全部行业</el-button>
        <el-button size="small" :loading="loading" @click="load(currentIndustry)">刷新</el-button>
      </div>
    </div>

    <!-- 概览随视图联动：下钻进某个行业后，这一行统计的就是该行业 -->
    <div v-if="ov" class="overview">
      <b>{{ currentIndustry || '全市场' }}</b>
      <span class="up">涨 {{ ov.up }}</span>
      <span class="down">跌 {{ ov.down }}</span>
      <span class="muted">平 {{ ov.flat }}</span>
      <span class="sep">·</span>
      <span>成交 {{ ov.amount_yi }} 亿</span>
      <span v-if="ov.amount_vs_prev != null" :class="ov.amount_vs_prev >= 1 ? 'up' : 'down'">
        较昨日全天 {{ (ov.amount_vs_prev * 100).toFixed(0) }}%
      </span>
      <span v-if="ov.amount_vs_prev != null" class="muted">（盘中未走完时天然小于 100%）</span>
      <!-- 操作提示并进这一行，不再单占一行：省下的高度全给瓦片 -->
      <span class="hint">
        {{ currentIndustry
          ? '点个股进深研 · 右上角下拉可直接换板块'
          : '点个股进深研 · 点行业标题条「›」进该板块热力图' }}
      </span>
    </div>

    <div v-loading="loading" class="chart-wrap">
      <div ref="chartEl" class="chart" />
      <el-empty v-if="!loading && !items.length" description="暂无数据：请先在数据中心同步行情" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onActivated, onBeforeUnmount, onMounted, ref } from 'vue'
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

const ov = computed(() => data.value?.overview || null)
// 全市场视图才是两层嵌套；进了板块就是一层个股
const nested = computed(() => items.value.some(it => (it.children || []).length > 0))

// 行业清单单独留一份：下钻后 items 变成个股了，下拉框还得能在板块之间横跳
const industryList = ref<HeatmapItem[]>([])
const industryOptions = computed(() =>
  industryList.value
    .map(it => ({ name: it.name, count: it.count || 0, val: valOf(it) }))
    .sort((a, b) => (b.val ?? -999) - (a.val ?? -999)))

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
const flatRgb = [58, 63, 75]
const pctColor = (pct: number | null | undefined) => {
  if (pct == null) return 'rgb(72,76,86)'
  const t = Math.max(-1, Math.min(1, pct / periodCap()))
  const mix = (a: number[], b: number[], k: number) =>
    `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * k)).join(',')})`
  return t >= 0 ? mix(flatRgb, [224, 64, 44], t) : mix(flatRgb, [30, 158, 99], -t)
}
const fmtPct = (v: number | null | undefined) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v}%`)

// 瓦片够大才写字：小格子上挤名字会糊成一片噪点，反而看不清颜色分布
const labelIfRoomy = {
  show: true,
  overflow: 'truncate' as const,
  formatter: (p: any) => (p.data?.symbol ? `${p.data.name}\n${fmtPct(valOf(p.data))}` : p.data?.name),
}

const tileData = () => items.value.map((it) => {
  const kids = (it.children || []).map(k => ({
    ...k, itemStyle: { color: pctColor(valOf(k)) },
  }))
  return kids.length
    // 行业块本身不着色：它的颜色由内部个股自然呈现，父块再上色会盖住子块
    ? { ...it, children: kids, itemStyle: { color: 'transparent' } }
    : { ...it, itemStyle: { color: pctColor(valOf(it)) } }
})

const render = () => {
  if (!chartEl.value) return
  if (!chart) {
    chart = echarts.init(chartEl.value)
    chart.on('click', (params: any) => {
      const it = params?.data as HeatmapItem | undefined
      if (!it) return
      // 点个股进深研；点行业块（无 symbol）只看该行业
      if (it.symbol) router.push({ path: '/stock-analysis', query: { symbol: it.symbol } })
      else if (!currentIndustry.value && it.name) load(it.name)
    })
  }
  chart.setOption({
    tooltip: {
      formatter: (p: any) => {
        const it = p?.data as HeatmapItem
        if (!it) return ''
        const head = it.symbol
          ? `${it.name} ${it.symbol}`
          : `${it.name}（${it.count} 只${it.shown && it.shown < (it.count || 0) ? `，图上画前 ${it.shown} 只` : ''}）`
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
      // 两层一起画：行业标题条 + 内部个股瓦片。leafDepth 不设，否则 ECharts
      // 只画到第一层、双击才展开，那就退回成原来的扁平图了。
      //
      // 标题条是进板块热力图的唯一图上入口，做到 26px 并带上涨跌幅和「›」：
      // 原来 18px 的窄边既看不出可点、也很难点中，等于这个功能不存在。
      upperLabel: {
        show: true, height: 26, fontSize: 12, color: '#e8ecf3',
        formatter: (p: any) => (p.data?.children?.length
          ? `${p.data.name}  ${fmtPct(valOf(p.data))}  ›`
          : p.data?.name),
      },
      label: labelIfRoomy,
      itemStyle: { borderColor: '#12141a', borderWidth: 1, gapWidth: 1 },
      // levels[0] 是**根节点**，不是行业层 —— 配错这一格，根节点就会顶着一条
      // 「undefined ›」的标题条白占 26px（2026-08-26 线上出现过）。
      // 行业在 levels[1]、个股在 levels[2]；板块视图里个股升到第 1 层，
      // 所以那时只给两级配置，否则个股会套上行业层的粗边框。
      levels: nested.value
        ? [
            { upperLabel: { show: false }, itemStyle: { borderWidth: 0, gapWidth: 0 } },
            { upperLabel: { show: true },
              itemStyle: { borderColor: '#0d0f14', borderWidth: 3, gapWidth: 3 } },
            { itemStyle: { borderColor: '#12141a', borderWidth: 1, gapWidth: 1 },
              label: { fontSize: 11 } },
          ]
        : [
            { upperLabel: { show: false }, itemStyle: { borderWidth: 0, gapWidth: 0 } },
            { itemStyle: { borderColor: '#12141a', borderWidth: 1, gapWidth: 1 } },
          ],
      data: tileData(),
    }],
  }, true)
}

const load = async (industry = '') => {
  loading.value = true
  try {
    const res = await heatmapApi.fetch(
      industry ? 'stock' : 'industry', industry || undefined, !industry)
    if (!res) return
    data.value = res
    items.value = res.items || []
    if (!industry) industryList.value = res.items || []
    currentIndustry.value = industry
    render()
  } finally {
    loading.value = false
  }
}

const backToIndustry = () => load('')

const onResize = () => chart?.resize()

// 容器自己变矮时也要重算：概览行（v-if="ov"）和顶部 MacroBar 都是数据回来后才出现的，
// 而 echarts.init 早在那之前就按更高的容器把画布尺寸定死了。少了这一步，
// 多出来的那截画布会被 .heatmap-page 的 overflow:hidden 剪掉，且不计入滚动高度——
// 用户拉到底也看不见最后一两行瓦片（2026-08-27 反馈）。
let ro: ResizeObserver | null = null

onMounted(() => {
  load()
  window.addEventListener('resize', onResize)
  if (chartEl.value) {
    // keep-alive 切走时容器会脱离文档、尺寸变 0，此时 resize 会把画布压没，
    // 回来再 resize 才恢复——直接跳过零尺寸即可。
    ro = new ResizeObserver(([e]) => {
      const box = e.contentRect
      if (box.width > 0 && box.height > 0) chart?.resize()
    })
    ro.observe(chartEl.value)
  }
})
// keep-alive：从详情页返回时组件被重新激活，treemap 若在隐藏期间容器尺寸变过需重算
onActivated(() => nextTick(() => chart?.resize()))
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  ro?.disconnect()
  ro = null
  chart?.dispose()
  chart = null
})
</script>

<style scoped lang="scss">
/* 整页不出滚动条：外层吃掉 el-main 的剩余空间，中间那块图再吃掉本页的剩余空间。
   这里必须是 flex:1 而不是 height:100% —— el-main 里还有一条 MacroBar，
   100% 会把它的高度重复占一遍，整页多出一截滚动条。
   min-height 一旦写死（原来是 520px），窗口矮一点就把页面撑出滚动条，
   而这一页的全部价值就是「一屏看完全市场」。 */
.heatmap-page { display: flex; flex-direction: column; flex: 1; min-height: 0; overflow: hidden; }
.page-head {
  display: flex; align-items: baseline; flex-wrap: nowrap; gap: 10px; margin-bottom: 6px;

  h2 { margin: 0; font-size: 20px; flex-shrink: 0; }
  .sub {
    margin: 0; font-size: 12px; color: var(--el-text-color-secondary);
    min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .actions { margin-left: auto; flex-shrink: 0; }
}
.actions { display: flex; align-items: center; gap: 8px; }
.industry-jump { width: 190px; }
.overview {
  display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
  padding: 5px 12px; margin-bottom: 6px; font-size: 12px; flex-shrink: 0;
  border: 1px solid var(--el-border-color-lighter); border-radius: 8px;

  b { font-size: 13px; }
  .up { color: #e0402c; font-weight: 600; }
  .down { color: #1e9e63; font-weight: 600; }
  .muted { color: var(--el-text-color-placeholder); }
  .sep { color: var(--el-text-color-placeholder); }
  .hint { margin-left: auto; color: var(--el-text-color-placeholder); }
}
/* 下拉里的涨跌幅与只数右对齐，扫一眼就能挑最强的板块进去 */
.opt-pct { float: right; margin-left: 12px; font-variant-numeric: tabular-nums;
  &.up { color: #e0402c; }
  &.down { color: #1e9e63; }
}
.opt-count { float: right; margin-left: 12px; color: var(--el-text-color-placeholder); font-size: 12px; }
.chart-wrap { position: relative; flex: 1; min-height: 0; }
.chart { width: 100%; height: 100%; }
.cov-note { color: var(--el-text-color-placeholder); }
</style>
