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

    <p class="foot-note">
      口径说明：留痕价为扫描当时的价格；T+N 为留痕后第 N 个交易日收盘价相对留痕价的涨跌幅；
      超额 = 个股收益 − 同期全市场中位收益（单位 pp），用于区分策略能力与大盘涨跌；
      目标日行情覆盖不足（数据同步缺口）的样本自动排除。历史表现不代表未来收益，不构成投资建议。
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { quantApi, type MarketContext, type PicksPoolStat, type PicksStatsItem } from '@/api/quant'

const loading = ref(false)
const error = ref('')
const days = ref(30)
const poolFilter = ref('')
const pools = ref<PicksPoolStat[]>([])
const items = ref<PicksStatsItem[]>([])
const marketCtx = ref<MarketContext | null>(null)
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

onMounted(() => {
  load()
  quantApi.marketContext().then((ctx) => { marketCtx.value = ctx || null }).catch(() => {})
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

.up { color: #ef232a; }
.down { color: #14b143; }
.muted { color: var(--el-text-color-secondary); }
</style>
