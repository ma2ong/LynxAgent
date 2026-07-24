<template>
  <div class="risk-page">
    <div class="page-head">
      <div>
        <h1>风险预警</h1>
        <p>大盘仓位红绿灯 + 全市场卖出信号扫描——回答「什么时候不能再买、什么时候该卖」。</p>
      </div>
      <el-button size="small" :loading="loading" @click="loadAll">刷新</el-button>
    </div>

    <!-- 市场级风险仪表 -->
    <section v-if="alert" class="gauge-card" :class="`lv-${levelKey}`">
      <div class="gauge-head">
        <div class="gauge-badge">
          <span class="gauge-level">{{ alert.level }}</span>
          <span class="gauge-score">风险分 {{ alert.score }}</span>
        </div>
        <div class="gauge-meta">
          <span>赚钱效应：{{ alert.market_state || '—' }}</span>
          <span v-if="alert.as_of">· {{ alert.intraday ? `${alert.as_of} 盘中` : `截至 ${alert.as_of} 收盘` }}</span>
        </div>
      </div>
      <div class="gauge-action">{{ alert.action }}</div>
      <div class="signal-grid">
        <div v-for="s in alert.signals" :key="s.key" class="signal-cell">
          <div class="signal-top">
            <span class="signal-name">{{ s.name }}</span>
            <span class="signal-risk" :class="riskTone(s.risk)">+{{ s.risk }}</span>
          </div>
          <div class="signal-bar"><i :style="{ width: Math.min(100, s.risk / 40 * 100) + '%' }" :class="riskTone(s.risk)" /></div>
          <div class="signal-detail">{{ s.detail }}</div>
        </div>
      </div>
      <div v-if="alert.history_anchor" class="gauge-anchor">{{ alert.history_anchor }}</div>
      <div class="gauge-disc">{{ alert.disclaimer }}</div>
    </section>
    <el-skeleton v-else-if="loading" :rows="4" animated />

    <!-- 全市场卖出信号 -->
    <section class="scan-card">
      <div class="scan-head">
        <h2>全市场卖出信号</h2>
        <div class="scan-sub" v-if="scan">
          共 <b>{{ scan.total_flagged }}</b> 只命中 · 其中破位 <b>{{ scan.breakdown_count }}</b> 只
          <span v-if="scan.universe">/ 全市场 {{ scan.universe }} 只</span>
          <span v-if="scan.as_of">· 截至 {{ scan.as_of }}</span>
        </div>
      </div>
      <el-radio-group v-model="sevFilter" size="small" class="sev-filter">
        <el-radio-button :value="0">全部</el-radio-button>
        <el-radio-button :value="2">仅卖出</el-radio-button>
      </el-radio-group>
      <el-table v-if="scan" :data="filteredScan" size="small" stripe max-height="520">
        <el-table-column label="信号" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.severity >= 2 ? 'danger' : 'warning'" effect="dark">{{ row.signal }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="股票" min-width="150">
          <template #default="{ row }">
            <a class="stk" @click="openStock(row.symbol)">{{ row.name }} <small>{{ row.symbol }}</small></a>
          </template>
        </el-table-column>
        <el-table-column label="现价" width="80">
          <template #default="{ row }">{{ row.close }}</template>
        </el-table-column>
        <el-table-column label="当日" width="90">
          <template #default="{ row }"><span :class="row.pct >= 0 ? 'up' : 'down'">{{ pct(row.pct) }}</span></template>
        </el-table-column>
        <el-table-column label="成交额" width="90">
          <template #default="{ row }">{{ row.amount_yi }}亿</template>
        </el-table-column>
        <el-table-column label="卖出理由" min-width="300">
          <template #default="{ row }"><span class="reason">{{ row.reason }}</span></template>
        </el-table-column>
      </el-table>
      <el-empty v-else-if="!loading" description="暂无扫描数据" :image-size="80" />
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { quantApi, type RiskAlert, type RiskScan } from '@/api/quant'
defineOptions({ name: 'RiskAlertPage' })

const router = useRouter()
const loading = ref(false)
const alert = ref<RiskAlert | null>(null)
const scan = ref<RiskScan | null>(null)
const sevFilter = ref(0)

const levelKey = computed(() => {
  const l = alert.value?.level
  return l === '极危' ? 'extreme' : l === '危险' ? 'danger' : l === '警惕' ? 'warn' : 'safe'
})
const riskTone = (r: number) => (r >= 20 ? 'risk-hi' : r >= 8 ? 'risk-mid' : 'risk-lo')
const pct = (v: number) => `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
const filteredScan = computed(() =>
  (scan.value?.items || []).filter((i) => sevFilter.value === 0 || i.severity >= sevFilter.value),
)
const openStock = (symbol: string) => router.push({ name: 'stock-analysis', query: { symbol } })

const loadAll = async () => {
  loading.value = true
  try {
    const [a, s] = await Promise.all([
      quantApi.riskAlert().catch(() => null),
      quantApi.riskScan(200).catch(() => null),
    ])
    alert.value = a
    scan.value = s
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped lang="scss">
.risk-page { display: flex; flex-direction: column; gap: 16px; }
.page-head { display: flex; align-items: flex-end; justify-content: space-between; flex-wrap: wrap; gap: 8px;
  h1 { margin: 0 0 4px; font-size: 24px; }
  p { margin: 0; color: var(--el-text-color-secondary); font-size: 13px; }
}

.gauge-card { border: 1px solid var(--el-border-color-light); border-left: 6px solid var(--el-border-color);
  border-radius: 12px; padding: 16px 18px; background: var(--el-fill-color-extra-light); }
.gauge-head { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
.gauge-badge { display: flex; align-items: baseline; gap: 12px; }
.gauge-level { font-size: 26px; font-weight: 800; }
.gauge-score { font-size: 14px; color: var(--el-text-color-secondary); }
.gauge-meta { font-size: 13px; color: var(--el-text-color-secondary); display: flex; gap: 6px; flex-wrap: wrap; }
.gauge-action { margin: 10px 0 14px; font-size: 15px; font-weight: 700; }
.signal-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
.signal-cell { background: var(--el-bg-color); border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px; padding: 10px 12px; }
.signal-top { display: flex; justify-content: space-between; align-items: baseline; }
.signal-name { font-weight: 600; font-size: 13px; }
.signal-risk { font-weight: 700; font-size: 13px; }
.signal-bar { height: 5px; border-radius: 3px; background: var(--el-fill-color); margin: 6px 0; overflow: hidden;
  i { display: block; height: 100%; border-radius: 3px; } }
.signal-detail { font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.5; }
.gauge-anchor { margin-top: 12px; font-size: 13px; color: var(--el-text-color-regular); font-weight: 600; }
.gauge-disc { margin-top: 6px; font-size: 12px; color: var(--el-text-color-placeholder); }

/* 档位配色：越危险越红 */
.lv-safe { border-left-color: #0e9f5a; background: #f0fff4; .gauge-level { color: #0e9f5a; } }
.lv-warn { border-left-color: #d48806; background: #fffbe6; .gauge-level { color: #d48806; } }
.lv-danger { border-left-color: #ef232a; background: #fff1f0; .gauge-level { color: #ef232a; } }
.lv-extreme { border-left-color: #a8071a; background: #fff1f0; .gauge-level { color: #a8071a; } }

.scan-card { border: 1px solid var(--el-border-color-light); border-radius: 12px; padding: 16px 18px;
  background: var(--el-bg-color); }
.scan-head { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
.scan-head h2 { margin: 0; font-size: 18px; }
.scan-sub { font-size: 13px; color: var(--el-text-color-secondary); }
.sev-filter { margin: 10px 0; }
.stk { color: var(--el-color-primary); cursor: pointer; small { color: var(--el-text-color-placeholder); } }
.reason { font-size: 12px; color: var(--el-text-color-regular); }
.up { color: #ef232a; }
.down { color: #0e9f5a; }
/* 风险色阶（与个股红涨绿跌无关）：高危红、中等橙、低灰 */
.risk-hi { color: #ef232a; }
.risk-mid { color: #d48806; }
.risk-lo { color: var(--el-text-color-placeholder); }
.signal-bar i.risk-hi { background: #ef232a; }
.signal-bar i.risk-mid { background: #d48806; }
.signal-bar i.risk-lo { background: var(--el-border-color); }
</style>
