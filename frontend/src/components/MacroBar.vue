<template>
  <div v-if="data && (data.indices.length || data.breadth)" class="macro-bar">
    <span v-for="idx in data.indices" :key="idx.code" class="item">
      <span class="label">{{ idx.name }}</span>
      <span :class="colorClass(idx.change_percent)">
        {{ idx.price?.toFixed(2) }}
        <template v-if="idx.change_percent != null">
          {{ idx.change_percent > 0 ? '+' : '' }}{{ idx.change_percent.toFixed(2) }}%
        </template>
      </span>
    </span>
    <span v-if="data.breadth" class="item">
      <span class="label">涨跌</span>
      <span class="up">{{ data.breadth.up }}</span>/<span class="down">{{ data.breadth.down }}</span>
    </span>
    <span v-if="data.breadth" class="item">
      <span class="label">两市成交</span>
      <span>{{ data.breadth.amount_yi }}亿</span>
    </span>
    <span v-if="risk" class="item risk-chip" :class="`lv-${riskKey}`" title="点击查看风险预警详情" @click="goRisk">
      <span class="label">风险</span>
      <b>{{ risk.level }}</b>
      <span class="risk-score">{{ risk.score }}</span>
    </span>
    <span class="item updated">{{ data.updated_at }}</span>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { macroBarApi, quantApi, type MacroBarData, type RiskAlert } from '@/api/quant'

const router = useRouter()
const data = ref<MacroBarData | null>(null)
const risk = ref<RiskAlert | null>(null)
let timer: number | undefined

// A股红涨绿跌
const colorClass = (pct: number | null) => (pct == null ? '' : pct > 0 ? 'up' : pct < 0 ? 'down' : '')

const riskKey = computed(() => {
  const l = risk.value?.level
  return l === '极危' ? 'extreme' : l === '危险' ? 'danger' : l === '警惕' ? 'warn' : 'safe'
})
const goRisk = () => router.push('/risk-alert')

const load = async () => {
  try {
    data.value = await macroBarApi.fetch()
  } catch {
    /* 静默：宏观条是辅助信息，失败不打扰用户 */
  }
  try {
    risk.value = await quantApi.riskAlert()
  } catch {
    /* 风险条同样静默降级 */
  }
}

onMounted(() => {
  load()
  timer = window.setInterval(load, 60000)
})
onUnmounted(() => { if (timer) window.clearInterval(timer) })
</script>

<style scoped>
.macro-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 4px 16px;
  font-size: 12px;
  overflow-x: auto;
  white-space: nowrap;
  border-bottom: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color);
}
.item { display: inline-flex; align-items: center; gap: 4px; }
.label { color: var(--el-text-color-secondary); }
.up { color: #f56c6c; }
.down { color: #67c23a; }
.updated { margin-left: auto; color: var(--el-text-color-placeholder); }
.risk-chip { cursor: pointer; padding: 1px 8px; border-radius: 10px; font-weight: 600;
  b { font-weight: 700; } .risk-score { opacity: .7; } }
.risk-chip.lv-safe { background: rgba(14,159,90,.12); color: #0e9f5a; }
.risk-chip.lv-warn { background: rgba(212,136,6,.14); color: #d48806; }
.risk-chip.lv-danger { background: rgba(239,35,42,.12); color: #ef232a; }
.risk-chip.lv-extreme { background: rgba(168,7,26,.16); color: #a8071a; }
</style>
