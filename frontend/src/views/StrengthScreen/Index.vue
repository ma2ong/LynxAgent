<template>
  <div class="strength-page">
    <div class="page-head">
      <div>
        <h1>强势股研究清单</h1>
        <p>筛强势、不筛低价：距 250 日低点 ≥+70%、ADR ≥4.5%、站上 EMA8/EMA21，按全市场相对强度(RS)排名</p>
      </div>
      <div class="head-ctrl">
        <span v-if="updatedAt" class="updated">更新于 {{ updatedAt }}</span>
        <el-button type="primary" :loading="loading" @click="load">运行扫描</el-button>
      </div>
    </div>

    <!-- 条件与说明 -->
    <div class="criteria-bar">
      <div class="crit-item">
        <label>距250日低点 ≥</label>
        <el-input-number v-model="distMin" :min="0" :max="500" :step="5" size="small" controls-position="right" /> %
      </div>
      <div class="crit-item">
        <label>ADR ≥</label>
        <el-input-number v-model="adrMin" :min="0" :max="20" :step="0.5" :precision="1" size="small" controls-position="right" /> %
      </div>
      <div class="crit-item">
        <label>站上 EMA8/EMA21</label>
        <el-switch v-model="requireEma" />
      </div>
      <el-tag type="info" effect="plain" class="hint">这是研究清单，不是买入清单</el-tag>
    </div>

    <div v-if="loading && !data" class="loading-hint">
      正在扫描全市场约 5000 只，计算相对强度…约需 30–60 秒。
    </div>

    <template v-if="data">
      <!-- 概览 -->
      <section class="kpi-band">
        <div class="kpi-card">
          <span class="kpi-lbl">入选强势股</span>
          <strong class="kpi-num up">{{ data.items.length }}</strong>
          <small>命中 {{ data.matched }} / 打分 {{ data.scored }}</small>
        </div>
        <div class="kpi-card" v-if="data.market_context">
          <span class="kpi-lbl">大盘环境</span>
          <strong class="kpi-num" :class="envClass(data.market_context)">{{ envLabel(data.market_context) }}</strong>
          <small>{{ data.market_context.breadth_note || '按环境定仓位' }}</small>
        </div>
        <div class="kpi-card">
          <span class="kpi-lbl">当前门槛</span>
          <strong class="kpi-num sm">+{{ data.criteria.dist_min }}% · ADR{{ data.criteria.adr_min }}%</strong>
          <small>{{ data.criteria.require_ema ? '需站上 EMA8/21' : '不限均线' }}</small>
        </div>
      </section>

      <el-empty v-if="!data.items.length" description="当前门槛下没有符合条件的强势股（可能是弱市，或把门槛调低）" />

      <el-table v-else :data="data.items" class="rs-table" @row-click="(row:any)=>openStock(row.code)" style="cursor:pointer">
        <el-table-column label="RS" width="72" fixed>
          <template #default="{ row }">
            <span class="rs-badge" :class="rsClass(row.rs_rating)">{{ row.rs_rating }}</span>
          </template>
        </el-table-column>
        <el-table-column label="代码" width="86" prop="code" fixed />
        <el-table-column label="名称" width="118" fixed>
          <template #default="{ row }">
            {{ row.name }}
            <el-tag v-if="row.limit_up" size="small" type="danger" effect="dark" style="margin-left:4px">涨停</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="行业" width="150">
          <template #default="{ row }">
            <span class="ind">{{ row.industry || '—' }}</span>
            <el-tag v-if="row.sector_pos === 'leading'" size="small" type="danger" effect="plain" style="margin-left:4px">领先板块</el-tag>
            <el-tag v-else-if="row.sector_pos === 'lagging'" size="small" type="success" effect="plain" style="margin-left:4px">落后板块</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="现价" width="88" align="right">
          <template #default="{ row }">{{ row.close?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="涨跌幅" width="90" align="right">
          <template #default="{ row }">
            <span :class="row.pct_chg >= 0 ? 'up' : 'down'">{{ row.pct_chg >= 0 ? '+' : '' }}{{ row.pct_chg?.toFixed(2) }}%</span>
          </template>
        </el-table-column>
        <el-table-column label="距250低" width="96" align="right">
          <template #default="{ row }"><span class="up">+{{ row.dist_from_low?.toFixed(0) }}%</span></template>
        </el-table-column>
        <el-table-column label="ADR" width="82" align="right">
          <template #default="{ row }">{{ row.adr?.toFixed(1) }}%</template>
        </el-table-column>
        <el-table-column label="均线" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.ema_stack ? 'danger' : 'warning'" effect="plain">
              {{ row.ema_stack ? '多头排列' : '站上均线' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="研究要点" min-width="300">
          <template #default="{ row }"><span class="reasons">{{ row.reasons?.join(' · ') }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="96" fixed="right">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click.stop="openStock(row.code)">深研</el-button>
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

const distMin = ref(70)
const adrMin = ref(4.5)
const requireEma = ref(true)

const rsClass = (rs: number) => (rs >= 90 ? 'rs-top' : rs >= 80 ? 'rs-hi' : rs >= 60 ? 'rs-mid' : 'rs-lo')

const envLabel = (ctx: any) => ctx?.state || '—'
const envClass = (ctx: any) => {
  const l = envLabel(ctx)
  if (String(l).includes('暖') || String(l).includes('强')) return 'up'
  if (String(l).includes('冷') || String(l).includes('弱')) return 'down'
  return ''
}

const openStock = (code: string) => {
  router.push({ name: 'stock-analysis', query: { symbol: code } })
}

const load = async () => {
  loading.value = true
  try {
    const res: any = await ApiClient.get(
      '/api/quant/rs-pool',
      { limit: 40, universe_limit: 5000, dist_min: distMin.value, adr_min: adrMin.value, require_ema: requireEma.value, _ts: Date.now() },
      { timeout: 180000 },
    )
    data.value = res?.data || res || null
    updatedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } catch (e: any) {
    ElMessage.error(e?.message || '扫描强势股失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped lang="scss">
.strength-page { display: flex; flex-direction: column; gap: 14px; }
.page-head { display: flex; align-items: flex-end; justify-content: space-between; flex-wrap: wrap; gap: 8px;
  h1 { margin: 0 0 4px; font-size: 24px; }
  p { margin: 0; color: var(--el-text-color-secondary); font-size: 13px; }
}
.head-ctrl { display: flex; align-items: center; gap: 10px; }
.updated { font-size: 12px; color: var(--el-text-color-secondary); }
.loading-hint { color: var(--el-text-color-secondary); font-size: 13px; padding: 20px 0; }

.up { color: #ef232a; }
.down { color: #14b143; }

.criteria-bar {
  display: flex; align-items: center; flex-wrap: wrap; gap: 18px;
  background: var(--el-fill-color-lighter); border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px; padding: 10px 16px; font-size: 13px;
}
.crit-item { display: flex; align-items: center; gap: 6px; color: var(--el-text-color-secondary);
  label { white-space: nowrap; } }
.criteria-bar .hint { margin-left: auto; }

.kpi-band { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
.kpi-card {
  background: var(--el-fill-color-lighter); border-radius: 10px; padding: 14px 16px;
  display: flex; flex-direction: column; gap: 4px; border: 1px solid var(--el-border-color-lighter);
}
.kpi-lbl { font-size: 12px; color: var(--el-text-color-secondary); }
.kpi-num { font-size: 24px; font-weight: 800; &.sm { font-size: 18px; } }
.kpi-card small { font-size: 12px; color: var(--el-text-color-placeholder); }

.rs-table { border: 1px solid var(--el-border-color-lighter); border-radius: 10px; }
.rs-badge {
  display: inline-grid; place-items: center; width: 40px; height: 26px; border-radius: 6px;
  font-weight: 800; font-variant-numeric: tabular-nums; color: #fff; font-size: 14px;
}
.rs-top { background: #b71c1c; }
.rs-hi { background: #ef232a; }
.rs-mid { background: #f0a020; }
.rs-lo { background: #909399; }
.ind { font-size: 12px; color: var(--el-text-color-secondary); }
.reasons { font-size: 12px; color: var(--el-text-color-secondary); }
.disclaimer { font-size: 11px; color: var(--el-text-color-placeholder); margin: 4px 0 0; }
</style>
