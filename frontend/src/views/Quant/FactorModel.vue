<template>
  <div class="factor-model">
    <div class="page-head">
      <h2>AI 因子模型 <el-tag size="small" type="success">LightGBM · 滚动再训练</el-tag></h2>
      <p class="sub">机器学习从上百个量价因子中学习选股权重，滚动再训练适应市场风格漂移，并用样本外回测验证。</p>
    </div>

    <el-card shadow="never" class="ctrl">
      <div class="ctrl-row">
        <span class="lbl">股票池</span>
        <el-select v-model="form.universe_limit" style="width: 130px">
          <el-option :value="300" label="300 只" />
          <el-option :value="500" label="500 只" />
          <el-option :value="800" label="800 只" />
          <el-option :value="0" label="全市场 (~5000)" />
        </el-select>
        <span class="lbl">持有周期</span>
        <el-select v-model="form.horizon" style="width: 100px">
          <el-option :value="5" label="5 日" />
          <el-option :value="10" label="10 日" />
          <el-option :value="20" label="20 日" />
        </el-select>
        <span class="lbl">选股数</span>
        <el-select v-model="form.k" style="width: 100px">
          <el-option :value="30" label="Top 30" />
          <el-option :value="50" label="Top 50" />
        </el-select>
        <span class="lbl">市值中性化</span>
        <el-switch v-model="form.neutralize" />
        <el-button type="primary" :loading="busy" @click="run(false)">
          {{ loading ? '提交中…' : computing ? '计算中…' : '运行模型' }}
        </el-button>
        <el-button text :disabled="busy" @click="run(true)">强制重算</el-button>
        <span v-if="result?.cached" class="cache-tip">缓存结果（{{ Math.round((result.age_sec || 0) / 60) }} 分钟前）</span>
      </div>
      <p v-if="computing" class="hint">
        {{ form.universe_limit === 0 ? '全市场（约 5000 只）' : `${form.universe_limit} 只` }}
        后台计算中，已用 {{ elapsed }}s{{ form.universe_limit === 0 ? '（全市场首次约 5-10 分钟）' : '' }}，页面会自动刷新。结果缓存 6 小时。
      </p>
      <p v-else-if="loading" class="hint">建因子面板 + 滚动训练多个模型，请稍候。结果会缓存 6 小时。</p>
    </el-card>

    <template v-if="result && !result.error">
      <div class="metric-grid">
        <div class="metric"><div class="m-val">{{ fmt(result.ic.rank_ic_mean, 4) }}</div><div class="m-lbl">RankIC<span class="m-hint">>0.03 即有效</span></div></div>
        <div class="metric"><div class="m-val">{{ fmt(result.ic.rank_icir, 2) }}</div><div class="m-lbl">RankICIR<span class="m-hint">信号稳定性</span></div></div>
        <div class="metric"><div class="m-val" :class="cls(result.metrics.topk.annual_return)">{{ pct(result.metrics.topk.annual_return) }}</div><div class="m-lbl">Top-K 年化</div></div>
        <div class="metric"><div class="m-val">{{ fmt(result.metrics.topk.sharpe, 2) }}</div><div class="m-lbl">Top-K 夏普</div></div>
        <div class="metric"><div class="m-val" :class="cls(excess)">{{ pct(excess) }}</div><div class="m-lbl">超额收益<span class="m-hint">vs 等权基准</span></div></div>
        <div class="metric"><div class="m-val" :class="cls(result.metrics.long_short.annual_return)">{{ pct(result.metrics.long_short.annual_return) }}</div><div class="m-lbl">多空年化<span class="m-hint">排序能力</span></div></div>
      </div>

      <div class="cols">
        <el-card shadow="never" class="chart-card">
          <template #header><span>样本外净值曲线</span>
            <span class="oos-range" v-if="result.curves.dates.length">{{ result.curves.dates[0] }} ~ {{ result.curves.dates[result.curves.dates.length - 1] }}</span>
          </template>
          <div ref="chartEl" class="chart"></div>
        </el-card>

        <el-card shadow="never" class="pick-card">
          <template #header><span>{{ result.pick_date }} Top-{{ result.k }} 选股</span></template>
          <el-table :data="result.picks" height="420" size="small" stripe>
            <el-table-column type="index" label="#" width="48" />
            <el-table-column prop="symbol" label="代码" width="90" />
            <el-table-column prop="name" label="名称" />
            <el-table-column prop="score" label="模型分" width="90" align="right">
              <template #default="{ row }">{{ fmt(row.score, 4) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </div>

      <p class="disclaimer">本结果为量化模型样本外回测，不构成投资建议。模型共训练 {{ result.n_models }} 个滚动窗口，股票池 {{ result.universe }} 只。</p>
    </template>

    <div v-else-if="computing" class="computing-box">
      <el-icon class="is-loading"><Loading /></el-icon>
      <span>模型计算中，已用 {{ elapsed }}s…</span>
    </div>
    <el-empty v-else-if="!busy && !result" description="选择参数后点击「运行模型」" />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import { echarts, type ECharts } from '@/utils/echarts'
import { quantApi, type MLFactorResult } from '@/api/quant'

const loading = ref(false)
const computing = ref(false)
const elapsed = ref(0)
const result = ref<MLFactorResult | null>(null)
const form = reactive({ universe_limit: 500, horizon: 5, k: 50, mode: 'rolling' as const, neutralize: true })
const busy = computed(() => loading.value || computing.value)

const chartEl = ref<HTMLDivElement>()
let chart: ECharts | null = null
let pollTimer: number | undefined
let elapsedTimer: number | undefined

function stopTimers() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = undefined }
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = undefined }
}

const excess = computed(() =>
  result.value ? result.value.metrics.topk.annual_return - result.value.metrics.benchmark.annual_return : 0
)

function renderChart() {
  const c = result.value?.curves
  if (!c || !chartEl.value) return
  if (!chart) chart = echarts.init(chartEl.value)
  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['Top-K组合', '等权基准', '多空对冲'], top: 0 },
    grid: { left: 48, right: 16, top: 36, bottom: 40 },
    xAxis: { type: 'category', data: c.dates, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', scale: true, axisLabel: { formatter: (v: number) => v.toFixed(2) } },
    series: [
      { name: 'Top-K组合', type: 'line', data: c.topk, smooth: true, showSymbol: false, lineStyle: { width: 2.5, color: '#e6433c' } },
      { name: '等权基准', type: 'line', data: c.benchmark, smooth: true, showSymbol: false, lineStyle: { width: 1.5, color: '#909399' } },
      { name: '多空对冲', type: 'line', data: c.long_short, smooth: true, showSymbol: false, lineStyle: { width: 1.5, color: '#409eff', type: 'dashed' } }
    ]
  })
}

watch(result, () => { if (result.value && !result.value.error) nextTick(renderChart) })

function onResize() { chart?.resize() }
onMounted(() => window.addEventListener('resize', onResize))
onUnmounted(() => { stopTimers(); window.removeEventListener('resize', onResize); chart?.dispose() })

function handle(res: MLFactorResult) {
  if (res?.status === 'computing') {
    if (!computing.value) {
      elapsed.value = res.elapsed_sec || 0
      elapsedTimer = window.setInterval(() => elapsed.value++, 1000)
    }
    computing.value = true
    pollTimer = window.setTimeout(poll, 12000)
    return
  }
  stopTimers()
  computing.value = false
  if (res?.status === 'error' || res?.error) {
    ElMessage.error(String(res.error || '计算失败'))
    return
  }
  result.value = res
}

async function poll() {
  try { handle(await quantApi.factorModel({ ...form })) }
  catch (e: any) { stopTimers(); computing.value = false; ElMessage.error(e?.message || '轮询失败') }
}

async function run(force: boolean) {
  stopTimers()
  computing.value = false
  result.value = null
  loading.value = true
  try { handle(await quantApi.factorModel({ ...form, force })) }
  catch (e: any) { ElMessage.error(e?.message || '运行失败') }
  finally { loading.value = false }
}

const fmt = (v: number, d = 2) => (v == null ? '-' : Number(v).toFixed(d))
const pct = (v: number) => (v == null ? '-' : (v * 100).toFixed(1) + '%')
const cls = (v: number) => (v > 0 ? 'up' : v < 0 ? 'down' : '')
</script>

<style scoped>
.factor-model { padding: 16px; }
.page-head h2 { margin: 0 0 4px; display: flex; align-items: center; gap: 8px; }
.sub { color: #909399; font-size: 13px; margin: 0 0 12px; }
.ctrl { margin-bottom: 16px; }
.ctrl-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.lbl { font-size: 13px; color: #606266; }
.cache-tip { font-size: 12px; color: #909399; }
.hint { font-size: 12px; color: #e6a23c; margin: 8px 0 0; }
.computing-box { display: flex; align-items: center; justify-content: center; gap: 10px; padding: 60px; color: #909399; font-size: 15px; }
.computing-box .el-icon { font-size: 22px; }
.metric-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; margin-bottom: 16px; }
.metric { background: #fff; border: 1px solid #ebeef5; border-radius: 8px; padding: 14px; text-align: center; }
.m-val { font-size: 22px; font-weight: 600; }
.m-val.up { color: #e6433c; } .m-val.down { color: #2ba471; }
.m-lbl { font-size: 12px; color: #909399; margin-top: 4px; display: flex; flex-direction: column; gap: 2px; }
.m-hint { font-size: 10px; color: #c0c4cc; }
.cols { display: grid; grid-template-columns: 1.6fr 1fr; gap: 16px; }
.chart { height: 420px; }
.oos-range { font-size: 12px; color: #909399; margin-left: 8px; }
.disclaimer { font-size: 12px; color: #c0c4cc; margin-top: 12px; }
@media (max-width: 1100px) { .metric-grid { grid-template-columns: repeat(3, 1fr); } .cols { grid-template-columns: 1fr; } }
</style>
