<template>
  <div class="portfolio-page">
    <header class="page-head">
      <div>
        <h1>模拟组合</h1>
        <p>从选股列表一键加入，按真实价格跟踪盈亏（含 A 股交易成本），持仓触发卖出信号会在这里提示。</p>
      </div>
      <el-button :loading="loading" @click="loadAll">刷新</el-button>
    </header>

    <el-alert v-if="error" :title="error" type="warning" show-icon :closable="false" />

    <section v-if="summary" class="summary-grid">
      <div class="sum-card">
        <span>持仓市值</span>
        <b>{{ fmtMoney(summary.market_value) }}</b>
        <small class="muted">{{ summary.open_count }} 只持仓</small>
      </div>
      <div class="sum-card">
        <span>浮动盈亏</span>
        <b :class="pnlClass(summary.unrealized_pnl)">{{ fmtMoney(summary.unrealized_pnl) }}</b>
        <small :class="pnlClass(summary.unrealized_pnl_pct)">{{ fmtPct(summary.unrealized_pnl_pct) }}</small>
      </div>
      <div class="sum-card">
        <span>已实现盈亏</span>
        <b :class="pnlClass(summary.realized_pnl)">{{ fmtMoney(summary.realized_pnl) }}</b>
        <small class="muted">{{ summary.closed_count }} 笔已卖出</small>
      </div>
      <div class="sum-card">
        <span>卖出胜率</span>
        <b>{{ summary.closed_win_rate == null ? '—' : `${(summary.closed_win_rate * 100).toFixed(0)}%` }}</b>
        <small class="muted">盈利卖出占比</small>
      </div>
    </section>

    <section v-if="nav.length > 1" class="panel">
      <h2>组合收益 vs 全市场中位</h2>
      <div ref="navChartEl" class="nav-chart" />
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>当前持仓</h2>
      </div>
      <el-table v-if="openItems.length" :data="openItems" size="small" stripe>
        <el-table-column label="股票" min-width="130">
          <template #default="{ row }">{{ row.name }} <small class="muted">{{ row.symbol }}</small></template>
        </el-table-column>
        <el-table-column prop="buy_date" label="买入日" width="100" />
        <el-table-column label="成本" width="90">
          <template #default="{ row }">{{ row.cost?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="现价" width="80">
          <template #default="{ row }">{{ row.price?.toFixed(2) ?? '—' }}</template>
        </el-table-column>
        <el-table-column prop="shares" label="股数" width="80" />
        <el-table-column label="浮动盈亏" width="130">
          <template #default="{ row }">
            <span :class="pnlClass(row.pnl)">{{ fmtMoney(row.pnl) }} ({{ fmtPct(row.pnl_pct) }})</span>
          </template>
        </el-table-column>
        <el-table-column label="卖出信号" min-width="220">
          <template #default="{ row }">
            <template v-if="row.signals?.length">
              <el-tooltip v-for="s in row.signals" :key="s.key" :content="s.detail" placement="top">
                <el-tag type="danger" size="small" effect="dark" class="sig-tag">{{ s.label }}</el-tag>
              </el-tooltip>
            </template>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="source" label="来源" width="90" />
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-popconfirm title="按当前价卖出？" confirm-button-text="卖出" cancel-button-text="再想想" @confirm="sell(row)">
              <template #reference>
                <el-button link type="danger" size="small">卖出</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else-if="!loading" description="还没有持仓。到「智能选股」页选中意的票，点「+组合」加入。" :image-size="90" />
    </section>

    <section v-if="closedItems.length" class="panel">
      <h2>历史卖出</h2>
      <el-table :data="closedItems" size="small" stripe max-height="360">
        <el-table-column label="股票" min-width="130">
          <template #default="{ row }">{{ row.name }} <small class="muted">{{ row.symbol }}</small></template>
        </el-table-column>
        <el-table-column prop="buy_date" label="买入日" width="100" />
        <el-table-column prop="sell_date" label="卖出日" width="100" />
        <el-table-column label="成本→卖价" width="130">
          <template #default="{ row }">{{ row.cost?.toFixed(2) }} → {{ row.sell_price?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="盈亏" width="130">
          <template #default="{ row }">
            <span :class="pnlClass(row.pnl)">{{ fmtMoney(row.pnl) }} ({{ fmtPct(row.pnl_pct) }})</span>
          </template>
        </el-table-column>
        <el-table-column prop="sell_reason" label="原因" width="100" />
      </el-table>
    </section>

    <p class="foot-note">
      模拟组合按每笔固定预算整手成交，计入佣金与印花税；价格为实时行情或最新收盘。
      卖出信号为规则化提示（跌破 MA20 / 止损 -8% / 持有超 10 交易日未盈利），不构成投资建议。
    </p>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { portfolioApi, type PortfolioNavPoint, type PortfolioPosition, type PortfolioSummary } from '@/api/quant'
import { echarts, type ECharts } from '@/utils/echarts'

const loading = ref(false)
const error = ref('')
const openItems = ref<PortfolioPosition[]>([])
const closedItems = ref<PortfolioPosition[]>([])
const summary = ref<PortfolioSummary | null>(null)
const nav = ref<PortfolioNavPoint[]>([])
const navChartEl = ref<HTMLElement | null>(null)
let navChart: ECharts | null = null

const fmtMoney = (v: number | null | undefined) =>
  v == null ? '—' : `${v >= 0 ? '' : '-'}¥${Math.abs(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`
const fmtPct = (v: number | null | undefined) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`)
const pnlClass = (v: number | null | undefined) => (v == null ? 'muted' : v > 0 ? 'up' : v < 0 ? 'down' : 'muted')

const renderNav = async () => {
  await nextTick()
  if (!navChartEl.value || nav.value.length < 2) return
  if (!navChart) navChart = echarts.init(navChartEl.value)
  navChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { top: 0 },
    grid: { left: 48, right: 16, top: 30, bottom: 24 },
    xAxis: { type: 'time' },
    yAxis: { type: 'value', name: '收益率(%)' },
    series: [
      { name: '组合', type: 'line', showSymbol: false, data: nav.value.map((p) => [p.date, p.pnl_pct]) },
      { name: '全市场中位', type: 'line', showSymbol: false, lineStyle: { type: 'dashed' }, data: nav.value.map((p) => [p.date, p.bench_cum_pct]) },
    ],
  })
}

const loadAll = async () => {
  loading.value = true
  error.value = ''
  try {
    const [res, navRes] = await Promise.all([portfolioApi.list(), portfolioApi.nav()])
    openItems.value = res?.open || []
    closedItems.value = res?.closed || []
    summary.value = res?.summary || null
    nav.value = navRes || []
    renderNav()
  } catch (e: any) {
    error.value = e?.message || '加载组合失败'
  } finally {
    loading.value = false
  }
}

const sell = async (row: PortfolioPosition) => {
  try {
    const res = await portfolioApi.sell(row.id)
    ElMessage.success(`已卖出 ${row.name}，盈亏 ${fmtMoney(res?.pnl)}`)
    loadAll()
  } catch (e: any) {
    ElMessage.error(e?.message || '卖出失败')
  }
}

onMounted(loadAll)
onBeforeUnmount(() => navChart?.dispose())
</script>

<style scoped lang="scss">
.portfolio-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;

  h1 { margin: 0 0 4px; font-size: 22px; }
  p { margin: 0; color: var(--el-text-color-secondary); font-size: 13px; }
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}

.sum-card {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  padding: 14px 16px;

  span { display: block; font-size: 12px; color: var(--el-text-color-secondary); }
  b { display: block; font-size: 20px; margin: 4px 0 2px; }
  small { font-size: 12px; }
}

.panel {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  padding: 14px 16px;

  h2 { margin: 0 0 10px; font-size: 16px; }
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.nav-chart { height: 260px; }

.sig-tag { margin-right: 6px; }

.foot-note {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.up { color: #ef232a; }
.down { color: #14b143; }
.muted { color: var(--el-text-color-secondary); }
</style>
