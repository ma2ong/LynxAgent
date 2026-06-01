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
import { favoritesApi, type FavoriteItem, type AddFavoriteReq } from '@/api/favorites'

const router = useRouter()
const loading = ref(false)
const syncing = ref(false)
const adding = ref(false)
const addVisible = ref(false)
const items = ref<FavoriteItem[]>([])
const tagsText = ref('')

const addForm = reactive<AddFavoriteReq>({
  symbol: '',
  stock_name: '',
  alert_price_low: null,
  alert_price_high: null,
})

const unwrap = (res: any): FavoriteItem[] =>
  Array.isArray(res) ? res : Array.isArray(res?.data) ? res.data : []

const load = async () => {
  loading.value = true
  try {
    items.value = unwrap(await favoritesApi.list())
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
</style>
