<template>
  <div class="pick-page">
    <div class="page-head">
      <div>
        <h1>AI选股流水线</h1>
        <p>多 Agent 流水线：题材 Agent → 多路选股 → Critic 打分优汰 → T+5 反向闭环。仅供研究分析，不构成投资建议。</p>
      </div>
    </div>

    <section class="controls">
      <span class="title">流水线</span>
      <div class="right">
        <el-input v-model="universeInput" placeholder="可选：自定义代码，逗号分隔（留空跑全市场池）" style="width: 360px" clearable />
        <el-button type="primary" :loading="running" @click="runPipeline">跑一次流水线</el-button>
        <el-button :loading="t5Loading" @click="runT5">T+5 反向复盘</el-button>
      </div>
    </section>

    <!-- KPI 概览 -->
    <section class="kpi-band" v-loading="running">
      <div class="kpi-card"><span class="kpi-title">候选数</span><strong class="kpi-num">{{ result?.candidates ?? '-' }}</strong></div>
      <div class="kpi-card"><span class="kpi-title">保留</span><strong class="kpi-num up">{{ kept.length }}</strong></div>
      <div class="kpi-card"><span class="kpi-title">拒绝</span><strong class="kpi-num down">{{ rejected.length }}</strong></div>
      <div class="kpi-card">
        <span class="kpi-title">LLM</span>
        <strong class="kpi-num" style="font-size:18px">
          题材 {{ result?.llm_used?.theme ? '✓' : '规则' }} ｜ 评审 {{ result?.llm_used?.critic ? '✓' : '规则' }}
        </strong>
      </div>
    </section>

    <el-alert v-if="result?.feedback_notes?.length" :closable="false" type="info" show-icon class="notes">
      <template #title>反馈优汰：{{ result.feedback_notes.join('；') }}</template>
    </el-alert>

    <!-- 热点主题标签 -->
    <div v-if="themes.length" class="themes">
      <span class="lbl">热点主题：</span>
      <el-tag v-for="t in themes" :key="t.name" effect="plain" :type="t.heat >= 70 ? 'danger' : t.heat >= 40 ? 'warning' : 'info'" class="theme-tag">
        {{ t.name }} <b>{{ t.heat }}</b>
      </el-tag>
    </div>

    <el-tabs v-model="tab" class="tabs">
      <el-tab-pane :label="`保留候选 (${kept.length})`" name="kept">
        <el-table :data="kept" stripe v-loading="running" empty-text="暂无保留候选，先跑一次流水线">
          <el-table-column prop="symbol" label="代码" width="90" />
          <el-table-column prop="name" label="名称" width="110" />
          <el-table-column label="Critic评分" width="120" sortable :sort-method="(a:any,b:any)=>a.score-b.score">
            <template #default="{ row }"><b :style="{ color: scoreColor(row.score) }">{{ row.score }}</b> / 10</template>
          </el-table-column>
          <el-table-column label="来源" min-width="180">
            <template #default="{ row }">
              <el-tag v-for="s in row.sources" :key="s" size="small" effect="plain" class="src-tag">{{ srcLabel(s) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="LLM/规则点评" min-width="240">
            <template #default="{ row }">{{ row.llm_comment || '—' }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane :label="`拒绝 (${rejected.length})`" name="rejected">
        <el-table :data="rejected" stripe empty-text="无拒绝项">
          <el-table-column prop="symbol" label="代码" width="90" />
          <el-table-column prop="name" label="名称" width="110" />
          <el-table-column label="评分" width="100">
            <template #default="{ row }">{{ row.score }} / 10</template>
          </el-table-column>
          <el-table-column label="来源" min-width="160">
            <template #default="{ row }">
              <el-tag v-for="s in row.sources" :key="s" size="small" effect="plain" class="src-tag">{{ srcLabel(s) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="拒绝理由" min-width="240">
            <template #default="{ row }"><span class="down">{{ row.reject_reason }}</span></template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="历史运行" name="runs">
        <el-table :data="runs" stripe empty-text="暂无历史" @row-click="openRun">
          <el-table-column prop="run_id" label="运行ID" min-width="160" />
          <el-table-column prop="date" label="日期" width="120" />
          <el-table-column prop="candidates" label="候选" width="90" />
          <el-table-column prop="kept" label="保留" width="90" />
          <el-table-column prop="rejected" label="拒绝" width="90" />
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <el-alert v-if="t5Result" :closable="true" type="success" show-icon class="notes">
      <template #title>
        T+5 复盘：评估 {{ t5Result.evaluated }} 只，胜率 {{ t5Result.win_rate ?? '-' }}，下跌 {{ t5Result.losers }} 只，
        已写回 {{ (t5Result.rules || []).length }} 条优汰规则供下轮使用。
      </template>
    </el-alert>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { stockPickApi } from '@/api/stockPick'

const running = ref(false)
const t5Loading = ref(false)
const tab = ref('kept')
const universeInput = ref('')
const result = ref<any>(null)
const runs = ref<any[]>([])
const t5Result = ref<any>(null)

const kept = computed(() => result.value?.kept || [])
const rejected = computed(() => result.value?.rejected || [])
const themes = computed(() => result.value?.themes || [])

const SRC: Record<string, string> = {
  strategy: '策略库', money_follow: '资金跟随', market: '随市趋势',
  contrarian: '逆向超跌', retail_sentiment: '情绪低吸', theme: '题材热点',
}
const srcLabel = (s: string) => SRC[s] || s
const scoreColor = (v: number) => (v >= 7.5 ? '#ef232a' : v >= 6 ? '#e6a23c' : '#14b143')

const runPipeline = async () => {
  running.value = true
  try {
    const universe = universeInput.value.split(/[，,\s]+/).map(s => s.trim()).filter(Boolean)
    const res: any = await stockPickApi.run(universe.length ? universe : undefined)
    result.value = res?.data || res
    tab.value = 'kept'
    loadRuns()
  } catch (e: any) {
    ElMessage.error(e?.message || '跑流水线失败')
  } finally {
    running.value = false
  }
}

const loadRuns = async () => {
  try {
    const res: any = await stockPickApi.runs()
    runs.value = (res?.data || res)?.runs || []
  } catch { /* ignore */ }
}

const openRun = async (row: any) => {
  try {
    const res: any = await stockPickApi.detail(row.run_id)
    result.value = res?.data || res
    tab.value = 'kept'
  } catch (e: any) {
    ElMessage.error(e?.message || '加载运行详情失败')
  }
}

const runT5 = async () => {
  t5Loading.value = true
  try {
    const res: any = await stockPickApi.t5Review(result.value?.run_id)
    t5Result.value = res?.data || res
    ElMessage.success('T+5 反向复盘完成')
  } catch (e: any) {
    ElMessage.error(e?.message || 'T+5 复盘失败')
  } finally {
    t5Loading.value = false
  }
}

onMounted(loadRuns)
</script>

<style scoped lang="scss">
.page-head { margin-bottom: 12px; h1 { margin: 0 0 4px; font-size: 22px; } p { margin: 0; color: var(--el-text-color-secondary); font-size: 13px; } }
.controls { display: flex; align-items: center; justify-content: space-between; margin: 6px 0 14px;
  .title { font-size: 18px; font-weight: 700; } .right { display: flex; gap: 10px; } }
.kpi-band { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 14px; }
.kpi-card { background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: 10px;
  padding: 14px 16px; display: flex; flex-direction: column; gap: 6px; }
.kpi-title { font-size: 13px; color: var(--el-text-color-secondary); }
.kpi-num { font-size: 26px; font-weight: 700; }
.notes { margin-bottom: 12px; }
.themes { margin-bottom: 12px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
  .lbl { font-size: 13px; color: var(--el-text-color-secondary); } }
.theme-tag b { margin-left: 4px; }
.src-tag { margin-right: 4px; }
.up { color: #ef232a; } .down { color: #14b143; }
.tabs { background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: 10px; padding: 8px 14px 14px; }
@media (max-width: 900px) { .kpi-band { grid-template-columns: 1fr 1fr; } }
</style>
