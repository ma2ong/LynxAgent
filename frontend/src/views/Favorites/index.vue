<template>
  <div class="fav-page">
    <div class="page-head">
      <div>
        <h1>我的自选股</h1>
        <p>跟踪关注的标的，同步实时行情，设置价格预警。</p>
      </div>
      <div class="head-actions">
        <el-button :loading="syncing" @click="syncRealtime">
          <el-icon><Refresh /></el-icon>同步行情
        </el-button>
        <el-button type="primary" @click="addVisible = true">
          <el-icon><Plus /></el-icon>添加自选
        </el-button>
      </div>
    </div>

    <section class="portfolio-panel" v-if="diagnostics">
      <div class="portfolio-head">
        <div>
          <span class="eyebrow">组合体检</span>
          <h2>{{ diagnostics.grade }} <strong>{{ Math.round(diagnostics.score || 0) }}</strong></h2>
          <p>{{ diagnostics.summary }}</p>
          <small>{{ diagnostics.assumption }}</small>
        </div>
        <el-button :loading="diagnosticsLoading" @click="loadDiagnostics">刷新体检</el-button>
      </div>

      <div class="metric-grid" v-if="diagnostics.portfolio">
        <div>
          <span>自选数量</span>
          <b>{{ diagnostics.portfolio.count }}</b>
        </div>
        <div>
          <span>等权波动</span>
          <b>{{ pct(diagnostics.portfolio.volatility) }}</b>
        </div>
        <div>
          <span>最大回撤</span>
          <b :class="diagnostics.portfolio.max_drawdown <= -0.22 ? 'down' : ''">{{ pct(diagnostics.portfolio.max_drawdown) }}</b>
        </div>
        <div>
          <span>平均相关性</span>
          <b>{{ diagnostics.portfolio.avg_correlation.toFixed(2) }}</b>
        </div>
        <div>
          <span>最高行业权重</span>
          <b>{{ pct(diagnostics.portfolio.top_industry_weight) }}</b>
        </div>
        <div>
          <span>历史覆盖率</span>
          <b>{{ pct(diagnostics.portfolio.return_coverage) }}</b>
        </div>
      </div>

      <div class="risk-strip" v-if="diagnostics.risk_flags?.length">
        <el-alert
          v-for="flag in diagnostics.risk_flags"
          :key="flag"
          :title="flag"
          type="warning"
          :closable="false"
          show-icon
        />
      </div>

      <div class="portfolio-cols">
        <div class="portfolio-box">
          <div class="box-title">优化动作</div>
          <ul>
            <li v-for="item in diagnostics.suggested_actions" :key="item">{{ item }}</li>
          </ul>
        </div>
        <div class="portfolio-box">
          <div class="box-title">行业暴露</div>
          <div v-for="item in diagnostics.industry_exposure.slice(0, 6)" :key="item.industry" class="exposure-row">
            <span>{{ item.industry }}</span>
            <el-progress :percentage="Math.round(item.weight * 100)" :stroke-width="8" />
          </div>
        </div>
        <div class="portfolio-box">
          <div class="box-title">高相关组合</div>
          <template v-if="diagnostics.correlation_pairs.length">
            <div v-for="pair in diagnostics.correlation_pairs.slice(0, 5)" :key="pair.left + pair.right" class="pair-row">
              <span>{{ pair.left_name }} / {{ pair.right_name }}</span>
              <b>{{ pair.correlation.toFixed(2) }}</b>
            </div>
          </template>
          <el-empty v-else description="暂无高相关组合" :image-size="48" />
        </div>
      </div>

      <el-table v-if="diagnostics.items?.length" :data="diagnostics.items" size="small" class="diagnostic-table">
        <el-table-column prop="symbol" label="代码" width="90" />
        <el-table-column prop="name" label="名称" width="110" />
        <el-table-column prop="industry" label="行业" min-width="120" show-overflow-tooltip />
        <el-table-column label="关注权重" width="100">
          <template #default="{ row }"><b>{{ pct(row.suggested_weight) }}</b></template>
        </el-table-column>
        <el-table-column label="量化分" width="90">
          <template #default="{ row }">{{ row.quant_score.toFixed(1) }}</template>
        </el-table-column>
        <el-table-column label="风控" width="90">
          <template #default="{ row }">{{ row.risk_control.toFixed(1) }}</template>
        </el-table-column>
        <el-table-column label="风险标签" min-width="180">
          <template #default="{ row }">
            <el-tag v-for="tag in row.risk_tags" :key="tag" size="small" type="warning" effect="plain" class="tag">{{ tag }}</el-tag>
            <span v-if="!row.risk_tags?.length">-</span>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <section class="panel" v-loading="loading">
      <div v-if="!items.length && !loading" class="table-empty">
        <strong>还没有自选股</strong>
        <p>添加常看的股票后，这里会同步实时行情、预警和组合体检。</p>
        <el-button type="primary" size="small" @click="addVisible = true">添加第一只</el-button>
      </div>
      <div v-if="selected.size" class="bulk-bar">
        <span>已选 <b>{{ selected.size }}</b> 只</span>
        <el-button size="small" @click="clearSelection">取消选择</el-button>
        <el-button size="small" type="danger" plain @click="removeSelected">批量删除</el-button>
      </div>
      <div v-if="items.length" class="t-wrap">
        <table class="t-table">
          <thead>
            <tr>
              <th class="c sel-col">
                <el-checkbox :model-value="allSelected" :indeterminate="someSelected" @change="toggleAll" />
              </th>
              <th>个股</th>
              <th class="r">现价</th>
              <th class="r">涨跌幅</th>
              <th class="r">加入后</th>
              <th class="r">预警 低/高</th>
              <th>标签</th>
              <th class="r">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in items" :key="row.symbol || row.stock_code"
                :class="{ picked: selected.has(row.symbol || row.stock_code) }">
              <td class="c sel-col">
                <el-checkbox :model-value="selected.has(row.symbol || row.stock_code)"
                             @change="toggleOne(row.symbol || row.stock_code)" />
              </td>
              <td>
                <div class="code-cell">
                  <span class="nm">{{ row.stock_name }}</span>
                  <span class="cd">{{ row.symbol || row.stock_code }}<template v-if="row.industry"> · {{ row.industry }}</template></span>
                </div>
              </td>
              <td class="r num">{{ row.current_price != null ? row.current_price.toFixed(2) : '-' }}</td>
              <td class="r">
                <span v-if="row.change_percent != null" class="pill" :class="row.change_percent >= 0 ? 'u' : 'd'">
                  {{ row.change_percent >= 0 ? '+' : '' }}{{ row.change_percent.toFixed(2) }}%
                </span>
                <span v-else class="muted">-</span>
              </td>
              <td class="r">
                <span v-if="row.change_since_added_percent != null" class="pill ghost" :class="row.change_since_added_percent >= 0 ? 'u' : 'd'">
                  {{ row.change_since_added_percent >= 0 ? '+' : '' }}{{ row.change_since_added_percent.toFixed(2) }}%
                </span>
                <span v-else class="muted">-</span>
              </td>
              <td class="r num muted">{{ row.alert_price_low ?? '-' }} / {{ row.alert_price_high ?? '-' }}</td>
              <td>
                <span v-for="t in row.tags || []" :key="t" class="chip-tag">{{ t }}</span>
                <span v-if="!(row.tags || []).length" class="muted">-</span>
              </td>
              <td class="r ops">
                <button class="op" @click="goResearch(row.symbol || row.stock_code)">深研</button>
                <button class="op del" @click="remove(row)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <el-dialog v-model="addVisible" title="添加自选股" width="420px">
      <el-form label-position="top">
        <el-form-item label="股票代码（6 位）">
          <el-input v-model="addForm.symbol" placeholder="如 000001" maxlength="6" />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="addForm.stock_name" placeholder="如 平安银行" />
        </el-form-item>
        <el-form-item label="标签（逗号分隔，可选）">
          <el-input v-model="tagsText" placeholder="如 银行,低估" />
        </el-form-item>
        <el-form-item label="价格预警 低 / 高（可选）">
          <div class="alert-row">
            <el-input-number v-model="addForm.alert_price_low" :min="0" :controls="false" placeholder="低" />
            <el-input-number v-model="addForm.alert_price_high" :min="0" :controls="false" placeholder="高" />
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addVisible = false">取消</el-button>
        <el-button type="primary" :loading="adding" @click="addFavorite">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Plus } from '@element-plus/icons-vue'
import { favoritesApi, type FavoriteItem, type AddFavoriteReq, type PortfolioDiagnostics } from '@/api/favorites'

const router = useRouter()
const loading = ref(false)
const syncing = ref(false)
const adding = ref(false)
const addVisible = ref(false)
const items = ref<FavoriteItem[]>([])
const diagnostics = ref<PortfolioDiagnostics | null>(null)
const diagnosticsLoading = ref(false)
const tagsText = ref('')
const selected = ref(new Set<string>())

const addForm = reactive<AddFavoriteReq>({
  symbol: '',
  stock_name: '',
  alert_price_low: null,
  alert_price_high: null,
})

const unwrap = (res: any): FavoriteItem[] =>
  Array.isArray(res) ? res : Array.isArray(res?.data) ? res.data : []

const unwrapDiagnostics = (res: any): PortfolioDiagnostics | null =>
  res?.data && !Array.isArray(res.data) ? res.data : res || null

const pct = (value?: number | null) =>
  value == null || Number.isNaN(Number(value)) ? '-' : `${(Number(value) * 100).toFixed(1)}%`

const loadDiagnostics = async () => {
  diagnosticsLoading.value = true
  try {
    diagnostics.value = unwrapDiagnostics(await favoritesApi.diagnostics())
  } catch (error: any) {
    ElMessage.error(error?.message || '组合体检失败')
  } finally {
    diagnosticsLoading.value = false
  }
}

const load = async () => {
  loading.value = true
  try {
    items.value = unwrap(await favoritesApi.list())
    const alive = new Set(items.value.map(keyOf))
    selected.value = new Set([...selected.value].filter((c) => alive.has(c)))
    await loadDiagnostics()
  } catch (error: any) {
    ElMessage.error(error?.message || '加载自选股失败')
  } finally {
    loading.value = false
  }
}

const syncRealtime = async () => {
  syncing.value = true
  try {
    await favoritesApi.syncRealtime('akshare')
    await load()
    ElMessage.success('行情已同步')
  } catch (error: any) {
    ElMessage.error(error?.message || '同步失败')
  } finally {
    syncing.value = false
  }
}

const addFavorite = async () => {
  if (!/^\d{6}$/.test((addForm.symbol || '').trim())) {
    ElMessage.warning('请输入 6 位股票代码')
    return
  }
  if (!addForm.stock_name?.trim()) {
    ElMessage.warning('请输入名称')
    return
  }
  adding.value = true
  try {
    const tags = tagsText.value.split(/[,，]/).map((t) => t.trim()).filter(Boolean)
    await favoritesApi.add({ ...addForm, tags })
    ElMessage.success('已添加')
    addVisible.value = false
    addForm.symbol = ''
    addForm.stock_name = ''
    addForm.alert_price_low = null
    addForm.alert_price_high = null
    tagsText.value = ''
    await load()
  } catch (error: any) {
    ElMessage.error(error?.message || '添加失败')
  } finally {
    adding.value = false
  }
}

const keyOf = (row: FavoriteItem) => row.symbol || row.stock_code || ''

const allSelected = computed(() => items.value.length > 0 && selected.value.size === items.value.length)
const someSelected = computed(() => selected.value.size > 0 && selected.value.size < items.value.length)
const toggleOne = (code: string) => {
  const next = new Set(selected.value)
  next.has(code) ? next.delete(code) : next.add(code)
  selected.value = next
}
const toggleAll = () => {
  selected.value = allSelected.value ? new Set() : new Set(items.value.map(keyOf))
}
const clearSelection = () => { selected.value = new Set() }

// 组合体检依赖自选列表，删完要重算；但它慢，绝不能挡住列表更新，所以后台跑。
const refreshDiagnosticsSoon = () => { void loadDiagnostics() }

/**
 * 删除走乐观更新：先把行从本地列表摘掉，再后台发请求。
 * 原来是「等服务端返回 → 重新拉整张表 → 全表 loading 遮罩」，而整表要为每只股票取
 * 实时报价和行业，所以点一下要等好几秒、整个表变灰。本地状态删掉之后就已经是正确
 * 结果了，没有理由再拉一次。失败才把行放回原位并提示。
 */
const removeCodes = async (codes: string[]) => {
  if (!codes.length) return
  const removed = items.value.filter((it) => codes.includes(keyOf(it)))
  const positions = new Map(removed.map((it) => [keyOf(it), items.value.indexOf(it)]))
  items.value = items.value.filter((it) => !codes.includes(keyOf(it)))
  const next = new Set(selected.value)
  codes.forEach((c) => next.delete(c))
  selected.value = next

  const results = await Promise.allSettled(codes.map((c) => favoritesApi.remove(c)))
  const failed = removed.filter((_, i) => results[i].status === 'rejected')
  if (failed.length) {
    // 只把失败的放回去，成功的保持已删除；插回原位置，避免顺序跳动
    const restored = [...items.value]
    failed
      .sort((a, b) => (positions.get(keyOf(a)) ?? 0) - (positions.get(keyOf(b)) ?? 0))
      .forEach((it) => restored.splice(Math.min(positions.get(keyOf(it)) ?? restored.length, restored.length), 0, it))
    items.value = restored
    ElMessage.error(`${failed.length} 只删除失败，已恢复`)
  }
  refreshDiagnosticsSoon()
}

const remove = (row: FavoriteItem) => {
  // 单只删除不再弹确认框——Allen 要的是点一下就没。误删可立即重新添加，代价很低。
  void removeCodes([keyOf(row)])
}

const removeSelected = async () => {
  const codes = [...selected.value]
  try {
    await ElMessageBox.confirm(`确认删除选中的 ${codes.length} 只自选股？`, '批量删除', { type: 'warning' })
  } catch {
    return
  }
  void removeCodes(codes)
}

const goResearch = (code: string) => {
  router.push({ path: '/stock-analysis', query: { symbol: code } })
}

onMounted(load)
</script>

<style scoped lang="scss">
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;

  h1 { margin: 0 0 4px; font-size: 22px; }
  p { margin: 0; color: var(--el-text-color-secondary); }
}

.head-actions { display: flex; gap: 10px; }

.portfolio-panel {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 12px;
}

.portfolio-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;

  h2 {
    margin: 4px 0;
    font-size: 20px;
  }

  h2 strong {
    margin-left: 8px;
    color: var(--el-color-primary);
  }

  p {
    margin: 0 0 4px;
    color: var(--el-text-color-primary);
  }

  small {
    color: var(--el-text-color-secondary);
  }
}

.eyebrow {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(120px, 1fr));
  gap: 8px;
  margin-bottom: 12px;

  div {
    background: var(--el-fill-color-lighter);
    border-radius: 6px;
    padding: 10px 12px;
  }

  span {
    display: block;
    font-size: 12px;
    color: var(--el-text-color-secondary);
    margin-bottom: 4px;
  }

  b {
    font-size: 18px;
  }
}

.risk-strip {
  display: grid;
  gap: 8px;
  margin-bottom: 12px;
}

.portfolio-cols {
  display: grid;
  grid-template-columns: 1.1fr 1fr 1fr;
  gap: 12px;
  margin-bottom: 12px;
}

.portfolio-box {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 12px;
  min-height: 126px;

  ul {
    margin: 0;
    padding-left: 18px;
    color: var(--el-text-color-regular);
    line-height: 1.7;
  }
}

.box-title {
  font-size: 13px;
  font-weight: 700;
  margin-bottom: 10px;
}

.exposure-row {
  display: grid;
  grid-template-columns: 96px 1fr;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;

  span {
    font-size: 12px;
    color: var(--el-text-color-regular);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.pair-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 13px;
  padding: 5px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);

  &:last-child {
    border-bottom: 0;
  }
}

.diagnostic-table {
  margin-top: 8px;
}

.panel {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 14px;
  overflow-x: auto;
}

.table-empty {
  padding: 28px 0;
  color: var(--el-text-color-secondary);

  strong {
    display: block;
    margin-bottom: 5px;
    color: var(--el-text-color-primary);
    font-size: 15px;
  }

  p {
    margin: 0 0 12px;
  }
}

.tag { margin-right: 4px; }

.alert-row { display: flex; gap: 10px; width: 100%; }
.alert-row :deep(.el-input-number) { flex: 1; }

/* 终端风自选列表 */
.t-wrap { overflow-x: auto; }
.t-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.t-table thead th { background: #fafbfc; color: #9aa3b0; font-weight: 600; font-size: 11.5px; text-align: left;
  padding: 11px 14px; border-bottom: 1px solid #e3e6eb; white-space: nowrap; }
.t-table thead th.r { text-align: right; }
.t-table tbody td { padding: 11px 14px; border-bottom: 1px solid #eceef2; vertical-align: middle; }
.t-table tbody tr:last-child td { border-bottom: 0; }
.t-table tbody tr:hover { background: #fafbfe; }
.t-table .r { text-align: right; }
.t-table .num { font-variant-numeric: tabular-nums; font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; letter-spacing: -.2px; }
.t-table .muted { color: #9aa3b0; }
.code-cell { display: flex; flex-direction: column; line-height: 1.35; }
.code-cell .nm { font-weight: 700; font-size: 13px; }
.code-cell .cd { font-size: 11.5px; color: #9aa3b0; font-family: ui-monospace, Menlo, monospace; }
.pill { display: inline-flex; align-items: center; justify-content: flex-end; min-width: 64px; padding: 2px 9px; border-radius: 6px;
  font-weight: 700; font-size: 12.5px; font-variant-numeric: tabular-nums; }
.pill.u { background: #fdeef0; color: #e5384d; } .pill.d { background: #e9f7f1; color: #16a06a; }
.pill.ghost { background: transparent; min-width: 0; padding: 2px 0; }
.pill.ghost.u { color: #e5384d; } .pill.ghost.d { color: #16a06a; }
.chip-tag { display: inline-block; background: #f3f5f9; border: 1px solid #e3e6eb; border-radius: 6px; padding: 1px 7px;
  font-size: 11.5px; color: #5b6573; margin-right: 4px; }
.ops { white-space: nowrap; }
.op { border: 0; background: transparent; font: inherit; font-size: 12.5px; font-weight: 600; cursor: pointer; padding: 2px 6px; color: #2f6bff; }
.op.del { color: #e5384d; }
.op:hover { text-decoration: underline; }

.up { color: #ef4444; }
.down { color: #16a34a; }

@media (max-width: 1100px) {
  .metric-grid,
  .portfolio-cols {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .page-head,
  .portfolio-head {
    align-items: stretch;
    flex-direction: column;
  }

  .head-actions {
    flex-wrap: wrap;
  }

  .head-actions :deep(.el-button) { flex: 1; }

  .metric-grid {
    grid-template-columns: 1fr 1fr;
  }

  .portfolio-panel {
    padding: 12px;
  }

  .panel :deep(.el-table),
  .diagnostic-table {
    min-width: 860px;
  }

  :deep(.el-dialog) {
    width: calc(100vw - 24px) !important;
  }

  .alert-row {
    align-items: stretch;
    flex-direction: column;
  }
}

@media (max-width: 520px) {
  .metric-grid {
    grid-template-columns: 1fr;
  }
}

/* 多选与批量删除 */
.sel-col { width: 40px; }
.t-table td.c, .t-table th.c { text-align: center; }
.t-table tr.picked { background: var(--el-color-primary-light-9); }
.bulk-bar { display: flex; align-items: center; gap: 10px; padding: 8px 10px; margin-bottom: 8px;
  border: 1px solid var(--el-color-primary-light-7); border-radius: 8px;
  background: var(--el-color-primary-light-9); font-size: 13px; }
.bulk-bar b { color: var(--el-color-primary); }
</style>
