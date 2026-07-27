<template>
  <div class="risk-page">
    <div class="page-head">
      <div>
        <h1>风险预警</h1>
        <p>大盘仓位红绿灯 + 持仓多因子复核——区分真正风险、普通退潮与次日反包。</p>
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
      <div class="gauge-scale">分档（分越高越危险）：0–34 安全 · 35–54 警惕 · 55–74 危险 · 75–100 极危</div>
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

    <!-- 全市场持仓风险复核 -->
    <section class="scan-card">
      <div class="scan-head">
        <h2>全市场持仓风险复核</h2>
        <div class="scan-sub" v-if="scan">
          建议退出 <b>{{ scan.recommendation_counts?.exit || 0 }}</b> 只
          · 减仓防守 <b>{{ scan.recommendation_counts?.reduce || 0 }}</b> 只
          · 反包观察 <b>{{ scan.recommendation_counts?.rebound || 0 }}</b> 只
          · 持有观察 <b>{{ scan.recommendation_counts?.watch || 0 }}</b> 只
          <span v-if="scan.universe">/ 全市场 {{ scan.universe }} 只</span>
          <span v-if="scan.as_of">· 日线截至 {{ scan.as_of }}</span>
        </div>
      </div>
      <div v-if="scan" class="method-note">
        <b>{{ scan.method_note }}</b>
        <span v-if="scan.market_context">
          市场中位涨幅 {{ pct(scan.market_context.median_pct) }} ·
          双均线下方 {{ (scan.market_context.breakdown_share * 100).toFixed(0) }}% ·
          {{ scan.market_context.broad_retreat ? '当前为广泛退潮，单只破位会降权' : '当前个股破位仍有区分度' }}
        </span>
      </div>
      <div class="scan-filters">
        <el-radio-group v-model="layerFilter" size="small" class="sev-filter">
          <el-radio-button value="actionable">重点复核 {{ scan?.actionable_count || 0 }}</el-radio-button>
          <el-radio-button value="rebound">反包观察 {{ scan?.recommendation_counts?.rebound || 0 }}</el-radio-button>
          <el-radio-button value="trouble">问题股 {{ scan?.layer_counts?.trouble || 0 }}</el-radio-button>
          <el-radio-button value="all">全部</el-radio-button>
        </el-radio-group>
        <el-input v-model="keyword" size="small" clearable placeholder="搜索股票/代码" class="stock-search" />
      </div>
      <el-table v-if="scan" :data="filteredScan" size="small" stripe max-height="520">
        <el-table-column label="综合建议" width="105" fixed="left">
          <template #default="{ row }">
            <el-tag size="small" :type="signalType(row.signal)" effect="dark">{{ row.signal }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="股票" width="150" fixed="left">
          <template #default="{ row }">
            <a class="stk" @click="openStock(row.symbol)">{{ row.name }} <small>{{ row.symbol }}</small></a>
          </template>
        </el-table-column>
        <el-table-column label="风险分" width="82">
          <template #default="{ row }">
            <b :class="row.risk_score >= 70 ? 'risk-hi' : row.risk_score >= 40 ? 'risk-mid' : 'risk-lo'">
              {{ row.risk_score || 0 }}
            </b>
          </template>
        </el-table-column>
        <el-table-column label="昨日收盘" width="105">
          <template #default="{ row }">
            <div>{{ row.close }}</div>
            <small :class="row.pct >= 0 ? 'up' : 'down'">{{ pct(row.pct) }}</small>
          </template>
        </el-table-column>
        <el-table-column label="盘中修正" width="110">
          <template #default="{ row }">
            <template v-if="row.current_price">
              <div>{{ row.current_price }}</div>
              <small :class="row.current_pct >= 0 ? 'up' : 'down'">{{ pct(row.current_pct) }}</small>
            </template>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column label="量能" width="95">
          <template #default="{ row }">
            <div>{{ row.amount_yi }}亿</div>
            <small>20日比 {{ row.amount_ratio || 0 }}×</small>
          </template>
        </el-table-column>
        <el-table-column label="价量资金代理" width="110">
          <template #default="{ row }">
            <span :class="row.capital_flow_5d >= 0 ? 'up' : 'down'">
              {{ pct(row.capital_flow_5d || 0) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="风险证据" min-width="360">
          <template #default="{ row }">
            <div class="evidence-list risk-evidence">
              <span v-for="item in row.risk_factors" :key="item">• {{ item }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="保护因素 / 反包修正" min-width="340">
          <template #default="{ row }">
            <div class="evidence-list protect-evidence">
              <span v-for="item in row.protect_factors" :key="item">• {{ item }}</span>
              <small v-for="item in row.context_factors" :key="item">{{ item }}</small>
            </div>
          </template>
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
const layerFilter = ref('actionable')
const keyword = ref('')

const levelKey = computed(() => {
  const l = alert.value?.level
  return l === '极危' ? 'extreme' : l === '危险' ? 'danger' : l === '警惕' ? 'warn' : 'safe'
})
const riskTone = (r: number) => (r >= 20 ? 'risk-hi' : r >= 8 ? 'risk-mid' : 'risk-lo')
const pct = (v: number) => `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
const signalType = (signal: string) =>
  signal === '退出/止损' ? 'danger' : signal === '减仓防守' ? 'warning'
    : signal === '反包观察' ? 'success' : 'info'
const filteredScan = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return (scan.value?.items || []).filter((item) => {
    // 分类精简：重点复核=退出+减仓（综合评估的可操作清单）/ 反包观察 / 问题股 / 全部
    const passLayer =
      layerFilter.value === 'all' ? true
        : layerFilter.value === 'actionable' ? (item.signal === '退出/止损' || item.signal === '减仓防守')
          : layerFilter.value === 'rebound' ? item.signal === '反包观察'
            : layerFilter.value === 'trouble' ? item.layer === 'trouble'
              : true
    const passKw = !kw || item.name.toLowerCase().includes(kw) || item.symbol.includes(kw)
    return passLayer && passKw
  })
})
const openStock = (symbol: string) => router.push({ name: 'stock-analysis', query: { symbol } })

const loadAll = async () => {
  loading.value = true
  try {
    const [a, s] = await Promise.all([
      quantApi.riskAlert().catch(() => null),
      quantApi.riskScan(500).catch(() => null),
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
.gauge-scale { margin-top: 6px; font-size: 12px; color: var(--el-text-color-placeholder); }
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
