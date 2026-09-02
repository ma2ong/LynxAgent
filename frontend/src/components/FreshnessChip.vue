<template>
  <!-- 两个轴分开显示：数据截至哪天、这份结果算于什么时候。只报其中一个都可能骗人——
       数据落后时"刚算过"没有意义，缓存很旧时"数据是今天的"同样没有意义。 -->
  <span v-if="f" class="freshness" :class="f.state" :title="title">
    <i class="dot" />
    {{ f.label }}
    <span class="detail">数据 {{ f.as_of || '—' }} · 算于 {{ f.computed_at }}</span>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

export interface Freshness {
  as_of: string | null
  latest_bar: string | null
  computed_at: string
  age_seconds: number
  data_behind: boolean
  state: 'fresh' | 'aging' | 'stale'
  label: string
}

const props = defineProps<{ f?: Freshness | null }>()

const title = computed(() => {
  const f = props.f
  if (!f) return ''
  if (f.data_behind) {
    return `数据只到 ${f.as_of}，库里最新交易日是 ${f.latest_bar}——重算也还是旧数据，先同步日线`
  }
  const mins = Math.round(f.age_seconds / 60)
  return `这份结果算于 ${f.computed_at}（${mins} 分钟前），数据截至 ${f.as_of}`
})
</script>

<style scoped>
.freshness { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; color: var(--el-text-color-secondary); cursor: default; }
.dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.freshness.fresh { color: var(--el-color-success); }
.freshness.aging { color: var(--el-color-warning); }
.freshness.stale { color: var(--el-color-danger); }
.detail { color: var(--el-text-color-placeholder); }
</style>
