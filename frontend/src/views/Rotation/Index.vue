<template>
  <div class="rotation-page">
    <div class="page-head">
      <h2>板块轮动</h2>
      <p class="sub">
        横轴=近 {{ data?.norm_window || 60 }} 日累计超额（中期强弱）· 纵轴=近
        {{ data?.mom_window || 20 }} 日累计超额（近期强弱）· 尾巴=最近八周走位
        <template v-if="data?.as_of"> · {{ data.as_of }}</template>
      </p>
      <div class="actions">
        <FreshnessChip :f="data?.freshness" />
        <el-checkbox v-model="showAllTails" size="small">显示全部轨迹</el-checkbox>
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <!-- 这句必须留在图上方：轮动图最容易被读成「涨幅图」，而它画的是相对位置。
         普涨日里所有点几乎不动——那不是没人涨，是它们相对彼此没拉开差距。 -->
    <div class="caveat">
      坐标是<b>相对</b>量，不能当收益读：全板块同步涨跌时点的位置几乎不变。
      要看赚了多少，看右侧「20 日超额」那一列。
    </div>

    <div v-if="data && !data.ready" class="empty">{{ data.message || '暂不可用' }}</div>

    <div v-else class="body">
      <div ref="chartEl" v-loading="loading" class="chart" />

      <div class="side">
        <div class="side-head">
          按 20 日强度排序
          <span class="hint">前 20% 这一档是审计里唯一站得住的信号</span>
        </div>
        <div
          v-for="it in ranked"
          :key="it.industry"
          class="row"
          :class="{ hot: it.sector_hot, active: it.industry === focus }"
          @mouseenter="focus = it.industry"
          @mouseleave="focus = ''"
          @click="goHeatmap(it.industry)"
        >
          <span class="nm">{{ it.industry }}</span>
          <span class="q" :class="qClass(it.quadrant)">{{ it.quadrant }}</span>
          <span class="mom" :class="it.mom20 >= 0 ? 'up' : 'down'">
            {{ it.mom20 > 0 ? '+' : '' }}{{ it.mom20 }}%
          </span>
          <span class="pct">{{ Math.round(it.mom20_pct * 100) }}%</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { echarts, type ECharts } from '@/utils/echarts'
import { ApiClient } from '@/api/request'
import FreshnessChip, { type Freshness } from '@/components/FreshnessChip.vue'

interface TailPoint { date: string; x: number; y: number }
interface RotationItem {
  industry: string
  rs_ratio: number
  rs_momentum: number
  quadrant: string
  mom20: number
  mom20_pct: number
  sector_hot: boolean
  members: number
  tail: TailPoint[]
}
interface Rotation {
  as_of: string
  ready: boolean
  message?: string
  computing?: boolean
  mom_window: number
  norm_window: number
  items: RotationItem[]
  freshness: Freshness
}

const router = useRouter()
const data = ref<Rotation | null>(null)
const loading = ref(false)
// 后端首次算全市场聚合要十几秒，期间返回 computing。自动重试一次而不是让用户
// 盯着「稍后刷新」自己猜时机——这类等待应该由系统承担，不是交给用户。
let retryTimer: ReturnType<typeof setTimeout> | undefined

const focus = ref('')
// 轨迹默认全关。二十多条八点折线叠在一起是毛线团，既读不出轮动方向，还会把坐标轴
// 撑到远超点云的范围，反过来把散点压成一小坨。轨迹是「选中某个板块后看它怎么走过来」
// 的下钻手段，不是默认图层——鼠标停在右侧任一行即可单独浮出那一条。
const showAllTails = ref(false)
const chartEl = ref<HTMLDivElement>()
let chart: ECharts | null = null

const ranked = computed(() => data.value?.items || [])

// 象限配色沿用全站红涨绿跌的语义。「改善」给暖色而不是中性色：它是资金正在进的方向，
// 也是这张图相对热力图唯一多出来的信息——只看当日涨幅是看不出「刚开始转强」的。
const Q_COLOR: Record<string, string> = {
  领先: '#e0402c', 改善: '#e08a2c', 走弱: '#4a7ba8', 落后: '#1e9e63',
}
const qClass = (q: string) => (q === '领先' || q === '改善' ? 'warm' : 'cool')
const goHeatmap = (industry: string) => router.push({ path: '/heatmap', query: { industry } })

const load = async () => {
  loading.value = true
  try {
    data.value = await ApiClient.get('/api/lite/sector-rotation')
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

  const wantTail = (it: RotationItem) =>
    it.industry === focus.value || showAllTails.value

  const tailSeries = d.items.filter(wantTail).map((it) => ({
    type: 'line' as const,
    name: it.industry,
    silent: true,
    showSymbol: false,
    lineStyle: {
      width: it.industry === focus.value ? 2.5 : 1,
      opacity: it.industry === focus.value ? 0.95 : 0.3,
      color: Q_COLOR[it.quadrant] || '#888',
    },
    data: it.tail.map(p => [p.x, p.y]),
    z: it.industry === focus.value ? 5 : 2,
  }))

  chart.setOption({
    grid: { left: 60, right: 24, top: 24, bottom: 44 },
    xAxis: {
      // scale:true 是这张图能不能读的关键。默认从 0 起算，而坐标全部落在 100 附近，
      // 于是所有点挤成右上角一小坨、八成画布空着，象限分割线（100）也会跑到边上，
      // 四象限在视觉上根本不成立。
      scale: true,
      name: '中期强度 →',
      nameLocation: 'middle',
      nameGap: 24,
      nameTextStyle: { color: '#6f7889' },
      splitLine: { lineStyle: { color: '#23262e' } },
      axisLabel: { color: '#8b93a3' },
    },
    yAxis: {
      scale: true,
      name: '近期强度 →',
      nameLocation: 'middle',
      nameGap: 40,
      nameTextStyle: { color: '#6f7889' },
      splitLine: { lineStyle: { color: '#23262e' } },
      axisLabel: { color: '#8b93a3' },
    },
    tooltip: {
      formatter: (p: any) => {
        const it = p?.data?.raw as RotationItem | undefined
        if (!it) return ''
        const hot = it.sector_hot
          ? '<span style="color:#e0402c">命中强板块档（前 20%）</span>'
          : ''
        return `<b>${it.industry}</b>（${it.members} 只）<br/>${it.quadrant}<br/>` +
          `20 日超额 ${it.mom20 > 0 ? '+' : ''}${it.mom20}% · 全市场第 ` +
          `${Math.round(it.mom20_pct * 100)} 分位<br/>${hot}`
      },
    },
    series: [
      ...tailSeries,
      {
        type: 'scatter',
        symbolSize: (v: any) => (v[2] ? 13 : 8),
        data: d.items.map(it => ({
          name: it.industry,
          value: [it.rs_ratio, it.rs_momentum, it.sector_hot ? 1 : 0],
          raw: it,
          itemStyle: {
            color: Q_COLOR[it.quadrant] || '#888',
            borderColor: it.sector_hot ? '#fff' : 'transparent',
            borderWidth: it.sector_hot ? 1.5 : 0,
            opacity: focus.value && it.industry !== focus.value ? 0.3 : 0.9,
          },
        })),
        // 只给强板块写名字：全标会互相压字，而冷门板块的名字本来也不是这张图的重点。
        // 但强板块本身也会挤在同一片区域，所以还要 hideOverlap 再挡一层——宁可少显示
        // 几个名字，也不要一堆叠在一起谁都读不出来。
        label: {
          show: true,
          position: 'right',
          fontSize: 10,
          color: '#c7cede',
          formatter: (p: any) => (p.data?.raw?.sector_hot ? p.data.name : ''),
        },
        labelLayout: { hideOverlap: true },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: '#4a4f5c', type: 'dashed' },
          label: { show: false },
          data: [{ xAxis: 100 }, { yAxis: 100 }],
        },
        z: 10,
      },
    ],
  }, true)
}

watch([focus, showAllTails], render)
const onResize = () => chart?.resize()
onMounted(() => {
  load()
  window.addEventListener('resize', onResize)
})
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
.rotation-page { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.page-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.page-head h2 { margin: 0; font-size: 18px; }
.sub { margin: 0; color: #8b93a3; font-size: 12px; }
.actions { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.caveat { margin: 8px 0; font-size: 12px; color: #9aa3b5; }
.empty { padding: 40px; text-align: center; color: #8b93a3; }
.body { flex: 1; min-height: 0; display: flex; gap: 12px; }
.chart { flex: 1; min-width: 0; background: #12141a; border-radius: 6px; }
.side { width: 264px; overflow-y: auto; background: #12141a; border-radius: 6px; padding: 8px; }
.side-head { font-size: 12px; color: #c7cede; padding: 4px 6px 8px; }
.hint { display: block; color: #6f7889; font-size: 11px; margin-top: 2px; }
.row { display: flex; align-items: center; gap: 6px; padding: 5px 6px; border-radius: 4px; cursor: pointer; font-size: 12px; }
.row:hover, .row.active { background: #1b1e26; }
.row.hot .nm { color: #fff; font-weight: 600; }
.nm { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #b9c1d1; }
.q { font-size: 11px; }
.q.warm { color: #e0802c; }
.q.cool { color: #5b8fb5; }
.mom { width: 58px; text-align: right; }
.pct { width: 36px; text-align: right; color: #6f7889; }
.up { color: #e0402c; }
.down { color: #1e9e63; }
</style>
