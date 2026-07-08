<template>
  <div class="arena-page">
    <div class="page-head">
      <div>
        <h2>AI 擂台</h2>
        <p class="sub">5 个 AI 人格各管 100 万虚拟盘，每交易日 15:40 自动调仓结算 · 虚拟资金，仅供观察风格差异</p>
      </div>
      <el-button size="small" :loading="running" @click="runNow">手动结算一次</el-button>
    </div>

    <el-table v-loading="loading" :data="board" size="small" @row-click="openDetail">
      <el-table-column label="#" type="index" width="46" />
      <el-table-column prop="persona" label="人格" width="100" />
      <el-table-column label="净值" width="130">
        <template #default="{ row }"><b>{{ (row.nav / 10000).toFixed(2) }} 万</b></template>
      </el-table-column>
      <el-table-column label="总收益" width="110">
        <template #default="{ row }">
          <span :class="row.return_pct > 0 ? 'up' : row.return_pct < 0 ? 'down' : ''">
            {{ row.return_pct > 0 ? '+' : '' }}{{ row.return_pct }}%
          </span>
        </template>
      </el-table-column>
      <el-table-column prop="positions" label="持仓数" width="80" />
      <el-table-column prop="days" label="结算天数" width="90" />
      <el-table-column prop="comment" label="今日判词" min-width="320" show-overflow-tooltip />
    </el-table>

    <div ref="chartEl" class="nav-chart" />

    <el-drawer v-model="detailVisible" :title="`${detailPersona} · 持仓与交易`" size="46%">
      <template v-if="detail">
        <p class="cash-line">现金 {{ (detail.cash / 10000).toFixed(2) }} 万</p>
        <h4>持仓</h4>
        <el-table :data="detail.positions" size="small">
          <el-table-column prop="symbol" label="代码" width="90" />
          <el-table-column prop="name" label="名称" width="110" />
          <el-table-column prop="shares" label="股数" width="90" />
          <el-table-column label="成本/现价" width="130">
            <template #default="{ row }">{{ row.avg_cost.toFixed(2) }} / {{ row.price.toFixed(2) }}</template>
          </el-table-column>
          <el-table-column label="浮盈" width="90">
            <template #default="{ row }">
              <span :class="row.pnl_pct > 0 ? 'up' : row.pnl_pct < 0 ? 'down' : ''">{{ row.pnl_pct > 0 ? '+' : '' }}{{ row.pnl_pct }}%</span>
            </template>
          </el-table-column>
        </el-table>
        <h4>交易历史</h4>
        <el-table :data="detail.trades" size="small" max-height="360">
          <el-table-column prop="date" label="日期" width="100" />
          <el-table-column label="方向" width="70">
            <template #default="{ row }">
              <el-tag size="small" :type="row.side === 'buy' ? 'danger' : 'success'">{{ row.side === 'buy' ? '买入' : '卖出' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="symbol" label="代码" width="90" />
          <el-table-column label="价格/股数" width="120">
            <template #default="{ row }">{{ row.price.toFixed(2) }} × {{ row.shares }}</template>
          </el-table-column>
          <el-table-column prop="reason" label="理由" min-width="200" show-overflow-tooltip />
        </el-table>
      </template>
    </el-drawer>
    <p class="hint">AI 模拟盘，非投资建议；点击行查看持仓与判词。</p>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { echarts, type ECharts } from '@/utils/echarts'
import { arenaApi, type ArenaBoardRow, type ArenaNavPoint } from '@/api/quant'

const loading = ref(false)
const running = ref(false)
const board = ref<ArenaBoardRow[]>([])
const series = ref<Record<string, ArenaNavPoint[]>>({})
const detailVisible = ref(false)
const detailPersona = ref('')
const detail = ref<Awaited<ReturnType<typeof arenaApi.detail>>>(null)
const chartEl = ref<HTMLDivElement>()
let chart: ECharts | null = null

const renderChart = () => {
  if (!chartEl.value) return
  const names = Object.keys(series.value)
  if (!names.length) return
  if (!chart) chart = echarts.init(chartEl.value)
  const dates = [...new Set(names.flatMap(n => series.value[n].map(p => p.date)))].sort()
  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { top: 0 },
    grid: { left: 60, right: 20, top: 30, bottom: 24 },
    xAxis: { type: 'category', data: dates },
    yAxis: { type: 'value', scale: true, axisLabel: { formatter: (v: number) => `${(v / 10000).toFixed(0)}万` } },
    series: names.map(n => ({
      name: n, type: 'line', showSymbol: false,
      data: dates.map(d => series.value[n].find(p => p.date === d)?.nav ?? null),
      connectNulls: true,
    })),
  }, true)
}

const load = async () => {
  loading.value = true
  try {
    const res = await arenaApi.board()
    if (!res) return
    board.value = res.board || []
    series.value = res.series || {}
    renderChart()
  } finally {
    loading.value = false
  }
}

const runNow = async () => {
  running.value = true
  try {
    await arenaApi.run()
    ElMessage.success('已触发结算（当日已结算的人格自动跳过）')
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '触发失败')
  } finally {
    running.value = false
  }
}

const openDetail = async (row: ArenaBoardRow) => {
  detailPersona.value = row.persona
  detail.value = await arenaApi.detail(row.persona)
  detailVisible.value = true
}

const onResize = () => chart?.resize()
onMounted(() => { load(); window.addEventListener('resize', onResize) })
onBeforeUnmount(() => { window.removeEventListener('resize', onResize); chart?.dispose(); chart = null })
</script>

<style scoped lang="scss">
.page-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 10px;
  h2 { margin: 0; font-size: 20px; }
  .sub { margin: 4px 0 0; font-size: 12px; color: var(--el-text-color-secondary); }
}
.up { color: #e0402c; }
.down { color: #1e9e63; }
.nav-chart { height: 320px; margin-top: 14px; }
.cash-line { margin: 0 0 8px; font-size: 13px; }
h4 { margin: 12px 0 6px; }
.hint { margin: 8px 0 0; font-size: 12px; color: var(--el-text-color-placeholder); }
</style>
