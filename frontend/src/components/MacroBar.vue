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
    <span class="item updated">{{ data.updated_at }}</span>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { macroBarApi, type MacroBarData } from '@/api/quant'

const data = ref<MacroBarData | null>(null)
let timer: number | undefined

// A股红涨绿跌
const colorClass = (pct: number | null) => (pct == null ? '' : pct > 0 ? 'up' : pct < 0 ? 'down' : '')

const load = async () => {
  try {
    data.value = await macroBarApi.fetch()
  } catch {
    /* 静默：宏观条是辅助信息，失败不打扰用户 */
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
</style>
