<template>
  <div class="review-page">
    <header class="review-head">
      <div>
        <h1>选股复盘</h1>
        <p>每次扫描自动留痕，按真实行情统计各池 T+1 / T+3 / T+5 胜率与相对大盘的超额——数据说话，不承诺胜率。</p>
      </div>
      <div class="head-actions">
        <el-radio-group v-model="days" size="small" @change="load">
          <el-radio-button :value="7">近 7 天</el-radio-button>
          <el-radio-button :value="30">近 30 天</el-radio-button>
          <el-radio-button :value="90">近 90 天</el-radio-button>
        </el-radio-group>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </div>
    </header>

    <el-alert v-if="error" :title="error" type="warning" show-icon :closable="false" />

    <div v-if="health" class="data-fresh" :class="freshTone">
      <b>数据{{ freshLabel }}</b>
      <span>日线截至 {{ health.latest_complete_date || health.latest_date || '—' }} · 覆盖 {{ health.latest_complete_count || health.latest_date_count || 0 }}/{{ health.meta_count || '—' }}</span>
      <span v-if="health.gap_dates?.length" class="gap">缺口日 {{ health.gap_dates.join('、') }}（已触发自动补齐，统计自动排除）</span>
      <span v-if="health.sync_running" class="muted">同步中 {{ health.sync_done || 0 }}/{{ health.sync_total || 0 }}…</span>
    </div>

    <div v-if="marketCtx?.state" class="market-context" :class="`ctx-${ctxTone}`">
      <b>大盘环境：{{ marketCtx.state }}</b>
      <span>近5日{{ marketCtx.as_of ? `(截至 ${marketCtx.as_of})` : '' }}全市场中位 {{ (marketCtx.median_5d_pct ?? 0) > 0 ? '+' : '' }}{{ marketCtx.median_5d_pct }}% · 上涨占比 {{ Math.round((marketCtx.breadth_up || 0) * 100) }}%</span>
      <span class="ctx-advice">{{ marketCtx.advice }}</span>
    </div>

    <section v-if="pools.length" class="pool-grid">
      <div v-for="p in pools" :key="p.pool" class="pool-card">
        <div class="pool-title">
          <b>{{ poolLabel(p.pool) }}</b>
          <small>{{ p.picks }} 条留痕</small>
        </div>
        <div class="horizon-row">
          <div v-for="h in horizons" :key="h.key" class="horizon-cell">
            <span>{{ h.label }}</span>
            <b :class="rateClass(stat(p, h.key)?.win_rate)">
              {{ fmtRate(stat(p, h.key)?.win_rate) }}
            </b>
            <small :class="retClass(stat(p, h.key)?.avg_return)">
              均 {{ fmtRet(stat(p, h.key)?.avg_return) }}
            </small>
            <small :class="retClass(stat(p, h.key)?.avg_excess)">
              超额 {{ fmtExcess(stat(p, h.key)?.avg_excess) }} · 胜 {{ fmtRate(stat(p, h.key)?.excess_win_rate) }}
            </small>
            <small class="muted">{{ stat(p, h.key)?.samples ?? 0 }} 样本</small>
          </div>
        </div>
      </div>
    </section>

    <el-empty
      v-if="!loading && !pools.length"
      description="暂无留痕数据。从今天起，每次「形态智选 / 智能推荐 / 短线波段」扫描都会自动留痕，运行几个交易日后这里会出现真实胜率统计。"
      :image-size="90"
    />

    <section v-if="items.length" class="panel">
      <div class="panel-head">
        <h2>留痕明细</h2>
        <el-radio-group v-model="poolFilter" size="small">
          <el-radio-button value="">全部</el-radio-button>
          <el-radio-button value="pattern">形态智选</el-radio-button>
          <el-radio-button value="smart">智能推荐</el-radio-button>
          <el-radio-button value="swing">短线波段</el-radio-button>
          <el-radio-button value="auction">竞价优选</el-radio-button>
        </el-radio-group>
        <el-radio-group v-model="retMode" size="small">
          <el-radio-button value="abs">绝对</el-radio-button>
          <el-radio-button value="excess">超额</el-radio-button>
        </el-radio-group>
      </div>
      <el-table :data="filteredItems" size="small" stripe max-height="560">
        <el-table-column prop="pick_date" label="日期" width="100" />
        <el-table-column label="池" width="90">
          <template #default="{ row }">{{ poolLabel(row.pool) }}</template>
        </el-table-column>
        <el-table-column label="股票" min-width="130">
          <template #default="{ row }">{{ row.name }} <small class="muted">{{ row.symbol }}</small></template>
        </el-table-column>
        <el-table-column prop="score" label="评分" width="70" />
        <el-table-column prop="rank" label="名次" width="60" />
        <el-table-column prop="base_close" label="留痕价" width="80" />
        <el-table-column v-for="h in horizons" :key="h.key" :label="h.label" width="90">
          <template #default="{ row }">
            <span :class="retClass(cellVal(row, h.key))">{{ fmtRet(cellVal(row, h.key)) }}</span>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>历史回放验证</h2>
        <div class="head-actions">
          <el-tag v-if="replayRunning" type="warning" effect="plain">
            回放中 {{ replayStat?.done || 0 }}/{{ replayStat?.total || 0 }}
          </el-tag>
          <el-button size="small" :loading="replayStarting" :disabled="replayRunning" @click="startReplay">
            重跑回放（约 10 分钟）
          </el-button>
        </div>
      </div>
      <template v-if="replay?.pools?.length">
        <p class="replay-meta muted">
          回放 {{ replay.params?.months || 12 }} 个月 · 每 {{ replay.params?.step || 5 }} 个交易日一期 ·
          每期 top{{ replay.params?.top_n || 20 }} · 统计 T+5 相对全市场中位的超额 · 完成于 {{ replay.created_at }}
        </p>
        <div v-for="p in replay.pools" :key="'verdict-' + p.pool" class="replay-verdict" :class="verdictClass(p)">
          <b>{{ poolLabel(p.pool) }}：</b>{{ verdictText(p) }}
        </div>
        <div class="pool-grid">
          <div v-for="p in replay.pools" :key="p.pool" class="pool-card">
            <div class="pool-title">
              <b>{{ poolLabel(p.pool) }}</b>
              <small>{{ p.evaluated }}/{{ p.picks }} 样本
                <template v-if="(p.limitup_ratio ?? 0) > 0.05"> · {{ fmtRate(p.limitup_ratio) }} 入选时已涨停</template>
              </small>
            </div>
            <div class="horizon-row replay-row">
              <div class="horizon-cell">
                <span>超额胜率</span>
                <b :class="rateClass(p.excess_win_rate)">{{ fmtRate(p.excess_win_rate) }}</b>
              </div>
              <div class="horizon-cell">
                <span>平均超额</span>
                <b :class="retClass(p.avg_excess)">{{ fmtExcess(p.avg_excess) }}</b>
              </div>
              <div class="horizon-cell">
                <span>中位超额</span>
                <b :class="retClass(p.median_excess ?? null)">{{ fmtExcess(p.median_excess ?? null) }}</b>
              </div>
              <div class="horizon-cell">
                <span>累计超额</span>
                <b :class="retClass(cumExcess(p))">{{ fmtExcess(cumExcess(p)) }}</b>
              </div>
            </div>
            <div v-if="p.open_entry?.evaluated" class="horizon-row replay-row open-entry-row">
              <div class="horizon-cell">
                <span>可成交口径（次日开盘买入）</span>
                <b />
              </div>
              <div class="horizon-cell">
                <span>超额胜率</span>
                <b :class="rateClass(p.open_entry.excess_win_rate)">{{ fmtRate(p.open_entry.excess_win_rate) }}</b>
              </div>
              <div class="horizon-cell">
                <span>平均超额</span>
                <b :class="retClass(p.open_entry.avg_excess)">{{ fmtExcess(p.open_entry.avg_excess) }}</b>
              </div>
              <div class="horizon-cell">
                <span>中位超额</span>
                <b :class="retClass(p.open_entry.median_excess)">{{ fmtExcess(p.open_entry.median_excess) }}</b>
              </div>
            </div>
            <div v-if="p.regimes?.length" class="regime-table">
              <div class="regime-head">分大盘环境表现（入选时点的近5日全市场结构）</div>
              <div v-for="r in p.regimes" :key="r.regime" class="regime-row">
                <span class="regime-name" :class="'regime-' + regimeKey(r.regime)">{{ r.regime }}</span>
                <span>{{ r.sessions }} 期 · {{ r.picks }} 样本</span>
                <b :class="retClass(r.avg_excess)">均 {{ fmtExcess(r.avg_excess) }}</b>
                <b :class="retClass(r.median_excess)">中位 {{ fmtExcess(r.median_excess) }}</b>
                <span :class="rateClass(r.excess_win_rate)">胜 {{ fmtRate(r.excess_win_rate) }}</span>
              </div>
            </div>
          </div>
        </div>
        <div ref="replayChartEl" class="replay-chart" />
        <el-table :data="monthlyRows" size="small" stripe>
          <el-table-column prop="month" label="月份" width="100" />
          <el-table-column v-for="p in replay.pools" :key="p.pool" :label="poolLabel(p.pool)">
            <template #default="{ row }">
              <span v-if="row[p.pool]" :class="retClass(row[p.pool].avg_excess)">
                {{ fmtExcess(row[p.pool].avg_excess) }} · 胜 {{ fmtRate(row[p.pool].excess_win_rate) }} ({{ row[p.pool].picks }})
              </span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
        </el-table>
        <p class="foot-note replay-note">
          回放口径：pattern 与 smart 两池的评分函数与线上选股完全同源（严格 point-in-time，无近似重建）；
          排除规则使用当前股票名称（历史 ST 状态无法还原），回放宇宙不含期间退市股（存活偏差方向为高估）。
          回放统计信号层收益、不计换手成本；实盘成本以模拟组合为准。
        </p>
      </template>
      <el-empty v-else-if="!replayRunning" description="还没有回放结果。点「重跑回放」用本地历史日线验证选股规则的真实超额表现。" :image-size="80" />
    </section>

    <p class="foot-note">
      口径说明：留痕价为扫描当时的价格；T+N 为留痕后第 N 个交易日收盘价相对留痕价的涨跌幅；
      超额 = 个股收益 − 同期全市场中位收益（单位 pp），用于区分策略能力与大盘涨跌；
      目标日行情覆盖不足（数据同步缺口）的样本自动排除。历史表现不代表未来收益，不构成投资建议。
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import { quantApi, type MarketContext, type PicksPoolStat, type PicksStatsItem, type QuantDataHealth, type ReplayPoolSummary, type ReplayStatus, type ReplaySummary } from '@/api/quant'
import { echarts, type ECharts } from '@/utils/echarts'

const loading = ref(false)
const error = ref('')
const days = ref(30)
const poolFilter = ref('')
const pools = ref<PicksPoolStat[]>([])
const items = ref<PicksStatsItem[]>([])
const marketCtx = ref<MarketContext | null>(null)
const health = ref<QuantDataHealth | null>(null)
const freshTone = computed(() => {
  const h = health.value
  if (!h) return 'fresh-ok'
  if (h.gap_dates?.length || !h.ready) return 'fresh-bad'
  if (h.needs_incremental_sync || h.status === 'partial_today' || h.status === 'stale_today') return 'fresh-warn'
  return 'fresh-ok'
})
const freshLabel = computed(() =>
  freshTone.value === 'fresh-bad' ? '有缺口' : freshTone.value === 'fresh-warn' ? '待补齐' : '新鲜',
)
const ctxTone = computed(() => {
  const s = marketCtx.value?.state
  return s === '偏暖' ? 'warm' : s === '偏冷' ? 'cold' : 'flat'
})

const horizons = [
  { key: 't1' as const, label: 'T+1' },
  { key: 't3' as const, label: 'T+3' },
  { key: 't5' as const, label: 'T+5' },
]

const POOL_LABELS: Record<string, string> = {
  pattern: '形态智选',
  smart: '智能推荐',
  swing: '短线波段',
  auction: '竞价优选',
  // 换评分公式必须换池名：旧公式的战绩不能挂在新公式名下（2026-07-14 v2→v3）
  smart_v2: '智能推荐 v2（已退役）',
  smart_fac: '因子实验（已转正为 v3）',
}
const poolLabel = (key: string) => POOL_LABELS[key] || key

const stat = (p: PicksPoolStat, key: 't1' | 't3' | 't5') => p.horizons?.[key]

const filteredItems = computed(() =>
  poolFilter.value ? items.value.filter((it) => it.pool === poolFilter.value) : items.value,
)

const retMode = ref<'abs' | 'excess'>('abs')
const cellVal = (row: PicksStatsItem, key: 't1' | 't3' | 't5') =>
  retMode.value === 'abs' ? row[key] : row[`excess_${key}`]

const fmtRate = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(0)}%`)
const fmtExcess = (v: number | null | undefined) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}pp`)
const fmtRet = (v: number | null | undefined) => (v == null ? '待更新' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`)
const rateClass = (v: number | null | undefined) => (v == null ? 'muted' : v >= 0.5 ? 'up' : 'down')
const retClass = (v: number | null | undefined) => (v == null ? 'muted' : v > 0 ? 'up' : v < 0 ? 'down' : 'muted')

const load = async () => {
  loading.value = true
  error.value = ''
  try {
    const res = await quantApi.picksStats(days.value)
    pools.value = res?.pools || []
    items.value = res?.items || []
  } catch (e: any) {
    error.value = e?.message || '加载复盘数据失败'
  } finally {
    loading.value = false
  }
}

// ---- 历史回放 ----
const replay = ref<ReplaySummary | null>(null)
const replayStat = ref<ReplayStatus | null>(null)
const replayStarting = ref(false)
const replayChartEl = ref<HTMLElement | null>(null)
let replayChart: ECharts | null = null
let pollTimer: number | undefined

const replayRunning = computed(() => !!replayStat.value?.running)

const cumExcess = (p: ReplayPoolSummary) => (p.curve?.length ? p.curve[p.curve.length - 1].cum_excess : null)

const monthlyRows = computed(() => {
  const months = new Map<string, Record<string, any>>()
  for (const p of replay.value?.pools || []) {
    for (const m of p.monthly || []) {
      if (!months.has(m.month)) months.set(m.month, { month: m.month })
      months.get(m.month)![p.pool] = m
    }
  }
  return [...months.values()].sort((a, b) => (a.month < b.month ? -1 : 1))
})

const renderReplayChart = async () => {
  await nextTick()
  if (!replayChartEl.value || !replay.value?.pools?.length) return
  if (!replayChart) replayChart = echarts.init(replayChartEl.value)
  const series = replay.value.pools.map((p) => ({
    name: poolLabel(p.pool),
    type: 'line' as const,
    showSymbol: false,
    data: (p.curve || []).map((c) => [c.as_of, c.cum_excess]),
  }))
  replayChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { top: 0 },
    grid: { left: 48, right: 16, top: 30, bottom: 24 },
    xAxis: { type: 'time' },
    yAxis: { type: 'value', name: '累计超额(pp)' },
    series,
  })
}

const loadReplay = async () => {
  try {
    const res = await quantApi.replayResults()
    if (res?.pools?.length) {
      replay.value = res
      renderReplayChart()
    }
  } catch { /* 无结果时忽略 */ }
}

const pollReplay = () => {
  window.clearInterval(pollTimer)
  pollTimer = window.setInterval(async () => {
    try {
      replayStat.value = await quantApi.replayStatus()
      if (!replayStat.value?.running) {
        window.clearInterval(pollTimer)
        loadReplay()
      }
    } catch { /* 轮询失败下轮重试 */ }
  }, 5000)
}

// ---- 结论卡：用可成交口径（缺失时退回收盘口径）把回放数据翻成人话 ----
const verdictStats = (p: ReplayPoolSummary) => {
  if (p.open_entry?.evaluated) return { avg: p.open_entry.avg_excess, med: p.open_entry.median_excess, win: p.open_entry.excess_win_rate, caliber: '次日开盘可成交口径' }
  return { avg: p.avg_excess, med: p.median_excess ?? null, win: p.excess_win_rate, caliber: '收盘回测口径' }
}

const regimeKey = (name: string) => (name === '偏暖' ? 'warm' : name === '偏冷' ? 'cold' : 'neutral')

// 环境建议：偏冷期均值<0 → 建议停跟；仍为正但不足偏暖一半 → 建议轻仓
const regimeAdvice = (p: ReplayPoolSummary) => {
  const regs = p.regimes || []
  const cold = regs.find((r) => r.regime === '偏冷')
  const warm = regs.find((r) => r.regime === '偏暖')
  if (!cold) return ''
  if (cold.avg_excess <= 0) {
    return `大盘偏冷期该池平均超额 ${cold.avg_excess}pp（${cold.picks} 样本）——弱市建议停跟。`
  }
  if (warm && cold.avg_excess < warm.avg_excess / 2) {
    return `环境敏感：偏暖期均 +${warm.avg_excess}pp、偏冷期缩至 +${cold.avg_excess}pp（中位 ${cold.median_excess}pp）——大盘偏冷时建议轻仓或观望。`
  }
  return ''
}

const verdictText = (p: ReplayPoolSummary) => {
  const { avg, med, win, caliber } = verdictStats(p)
  if (avg == null) return '样本不足，暂无法下结论。'
  const months = replay.value?.params?.months || 12
  const head = `过去 ${months} 个月每期等权买入整池，T+5 平均超额 ${avg > 0 ? '+' : ''}${avg}pp/期（${caliber}）`
  const tail = regimeAdvice(p)
  if (avg <= 0) return `${head}——该池规则未跑赢全市场中位，判为无效，不建议跟随。`
  if ((med ?? 0) <= 0) {
    return `${head}，但单票中位 ${med}pp、胜率 ${win != null ? Math.round(win * 100) : '-'}%：` +
      '收益依赖整池分散接住少数大涨股，单买一两只大概率跑输——要跟就整池等权跟，不适合单票押注。' +
      (tail ? ` ${tail}` : '')
  }
  return `${head}，中位 +${med}pp、胜率 ${win != null ? Math.round(win * 100) : '-'}%，组合与单票口径均为正。${tail ? ` ${tail}` : ''}`
}

const verdictClass = (p: ReplayPoolSummary) => {
  const { avg, med } = verdictStats(p)
  if (avg == null) return 'verdict-unknown'
  if (avg <= 0) return 'verdict-bad'
  return (med ?? 0) <= 0 ? 'verdict-mixed' : 'verdict-good'
}

const startReplay = async () => {
  try {
    await ElMessageBox.confirm(
      '重跑将全量扫描本地历史日线（约 10 分钟，期间 CPU 占用较高），完成后覆盖当前回放结论。确定重跑？',
      '重跑历史回放',
      { confirmButtonText: '重跑', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  replayStarting.value = true
  try {
    replayStat.value = await quantApi.replayRun()
    pollReplay()
  } catch (e: any) {
    error.value = e?.message || '启动回放失败'
  } finally {
    replayStarting.value = false
  }
}

onMounted(() => {
  load()
  quantApi.marketContext().then((ctx) => { marketCtx.value = ctx || null }).catch(() => {})
  quantApi.dataHealth(true).then((h) => { health.value = h || null }).catch(() => {})
  loadReplay()
  quantApi.replayStatus().then((s) => {
    replayStat.value = s
    if (s?.running) pollReplay()
  }).catch(() => {})
})

onBeforeUnmount(() => {
  window.clearInterval(pollTimer)
  replayChart?.dispose()
})
</script>

<style scoped lang="scss">
.review-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.review-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;

  h1 { margin: 0 0 4px; font-size: 22px; }
  p { margin: 0; color: var(--el-text-color-secondary); font-size: 13px; }
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.pool-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px;
}

.pool-card {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  padding: 14px 16px;
}

.pool-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 10px;

  b { font-size: 15px; }
  small { color: var(--el-text-color-secondary); }
}

.horizon-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}

.horizon-cell {
  text-align: center;
  padding: 8px 4px;
  border-radius: 8px;
  background: var(--el-fill-color-lighter);

  span { display: block; font-size: 12px; color: var(--el-text-color-secondary); }
  b { display: block; font-size: 18px; margin: 2px 0; }
  small { display: block; font-size: 11px; }
}

.panel {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  padding: 14px 16px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  flex-wrap: wrap;
  gap: 8px;

  h2 { margin: 0; font-size: 16px; }
}

.foot-note {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.market-context {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  background: var(--el-fill-color-extra-light);

  b { font-size: 14px; }
  span { color: var(--el-text-color-secondary); font-size: 12px; }
  .ctx-advice { color: var(--el-text-color-regular); }
}

.ctx-warm {
  border-color: #ffb3a7;
  background: #fff1f0;
  b { color: #ef232a; }
}

.ctx-cold {
  border-color: #a7d4b4;
  background: #f0fff4;
  b { color: #0e9f5a; }
}

.data-fresh {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 8px 12px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  font-size: 12px;

  b { font-size: 13px; }
  span { color: var(--el-text-color-secondary); }
  .gap { color: #cf1322; font-weight: 600; }
}

.fresh-ok { border-color: #a7d4b4; background: #f0fff4; b { color: #0e9f5a; } }
.fresh-warn { border-color: #ffd591; background: #fffbe6; b { color: #d46b08; } }
.fresh-bad { border-color: #ffccc7; background: #fff2f0; b { color: #cf1322; } }

.replay-chart {
  height: 280px;
  margin: 12px 0;
}

.replay-meta {
  margin: 0 0 10px;
  font-size: 12px;
}

.replay-row {
  grid-template-columns: repeat(4, 1fr);
}

.open-entry-row {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed #e5e8ef;

  .horizon-cell:first-child span {
    font-weight: 600;
    color: #475069;
  }
}

.replay-verdict {
  margin: 0 0 10px;
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.7;
  border: 1px solid #e5e8ef;

  &.verdict-good { border-color: #a7d4b4; background: #f0fff4; }
  &.verdict-mixed { border-color: #ffd591; background: #fffbe6; }
  &.verdict-bad { border-color: #ffccc7; background: #fff2f0; }
  &.verdict-unknown { background: #fafbfc; }
}

.regime-table {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed #e5e8ef;
  font-size: 12px;

  .regime-head {
    color: #8a93a6;
    margin-bottom: 4px;
  }

  .regime-row {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 2px 0;

    .regime-name {
      width: 40px;
      font-weight: 600;
    }
    .regime-warm { color: #cf1322; }
    .regime-cold { color: #0958d9; }
    .regime-neutral { color: #8a93a6; }
  }
}

.replay-note {
  margin-top: 10px;
}

.up { color: #ef232a; }
.down { color: #14b143; }
.muted { color: var(--el-text-color-secondary); }
</style>
