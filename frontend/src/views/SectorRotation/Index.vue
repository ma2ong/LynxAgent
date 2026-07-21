<template>
  <div class="rotation-page">
    <div class="page-head">
      <div>
        <h1>板块轮动</h1>
        <p>行业 4周/12周相对大盘的超额强度(RS)，看资金往哪个方向流——板块决定方向、大盘决定仓位</p>
      </div>
      <div class="head-ctrl">
        <span v-if="updatedAt" class="updated">更新于 {{ updatedAt }}</span>
        <el-button type="primary" :loading="loading" @click="load">运行扫描</el-button>
      </div>
    </div>

    <div v-if="loading && !data" class="loading-hint">正在计算全市场各行业相对强度…约需 20–40 秒。</div>

    <template v-if="data">
      <div class="verdict">{{ data.verdict }}</div>

      <section class="kpi-band">
        <div class="kpi-card">
          <span class="kpi-lbl">领先板块（关注）</span>
          <strong class="kpi-num up sm">{{ (data.leaders || []).join(' / ') || '—' }}</strong>
          <small>12周相对强度居前 3</small>
        </div>
        <div class="kpi-card">
          <span class="kpi-lbl">落后板块（回避）</span>
          <strong class="kpi-num down sm">{{ (data.laggards || []).join(' / ') || '—' }}</strong>
          <small>12周相对强度垫底 3</small>
        </div>
        <div class="kpi-card" v-if="data.market">
          <span class="kpi-lbl">大盘中位（基准）</span>
          <strong class="kpi-num sm">
            4周 {{ fmt(data.market.ret_20) }} · 12周 {{ fmt(data.market.ret_60) }}
          </strong>
          <small>全市场收益中位 = RS 的零线</small>
        </div>
      </section>

      <el-empty v-if="!data.sectors?.length" description="暂无行业数据：请先在数据中心同步行情" />

      <el-table v-else :data="data.sectors" class="sec-table" :row-class-name="rowClass">
        <el-table-column label="#" width="52" fixed>
          <template #default="{ row }"><span class="rank" :class="posClass(row)">{{ row.rank }}</span></template>
        </el-table-column>
        <el-table-column label="行业" width="150" prop="name" fixed />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.rank <= 3" size="small" type="danger" effect="dark">领先</el-tag>
            <el-tag v-else-if="isLaggard(row)" size="small" type="success" effect="dark">落后</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="12周 RS" width="120" align="right" sortable :sort-method="(a:any,b:any)=>a.rs_12w-b.rs_12w">
          <template #default="{ row }"><b :class="row.rs_12w >= 0 ? 'up' : 'down'">{{ fmt(row.rs_12w) }}</b></template>
        </el-table-column>
        <el-table-column label="4周 RS" width="110" align="right">
          <template #default="{ row }"><span :class="row.rs_4w >= 0 ? 'up' : 'down'">{{ fmt(row.rs_4w) }}</span></template>
        </el-table-column>
        <el-table-column label="12周涨幅" width="110" align="right">
          <template #default="{ row }"><span :class="row.ret_60 >= 0 ? 'up' : 'down'">{{ fmt(row.ret_60) }}</span></template>
        </el-table-column>
        <el-table-column label="成分股" width="90" align="right" prop="member_count" />
        <el-table-column label="领涨龙头" min-width="140">
          <template #default="{ row }">
            <el-button v-if="row.leader?.code" size="small" text type="primary" @click="openStock(row.leader.code)">
              {{ row.leader.code }} <span class="lead-ret up">+{{ row.leader.ret_60?.toFixed(0) }}%</span>
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <p class="disclaimer">{{ data.note }}</p>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ApiClient } from '@/api/request'

const router = useRouter()
const loading = ref(false)
const data = ref<any>(null)
const updatedAt = ref('')

const fmt = (v: number) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`)
const isLaggard = (row: any) => (data.value?.laggards || []).includes(row.name)
const posClass = (row: any) => (row.rank <= 3 ? 'r-lead' : isLaggard(row) ? 'r-lag' : '')
const rowClass = ({ row }: any) => (row.rank <= 3 ? 'lead-row' : isLaggard(row) ? 'lag-row' : '')

const openStock = (code: string) => router.push({ name: 'stock-analysis', query: { symbol: code } })

const load = async () => {
  loading.value = true
  try {
    const res: any = await ApiClient.get('/api/quant/sector-rotation', { _ts: Date.now() }, { timeout: 120000 })
    data.value = res?.data || res || null
    updatedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } catch (e: any) {
    ElMessage.error(e?.message || '扫描板块轮动失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped lang="scss">
.rotation-page { display: flex; flex-direction: column; gap: 14px; }
.page-head { display: flex; align-items: flex-end; justify-content: space-between; flex-wrap: wrap; gap: 8px;
  h1 { margin: 0 0 4px; font-size: 24px; }
  p { margin: 0; color: var(--el-text-color-secondary); font-size: 13px; }
}
.head-ctrl { display: flex; align-items: center; gap: 10px; }
.updated { font-size: 12px; color: var(--el-text-color-secondary); }
.loading-hint { color: var(--el-text-color-secondary); font-size: 13px; padding: 20px 0; }

.up { color: #ef232a; }
.down { color: #14b143; }
.muted { color: var(--el-text-color-placeholder); }

.verdict {
  background: var(--el-color-primary-light-9); border-left: 3px solid var(--el-color-primary);
  padding: 10px 14px; border-radius: 6px; font-size: 13px;
}
.kpi-band { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
.kpi-card {
  background: var(--el-fill-color-lighter); border-radius: 10px; padding: 14px 16px;
  display: flex; flex-direction: column; gap: 4px; border: 1px solid var(--el-border-color-lighter);
}
.kpi-lbl { font-size: 12px; color: var(--el-text-color-secondary); }
.kpi-num { font-size: 22px; font-weight: 800; &.sm { font-size: 15px; } }
.kpi-card small { font-size: 12px; color: var(--el-text-color-placeholder); }

.sec-table { border: 1px solid var(--el-border-color-lighter); border-radius: 10px; }
.rank { display: inline-grid; place-items: center; width: 26px; height: 22px; border-radius: 5px;
  font-weight: 700; font-size: 12px; background: var(--el-fill-color); }
.rank.r-lead { background: #b71c1c; color: #fff; }
.rank.r-lag { background: #14b143; color: #fff; }
.lead-ret { font-size: 11px; margin-left: 2px; }
:deep(.lead-row) { background: rgba(239, 35, 42, .04); }
:deep(.lag-row) { background: rgba(20, 177, 67, .04); }
.disclaimer { font-size: 11px; color: var(--el-text-color-placeholder); margin: 4px 0 0; }
</style>
