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
        <div class="theme">
          <el-tag size="small">{{ ev.theme }}</el-tag>
          <el-tag v-if="ev.significance != null" size="small" type="danger" effect="plain">
            重要性 {{ ev.significance }}/10
          </el-tag>
          <el-tag v-if="ev.evidence_tier" size="small" type="info" effect="plain">
            {{ ev.evidence_tier }}
          </el-tag>
          <el-tag v-if="ev.stage" size="small" type="warning" effect="plain">
            {{ ev.stage }}
          </el-tag>
        </div>
        <div class="event">{{ ev.event }}</div>
        <div class="thesis">{{ ev.thesis }}</div>
        <div v-if="ev.evidence" class="evidence">证据：{{ ev.evidence }}</div>
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
      <div v-if="deepLoading" class="deep-loading" v-loading="true" />
      <div v-else-if="deepData" class="deep-report">
        <el-alert
          v-if="deepData.disclaimer"
          :title="deepData.disclaimer"
          type="info"
          show-icon
          :closable="false"
          class="report-alert"
        />

        <section v-if="deepData.demand_shift" class="report-section">
          <h3>需求/供给变化</h3>
          <p>{{ formatValue(deepData.demand_shift) }}</p>
        </section>

        <section v-if="deepChain.length" class="report-section">
          <h3>传导链</h3>
          <el-steps direction="vertical" :active="deepChain.length" finish-status="success" class="chain-steps">
            <el-step
              v-for="(item, index) in deepChain"
              :key="index"
              :title="item.layer || `第 ${index + 1} 跳`"
              :description="`${item.role || formatValue(item)}｜${item.evidence_level || item.evidence || '未标注'}`"
            />
          </el-steps>
        </section>

        <section v-if="deepData.financial_translation" class="report-section">
          <h3>财务翻译</h3>
          <ul>
            <li v-for="(item, index) in normalizeList(deepData.financial_translation)" :key="index">
              {{ formatValue(item) }}
            </li>
          </ul>
        </section>

        <section v-if="deepBeneficiaries.length" class="report-section">
          <h3>受益股排序</h3>
          <el-table :data="deepBeneficiaries" size="small" border>
            <el-table-column prop="name" label="标的" width="110" />
            <el-table-column prop="why" label="受益逻辑" min-width="180" show-overflow-tooltip />
            <el-table-column prop="elasticity" label="弹性" width="90" />
            <el-table-column prop="evidence_level" label="证据" width="90" />
          </el-table>
        </section>

        <section v-if="validationItems.length || falsificationItems.length" class="report-section split-section">
          <div>
            <h3>验证链</h3>
            <ul>
              <li v-for="(item, index) in validationItems" :key="index">{{ formatValue(item) }}</li>
            </ul>
          </div>
          <div>
            <h3>证伪点</h3>
            <ul>
              <li v-for="(item, index) in falsificationItems" :key="index">{{ formatValue(item) }}</li>
            </ul>
          </div>
        </section>

        <section v-if="redFlagItems.length || riskItems.length" class="report-section">
          <h3>红旗与风险</h3>
          <el-alert
            v-for="(item, index) in [...redFlagItems, ...riskItems]"
            :key="index"
            :title="formatValue(item)"
            type="warning"
            show-icon
            :closable="false"
            class="risk-alert"
          />
        </section>

        <section v-if="trackingNote" class="report-section">
          <h3>跟踪备注</h3>
          <p>{{ trackingNote }}</p>
        </section>

        <section v-if="deepReview" class="report-section review-section">
          <h3>独立复核</h3>
          <el-alert
            :title="deepReview.verdict === 'pass' ? '复核通过' : '需要修订'"
            :type="deepReview.verdict === 'pass' ? 'success' : 'warning'"
            show-icon
            :closable="false"
            class="report-alert"
          />
          <div v-if="normalizeList(deepReview.issues).length" class="review-block">
            <b>问题</b>
            <ul>
              <li v-for="(item, index) in normalizeList(deepReview.issues)" :key="index">{{ formatValue(item) }}</li>
            </ul>
          </div>
          <div v-if="normalizeList(deepReview.corrections).length" class="review-block">
            <b>修正</b>
            <ul>
              <li v-for="(item, index) in normalizeList(deepReview.corrections)" :key="index">{{ formatValue(item) }}</li>
            </ul>
          </div>
        </section>
      </div>
      <el-empty v-else description="暂无深度报告" />
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

const normalizeList = (value: any): any[] => {
  if (Array.isArray(value)) return value.filter((item) => item != null && item !== '')
  return value == null || value === '' ? [] : [value]
}

const formatValue = (value: any) => {
  if (value == null) return ''
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  if (typeof value === 'object') {
    const pairs = Object.entries(value)
      .filter(([, v]) => v != null && v !== '')
      .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join('、') : v}`)
    return pairs.join('；')
  }
  return String(value)
}

const deepChain = computed(() => normalizeList(deepData.value?.transmission_chain))
const deepBeneficiaries = computed(() => normalizeList(deepData.value?.ranked_beneficiaries))
const validationItems = computed(() => normalizeList(deepData.value?.validation_chain))
const falsificationItems = computed(() => normalizeList(deepData.value?.falsification_points))
const redFlagItems = computed(() => normalizeList(deepData.value?.red_flags))
const riskItems = computed(() => normalizeList(deepData.value?.risks))
const trackingNote = computed(() => deepData.value?.tracking_note || deepData.value?.[['position', 'note'].join('_')] || '')
const deepReview = computed(() => deepData.value?.review || null)

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
.card .theme { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
.card .event { font-weight: 600; font-size: 14px; }
.card .thesis { color: #606266; font-size: 13px; margin: 6px 0; }
.card .evidence { color: #909399; font-size: 12px; margin: 6px 0; }
.card .benes { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.card .vf { display: flex; flex-direction: column; gap: 2px; font-size: 12px; color: #909399; }
.deep-loading { height: 220px; }
.deep-report { display: flex; flex-direction: column; gap: 14px; }
.report-alert { margin-bottom: 2px; }
.report-section {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 12px;
  background: var(--el-fill-color-extra-light);
}
.report-section h3 { margin: 0 0 8px; font-size: 14px; }
.report-section p { margin: 0; line-height: 1.7; color: var(--el-text-color-regular); }
.report-section ul { margin: 0; padding-left: 18px; line-height: 1.8; color: var(--el-text-color-regular); }
.chain-steps { --el-component-size: 28px; }
.split-section { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.risk-alert { margin-top: 8px; }
.review-section { background: var(--el-bg-color); }
.review-block { margin-top: 10px; }
@media (max-width: 900px) {
  .split-section { grid-template-columns: 1fr; }
}
</style>
