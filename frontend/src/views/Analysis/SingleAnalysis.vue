<template>
  <div class="research-page">
    <div class="page-head">
      <div>
        <h1>个股深研</h1>
        <p>对单只 A 股运行多智能体深度分析（行业 / 估值 / 情景），生成结构化研究结论。</p>
      </div>
    </div>

    <section class="search-band">
      <el-input
        v-model="symbol"
        placeholder="输入股票代码或名称，如 000001 / 平安银行"
        class="search-input"
        @keyup.enter="run"
      />
      <el-button type="primary" :loading="loading" @click="run">
        <el-icon><Search /></el-icon>深度分析
      </el-button>
    </section>

    <el-empty v-if="!loading && !result" description="输入股票代码开始深度分析" />

    <div v-if="loading" class="loading-hint">
      <el-icon class="is-loading"><Loading /></el-icon>
      正在运行多智能体分析，首次可能需要十几秒……
    </div>

    <template v-if="result && !loading">
      <section class="head-card">
        <div class="title">
          <strong>{{ result.stock_name || result.name || result.symbol }}</strong>
          <span class="code">{{ result.symbol }}</span>
          <el-tag v-if="rating" :type="ratingType" effect="dark" size="large">{{ rating }}</el-tag>
        </div>
        <div class="metrics">
          <div><span>量化评分</span><strong>{{ scoreDisplay }}</strong></div>
          <div>
            <span>现价</span>
            <strong>{{ result.current_price != null ? Number(result.current_price).toFixed(2) : '-' }}</strong>
          </div>
          <div>
            <span>涨跌幅</span>
            <strong :class="pctClass">
              {{ result.price_change_percent != null
                ? (result.price_change_percent >= 0 ? '+' : '') + Number(result.price_change_percent).toFixed(2) + '%'
                : '-' }}
            </strong>
          </div>
        </div>
        <el-button class="to-paper" @click="goPaper">去模拟交易</el-button>
      </section>

      <el-alert v-if="degraded" type="info" :closable="false" show-icon class="degraded">
        深度分析模块本次降级，下方为量化画像结论。
      </el-alert>

      <section v-for="sec in sections" :key="sec.key" class="panel">
        <div class="panel-title">{{ sec.label }}</div>
        <p class="sec-text">{{ sec.text }}</p>
      </section>

      <el-collapse v-if="hasRaw" class="raw">
        <el-collapse-item title="完整分析数据（JSON）" name="raw">
          <pre>{{ rawJson }}</pre>
        </el-collapse-item>
      </el-collapse>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search, Loading } from '@element-plus/icons-vue'
import { analysisApi } from '@/api/analysis'

const route = useRoute()
const router = useRouter()
const symbol = ref('')
const loading = ref(false)
const result = ref<Record<string, any> | null>(null)

// Only these meaningful narrative fields are shown, in this order. Everything
// else in the result (ids, timestamps, scores, llm metadata) stays hidden.
const SECTIONS: Array<[string, string]> = [
  ['summary', '综合结论'],
  ['recommendation', '操作建议'],
  ['technical_analysis', '技术面分析'],
  ['fundamental_analysis', '基本面分析'],
  ['sentiment_analysis', '情绪面分析'],
  ['news_analysis', '消息面分析'],
  ['risk_assessment', '风险评估'],
]

const rating = computed(() => result.value?.overall_rating || result.value?.deep_rating || '')
const ratingType = computed(() => {
  const r = String(rating.value)
  if (/买入|强烈|看好|positive|buy/i.test(r)) return 'danger'
  if (/卖出|回避|看空|negative|sell/i.test(r)) return 'success'
  return 'warning'
})
const pctClass = computed(() => {
  const v = result.value?.price_change_percent
  return v == null ? '' : v >= 0 ? 'up' : 'down'
})

const scoreDisplay = computed(() => {
  const r = result.value
  const s = r?.overall_score ?? r?.quant_score ?? r?.technical_score
  return s == null ? '-' : Number(s).toFixed(1)
})

const degraded = computed<string>(() => result.value?.deep_analysis_error || '')

const sections = computed(() => {
  const r = result.value
  if (!r) return [] as Array<{ key: string; label: string; text: string }>
  return SECTIONS
    .filter(([k]) => typeof r[k] === 'string' && (r[k] as string).trim().length > 0)
    .map(([key, label]) => ({ key, label, text: r[key] as string }))
})

const hasRaw = computed(() => !!result.value)
const rawJson = computed(() => (result.value ? JSON.stringify(result.value, null, 2) : ''))

const run = async () => {
  const q = symbol.value.trim()
  if (!q) {
    ElMessage.warning('请输入股票代码或名称')
    return
  }
  loading.value = true
  result.value = null
  try {
    const started: any = await analysisApi.runSingle(q)
    if (started?.success === false) {
      ElMessage.error(started?.message || '分析失败')
      return
    }
    const taskId = started?.data?.task_id || started?.data?.analysis_id
    if (!taskId) {
      ElMessage.error('未获得分析任务编号')
      return
    }
    const res: any = await analysisApi.result(taskId)
    if (!res?.data) {
      ElMessage.error(res?.message || '分析结果为空（可能数据源暂不可用）')
      return
    }
    result.value = res.data
  } catch (error: any) {
    ElMessage.error(error?.message || '分析失败')
  } finally {
    loading.value = false
  }
}

const goPaper = () => router.push('/paper')

onMounted(() => {
  const s = route.query.stock
  if (typeof s === 'string' && s) {
    symbol.value = s
    run()
  }
})
</script>

<style scoped lang="scss">
.page-head { margin-bottom: 12px; h1 { margin: 0 0 4px; font-size: 22px; } p { margin: 0; color: var(--el-text-color-secondary); } }

.search-band {
  display: flex;
  gap: 10px;
  margin-bottom: 14px;
}
.search-input { max-width: 420px; }

.loading-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--el-text-color-secondary);
  padding: 24px 0;
}

.head-card {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 24px;
  flex-wrap: wrap;

  .title { display: flex; align-items: center; gap: 12px; }
  .title strong { font-size: 22px; }
  .code { color: var(--el-text-color-secondary); }

  .metrics { display: flex; gap: 28px; }
  .metrics span { display: block; font-size: 12px; color: var(--el-text-color-secondary); }
  .metrics strong { font-size: 18px; }

  .to-paper { margin-left: auto; }
}

.panel {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 14px 16px;
  margin-bottom: 10px;
}
.panel-title { font-weight: 700; font-size: 16px; margin-bottom: 8px; }
.sec-text { margin: 0; line-height: 1.8; color: var(--el-text-color-primary); white-space: pre-wrap; }

.raw { margin-top: 8px; }
.raw pre { white-space: pre-wrap; word-break: break-all; font-size: 12px; color: var(--el-text-color-secondary); }

.up { color: #ef4444; }
.down { color: #16a34a; }
</style>
