<template>
  <el-drawer :model-value="modelValue" size="440px" :title="title" @update:model-value="$emit('update:modelValue', $event)">
    <div v-if="row" class="why-body">
      <section>
        <h4>综合评分</h4>
        <div class="score-line">
          <b class="big-score">{{ row.score ?? row.quant_score ?? '—' }}</b>
          <el-tag v-if="row.signal" size="small" effect="plain">{{ row.signal }}</el-tag>
        </div>
      </section>

      <section v-if="factorRows.length">
        <h4>因子分解</h4>
        <div v-for="f in factorRows" :key="f.key" class="factor-row">
          <span class="factor-name">{{ f.label }}</span>
          <el-progress :percentage="Math.min(100, Math.max(0, f.value))" :stroke-width="10" :show-text="false" :color="f.value >= 60 ? '#ef232a' : f.value >= 45 ? '#e6a23c' : '#14b143'" />
          <b class="factor-val">{{ f.value.toFixed(0) }}</b>
        </div>
      </section>

      <section v-if="row.reasons?.length">
        <h4>入选理由</h4>
        <el-tag v-for="r in row.reasons" :key="r" class="reason-tag" effect="plain">{{ r }}</el-tag>
      </section>

      <section v-if="row.trade_plan?.buy_price">
        <h4>交易计划</h4>
        <div class="plan">
          <span>买入 <b>{{ row.trade_plan.buy_price }}</b></span>
          <span>止损 <b class="down">{{ row.trade_plan.stop_loss }}</b>（{{ row.trade_plan.stop_loss_pct }}%）</span>
          <span>止盈 <b class="up">{{ row.trade_plan.take_profit }}</b>（+{{ row.trade_plan.take_profit_pct }}%）</span>
        </div>
      </section>

      <section>
        <h4>该信号的历史表现</h4>
        <div v-if="loading" class="muted">
          加载中…<template v-if="loadingSlow">首次统计约需 20 秒（当日会缓存，之后秒开），可先看上方因子与交易计划。</template>
        </div>
        <template v-else-if="stats">
          <div class="hist-grid">
            <div class="hist-cell">
              <span>实盘留痕（近{{ stats.days }}天 · T+5）</span>
              <b v-if="liveT5?.samples">超额胜率 {{ fmtRate(liveT5.excess_win_rate) }} · 均 {{ fmtExcess(liveT5.avg_excess) }} <small class="muted">({{ liveT5.samples }}样本)</small></b>
              <b v-else class="muted">样本积累中</b>
            </div>
            <div class="hist-cell">
              <span>历史回放（12个月 · T+5）</span>
              <b v-if="stats.replay">超额胜率 {{ fmtRate(stats.replay.excess_win_rate) }} · 均 {{ fmtExcess(stats.replay.avg_excess) }} <small class="muted">({{ stats.replay.evaluated }}样本)</small></b>
              <b v-else class="muted">尚未回放</b>
            </div>
          </div>
          <template v-if="rowPatternStats.length">
            <p class="muted small">本票命中形态的历史 T+5 超额（留痕口径）：</p>
            <div v-for="p in rowPatternStats" :key="p.name" class="pat-row">
              <span>{{ p.name }}</span>
              <b :class="p.avg_excess > 0 ? 'up' : 'down'">{{ fmtExcess(p.avg_excess) }}</b>
              <small class="muted">胜 {{ fmtRate(p.excess_win_rate) }} · {{ p.samples }}样本</small>
            </div>
          </template>
        </template>
        <div v-else class="muted">暂无统计</div>
        <p class="muted small">历史表现不代表未来收益，不构成投资建议。</p>
      </section>
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { quantApi, type SignalStats } from '@/api/quant'

const props = defineProps<{ modelValue: boolean; row: any | null; pool: 'smart' | 'pattern' }>()
defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const FACTOR_LABELS: Record<string, string> = {
  trend: '趋势', momentum: '动量', rsi: 'RSI', risk_control: '风控',
  liquidity: '流动性', macd: 'MACD', bollinger: '布林位置', capital_flow: '资金流',
}

const title = computed(() => (props.row ? `为什么入选：${props.row.name || ''} ${props.row.symbol || ''}` : '为什么入选'))

const factorRows = computed(() => {
  const f = props.row?.factors || {}
  return Object.entries(f)
    .filter(([, v]) => typeof v === 'number')
    .map(([k, v]) => ({ key: k, label: FACTOR_LABELS[k] || k, value: Number(v) }))
})

const statsCache = new Map<string, SignalStats>()
const stats = ref<SignalStats | null>(null)
const loading = ref(false)
const loadingSlow = ref(false)
let slowTimer = 0

const liveT5 = computed(() => stats.value?.live?.t5 || null)

const rowPatternStats = computed(() => {
  const names = new Set((props.row?.patterns || []).map((p: any) => String(p?.name || '')).filter(Boolean))
  return (stats.value?.patterns || []).filter((p) => names.has(p.name))
})

watch(
  () => [props.modelValue, props.pool] as const,
  async ([visible, pool]) => {
    if (!visible) return
    if (statsCache.has(pool)) {
      stats.value = statsCache.get(pool)!
      return
    }
    loading.value = true
    loadingSlow.value = false
    slowTimer = window.setTimeout(() => { loadingSlow.value = true }, 3000)
    try {
      const res = await quantApi.signalStats(pool)
      statsCache.set(pool, res)
      stats.value = res
    } catch {
      stats.value = null
    } finally {
      window.clearTimeout(slowTimer)
      loading.value = false
      loadingSlow.value = false
    }
  },
  { immediate: true },
)

const fmtRate = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(0)}%`)
const fmtExcess = (v: number | null | undefined) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}pp`)
</script>

<style scoped lang="scss">
.why-body {
  display: flex;
  flex-direction: column;
  gap: 18px;

  h4 { margin: 0 0 8px; font-size: 14px; }
}

.score-line {
  display: flex;
  align-items: center;
  gap: 10px;
}

.big-score { font-size: 26px; }

.factor-row {
  display: grid;
  grid-template-columns: 64px 1fr 36px;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;

  .factor-name { font-size: 12px; color: var(--el-text-color-secondary); }
  .factor-val { font-size: 12px; text-align: right; }
}

.reason-tag { margin: 0 6px 6px 0; }

.plan {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
}

.hist-grid {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 8px;
}

.hist-cell {
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--el-fill-color-lighter);

  span { display: block; font-size: 12px; color: var(--el-text-color-secondary); }
  b { font-size: 13px; }
}

.pat-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 13px;
  padding: 2px 0;
}

.small { font-size: 12px; }
.up { color: #ef232a; }
.down { color: #14b143; }
.muted { color: var(--el-text-color-secondary); }
</style>
