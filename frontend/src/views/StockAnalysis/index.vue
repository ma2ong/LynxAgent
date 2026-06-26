<template>
  <div class="stock-analysis">
    <!-- 搜索栏 -->
    <div class="search-bar">
      <el-input
        v-model="symbolInput"
        placeholder="输入股票代码，如 600519"
        clearable
        size="large"
        style="max-width:320px"
        @keyup.enter="() => analyze()"
      />
      <el-button type="primary" size="large" :loading="loading" @click="() => analyze()">
        <el-icon><Search /></el-icon> 分析
      </el-button>

      <!-- 常驻最近搜索：出结果后也能随时调出 -->
      <el-popover v-if="history.length" placement="bottom-end" :width="300" trigger="click" popper-class="recent-popover">
        <template #reference>
          <el-button size="large" plain class="recent-trigger">
            <el-icon><Clock /></el-icon> 最近搜索
            <span class="recent-count">{{ history.length }}</span>
          </el-button>
        </template>
        <div class="recent-pop">
          <div class="recent-pop-head">
            <span>最近搜索</span>
            <el-button link size="small" @click="clearHistory">清空</el-button>
          </div>
          <div class="recent-pop-list">
            <div v-for="item in history" :key="item.code" class="recent-pop-item" @click="analyze(item.code)">
              <span class="rp-name">{{ item.name || '未知' }}</span>
              <span class="rp-code">{{ item.code }}</span>
              <el-icon class="rp-del" @click.stop="(e: Event) => removeHistory(item.code, e)"><Close /></el-icon>
            </div>
          </div>
        </div>
      </el-popover>
    </div>

    <!-- 搜索历史（空态内联展示）-->
    <div v-if="!loading && !data && history.length" class="history-bar">
      <span class="history-label">最近搜索</span>
      <el-tag
        v-for="item in history" :key="item.code"
        class="history-chip"
        closable
        size="small"
        @click="analyze(item.code)"
        @close="(e: Event) => removeHistory(item.code, e)"
      >{{ item.name ? item.name + ' ' : '' }}{{ item.code }}</el-tag>
    </div>

    <div v-if="!loading && !data && !history.length" class="starter-panel">
      <div>
        <h2>从一个标的开始</h2>
        <p>输入代码后会生成行情、K线、技术因子、财务速览、相关新闻和深度研究入口。</p>
      </div>
      <div class="starter-tags">
        <el-tag v-for="sym in starterSymbols" :key="sym" effect="plain" @click="analyze(sym)">
          {{ sym }}
        </el-tag>
      </div>
    </div>

    <div v-if="loading" class="loading-hint">正在分析，请稍候（约10-30秒）…</div>

    <template v-if="data && data.available">
      <!-- Hero 卡片 -->
      <div class="hero-card">
        <div class="hero-main">
          <span class="stock-name">{{ data.header?.name || symbolInput }}</span>
          <el-tag size="small" effect="plain" class="code-tag">{{ data.header?.symbol }}</el-tag>
          <el-tag :type="signalTagType" size="default" effect="dark" class="signal-tag">
            {{ data.rating?.label || '-' }}
          </el-tag>
        </div>
        <div class="hero-prices">
          <span class="price">{{ fmt(data.header?.last_price) }}</span>
          <span :class="['chg', pctClass(data.header?.pct_chg)]">
            {{ signedPct(data.header?.pct_chg) }}
          </span>
          <span class="score-pill">量化 {{ Math.round(data.rating?.tech_score ?? 0) }}</span>
        </div>
        <div class="hero-meta" v-if="data.header?.sector || data.header?.industry">
          <span>{{ data.header?.sector || data.header?.industry }}</span>
          <span v-if="data.header?.pe">PE {{ fmt(data.header.pe, 1) }}</span>
          <span v-if="data.header?.market_cap_yi">市值 {{ fmt(data.header.market_cap_yi) }}亿</span>
        </div>
        <div class="hero-meta quote-meta" v-if="data.header?.quote_updated_at || data.realtime_quote?.amount">
          <span v-if="data.header?.quote_updated_at">实时 {{ data.header.quote_updated_at }}</span>
          <span v-if="data.realtime_quote?.amount">成交额 {{ fmtAmount(data.realtime_quote.amount) }}</span>
          <span v-if="data.header?.quote_source">{{ data.header.quote_source }}</span>
        </div>
      </div>

      <!-- K线图 -->
      <div class="card" v-if="data.kline?.dates?.length">
        <div class="card-title">价格走势（近40日，可缩放）</div>
        <div ref="klineEl" class="kline-chart"></div>
      </div>

      <!-- AI研究三栏 -->
      <div class="ai-card" v-if="data.ai_view">
        <div class="ai-col bull">
          <div class="ai-col-head">📈 看多逻辑</div>
          <ul><li v-for="(t, i) in (data.ai_view.bull || [])" :key="i">{{ t }}</li></ul>
        </div>
        <div class="ai-col risk">
          <div class="ai-col-head">⚠️ 风险提示</div>
          <ul><li v-for="(t, i) in (data.ai_view.risk || [])" :key="i">{{ t }}</li></ul>
        </div>
        <div class="ai-col cat">
          <div class="ai-col-head">⚡ 关键催化</div>
          <ul><li v-for="(t, i) in (data.ai_view.catalyst || [])" :key="i">{{ t }}</li></ul>
        </div>
      </div>

      <div class="insight-grid" v-if="data.investor_profile || data.capital_flow_panel?.available || data.red_flags?.length">
        <div class="card insight-card" v-if="data.investor_profile">
          <div class="card-title">投资者画像</div>
          <div class="profile-head">
            <strong>{{ data.investor_profile.profile }}</strong>
            <el-tag size="small" effect="plain">适配 {{ Math.round(data.investor_profile.fit_score || 0) }}</el-tag>
          </div>
          <div class="profile-horizon">{{ data.investor_profile.horizon }}</div>
          <div class="mini-title">适合</div>
          <ul class="compact-list">
            <li v-for="item in data.investor_profile.suitable_for" :key="item">{{ item }}</li>
          </ul>
          <div class="mini-title">不适合</div>
          <ul class="compact-list muted-list">
            <li v-for="item in data.investor_profile.not_suitable_for" :key="item">{{ item }}</li>
          </ul>
        </div>

        <div class="card insight-card" v-if="data.capital_flow_panel?.available">
          <div class="card-title">资金面板</div>
          <div class="profile-head">
            <strong>{{ data.capital_flow_panel.state }}</strong>
            <el-tag size="small" :type="data.capital_flow_panel.score >= 65 ? 'danger' : data.capital_flow_panel.score <= 40 ? 'success' : 'warning'" effect="plain">
              {{ Math.round(data.capital_flow_panel.score || 0) }}
            </el-tag>
          </div>
          <div class="metric-list">
            <div v-for="m in data.capital_flow_panel.metrics" :key="m.name">
              <span>{{ m.name }}</span>
              <b>{{ m.value == null ? '-' : `${m.value}${m.unit || ''}` }}</b>
            </div>
          </div>
          <ul class="compact-list">
            <li v-for="item in data.capital_flow_panel.notes" :key="item">{{ item }}</li>
          </ul>
        </div>

        <div class="card insight-card" v-if="data.red_flags?.length">
          <div class="card-title">红旗扫描</div>
          <div class="flag-list">
            <div v-for="flag in data.red_flags" :key="flag.title" class="flag-row">
              <el-tag size="small" :type="flagTagType(flag.level)" effect="dark">{{ flagLevel(flag.level) }}</el-tag>
              <div>
                <b>{{ flag.title }}</b>
                <p>{{ flag.detail }}</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- AI 评委打分（按需加载，消耗 AI 额度）-->
      <div class="card" v-if="data.header?.symbol">
        <div class="card-title">AI 评委打分<small class="src">（5 位不同风格投资人独立打分）</small></div>
        <div v-if="panelLoading" class="panel-empty">评委评分中…</div>
        <template v-else-if="panel && !panel.empty">
          <div class="panel-head">
            <div class="consensus" :style="{ color: scoreColor(panel.consensus_score) }">
              <span class="label">共识分</span><strong>{{ panel.consensus_score }}</strong><small>/ 100</small>
            </div>
            <div class="panel-tags">
              <el-tag size="small" type="danger" effect="plain">看多 {{ panel.bull_count }}</el-tag>
              <el-tag size="small" type="success" effect="plain">看空 {{ panel.bear_count }}</el-tag>
              <el-tag size="small" type="info" effect="plain">分歧 {{ panel.divergence }}</el-tag>
            </div>
            <p class="panel-summary">{{ panel.summary }}</p>
          </div>
          <div class="verdict-grid">
            <div v-for="(v, i) in panel.verdicts" :key="i" class="verdict-card">
              <div class="vc-head">
                <span class="persona">{{ v.persona }}</span>
                <span class="stance" :class="stanceClass(v.stance)">{{ v.stance }}</span>
              </div>
              <div class="vc-score" :style="{ color: scoreColor(v.score) }">{{ v.score }}</div>
              <p class="vc-reason">{{ v.reason }}</p>
            </div>
          </div>
        </template>
        <div v-else-if="panel && panel.empty" class="panel-empty">{{ panel.message || '暂无评委打分' }}</div>
        <div v-else class="panel-cta">
          <el-button type="primary" plain :loading="panelLoading" @click="loadPanel">生成 AI 评委打分</el-button>
          <span class="cta-hint">5 位风格投资人独立打分 · 消耗 1 次 AI 额度</span>
        </div>
      </div>

      <!-- 跟踪观察 + 技术因子 -->
      <div class="two-col">
        <div class="card">
          <div class="card-title">跟踪观察</div>
          <div class="action-rows">
            <div class="action-row">
              <span class="ak">研究信号</span>
              <span class="av" :class="signalClass">{{ data.rating?.label || '-' }}</span>
            </div>
            <div class="action-row" v-if="data.rating?.entry_low">
              <span class="ak">价格观察区间</span>
              <span class="av">{{ fmt(data.rating.entry_low) }} – {{ fmt(data.rating.entry_high) }}</span>
            </div>
            <div class="action-row" v-if="data.rating?.stop_loss">
              <span class="ak">止损位</span>
              <span class="av loss">{{ fmt(data.rating.stop_loss) }}<small v-if="data.trade_plan?.stop_loss_pct">（{{ data.trade_plan.stop_loss_pct }}%）</small></span>
            </div>
            <div class="action-row" v-if="data.rating?.target">
              <span class="ak">止盈位</span>
              <span class="av gain">{{ fmt(data.rating.target) }}<small v-if="data.trade_plan?.take_profit_pct">（+{{ data.trade_plan.take_profit_pct }}%）</small></span>
            </div>
            <div class="action-row" v-if="data.rating?.risk_reward_ratio">
              <span class="ak">盈亏比</span>
              <span class="av">{{ data.rating.risk_reward_ratio }}:1<small>（{{ data.trade_plan?.basis === 'pct' ? '比例估算' : 'ATR 动态' }}）</small></span>
            </div>
            <div class="action-row" v-if="data.rating?.tracking_note">
              <span class="ak">跟踪备注</span>
              <span class="av">{{ data.rating.tracking_note }}</span>
            </div>
          </div>
          <div class="core-summary" v-if="data.core_summary">{{ data.core_summary }}</div>
        </div>

        <!-- 技术因子 -->
        <div class="card">
          <div class="card-title">技术因子</div>
          <div class="factor-list">
            <div v-for="f in factorItems" :key="f.key" class="factor-row">
              <span class="fl">{{ f.label }}</span>
              <div class="fbar">
                <div class="ffill" :style="{ width: f.pct + '%', background: f.color }" />
              </div>
              <span class="fval">{{ Math.round(f.val) }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 财务速览 -->
      <div class="card" v-if="data.financial_summary?.available">
        <div class="card-title">财务速览</div>
        <div class="fin-grid">
          <div v-for="row in visibleFinRows" :key="row.name" class="fin-item">
            <div class="fi-name">{{ row.name }}</div>
            <div class="fi-val">{{ fmtFin(row.value) }}</div>
            <div v-if="row.yoy != null" class="fi-yoy" :class="row.yoy >= 0 ? 'up' : 'down'">
              {{ row.yoy >= 0 ? '↑' : '↓' }}{{ Math.abs(row.yoy).toFixed(1) }}%
            </div>
          </div>
        </div>
      </div>

      <!-- PEG 估值分档 -->
      <div class="card" v-if="data.peg_valuation">
        <div class="card-title">PEG 估值</div>
        <div class="peg-row">
          <span class="peg-tier" :class="'peg-' + (data.peg_valuation.tier || '')">{{ data.peg_valuation.tier || '-' }}</span>
          <span class="peg-val" v-if="data.peg_valuation.available">PEG {{ data.peg_valuation.peg }}</span>
          <span class="peg-meta" v-if="data.peg_valuation.available">PE {{ data.peg_valuation.pe }} / 净利增速 {{ data.peg_valuation.growth }}%</span>
        </div>
        <div class="peg-note">{{ data.peg_valuation.note }}</div>
      </div>

      <!-- 市场表现 -->
      <div class="card" v-if="data.market_performance && hasPerfData">
        <div class="card-title">市场表现</div>
        <div class="perf-grid">
          <div v-for="(label, key) in PERF_LABELS" :key="key" class="perf-item">
            <div class="pl">{{ label }}</div>
            <div class="pv" :class="pctClass(data.market_performance[key])">
              {{ signedPct(data.market_performance[key]) }}
            </div>
          </div>
        </div>
      </div>

      <!-- 相关新闻 -->
      <div class="card" v-if="data.news?.length">
        <div class="card-title">相关新闻（近7天）</div>
        <div class="news-list">
          <a v-for="n in data.news" :key="n.url || n.title" class="news-item"
             :href="n.url || '#'" target="_blank">
            <span class="nt">{{ n.title }}</span>
            <span class="nd">{{ (n.time || n.published || '').slice(0, 10) }}</span>
          </a>
        </div>
      </div>

      <!-- 深度分析（按需触发） -->
      <div class="card deep-card">
        <div class="card-title">
          深度多智能体分析
          <el-tag size="small" type="info" effect="plain">约30-60秒</el-tag>
        </div>
        <template v-if="!deepStarted">
          <p class="deep-hint">多智能体分析行业/估值/风险/跟踪计划，生成结构化研究结论。</p>
          <el-button type="primary" plain @click="startDeep">启动深度分析</el-button>
        </template>
        <div v-else-if="deepLoading" class="deep-spin">
          <el-steps :active="deepStep" finish-status="success" align-center class="deep-steps">
            <el-step title="获取数据" />
            <el-step title="分析链条" />
            <el-step title="独立复核" />
          </el-steps>
          <el-progress :percentage="deepProgress" :stroke-width="8" />
          <div class="deep-status-grid">
            <div v-for="item in deepStatusItems" :key="item.title" :class="{ active: item.active, done: item.done }">
              <b>{{ item.title }}</b>
              <span>{{ item.desc }}</span>
            </div>
          </div>
          <span>多智能体分析中，已用 {{ deepElapsed }} 秒，可停留等待结果。</span>
        </div>
        <div v-else-if="deepResult" class="deep-result">
          <div v-if="deepAgentReview" class="agent-review">
            <div class="agent-review-head">
              <div>
                <span>多智能体审查</span>
                <b>{{ deepAgentReview.final_action }}</b>
              </div>
              <el-tag type="success" effect="plain">共识 {{ deepAgentReview.consensus_score }}</el-tag>
            </div>
            <div class="agent-grid">
              <div v-for="agent in deepAgentReview.agents" :key="agent.role" class="agent-card">
                <div class="agent-title">
                  <span>{{ agent.role }}</span>
                  <el-tag size="small" effect="plain">{{ agent.stance }}</el-tag>
                </div>
                <div class="agent-confidence">置信度 {{ Math.round((agent.confidence || 0) * 100) }}%</div>
                <ul>
                  <li v-for="point in agent.points" :key="point">{{ point }}</li>
                </ul>
              </div>
            </div>
          </div>
          <div v-if="deepAudit" class="audit-box">
            <div class="audit-head">
              <span>研究自检</span>
              <el-tag size="small" :type="deepAudit.confidence >= 0.65 ? 'success' : 'warning'">
                置信度 {{ Math.round(deepAudit.confidence * 100) }}%
              </el-tag>
            </div>
            <p>{{ deepAudit.verdict }}</p>
            <div class="audit-cols">
              <div>
                <b>证据链</b>
                <ul><li v-for="item in deepAudit.evidence" :key="item.name">{{ item.name }}：{{ item.value }}</li></ul>
              </div>
              <div>
                <b>数据缺口</b>
                <ul><li v-for="item in (deepAudit.gaps?.length ? deepAudit.gaps : ['暂无明显数据缺口'])" :key="item">{{ item }}</li></ul>
              </div>
              <div>
                <b>风控自检</b>
                <ul><li v-for="item in (deepAudit.risk_checks?.length ? deepAudit.risk_checks : ['暂无硬性风控拦截'])" :key="item">{{ item }}</li></ul>
              </div>
            </div>
          </div>
          <div v-for="s in deepSections" :key="s.title" class="deep-section">
            <div class="ds-title">{{ s.title }}</div>
            <div class="ds-body">{{ s.body }}</div>
          </div>
        </div>
        <el-alert v-else-if="deepError" :title="deepError" type="error" :closable="false" />
      </div>
    </template>

    <el-alert
      v-else-if="data && !data.available"
      title="未找到该股票数据，请检查代码是否正确"
      type="warning"
      :closable="false"
      show-icon
    />

    <el-empty v-else-if="!loading" description="输入股票代码开始分析" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { echarts, type ECharts } from '@/utils/echarts'
import { ElMessage } from 'element-plus'
import { Search, Clock, Close } from '@element-plus/icons-vue'
import { ApiClient } from '@/api/request'
import { quantApi } from '@/api/quant'

const symbolInput = ref('')
const loading = ref(false)
const data = ref<any>(null)

// AI 评委打分（按需，走 LLM 计配额）
const panel = ref<any>(null)
const panelLoading = ref(false)
const scoreColor = (s: number) => (s >= 62 ? '#ef232a' : s >= 45 ? '#e6a23c' : '#14b143')
const stanceClass = (s: string) => (s === '看多' ? 'st-bull' : s === '看空' ? 'st-bear' : 'st-neutral')
const loadPanel = () => {
  const code = data.value?.header?.symbol
  if (!code) return
  panelLoading.value = true
  quantApi.investorPanel(code)
    .then((res) => { panel.value = res })
    .catch((e: any) => { panel.value = { empty: true, message: e?.message || '评委打分生成失败' } })
    .finally(() => { panelLoading.value = false })
}

const klineEl = ref<HTMLDivElement>()
let klineChart: ECharts | null = null
const route = useRoute()

const HISTORY_KEY = 'stock_analysis_history'
type HistItem = { code: string; name: string }
function loadHistory(): HistItem[] {
  try {
    const raw = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]')
    return (raw as any[])
      .map((it) => (typeof it === 'string' ? { code: it, name: '' } : { code: String(it.code || ''), name: String(it.name || '') }))
      .filter((it) => it.code)
  } catch {
    return []
  }
}
const history = ref<HistItem[]>(loadHistory())
const starterSymbols = ['600519', '300570', '300502', '000001', '002594']

function saveHistory(code: string, name?: string) {
  const prevName = history.value.find((s) => s.code === code)?.name
  const h = history.value.filter((s) => s.code !== code)
  h.unshift({ code, name: name || prevName || '' })
  if (h.length > 12) h.splice(12)
  history.value = h
  localStorage.setItem(HISTORY_KEY, JSON.stringify(h))
}

function removeHistory(code: string, e: Event) {
  e.stopPropagation()
  history.value = history.value.filter((s) => s.code !== code)
  localStorage.setItem(HISTORY_KEY, JSON.stringify(history.value))
}

function clearHistory() {
  history.value = []
  localStorage.removeItem(HISTORY_KEY)
}

const deepStarted = ref(false)
const deepLoading = ref(false)
const deepResult = ref<any>(null)
const deepError = ref('')
const deepStep = ref(0)
const deepElapsed = ref(0)
let deepTimer: ReturnType<typeof setTimeout> | null = null
let deepElapsedTimer: ReturnType<typeof setInterval> | null = null

const PERF_LABELS: Record<string, string> = {
  d1: '1日', d5: '5日', m1: '1月', m3: '3月', ytd: '年初至今', y1: '1年',
}

const FACTOR_LABELS: Record<string, string> = {
  trend: '趋势', momentum: '动量', rsi: 'RSI', risk_control: '风控',
  liquidity: '流动性', macd: 'MACD', bollinger: '布林', capital_flow: '资金流',
}

const DEEP_TITLES: Record<string, string> = {
  overall_conclusion: '综合结论', operation_advice: '跟踪观察',
  technical_analysis: '技术面分析', industry_analysis: '行业分析',
  valuation_analysis: '估值分析', risk_assessment: '风险评估', tracking_plan: '跟踪计划',
}

const signalTagType = computed(() => {
  const s = data.value?.rating?.signal
  return s === 'strong_buy' ? 'danger' : s === 'buy' ? 'warning' : s === 'sell' ? 'info' : ''
})

const signalClass = computed(() => {
  const s = data.value?.rating?.signal
  return s?.includes('buy') ? 'sig-buy' : s?.includes('sell') ? 'sig-sell' : ''
})

const factorItems = computed(() => {
  const factors = data.value?.rating?.factors || data.value?.scores?.factors || {}
  return Object.entries(FACTOR_LABELS)
    .filter(([k]) => factors[k] != null)
    .map(([k, label]) => {
      const val = Number(factors[k])
      const color = val >= 70 ? '#ef232a' : val >= 45 ? '#e6a23c' : '#14b143'
      return { key: k, label, val, pct: Math.min(100, val), color }
    })
})

const visibleFinRows = computed(() =>
  (data.value?.financial_summary?.rows || []).filter((r: any) => r.value != null)
)

const hasPerfData = computed(() => {
  const p = data.value?.market_performance
  return p && Object.values(p).some((v) => v != null)
})

const deepSections = computed(() => {
  if (!deepResult.value) return []
  return Object.entries(DEEP_TITLES)
    .filter(([k]) => deepResult.value[k])
    .map(([k, title]) => ({ title, body: deepResult.value[k] }))
})

const deepAudit = computed(() => deepResult.value?.analysis_audit || null)
const deepAgentReview = computed(() => deepResult.value?.agent_review || null)
const deepProgress = computed(() => Math.min(92, deepStep.value * 28 + Math.floor(deepElapsed.value / 4)))
const deepStatusItems = computed(() => [
  {
    title: '数据准备',
    desc: '同步行情、财务、新闻和量化因子',
    active: deepStep.value === 1,
    done: deepStep.value > 1,
  },
  {
    title: '研究生成',
    desc: '组合行业、估值、风险和跟踪计划',
    active: deepStep.value === 2,
    done: deepStep.value > 2,
  },
  {
    title: '独立复核',
    desc: '检查证据链、风险缺口和结论一致性',
    active: deepStep.value >= 3,
    done: Boolean(deepResult.value),
  },
])

const fmt = (v?: number | null, dp = 2) =>
  v == null ? '-' : v >= 1e8 ? `${(v / 1e8).toFixed(dp)}亿` : v.toFixed(dp)

const fmtAmount = (v?: number | null) =>
  v == null ? '-' : v >= 1e8 ? `${(v / 1e8).toFixed(2)}亿` : v >= 1e4 ? `${(v / 1e4).toFixed(1)}万` : v.toFixed(0)

const fmtFin = (v?: number | null) => {
  if (v == null) return '-'
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(2)}万`
  return v.toFixed(2)
}

const signedPct = (v?: number | null) =>
  v == null ? '-' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`

const pctClass = (v?: number | null) => (v == null ? '' : v >= 0 ? 'up' : 'down')
const flagTagType = (level?: string) => level === 'high' ? 'danger' : level === 'medium' ? 'warning' : 'info'
const flagLevel = (level?: string) => level === 'high' ? '高' : level === 'medium' ? '中' : '低'

const renderKline = () => {
  if (!klineEl.value || !data.value?.kline?.dates?.length) return
  if (klineChart) { klineChart.dispose(); klineChart = null }
  klineChart = echarts.init(klineEl.value)
  const k = data.value.kline
  const total = k.dates.length
  const startPct = total > 40 ? Math.round((1 - 40 / total) * 100) : 0

  // 成交量：优先 volume（手），fallback amount（元）
  const volData: number[] = k.volume?.some((v: number) => v > 0)
    ? k.volume
    : (k.amount || [])
  const hasVol = volData.length > 0 && volData.some((v: number) => v > 0)

  const klineBottom = hasVol ? 120 : 60
  const volFmt = (v: number) =>
    v >= 1e8 ? `${(v / 1e8).toFixed(1)}亿` : v >= 1e4 ? `${(v / 1e4).toFixed(0)}万` : String(v)

  klineChart.setOption({
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross' },
      formatter: (params: any[]) => {
        const p = params.find((x: any) => x.seriesName === 'K线')
        if (!p) return ''
        const [o, c, l, h] = p.value
        let s = `${p.name}<br/>开 ${o}  收 ${c}  低 ${l}  高 ${h}`
        const vp = params.find((x: any) => x.seriesName === '成交量')
        if (vp) s += `<br/>成交量 ${volFmt(vp.value)}`
        return s
      },
    },
    legend: { data: ['K线', 'MA5', 'MA10', 'MA20'], top: 0, right: 12, textStyle: { fontSize: 11 } },
    grid: [
      { left: 60, right: 16, top: 24, bottom: klineBottom },
      ...(hasVol ? [{ left: 60, right: 16, top: '72%', bottom: 48 }] : []),
    ],
    xAxis: [
      { type: 'category', data: k.dates, scale: true, axisLabel: { fontSize: 10 }, axisLine: { onZero: false }, gridIndex: 0 },
      ...(hasVol ? [{ type: 'category', data: k.dates, scale: true, axisLabel: { show: false }, gridIndex: 1 }] : []),
    ],
    yAxis: [
      { type: 'value', scale: true, gridIndex: 0 },
      ...(hasVol ? [{ type: 'value', scale: true, gridIndex: 1, splitNumber: 2, axisLabel: { formatter: volFmt, fontSize: 10 } }] : []),
    ],
    dataZoom: [
      { type: 'inside', start: startPct, end: 100, xAxisIndex: hasVol ? [0, 1] : [0] },
      { type: 'slider', start: startPct, end: 100, height: 18, bottom: hasVol ? 26 : 8, xAxisIndex: hasVol ? [0, 1] : [0] },
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0,
        data: k.dates.map((_: string, i: number) => [k.open[i], k.close[i], k.low[i], k.high[i]]),
        itemStyle: { color: '#ef232a', color0: '#14b143', borderColor: '#ef232a', borderColor0: '#14b143' },
      },
      ...(k.ma5?.length ? [{
        name: 'MA5', type: 'line', xAxisIndex: 0, yAxisIndex: 0,
        data: k.ma5, symbol: 'none', lineStyle: { color: '#f5a623', width: 1.5 },
        itemStyle: { color: '#f5a623' },
      }] : []),
      ...(k.ma10?.length ? [{
        name: 'MA10', type: 'line', xAxisIndex: 0, yAxisIndex: 0,
        data: k.ma10, symbol: 'none', lineStyle: { color: '#4f8ef7', width: 1.5 },
        itemStyle: { color: '#4f8ef7' },
      }] : []),
      ...(k.ma20?.length ? [{
        name: 'MA20', type: 'line', xAxisIndex: 0, yAxisIndex: 0,
        data: k.ma20, symbol: 'none', lineStyle: { color: '#9b59b6', width: 1.5 },
        itemStyle: { color: '#9b59b6' },
      }] : []),
      ...(hasVol ? [{
        name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
        data: volData,
        itemStyle: {
          color: (params: any) =>
            k.close[params.dataIndex] >= k.open[params.dataIndex] ? '#ef232a88' : '#14b14388',
        },
      }] : []),
    ],
  })
}

const analyze = async (sym?: string) => {
  const s = (sym || symbolInput.value).trim()
  if (!s) return
  symbolInput.value = s
  loading.value = true
  data.value = null
  panel.value = null
  deepStarted.value = false
  deepResult.value = null
  deepError.value = ''
  try {
    const res: any = await ApiClient.get(`/api/quant/stock-analysis/${s}`, { _ts: Date.now() }, { timeout: 120000 })
    data.value = res?.data || null
    if (data.value?.available) {
      saveHistory(s, data.value?.header?.name)
      await nextTick()
      renderKline()
    } else {
      ElMessage.warning('未找到该股票数据')
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '分析失败')
  } finally {
    loading.value = false
  }
}

const startDeep = async () => {
  deepStarted.value = true
  deepLoading.value = true
  deepError.value = ''
  deepStep.value = 1
  deepElapsed.value = 0
  if (deepElapsedTimer) clearInterval(deepElapsedTimer)
  deepElapsedTimer = setInterval(() => {
    deepElapsed.value += 1
    if (deepElapsed.value > 12 && deepStep.value < 2) deepStep.value = 2
    if (deepElapsed.value > 28 && deepStep.value < 3) deepStep.value = 3
  }, 1000)
  const sym = symbolInput.value.trim()
  try {
    const res: any = await ApiClient.post('/api/analysis/single', {
      symbol: sym, depth: 3, use_llm: true,
    })
    const taskId = res?.data?.task_id
    if (!taskId) throw new Error('未获取到任务ID')
    deepStep.value = 2
    pollDeep(taskId)
  } catch (e: any) {
    if (deepElapsedTimer) { clearInterval(deepElapsedTimer); deepElapsedTimer = null }
    deepLoading.value = false
    deepError.value = e?.message || '启动失败'
  }
}

const pollDeep = (taskId: string) => {
  deepTimer = setTimeout(async () => {
    try {
      const res: any = await ApiClient.get(`/api/analysis/tasks/${taskId}/status`)
      const status = res?.data?.status
      if (status === 'completed') {
        const r: any = await ApiClient.get(`/api/analysis/tasks/${taskId}/result`)
        deepResult.value = r?.data || null
        deepStep.value = 3
        if (deepElapsedTimer) { clearInterval(deepElapsedTimer); deepElapsedTimer = null }
        deepLoading.value = false
      } else if (status === 'failed') {
        deepError.value = res?.data?.error || '深度分析失败'
        if (deepElapsedTimer) { clearInterval(deepElapsedTimer); deepElapsedTimer = null }
        deepLoading.value = false
      } else {
        pollDeep(taskId)
      }
    } catch {
      pollDeep(taskId)
    }
  }, 3000)
}

async function backfillHistoryNames() {
  const missing = history.value.filter((h) => !h.name).map((h) => h.code)
  if (!missing.length) return
  try {
    const res: any = await ApiClient.get('/api/lite/stock-names', { codes: missing.join(',') })
    const map = res?.data || {}
    let changed = false
    history.value = history.value.map((h) => {
      if (!h.name && map[h.code]) { changed = true; return { ...h, name: map[h.code] } }
      return h
    })
    if (changed) localStorage.setItem(HISTORY_KEY, JSON.stringify(history.value))
  } catch { /* 忽略：名称只是显示增强 */ }
}

onMounted(() => {
  backfillHistoryNames()
  const symbol = String(route.query.symbol || route.query.stock || '').trim()
  if (symbol) analyze(symbol)
})

onUnmounted(() => {
  if (deepTimer) clearTimeout(deepTimer)
  if (deepElapsedTimer) clearInterval(deepElapsedTimer)
  klineChart?.dispose()
})
</script>

<style scoped lang="scss">
.stock-analysis { display: flex; flex-direction: column; gap: 16px; }

.search-bar { display: flex; gap: 10px; align-items: center; }
.loading-hint { color: var(--el-text-color-secondary); font-size: 13px; }

/* 搜索历史 */
.history-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.history-label { font-size: 12px; color: var(--el-text-color-secondary); white-space: nowrap; }
.history-chip { cursor: pointer; }
.history-chip:hover { border-color: var(--el-color-primary); color: var(--el-color-primary); }

/* 常驻最近搜索下拉 */
.recent-trigger { display: inline-flex; align-items: center; }
.recent-count {
  margin-left: 6px; font-size: 11px; line-height: 1; font-weight: 600;
  background: var(--el-color-primary-light-8); color: var(--el-color-primary);
  border-radius: 9px; padding: 2px 7px;
}
.recent-pop { display: flex; flex-direction: column; }
.recent-pop-head {
  display: flex; justify-content: space-between; align-items: center;
  font-size: 13px; font-weight: 600; color: var(--el-text-color-primary);
  padding: 0 2px 8px; margin-bottom: 4px; border-bottom: 1px solid var(--el-border-color-lighter);
}
.recent-pop-list { display: flex; flex-direction: column; max-height: 320px; overflow-y: auto; }
.recent-pop-item {
  display: flex; align-items: center; gap: 10px; padding: 7px 8px;
  border-radius: 6px; cursor: pointer;
}
.recent-pop-item:hover { background: var(--el-fill-color-light); }
.rp-name {
  flex: 1; font-size: 13px; font-weight: 500; color: var(--el-text-color-primary);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.rp-code { font-size: 12px; color: var(--el-text-color-secondary); font-variant-numeric: tabular-nums; }
.rp-del { color: var(--el-text-color-placeholder); font-size: 13px; opacity: 0; transition: opacity .15s; }
.recent-pop-item:hover .rp-del { opacity: 1; }
.rp-del:hover { color: var(--el-color-danger); }
.starter-panel {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 18px 20px;

  h2 { margin: 0 0 6px; font-size: 18px; }
  p { margin: 0; color: var(--el-text-color-secondary); font-size: 13px; }
}
.starter-tags { display: flex; gap: 8px; flex-wrap: wrap; }
.starter-tags :deep(.el-tag) { cursor: pointer; }

/* Hero */
.hero-card {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.hero-main { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.stock-name { font-size: 22px; font-weight: 700; }
.code-tag { font-size: 12px; }
.signal-tag { font-size: 13px; padding: 4px 12px; }
.hero-prices { display: flex; align-items: baseline; gap: 12px; }
.price { font-size: 26px; font-weight: 700; }
.chg { font-size: 16px; font-weight: 600; }
.score-pill {
  background: var(--el-fill-color);
  border-radius: 20px;
  padding: 2px 10px;
  font-size: 13px;
  font-weight: 600;
}
.hero-meta { display: flex; gap: 16px; font-size: 13px; color: var(--el-text-color-secondary); }

/* Card base */
.card {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 16px;
}
.card-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}

/* K线图 */
.kline-chart { height: 380px; }

/* AI三栏 */
.ai-card {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  overflow: hidden;
}
.ai-col { padding: 14px 16px; }
.ai-col + .ai-col { border-left: 1px solid var(--el-border-color-lighter); }
.ai-col-head { font-size: 13px; font-weight: 600; margin-bottom: 8px; }
.bull .ai-col-head { color: #ef232a; }
.risk .ai-col-head { color: #e6a23c; }
.cat .ai-col-head { color: #409eff; }
.ai-col ul { margin: 0; padding-left: 16px; font-size: 13px; line-height: 1.8; color: var(--el-text-color-regular); }

/* Insight cards */
.insight-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.insight-card { min-height: 220px; }
.profile-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}
.profile-head strong { font-size: 17px; }
.profile-horizon { color: var(--el-text-color-secondary); font-size: 13px; margin-bottom: 10px; }
.mini-title { font-size: 12px; color: var(--el-text-color-secondary); margin: 10px 0 4px; }
.compact-list { margin: 0; padding-left: 16px; line-height: 1.7; font-size: 13px; }
.muted-list { color: var(--el-text-color-secondary); }
.metric-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin: 10px 0;
}
.metric-list div { background: var(--el-fill-color-lighter); border-radius: 6px; padding: 8px 10px; }
.metric-list span { display: block; color: var(--el-text-color-secondary); font-size: 11px; margin-bottom: 2px; }
.metric-list b { font-size: 14px; }
.flag-list { display: flex; flex-direction: column; gap: 10px; }
.flag-row {
  display: grid;
  grid-template-columns: 36px 1fr;
  gap: 8px;
  align-items: flex-start;
}
.flag-row b { font-size: 13px; }
.flag-row p { margin: 3px 0 0; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; }

/* Two col */
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }

/* Action */
.action-rows { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; }
.action-row { display: flex; justify-content: space-between; font-size: 14px; }
.ak { color: var(--el-text-color-secondary); }
.av { font-weight: 600; }
.loss { color: #14b143; }
.gain { color: #ef232a; }
.sig-buy { color: #ef232a; }
.sig-sell { color: #67c23a; }
.core-summary {
  font-size: 13px;
  line-height: 1.8;
  color: var(--el-text-color-regular);
  border-top: 1px solid var(--el-border-color-lighter);
  padding-top: 10px;
}

/* Factors */
.factor-list { display: flex; flex-direction: column; gap: 7px; }
.factor-row { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.fl { width: 52px; color: var(--el-text-color-secondary); flex-shrink: 0; }
.fbar { flex: 1; height: 6px; background: var(--el-fill-color); border-radius: 3px; overflow: hidden; }
.ffill { height: 100%; border-radius: 3px; transition: width .3s; }
.fval { width: 28px; text-align: right; font-weight: 600; }

/* Finance */
.fin-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 10px; }
.fin-item { background: var(--el-fill-color-lighter); border-radius: 6px; padding: 10px 12px; }
.fi-name { font-size: 11px; color: var(--el-text-color-secondary); }
.fi-val { font-size: 16px; font-weight: 700; margin: 4px 0 2px; }
.fi-yoy { font-size: 12px; }

/* PEG */
.peg-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.peg-tier { font-size: 15px; font-weight: 700; padding: 2px 10px; border-radius: 4px; background: var(--el-fill-color); }
.peg-tier.peg-低估 { color: #fff; background: var(--el-color-danger); }
.peg-tier.peg-合理 { color: #fff; background: var(--el-color-primary); }
.peg-tier.peg-偏高 { color: #fff; background: var(--el-color-warning); }
.peg-tier.peg-高估 { color: #fff; background: var(--el-color-info); }
.peg-val { font-size: 16px; font-weight: 700; }
.peg-meta { font-size: 12px; color: var(--el-text-color-secondary); }
.peg-note { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 6px; }

/* Perf */
.perf-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; }
.perf-item { text-align: center; background: var(--el-fill-color-lighter); border-radius: 6px; padding: 8px 4px; }
.pl { font-size: 11px; color: var(--el-text-color-secondary); margin-bottom: 4px; }
.pv { font-size: 14px; font-weight: 600; }

/* News */
.news-list { display: flex; flex-direction: column; gap: 6px; }
.news-item {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  text-decoration: none;
  color: inherit;
  padding: 6px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
  font-size: 13px;
  &:last-child { border-bottom: none; }
}
.nt { flex: 1; color: #409eff; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nd { flex-shrink: 0; font-size: 11px; color: var(--el-text-color-secondary); margin-left: 12px; }

/* Deep */
.deep-hint { font-size: 13px; color: var(--el-text-color-secondary); margin-bottom: 12px; }
.deep-spin { display: flex; flex-direction: column; gap: 12px; color: var(--el-text-color-secondary); padding: 12px 0; }
.deep-steps { width: 100%; }
.deep-status-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}
.deep-status-grid div {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 9px 10px;
  background: var(--el-fill-color-extra-light);
}
.deep-status-grid div.active {
  border-color: var(--el-color-primary-light-5);
  background: var(--el-color-primary-light-9);
}
.deep-status-grid div.done {
  border-color: var(--el-color-success-light-5);
  background: var(--el-color-success-light-9);
}
.deep-status-grid b {
  display: block;
  color: var(--el-text-color-primary);
  font-size: 13px;
  margin-bottom: 3px;
}
.deep-status-grid span {
  display: block;
  font-size: 12px;
  line-height: 1.45;
}
.deep-section { margin-bottom: 16px; &:last-child { margin-bottom: 0; } }
.ds-title { font-size: 14px; font-weight: 600; margin-bottom: 6px; }
.ds-body { font-size: 13px; line-height: 1.8; white-space: pre-wrap; color: var(--el-text-color-regular); }
.agent-review {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 14px;
  background: var(--el-fill-color-extra-light);
}
.agent-review-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}
.agent-review-head span {
  display: block;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-bottom: 2px;
}
.agent-review-head b { font-size: 18px; }
.agent-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 10px;
}
.agent-card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 10px;
  background: var(--el-bg-color);
}
.agent-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  font-size: 13px;
}
.agent-confidence {
  margin-top: 6px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.agent-card ul {
  margin: 8px 0 0;
  padding-left: 16px;
  line-height: 1.65;
  font-size: 12px;
}
.audit-box {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 14px;
  background: var(--el-fill-color-extra-light);
}
.audit-head { display: flex; align-items: center; justify-content: space-between; font-weight: 700; margin-bottom: 6px; }
.audit-box p { margin: 0 0 10px; color: var(--el-text-color-secondary); font-size: 13px; }
.audit-cols { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.audit-cols b { font-size: 13px; }
.audit-cols ul { margin: 6px 0 0; padding-left: 16px; font-size: 12px; line-height: 1.7; }

/* Colors */
.up { color: #ef232a; }
.down { color: #14b143; }

/* AI 评委打分 */
.card-title .src { font-weight: 400; font-size: 11px; color: var(--el-text-color-secondary); }
.panel-empty { color: var(--el-text-color-secondary); padding: 24px 0; text-align: center; }
.panel-cta { display: flex; align-items: center; gap: 12px; padding: 10px 0; flex-wrap: wrap; }
.cta-hint { font-size: 12px; color: var(--el-text-color-secondary); }
.panel-head { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; margin-bottom: 12px; }
.consensus { display: flex; align-items: baseline; gap: 4px;
  .label { font-size: 12px; color: var(--el-text-color-secondary); } strong { font-size: 32px; font-weight: 800; }
  small { font-size: 12px; color: var(--el-text-color-secondary); } }
.panel-tags { display: flex; gap: 6px; }
.panel-summary { margin: 0; flex: 1 1 240px; font-size: 13px; color: var(--el-text-color-secondary); }
.verdict-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; }
.verdict-card { border: 1px solid var(--el-border-color-light); border-radius: 8px; padding: 10px; text-align: center;
  background: var(--el-fill-color-light); }
.vc-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px;
  .persona { font-weight: 700; font-size: 13px; } }
.stance { font-size: 11px; padding: 1px 6px; border-radius: 4px; }
.st-bull { color: #ef232a; background: #fdeaea; }
.st-bear { color: #14b143; background: #eef9f0; }
.st-neutral { color: #e6a23c; background: #fdf6ec; }
.vc-score { font-size: 28px; font-weight: 800; line-height: 1.2; }
.vc-reason { margin: 4px 0 0; font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.5; }
@media (max-width: 900px) { .verdict-grid { grid-template-columns: repeat(2, 1fr); } }

@media (max-width: 900px) {
  .search-bar { align-items: stretch; flex-direction: column; }
  .starter-panel { align-items: flex-start; flex-direction: column; }
  .ai-card, .two-col, .insight-grid { grid-template-columns: 1fr; }
  .ai-col + .ai-col { border-left: none; border-top: 1px solid var(--el-border-color-lighter); }
  .perf-grid { grid-template-columns: repeat(3, 1fr); }
  .audit-cols { grid-template-columns: 1fr; }
}

@media (max-width: 640px) {
  .stock-analysis { gap: 12px; }
  .search-bar :deep(.el-input) { max-width: none !important; }
  .hero-prices,
  .hero-meta {
    flex-wrap: wrap;
  }
  .price { font-size: 24px; }
  .kline-chart { height: 320px; }
  .metric-list,
  .perf-grid,
  .deep-status-grid {
    grid-template-columns: 1fr;
  }
  .news-item {
    align-items: flex-start;
    flex-direction: column;
    gap: 3px;
  }
  .nt { white-space: normal; }
  .nd { margin-left: 0; }
}
</style>
