<template>
  <div class="serenity">
    <div class="head">
      <h2>催化剂监控 <el-tag size="small" type="warning">事件驱动 · serenity</el-tag></h2>
      <p class="sub">从真实新闻中筛出"可观察需求变化"，映射到可投资 A 股受益标的。研究假设，非投资建议。</p>
      <el-button size="small" :loading="busy" @click="load(true)">刷新</el-button>
    </div>

    <div v-if="computing" class="computing"><el-icon class="is-loading"><Loading /></el-icon> 扫描新闻中，已用 {{ elapsed }}s…</div>

    <div v-else class="cards">
      <el-card v-for="(ev, i) in events" :key="i" shadow="hover" class="card">
        <div class="theme"><el-tag size="small">{{ ev.theme }}</el-tag></div>
        <div class="event">{{ ev.event }}</div>
        <div class="thesis">{{ ev.thesis }}</div>
        <div class="benes">
          <el-tag v-for="b in ev.beneficiaries" :key="b.symbol" size="small" type="success" effect="plain">
            {{ b.name }} {{ b.symbol }}
          </el-tag>
        </div>
        <div class="vf"><span>✅ 验证：{{ ev.validation }}</span><span>❌ 证伪：{{ ev.falsification }}</span></div>
        <el-button text size="small" @click="openDeep(ev)">深度报告 →</el-button>
      </el-card>
      <el-empty v-if="!events.length && !busy" description="暂无事件，点刷新扫描" />
    </div>

    <el-drawer v-model="drawer" :title="`${deepTheme} · serenity 深度报告`" size="42%">
      <div v-if="deepLoading" v-loading="true" style="height:200px" />
      <pre v-else class="deep">{{ JSON.stringify(deepData, null, 2) }}</pre>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import { quantApi, type SerenityEvent } from '@/api/quant'

const events = ref<SerenityEvent[]>([])
const loading = ref(false)
const computing = ref(false)
const elapsed = ref(0)
const busy = computed(() => loading.value || computing.value)
let pollTimer: number | undefined
let elapsedTimer: number | undefined

function stop() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = undefined }
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = undefined }
}

async function load(force = false) {
  loading.value = true
  try { handle(await quantApi.serenityEvents(force)) }
  catch (e: any) { ElMessage.error(e?.message || '加载失败') }
  finally { loading.value = false }
}

function handle(res: any) {
  if (res?.status === 'computing') {
    if (!computing.value) { elapsed.value = res.elapsed_sec || 0; elapsedTimer = window.setInterval(() => elapsed.value++, 1000) }
    computing.value = true
    pollTimer = window.setTimeout(() => poll(), 12000)
    return
  }
  stop(); computing.value = false
  events.value = res?.events || []
}

async function poll() {
  try { handle(await quantApi.serenityEvents()) }
  catch { stop(); computing.value = false }
}

const drawer = ref(false)
const deepLoading = ref(false)
const deepData = ref<any>(null)
const deepTheme = ref('')

async function openDeep(ev: SerenityEvent) {
  deepTheme.value = ev.theme; drawer.value = true; deepLoading.value = true; deepData.value = null
  try { deepData.value = await quantApi.serenityDeep({ theme: ev.theme, event: ev.event, beneficiaries: ev.beneficiaries }) }
  catch (e: any) { ElMessage.error(e?.message || '深度分析失败') }
  finally { deepLoading.value = false }
}

onMounted(() => load(false))
onUnmounted(stop)
</script>

<style scoped>
.serenity { padding: 16px; }
.head h2 { margin: 0 0 4px; display: flex; gap: 8px; align-items: center; }
.sub { color: #909399; font-size: 13px; margin: 0 0 12px; }
.computing { display: flex; gap: 8px; align-items: center; color: #909399; padding: 40px; justify-content: center; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 12px; }
.card .theme { margin-bottom: 6px; }
.card .event { font-weight: 600; font-size: 14px; }
.card .thesis { color: #606266; font-size: 13px; margin: 6px 0; }
.card .benes { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.card .vf { display: flex; flex-direction: column; gap: 2px; font-size: 12px; color: #909399; }
.deep { white-space: pre-wrap; font-size: 12px; }
</style>
