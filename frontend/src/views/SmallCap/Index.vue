<template>
  <div class="smallcap-page">
    <div class="page-head">
      <h2>小市值周频组合</h2>
      <p class="sub">每周一开盘换仓，持有中小板里成交最平稳的 5 只，拿满一周</p>
      <div class="actions">
        <span v-if="data?.as_of" class="dim">日线截至 {{ data.as_of }}</span>
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <!-- 风险必须在名单上方：这是一个回撤 −33% 的组合，不是「买了就涨」的推荐。
         数字来自 experiments/alpha70_replica.py 的 2020-09~2026-09 复刻。 -->
    <el-alert type="warning" :closable="false" show-icon class="risk">
      <template #title>
        先看风险：历史最大回撤 <b>−33%</b>，最差的一周 <b>−13%</b>（2024 年 1 月微盘股踩踏）
      </template>
      <div class="risk-body">
        它吃的是 A 股「小市值 / 冷门股」溢价：单只可能连跌数周，要按组合整体看、用小仓位。
        下面的历史成绩只统计了<b>至今仍在上市</b>的股票，期间退市的没算进去，真实收益会比这个低。
        不适合「买了当天就要涨」的用法。
      </div>
    </el-alert>

    <section class="panel">
      <div class="panel-head">
        <h3>本周组合</h3>
        <span v-if="data?.week_start" class="dim">{{ data.week_start }} 开盘建仓 · 下周一开盘换仓</span>
      </div>
      <el-table v-if="data?.items.length" :data="data.items" size="small" v-loading="loading">
        <el-table-column label="股票" min-width="130">
          <template #default="{ row }">
            <a class="stk" @click="openStock(row.symbol)">{{ row.name }}</a>
            <span class="dim code">{{ row.symbol }}</span>
            <el-tag v-if="row.kept_limit_up" size="small" type="danger" effect="plain" title="上周持有、换仓前一日涨停，按规则本周继续持有">涨停续持</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="周一开盘价" width="110" align="right">
          <template #default="{ row }">{{ row.entry_price.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="现价" width="100" align="right">
          <template #default="{ row }">{{ row.price.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="本周以来" width="110" align="right">
          <template #default="{ row }">
            <b :class="tone(row.since_entry_pct)">{{ pct(row.since_entry_pct) }}</b>
          </template>
        </el-table-column>
        <el-table-column label="近 6 日成交额波动" width="150" align="right">
          <template #default="{ row }"><span class="dim">{{ row.amount_std_wan.toFixed(0) }} 万</span></template>
        </el-table-column>
      </el-table>
      <el-empty v-else-if="!loading" description="本周组合还没算出来（需要至少 6 个完整交易日的日线）" :image-size="70" />
      <p v-if="weekAvg != null" class="week-avg">
        组合本周以来平均 <b :class="tone(weekAvg)">{{ pct(weekAvg) }}</b>，每只等额买入即为这个收益
      </p>
    </section>

    <section v-if="data?.summary" class="panel">
      <div class="panel-head">
        <h3>最近 {{ data.summary.weeks }} 周</h3>
        <span class="dim">按同一规则逐周回算，周一开盘买、下周一开盘卖，未扣手续费</span>
      </div>
      <div class="kpis">
        <div class="kpi"><span>组合累计</span><b :class="tone(data.summary.cum_ret)">{{ pct(data.summary.cum_ret, 1) }}</b></div>
        <div class="kpi"><span>随便买中小板</span><b :class="tone(data.summary.pool_cum_ret)">{{ pct(data.summary.pool_cum_ret, 1) }}</b></div>
        <div class="kpi"><span>跑赢的周</span><b>{{ Math.round(data.summary.win_vs_pool * 100) }}%</b></div>
        <div class="kpi"><span>期间最大回撤</span><b class="down">{{ data.summary.max_drawdown.toFixed(1) }}%</b></div>
      </div>
      <div class="weeks">
        <div v-for="w in data.history" :key="w.week_start" class="week-row">
          <span class="dim wk">{{ w.week_start.slice(5) }}</span>
          <div class="bar-track">
            <div class="bar" :class="(w.ret ?? 0) >= 0 ? 'bar-up' : 'bar-down'"
                 :style="barStyle(w.ret ?? 0)" />
          </div>
          <b class="wk-ret" :class="tone(w.ret)">{{ pct(w.ret) }}</b>
          <span class="dim wk-pool">中小板 {{ pct(w.pool_ret) }}</span>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h3>更长的历史（2020-09 起）</h3>
        <span class="dim">同一规则在本地日线上的复刻，已扣双边 0.13% 交易成本</span>
      </div>
      <el-table :data="YEARS" size="small" class="years">
        <el-table-column prop="year" label="年份" width="120" />
        <el-table-column label="组合" align="right">
          <template #default="{ row }"><b :class="tone(row.ret)">{{ pct(row.ret, 1) }}</b></template>
        </el-table-column>
        <el-table-column label="随便买中小板" align="right">
          <template #default="{ row }"><span :class="tone(row.pool)">{{ pct(row.pool, 1) }}</span></template>
        </el-table-column>
      </el-table>
      <p class="dim foot">
        7 个年份里 5 年跑赢；2021 年和 2024 年跑输。规则源自 PTrade 社区的 Alpha70 策略，
        选股逻辑与其实盘成交逐只核对一致。仅供研究参考，不构成投资建议。
      </p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { quantApi, type SmallcapResult } from '@/api/quant'

// 来自 experiments/alpha70_replica.py（2026-09-28 跑，存活偏差未扣除，见上方风险提示）
const YEARS = [
  { year: '2020（9 月起）', ret: 4.3, pool: -3.5 },
  { year: '2021', ret: 15.6, pool: 29.5 },
  { year: '2022', ret: 46.1, pool: -2.4 },
  { year: '2023', ret: 38.1, pool: 6.6 },
  { year: '2024', ret: -8.7, pool: -1.9 },
  { year: '2025', ret: 77.2, pool: 48.8 },
  { year: '2026（至 9 月）', ret: 16.6, pool: -2.8 },
]

const router = useRouter()
const data = ref<SmallcapResult | null>(null)
const loading = ref(false)

const load = async () => {
  loading.value = true
  try {
    data.value = await quantApi.smallcap()
  } finally {
    loading.value = false
  }
}
onMounted(load)

const weekAvg = computed(() => {
  const vals = (data.value?.items || []).map((it) => it.since_entry_pct).filter((v): v is number => v != null)
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null
})
const maxAbs = computed(() => Math.max(3, ...(data.value?.history || []).map((w) => Math.abs(w.ret ?? 0))))
const barStyle = (v: number) => ({ width: `${(Math.abs(v) / maxAbs.value) * 50}%`, [v >= 0 ? 'left' : 'right']: '50%' })

const pct = (v: number | null | undefined, digits = 2) =>
  v == null ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(digits)}%`
const tone = (v: number | null | undefined) => (v == null || v === 0 ? '' : v > 0 ? 'up' : 'down')
const openStock = (symbol: string) => router.push({ name: 'stock-analysis', query: { symbol } })
</script>

<style scoped>
.smallcap-page { display: flex; flex-direction: column; gap: 12px; }
.page-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.page-head h2 { margin: 0; font-size: 18px; }
.sub { margin: 0; color: var(--el-text-color-secondary); font-size: 13px; }
.actions { margin-left: auto; display: flex; gap: 10px; align-items: center; }
.dim { color: var(--el-text-color-secondary); font-size: 12px; }
.risk-body { line-height: 1.7; font-size: 13px; }
.panel { background: var(--el-bg-color); border: 1px solid var(--el-border-color-lighter); border-radius: 8px; padding: 12px 14px; }
.panel-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }
.panel-head h3 { margin: 0; font-size: 15px; }
.stk { cursor: pointer; font-weight: 600; color: var(--el-color-primary); margin-right: 6px; }
.code { margin-right: 6px; }
.week-avg { margin: 10px 0 0; font-size: 13px; }
.up { color: #e0402c; }
.down { color: #14a34a; }
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 12px; }
.kpi { background: var(--el-fill-color-light); border-radius: 6px; padding: 8px 10px; display: flex; flex-direction: column; gap: 2px; }
.kpi span { font-size: 12px; color: var(--el-text-color-secondary); }
.kpi b { font-size: 18px; }
.weeks { display: flex; flex-direction: column; gap: 3px; }
.week-row { display: grid; grid-template-columns: 48px 1fr 70px 110px; align-items: center; gap: 8px; font-size: 12px; }
.bar-track { position: relative; height: 10px; background: var(--el-fill-color-lighter); border-radius: 2px; }
.bar { position: absolute; top: 0; height: 100%; border-radius: 2px; }
.bar-up { background: #e0402c; }
.bar-down { background: #14a34a; }
.wk-ret { text-align: right; }
.foot { margin: 8px 0 0; line-height: 1.6; }
@media (max-width: 760px) {
  .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .week-row { grid-template-columns: 42px 1fr 62px; }
  .wk-pool { display: none; }
}
</style>
