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
          <div class="box-title">建议动作</div>
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
        <el-table-column label="建议仓位" width="100">
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

    <section class="panel">
      <el-table :data="items" v-loading="loading" empty-text="还没有自选股，点右上角添加" size="small">
        <el-table-column label="代码" width="100">
          <template #default="{ row }">{{ row.symbol || row.stock_code }}</template>
        </el-table-column>
        <el-table-column prop="stock_name" label="名称" width="120" />
        <el-table-column prop="industry" label="行业" width="130" show-overflow-tooltip />
        <el-table-column label="现价" width="90">
          <template #default="{ row }">{{ row.current_price != null ? row.current_price.toFixed(2) : '-' }}</template>
        </el-table-column>
        <el-table-column label="涨跌幅" width="100">
          <template #default="{ row }">
            <span v-if="row.change_percent != null" :class="row.change_percent >= 0 ? 'up' : 'down'">
              {{ row.change_percent >= 0 ? '+' : '' }}{{ row.change_percent.toFixed(2) }}%
            </span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="持有涨跌" width="100">
          <template #default="{ row }">
            <span v-if="row.change_since_added_percent != null" :class="row.change_since_added_percent >= 0 ? 'up' : 'down'">
              {{ row.change_since_added_percent >= 0 ? '+' : '' }}{{ row.change_since_added_percent.toFixed(2) }}%
            </span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="预警(低/高)" width="130">
          <template #default="{ row }">
            {{ row.alert_price_low ?? '-' }} / {{ row.alert_price_high ?? '-' }}
          </template>
        </el-table-column>
        <el-table-column label="标签">
          <template #default="{ row }">
            <el-tag v-for="t in row.tags || []" :key="t" size="small" effect="plain" class="tag">{{ t }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140">
          <template #default="{ row }">
            <el-button link type="primary" @click="goResearch(row.symbol || row.stock_code)">深研</el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
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
import { onMounted, reactive, ref } from 'vue'
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

const remove = async (row: FavoriteItem) => {
  const code = row.symbol || row.stock_code || ''
  try {
    await ElMessageBox.confirm(`确认删除自选股 ${code}？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await favoritesApi.remove(code)
    ElMessage.success('已删除')
    await load()
  } catch (error: any) {
    ElMessage.error(error?.message || '删除失败')
  }
}

const goResearch = (code: string) => {
  router.push({ path: '/analysis/single', query: { stock: code } })
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
}

.tag { margin-right: 4px; }

.alert-row { display: flex; gap: 10px; width: 100%; }
.alert-row :deep(.el-input-number) { flex: 1; }

.up { color: #ef4444; }
.down { color: #16a34a; }

@media (max-width: 1100px) {
  .metric-grid,
  .portfolio-cols {
    grid-template-columns: 1fr;
  }
}
</style>
