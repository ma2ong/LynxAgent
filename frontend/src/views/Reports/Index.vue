<template>
  <div class="reports-page">
    <div class="header">
      <h1>每日盘报</h1>
      <div class="controls">
        <el-radio-group v-model="kind" size="small" @change="loadByDate">
          <el-radio-button value="premarket">盘前看点</el-radio-button>
          <el-radio-button value="close">收盘复盘</el-radio-button>
        </el-radio-group>
        <el-select v-model="selectedDate" size="small" style="width: 140px" @change="loadByDate">
          <el-option v-for="d in datesForKind" :key="d" :label="d" :value="d" />
        </el-select>
        <el-button size="small" :loading="generating" @click="regenerate">立即生成</el-button>
      </div>
    </div>

    <el-alert v-if="staleNotice" type="warning" :closable="false" show-icon
              :title="staleNotice" />

    <el-alert v-if="report && !report.llm" type="info" :closable="false" show-icon
              title="当前为纯数据版盘报（未配置 LLM 密钥），配置后可获得 AI 解读。" />

    <template v-if="report">
      <el-card v-for="(s, i) in report.sections" :key="`${i}-${s.title}`" class="section" shadow="never">
        <h2>{{ s.title }}</h2>
        <p>{{ s.body }}</p>
      </el-card>
      <p class="meta">生成于 {{ report.generated_at }} · 信息整理，非投资建议</p>
    </template>
    <el-empty v-else-if="!loading" description="该日期暂无盘报，交易日 9:26 / 15:35 自动生成，也可点「立即生成」" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { reportsApi, type DailyReport } from '@/api/quant'

const kind = ref<'premarket' | 'close'>('close')
const report = ref<DailyReport | null>(null)
const available = ref<{ date: string; kind: string }[]>([])
const selectedDate = ref('')
const loading = ref(false)
const generating = ref(false)

const datesForKind = computed(() =>
  [...new Set(available.value.filter(d => d.kind === kind.value).map(d => d.date))])

// 陈旧提示：盘报缺失时接口会退回「最近一篇」，页面若不说破，用户会把 4 天前的
// 「盘前看点」当成今天的（实测 07-13 整天、07-10 收盘版都缺过）。
const todayStr = new Date().toLocaleDateString('sv-SE') // YYYY-MM-DD（本地时区）
const staleNotice = computed(() => {
  const r = report.value
  if (!r?.date || r.date === todayStr) return ''
  const isTradingHourPassed = kind.value === 'premarket'
    ? new Date().getHours() * 60 + new Date().getMinutes() >= 9 * 60 + 26
    : new Date().getHours() * 60 + new Date().getMinutes() >= 15 * 60 + 35
  const label = kind.value === 'premarket' ? '盘前看点' : '收盘复盘'
  if (!isTradingHourPassed) {
    return `以下是 ${r.date} 的${label}存档。今日${label}将在${kind.value === 'premarket' ? ' 9:26 竞价结束后' : ' 15:35 收盘后'}自动生成。`
  }
  return `⚠ 今日（${todayStr}）的${label}尚未生成，以下是 ${r.date} 的存档，不代表今日行情。可点「立即生成」补出今日版本（非交易日无需生成）。`
})

let loadSeq = 0 // 快速切 tab/日期时，只让最后一次请求的结果落地

const loadByDate = async () => {
  const seq = ++loadSeq
  loading.value = true
  try {
    if (!datesForKind.value.includes(selectedDate.value)) selectedDate.value = datesForKind.value[0] ?? ''
    const result = selectedDate.value
      ? await reportsApi.byDate(selectedDate.value, kind.value)
      : await reportsApi.latest(kind.value)
    if (seq === loadSeq) report.value = result
  } catch {
    if (seq === loadSeq) report.value = null
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

const regenerate = async () => {
  generating.value = true
  try {
    report.value = await reportsApi.generate(kind.value)
    available.value = await reportsApi.available()
    if (report.value) selectedDate.value = report.value.date
    ElMessage.success('盘报已生成')
  } catch {
    ElMessage.error('生成失败，请稍后重试')
  } finally {
    generating.value = false
  }
}

onMounted(async () => {
  available.value = await reportsApi.available()
  await loadByDate()
})
</script>

<style scoped>
.reports-page { max-width: 860px; margin: 0 auto; padding: 16px; }
.header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }
.controls { display: flex; gap: 8px; align-items: center; }
.section { margin-top: 12px; }
.section h2 { margin: 0 0 8px; font-size: 15px; }
.section p { margin: 0; line-height: 1.8; white-space: pre-wrap; }
.meta { margin-top: 12px; font-size: 12px; color: var(--el-text-color-placeholder); text-align: center; }
</style>
