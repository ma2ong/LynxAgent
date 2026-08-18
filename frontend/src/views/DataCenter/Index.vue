<template>
  <div class="data-center">
    <div class="page-head">
      <div>
        <h1>数据中心</h1>
        <p>统一查看本地行情池、数据源优先级、同步进度和可用性。</p>
      </div>
      <div class="actions">
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
        <el-button type="primary" :loading="syncing" :disabled="isIntradayMode" @click="sync(false)">收盘后补日线</el-button>
        <el-button :loading="syncing" @click="sync(true)">重建历史日线</el-button>
      </div>
    </div>

    <el-alert
      v-if="health"
      :title="health.message"
      :type="health.grade === 'fresh' ? 'success' : health.grade === 'blocked' ? 'error' : 'warning'"
      show-icon
      :closable="false"
      class="status-alert"
    />

    <el-alert
      v-if="isIntradayMode"
      title="盘中无需等待日 K 同步"
      description="市场雷达、涨停热点和个股价格优先使用实时行情；本地日 K 主要用于回测、形态扫描和历史统计，收盘后再补齐即可。"
      type="info"
      show-icon
      :closable="false"
      class="status-alert"
    />

    <section v-if="!loading && !health" class="panel empty-panel">
      <el-empty description="暂时没有读取到本地数据状态" :image-size="86">
        <div class="empty-actions">
          <el-button @click="load">重新检查</el-button>
          <el-button type="primary" :loading="syncing" @click="sync(false)">收盘后补日线</el-button>
        </div>
      </el-empty>
    </section>

    <section class="kpi-grid" v-if="health">
      <div class="kpi">
        <span>本地股票池</span>
        <strong>{{ health.local?.meta_count || 0 }}</strong>
      </div>
      <div class="kpi">
        <span>K线覆盖</span>
        <strong>{{ health.local?.kline_symbols || 0 }}</strong>
      </div>
      <div class="kpi">
        <span>最新交易日</span>
        <strong>{{ health.local?.latest_complete_date || '-' }}</strong>
      </div>
      <div class="kpi">
        <span>今日覆盖</span>
        <strong>{{ health.local?.today_count || 0 }}/{{ health.local?.meta_count || 0 }}</strong>
      </div>
    </section>

    <div class="grid" v-if="health">
      <section class="panel">
        <div class="panel-title">数据源状态</div>
        <el-table :data="health.sources" size="small" stripe>
          <el-table-column prop="priority" label="#" width="48" />
          <el-table-column prop="name" label="数据源" width="120" />
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag size="small" :type="row.enabled ? 'success' : row.installed ? 'warning' : 'danger'">
                {{ row.enabled ? '可用' : row.installed ? '已安装' : '未安装' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="能力">
            <template #default="{ row }">
              <el-tag v-for="cap in row.capabilities" :key="cap" size="small" effect="plain" class="cap">{{ cap }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="notes" label="说明" min-width="220" />
        </el-table>
      </section>

      <section class="panel">
        <div class="panel-title">同步策略</div>
        <div class="policy-list">
          <div v-for="item in health.policy" :key="item.step" class="policy-item">
            <b>{{ item.step }}</b>
            <div>
              <strong>{{ item.name }}</strong>
              <p>{{ item.role }}</p>
            </div>
          </div>
        </div>
      </section>
    </div>

    <section class="panel" v-if="health">
      <div class="panel-title">同步进度</div>
      <div class="sync-row">
        <span>状态：{{ health.sync?.running ? '同步中' : '空闲' }}</span>
        <span>阶段：{{ health.sync?.phase || '-' }}</span>
        <span>进度：{{ health.sync?.done || 0 }}/{{ health.sync?.total || 0 }}</span>
        <span>错误：{{ health.sync?.errors_count || 0 }}</span>
      </div>
      <el-progress
        :percentage="progress"
        :status="health.sync?.errors_count ? 'exception' : health.sync?.running ? undefined : 'success'"
      />
      <div class="sync-meta">
        <span>最近全量：{{ health.sync?.last_full_sync || '-' }}</span>
        <span>最近增量：{{ health.sync?.last_incremental_sync || '-' }}</span>
      </div>
    </section>

    <!-- 本地股票池：原来挂在「智能选股 → 数据同步」页里，那页整体并入数据中心。
         同步按钮和健康状态这边本来就有，只有这张明细表是独有的。 -->
    <section class="panel">
      <div class="panel-title">
        本地股票池
        <div class="pool-actions">
          <el-input-number v-model="poolLimit" :min="1" :max="5000" size="small" controls-position="right" />
          <el-button size="small" :loading="poolLoading" @click="loadPool">读取股票池</el-button>
        </div>
      </div>
      <el-table v-if="poolResult?.items.length" :data="poolResult.items" size="small" stripe max-height="420">
        <el-table-column prop="symbol" label="代码" min-width="110" fixed />
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column prop="market" label="市场" min-width="100" />
        <el-table-column prop="source" label="来源" min-width="120" />
      </el-table>
      <el-empty v-else description="点「读取股票池」查看本地已入库的标的明细" :image-size="72" />
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { quantApi, type QuantSourceHealth, type QuantStockPoolResult } from '@/api/quant'

const loading = ref(false)
const syncing = ref(false)
const health = ref<QuantSourceHealth | null>(null)
const poolLimit = ref(200)
const poolLoading = ref(false)
const poolResult = ref<QuantStockPoolResult | null>(null)
let timer: number | undefined

const localStatus = computed(() => health.value?.local?.status || '')
const isIntradayMode = computed(() => localStatus.value === 'intraday')

const progress = computed(() => {
  const done = Number(health.value?.sync?.done || 0)
  const total = Number(health.value?.sync?.total || 0)
  return total > 0 ? Math.min(100, Math.round(done / total * 100)) : 0
})

async function loadPool() {
  poolLoading.value = true
  try {
    poolResult.value = await quantApi.pool(poolLimit.value)
    const n = poolResult.value?.items?.length || 0
    if (n) ElMessage.success(`已读取股票池 ${n} 只`)
    else ElMessage.warning('股票池为空，请检查数据源或稍后重试')
  } catch (error: any) {
    ElMessage.error(error?.message || '读取股票池失败')
  } finally {
    poolLoading.value = false
  }
}

async function load() {
  loading.value = true
  try {
    health.value = await quantApi.sourceHealth()
    if (health.value?.sync?.running) startPolling()
  } catch (error: any) {
    health.value = null
    ElMessage.error(error?.message || '数据中心加载失败')
  } finally {
    loading.value = false
  }
}

function startPolling() {
  if (timer) window.clearTimeout(timer)
  timer = window.setTimeout(async () => {
    await load()
    if (health.value?.sync?.running) startPolling()
  }, 5000)
}

async function sync(full: boolean) {
  if (!full && isIntradayMode.value) {
    ElMessage.info('当前是盘中实时行情模式，收盘后再补日线；页面数据不需要等同步完成。')
    return
  }
  syncing.value = true
  try {
    await quantApi.syncMarket(full)
    ElMessage.success(full ? '已启动历史日线重建' : '已启动收盘后日线补齐')
    await load()
    startPolling()
  } catch (error: any) {
    ElMessage.error(error?.message || '启动同步失败')
  } finally {
    syncing.value = false
  }
}

onMounted(load)
onUnmounted(() => { if (timer) window.clearTimeout(timer) })
</script>

<style scoped lang="scss">
.data-center { display: flex; flex-direction: column; gap: 14px; }
.page-head { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
.page-head h1 { margin: 0 0 4px; font-size: 22px; }
.page-head p { margin: 0; color: var(--el-text-color-secondary); }
.actions { display: flex; gap: 8px; }
.status-alert { margin-bottom: 2px; }
.kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.kpi { background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: 8px; padding: 12px 14px; }
.kpi span { display: block; color: var(--el-text-color-secondary); font-size: 12px; margin-bottom: 6px; }
.kpi strong { font-size: 20px; }
.grid { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(320px, .8fr); gap: 12px; }
.panel { background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: 8px; padding: 14px; overflow-x: auto; }
.empty-panel { min-height: 260px; display: flex; align-items: center; justify-content: center; }
.empty-actions { display: flex; justify-content: center; gap: 8px; }
.panel-title { font-size: 16px; font-weight: 700; margin-bottom: 12px; }
.cap { margin: 2px 4px 2px 0; }
.policy-list { display: flex; flex-direction: column; gap: 10px; }
.policy-item { display: flex; gap: 10px; align-items: flex-start; }
.policy-item b { width: 24px; height: 24px; line-height: 24px; border-radius: 50%; background: var(--el-color-primary-light-9); color: var(--el-color-primary); text-align: center; flex: 0 0 auto; }
.policy-item strong { font-size: 14px; }
.policy-item p { margin: 3px 0 0; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.sync-row, .panel-title .pool-actions { display: flex; gap: 8px; align-items: center; margin-left: auto; }
.panel-title { display: flex; align-items: center; gap: 10px; }
.sync-meta { display: flex; flex-wrap: wrap; gap: 14px; color: var(--el-text-color-secondary); font-size: 13px; margin-bottom: 10px; }
.panel-title .pool-actions { display: flex; gap: 8px; align-items: center; margin-left: auto; }
.panel-title { display: flex; align-items: center; gap: 10px; }
.sync-meta { margin: 10px 0 0; }
@media (max-width: 1000px) {
  .page-head, .actions { align-items: stretch; flex-direction: column; }
  .kpi-grid, .grid { grid-template-columns: 1fr; }
  .empty-actions { flex-direction: column; }
}
</style>
