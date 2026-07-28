<template>
  <div class="dash">
    <!-- 盘面环境：从智能选股页迁移到首页，盘中自动刷新 -->
    <section class="market-overview" :class="`ctx-${ctxTone}`">
      <div class="market-head">
        <div class="mb-row">
          <span class="mb-tag">赚钱效应</span>
          <b class="mb-state">{{ marketCtx?.state || '评估中' }}</b>
          <span v-if="marketCtx?.temp != null" class="mb-temp">温度 {{ marketCtx.temp }}</span>
          <span v-if="marketCtx" class="mb-metrics">
            <span class="mb-when" :class="{ live: marketCtx.intraday }">{{ asOfText }}</span>
            个股中位 <b>{{ pct(marketCtx.latest_day?.median_pct) }}</b>
            · 上涨家数占比 <b>{{ Math.round((marketCtx.latest_day?.breadth_up || 0) * 100) }}%</b>
            <!-- 成交额加权：钱实际赚没赚到。与等权中位常有 4~5pp 的差，且两者背离时
                 恰恰是权重股/龙头在杀跌——只看中位会在最该警惕的日子里读数祥和。 -->
            <template v-if="marketCtx.latest_day?.weighted_pct != null">
              · 成交额加权
              <b :class="marketCtx.latest_day.weighted_pct >= 0 ? 'up' : 'down'">
                {{ pct(marketCtx.latest_day.weighted_pct) }}
              </b>
              <span v-if="weightedGap" class="mb-gap">（{{ weightedGap }}）</span>
            </template>
          </span>
          <span v-if="marketCtx?.latest_day?.label && marketCtx.latest_day.label !== '—'"
            class="mb-day" :class="{ rebound: marketCtx.latest_day.rebound }">
            {{ marketCtx.latest_day.label }}
          </span>
        </div>
        <div class="market-actions">
          <span v-if="risk" class="risk-pill" :class="riskToneClass"
            title="风险分档（分越高越危险）：0–34 安全 · 35–54 警惕 · 55–74 危险 · 75–100 极危">
            风险 {{ risk.level }} {{ risk.score }}
          </span>
          <el-button link type="primary" size="small" :loading="heroLoading" @click="loadHero()">刷新</el-button>
        </div>
      </div>

      <div v-if="marketCtx?.index?.items?.length" class="mb-row mb-index">
        <span class="mb-tag">大盘指数</span>
        <span v-for="item in marketCtx.index.items" :key="item.code" class="mb-idx"
          :class="item.last_pct >= 0 ? 'up' : 'down'">
          {{ item.name }} <b>{{ pct(item.last_pct) }}</b>
        </span>
        <span class="mb-idx-note">
          近5日 {{ marketCtx.index.items.map((item) => pct(item.window_pct)).join(' / ') }}
        </span>
      </div>
      <div v-if="marketCtx?.divergence" class="mb-diverge">⚠ {{ marketCtx.divergence }}</div>
      <div class="mb-advice">{{ marketCtx?.advice || '正在评估大盘环境…' }}</div>
      <div v-if="marketCtx?.daily?.length" class="mb-daily">
        近5日逐日：
        <span v-for="day in marketCtx.daily" :key="day.date" :class="day.median_pct >= 0 ? 'up' : 'down'">
          {{ day.date.slice(5) }} {{ pct(day.median_pct) }}/{{ Math.round(day.breadth_up * 100) }}%
        </span>
      </div>
      <div v-if="coldEvidence" class="mb-evidence">{{ coldEvidence }}</div>
      <div v-if="risk" class="risk-verdict" :class="`risk-verdict-${riskKey}`">
        <div class="risk-verdict-title">
          <b>{{ verdict.word }}</b>
          <span>{{ verdict.sub }}</span>
        </div>
        <div class="risk-verdict-action">{{ risk.action }}</div>
      </div>
      <div v-if="risk" class="risk-verdict-scale">风险分档（分越高越危险）：0–34 安全 · 35–54 警惕 · 55–74 危险 · 75–100 极危</div>
      <div class="market-foot">每 60 秒自动刷新 · 规则化提示，不构成投资建议</div>
    </section>

    <!-- 模块预览 -->
    <section class="grid">
      <!-- 智能选股 -->
      <article class="card">
        <header @click="go('/quant')">
          <span class="c-ico">🎯</span><h3>智能选股</h3>
          <span v-if="smartWin != null" class="c-badge">T+5 {{ smartWin }}%</span>
          <span class="c-more">进入 →</span>
        </header>
        <div class="c-body">
          <template v-if="smartPicks.length">
            <div class="rank-list">
              <div v-for="(s, i) in smartPicks" :key="s.symbol" class="rank-row" @click="goStock(s.symbol)">
                <span class="rk">{{ i + 1 }}</span>
                <span class="rk-name">{{ s.name }}<small>{{ s.symbol }}</small></span>
                <span v-if="s.score" class="rk-score">{{ s.score }}分</span>
              </div>
            </div>
            <small class="c-note">最近一批一键智选留痕（{{ smartDate }}）</small>
          </template>
          <div v-else class="c-empty">
            <p>横向比较全市场 A 股，输出量化推荐池</p>
            <el-button size="small" type="success" plain @click="go('/quant')">去生成推荐 →</el-button>
          </div>
        </div>
      </article>

      <!-- 风险预警 -->
      <article class="card" :class="riskCardClass">
        <header @click="go('/risk-alert')">
          <span class="c-ico">⚠️</span><h3>风险预警</h3>
          <span v-if="risk" class="pill" :class="riskToneClass">{{ risk.level }} {{ risk.score }}</span>
          <span class="c-more">进入 →</span>
        </header>
        <div class="c-body">
          <template v-if="riskScan">
            <div class="kpi-mini">
              <div><b class="down2">{{ riskScan.recommendation_counts?.exit || 0 }}</b><span>退出/止损</span></div>
              <div><b class="warn">{{ riskScan.recommendation_counts?.reduce || 0 }}</b><span>减仓防守</span></div>
              <div><b class="up2">{{ riskScan.recommendation_counts?.rebound || 0 }}</b><span>反包观察</span></div>
            </div>
            <div v-if="topSell.length" class="rank-list">
              <div v-for="s in topSell" :key="s.symbol" class="rank-row" @click="goStock(s.symbol)">
                <el-tag size="small" type="danger" effect="dark">{{ s.signal }}</el-tag>
                <span class="rk-name">{{ s.name }}<small>{{ s.symbol }}</small></span>
              </div>
            </div>
          </template>
          <p v-else class="c-cta">大盘仓位红绿灯 + 全市场卖出信号扫描</p>
        </div>
      </article>

      <!-- 集合竞价 -->
      <article class="card">
        <header @click="go('/call-auction')">
          <span class="c-ico">🌅</span><h3>集合竞价</h3><span class="c-more">进入 →</span>
        </header>
        <div class="c-body">
          <template v-if="auction">
            <div class="kpi-mini">
              <div><b class="up2">{{ auction.pc.accumulation }}</b><span>主力抢筹</span></div>
              <div><b class="up2">{{ auction.pc.shakeout }}</b><span>洗盘低吸</span></div>
              <div><b class="down2">{{ auction.pc.distribution }}</b><span>诱多出货</span></div>
            </div>
            <div v-if="auction.top.length" class="rank-list">
              <div v-for="c in auction.top" :key="c.code" class="rank-row" @click="goStock(c.code)">
                <span class="rk-name">{{ c.name }}<small>{{ c.code }}</small></span>
                <span class="up2">+{{ c.open_pct }}%</span>
              </div>
            </div>
            <small v-else class="c-note">当日强势板块暂无高开候选</small>
          </template>
          <p v-else class="c-cta">竞价高开推导情绪 + 盘口四形态标注买入候选</p>
        </div>
      </article>

      <!-- 涨停热点 -->
      <article class="card">
        <header @click="go('/limit-up')">
          <span class="c-ico">🔥</span><h3>涨停热点</h3><span class="c-more">进入 →</span>
        </header>
        <div class="c-body">
          <template v-if="limitUp">
            <div class="kpi-mini">
              <div><b class="up2">{{ limitUp.up }}</b><span>涨停</span></div>
              <div><b class="down2">{{ limitUp.down }}</b><span>跌停</span></div>
              <div><b>{{ limitUp.maxBoards }}</b><span>最高板</span></div>
            </div>
            <div v-if="limitUp.causes.length" class="chip-row">
              <span v-for="c in limitUp.causes" :key="c.name" class="chip">{{ c.name }} <b>{{ c.n }}</b></span>
            </div>
          </template>
          <p v-else class="c-cta">涨停家数、连板高度与题材分布</p>
        </div>
      </article>

      <!-- 行业热力 -->
      <article class="card">
        <header @click="go('/heatmap')">
          <span class="c-ico">📊</span><h3>行业热力</h3><span class="c-more">进入 →</span>
        </header>
        <div class="c-body">
          <template v-if="hotIndustries.length">
            <div class="ind-row" v-for="i in hotIndustries" :key="i.name">
              <span class="ind-name">{{ i.name }}</span>
              <span class="ind-bar"><i :class="i.pct >= 0 ? 'up-bg' : 'down-bg'"
                :style="{ width: Math.min(100, Math.abs(i.pct) * 12) + '%' }" /></span>
              <span :class="i.pct >= 0 ? 'up' : 'down'">{{ pct(i.pct) }}</span>
            </div>
          </template>
          <p v-else class="c-cta">全市场行业涨跌热力，红涨绿跌</p>
        </div>
      </article>

      <!-- 选股复盘 -->
      <article class="card">
        <header @click="go('/review')">
          <span class="c-ico">📈</span><h3>选股复盘</h3><span class="c-more">进入 →</span>
        </header>
        <div class="c-body">
          <template v-if="poolStats.length">
            <table class="mini-table">
              <thead><tr><th>池</th><th>T+1</th><th>T+3</th><th>T+5</th></tr></thead>
              <tbody>
                <tr v-for="p in poolStats" :key="p.label">
                  <td>{{ p.label }}</td>
                  <td :class="winCls(p.t1)">{{ fmtWin(p.t1) }}</td>
                  <td :class="winCls(p.t3)">{{ fmtWin(p.t3) }}</td>
                  <td :class="winCls(p.t5)">{{ fmtWin(p.t5) }}</td>
                </tr>
              </tbody>
            </table>
          </template>
          <p v-else class="c-cta">各池 T+1/T+3/T+5 真实胜率与超额</p>
        </div>
      </article>

      <!-- 我的自选股 -->
      <article class="card">
        <header @click="go('/favorites')">
          <span class="c-ico">⭐</span><h3>我的自选股</h3>
          <span v-if="favs.length" class="c-badge">{{ favs.length }} 只</span>
          <span class="c-more">进入 →</span>
        </header>
        <div class="c-body">
          <template v-if="favTop.length">
            <div class="rank-list">
              <div v-for="f in favTop" :key="f.symbol" class="rank-row" @click="goStock(f.symbol)">
                <span class="rk-name">{{ f.name }}<small>{{ f.symbol }}</small></span>
                <span :class="(f.pct ?? 0) >= 0 ? 'up' : 'down'">{{ pct(f.pct ?? 0) }}</span>
              </div>
            </div>
            <small class="c-note">涨跌幅最大的 {{ favTop.length }} 只</small>
          </template>
          <p v-else class="c-cta">收藏个股，实时跟踪涨跌与预警</p>
        </div>
      </article>

      <!-- 个股深研 -->
      <article class="card card-search">
        <header><span class="c-ico">🔍</span><h3>个股深研</h3>
          <span class="c-more" @click="go('/stock-analysis')">进入 →</span></header>
        <div class="c-body">
          <el-input v-model="searchKw" size="large" clearable placeholder="输入名称或代码，回车研究"
            @keyup.enter="doSearch">
            <template #append><el-button @click="doSearch">研究</el-button></template>
          </el-input>
          <div v-if="recent.length" class="chip-row">
            <span class="chip-label">最近</span>
            <span v-for="r in recent" :key="r.code" class="chip clickable" @click="goStock(r.code)">
              {{ r.name || r.code }}
            </span>
          </div>
        </div>
      </article>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ApiClient } from '@/api/request'
import { quantApi, heatmapApi, type MarketContext, type RiskAlert, type RiskScan } from '@/api/quant'
import { favoritesApi } from '@/api/favorites'
defineOptions({ name: 'DashboardPage' })

const router = useRouter()
const go = (path: string) => router.push(path)
const goStock = (code: string) => router.push({ name: 'stock-analysis', query: { symbol: code } })
const pct = (v: number) => `${v > 0 ? '+' : ''}${(v ?? 0).toFixed(2)}%`

const marketCtx = ref<MarketContext | null>(null)
const risk = ref<RiskAlert | null>(null)
const riskScan = ref<RiskScan | null>(null)
const heroLoading = ref(false)

// 等权中位与成交额加权背离超过 1.5pp 时点破：小票普涨掩盖了权重杀跌（或反之）
const weightedGap = computed(() => {
  const d: any = marketCtx.value?.latest_day
  if (!d || d.weighted_pct == null || d.median_pct == null) return ''
  const gap = d.median_pct - d.weighted_pct
  if (gap >= 1.5) return '小票普涨，权重股在跌'
  if (gap <= -1.5) return '权重股拉指数，多数个股没跟上'
  return ''
})
const asOfText = computed(() => {
  const c = marketCtx.value
  if (!c?.as_of) return ''
  return c.intraday ? `${c.as_of} ${c.as_of_time || ''} 实时` : `截至 ${c.as_of} 收盘`
})
const ctxTone = computed(() => {
  const state = marketCtx.value?.state
  return state === '偏暖' ? 'warm' : state === '偏冷' ? 'cold' : state ? 'flat' : 'loading'
})
const riskKey = computed(() => {
  if (!risk.value) return 'loading'
  const l = risk.value?.level
  return l === '极危' ? 'extreme' : l === '危险' ? 'danger' : l === '警惕' ? 'warn' : 'safe'
})
const riskToneClass = computed(() => `rk-${riskKey.value}`)
const riskCardClass = computed(() => (['危险', '极危'].includes(risk.value?.level || '') ? 'card-alert' : ''))
const verdictKey = riskKey
const verdict = computed(() => {
  const l = risk.value?.level
  if (l === '极危') return { word: '清仓观望', sub: '流动性踩踏风险，停止买入' }
  if (l === '危险') return { word: '防守减仓', sub: '停止新增买入，只留最强主线' }
  if (l === '警惕') return { word: '谨慎参与', sub: '控制仓位，只打强势方向' }
  if (l === '安全') return { word: '可以进攻', sub: '赚钱效应尚可，按纪律选股' }
  return { word: '评估中', sub: '正在加载大盘环境' }
})
const coldEvidence = ref('')
const loadColdEvidence = async () => {
  if (marketCtx.value?.state !== '偏冷') {
    coldEvidence.value = ''
    return
  }
  if (coldEvidence.value) return
  try {
    const replay = await quantApi.replayResults()
    const parts: string[] = []
    let bestCold: { label: string; avg: number } | null = null
    for (const pool of replay?.pools || []) {
      const cold = (pool.regimes || []).find((row) => row.regime === '偏冷')
      if (!cold) continue
      const label = pool.pool === 'pattern' ? '形态池' : pool.pool === 'smart' ? '智能池' : pool.pool
      parts.push(`${label} ${cold.avg_excess > 0 ? '+' : ''}${cold.avg_excess}pp（中位 ${cold.median_excess}pp，${cold.picks} 样本）`)
      if (!bestCold || cold.avg_excess > bestCold.avg) bestCold = { label, avg: cold.avg_excess }
    }
    if (!parts.length) return
    const advice = bestCold && bestCold.avg >= 0.5
      ? `——弱市里${bestCold.label}历史上仍有正超额，但仓位仍应克制；其余池贴零或为负，优先只跟${bestCold.label}。`
      : '——各池弱市均无有效超额，建议轻仓或观望。'
    coldEvidence.value = `历史回放偏冷期 T+5 超额：${parts.join('；')}${advice}`
  } catch { /* 无回放结果时不展示 */ }
}

// —— 卡片数据 ——
const smartPicks = ref<{ symbol: string; name: string; score: number }[]>([])
const smartWin = ref<number | null>(null)
const smartDate = ref('')
const poolStats = ref<{ label: string; t1: number | null; t3: number | null; t5: number | null }[]>([])
const topSell = ref<{ symbol: string; name: string; signal: string }[]>([])
const auction = ref<{ pc: Record<string, number>; top: any[] } | null>(null)
const limitUp = ref<{ up: number; down: number; maxBoards: number; causes: { name: string; n: number }[] } | null>(null)
const hotIndustries = ref<{ name: string; pct: number }[]>([])
const favs = ref<{ symbol: string; name: string; pct: number | null }[]>([])
const favTop = computed(() =>
  [...favs.value].sort((a, b) => Math.abs(b.pct ?? 0) - Math.abs(a.pct ?? 0)).slice(0, 4))

const POOL_LABEL: Record<string, string> = { smart: '一键智选', auction: '竞价优选' }
const winCls = (v: number | null) => (v == null ? 'muted' : v >= 50 ? 'up' : 'down')
const fmtWin = (v: number | null) => (v == null ? '—' : `${v}%`)

const searchKw = ref('')
const recent = ref<{ code: string; name: string }[]>([])
const doSearch = () => {
  const kw = searchKw.value.trim()
  if (kw) router.push({ name: 'stock-analysis', query: { symbol: kw } })
}
const loadRecent = () => {
  try {
    recent.value = (JSON.parse(localStorage.getItem('stock_analysis_history') || '[]') || []).slice(0, 6)
  } catch { recent.value = [] }
}

// Hero 单独可重试：冷启动首屏请求被挤掉时不至于永久留在「评估中」
const loadHero = async (retries = 2) => {
  if (heroLoading.value) return
  heroLoading.value = true
  let ok = true
  await Promise.all([
    quantApi.marketContext().then((c) => { if (c) marketCtx.value = c; else ok = false }).catch(() => { ok = false }),
    quantApi.riskAlert().then((r) => { if (r) risk.value = r; else ok = false }).catch(() => { ok = false }),
  ])
  heroLoading.value = false
  if (ok) {
    heroLoadedAt = Date.now()
    loadColdEvidence()
  }
  if (!ok && retries > 0) setTimeout(() => loadHero(retries - 1), 1800)
}

const loadCards = async (retries = 1) => {
  let failed = false
  await Promise.all([
    quantApi.riskScan(30).then((s) => {
    riskScan.value = s || null
    topSell.value = (s?.items || []).filter((i) => i.severity >= 2)
      .slice(0, 3).map((i) => ({ symbol: i.symbol, name: i.name, signal: i.signal }))
    }).catch(() => { failed = true }),
    quantApi.picksStats(30, '', false).then((res) => {
      const stats = (res?.pools || []).filter((p) => ['smart', 'auction'].includes(p.pool))
      poolStats.value = stats.map((p) => ({
        label: POOL_LABEL[p.pool] || p.pool,
        t1: p.horizons?.t1?.win_rate == null ? null : Math.round(p.horizons.t1.win_rate * 100),
        t3: p.horizons?.t3?.win_rate == null ? null : Math.round(p.horizons.t3.win_rate * 100),
        t5: p.horizons?.t5?.win_rate == null ? null : Math.round(p.horizons.t5.win_rate * 100),
      }))
      smartWin.value = poolStats.value.find((p) => p.label === '一键智选')?.t5 ?? null
      const smartItems = (res?.latest || []).filter((i) => i.pool === 'smart')
      smartDate.value = (smartItems[0]?.batch_at || smartItems[0]?.pick_date || '')
        .replace('T', ' ').slice(0, 16)
      smartPicks.value = smartItems
        .sort((a, b) => a.rank - b.rank).slice(0, 5)
        .map((i) => ({ symbol: i.symbol, name: i.name, score: Math.round(i.score) }))
    }).catch(() => { failed = true }),
    heatmapApi.fetch('industry').then((d) => {
      const items = (d?.items || [])
      const hot = [...items].sort((a, b) => b.pct - a.pct).slice(0, 3)
      const cold = [...items].sort((a, b) => a.pct - b.pct).slice(0, 2)
      hotIndustries.value = [...hot, ...cold].map((i) => ({ name: i.name, pct: i.pct }))
    }).catch(() => { failed = true }),
    favoritesApi.list().then((raw: any) => {
      const list = (raw?.data ?? raw) || []
      favs.value = list.map((f: any) => ({
        symbol: f.symbol || f.stock_code, name: f.stock_name, pct: f.change_percent ?? null,
      }))
    }).catch(() => { failed = true }),
    ApiClient.get<any>('/api/lite/call-auction', { _ts: Date.now() }, { timeout: 30000 }).then((raw) => {
      const d = raw?.data ?? raw
      const pc = d?.auction_tape?.pattern_counts
      if (d) auction.value = {
        pc: { accumulation: pc?.accumulation || 0, shakeout: pc?.shakeout || 0, distribution: pc?.distribution || 0 },
        top: (d.buy_candidates || []).slice(0, 3),
      }
    }).catch(() => { failed = true }),
    ApiClient.get<any>('/api/lite/limit-up', {}, { timeout: 45000 }).then((raw) => {
      const d = raw?.data ?? raw
      if (d?.total_limit_up != null) limitUp.value = {
        up: d.total_limit_up, down: d.total_limit_down, maxBoards: d.max_boards,
        causes: (d.causes || []).slice(0, 3).map((c: string) => ({ name: c, n: d.cause_total?.[c] || 0 })),
      }
    }).catch(() => { failed = true }),
  ])
  cardsLoadedAt = Date.now()
  if (failed && retries > 0) setTimeout(() => loadCards(retries - 1), 1800)
}

const loadAll = () => { loadRecent(); loadHero(); loadCards() }
let heroLoadedAt = 0
let cardsLoadedAt = 0
let firstActivation = true
let heroTimer: number | undefined
const startHeroPolling = () => {
  if (heroTimer) window.clearInterval(heroTimer)
  heroTimer = window.setInterval(() => loadHero(1), 60_000)
}
const stopHeroPolling = () => {
  if (heroTimer) window.clearInterval(heroTimer)
  heroTimer = undefined
}
onMounted(() => {
  loadAll()
  startHeroPolling()
})
onActivated(() => {
  if (firstActivation) {
    firstActivation = false
    return
  }
  startHeroPolling()
  loadRecent()
  const now = Date.now()
  if (!marketCtx.value || !risk.value || now - heroLoadedAt >= 60_000) loadHero(1)
  if (now - cardsLoadedAt >= 180_000) loadCards()
})
onDeactivated(stopHeroPolling)
onUnmounted(stopHeroPolling)
</script>

<style scoped lang="scss">
.dash { display: flex; flex-direction: column; gap: 18px; }

/* ---------- Market overview ---------- */
.market-overview { padding: 16px 20px; border: 1px solid var(--el-border-color-light);
  border-left: 6px solid var(--el-border-color); border-radius: 14px;
  background: var(--el-fill-color-extra-light); }
.risk-verdict { display: flex; align-items: center; gap: 18px; margin-top: 12px; padding: 10px 14px;
  border-left: 4px solid var(--el-border-color); border-radius: 9px; background: rgba(255, 255, 255, .72); }
.risk-verdict-title { display: flex; align-items: baseline; gap: 10px; min-width: 280px;
  b { font-size: 28px; font-weight: 800; line-height: 1.1; white-space: nowrap; }
  span { color: var(--el-text-color-secondary); font-size: 13px; white-space: nowrap; } }
.risk-verdict-action { flex: 1; font-size: 13px; font-weight: 600; line-height: 1.6; }
.risk-verdict-scale { margin-top: 6px; font-size: 12px; color: var(--el-text-color-placeholder); }
.risk-verdict-safe { border-left-color: #0e9f5a; .risk-verdict-title b { color: #0e9f5a; } }
.risk-verdict-warn { border-left-color: #d48806; .risk-verdict-title b { color: #d48806; } }
.risk-verdict-danger { border-left-color: #ef232a; .risk-verdict-title b { color: #ef232a; } }
.risk-verdict-extreme { border-left-color: #a8071a; .risk-verdict-title b { color: #a8071a; } }
.market-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; }
.market-actions { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.mb-row { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.mb-tag { font-size: 12px; font-weight: 600; color: #fff; background: var(--el-text-color-secondary);
  padding: 2px 8px; border-radius: 6px; }
.mb-state { font-size: 22px; font-weight: 800; }
.mb-temp { font-size: 12px; color: var(--el-text-color-secondary); padding: 1px 6px;
  border: 1px solid var(--el-border-color); border-radius: 4px; }
.mb-metrics { font-size: 13px; color: var(--el-text-color-regular);
  b { font-size: 15px; font-weight: 700; } }
.mb-when { font-weight: 600; &.live { color: #ef232a; } }
.mb-day { font-size: 13px; color: var(--el-text-color-secondary); padding: 2px 8px;
  border-radius: 4px; background: var(--el-fill-color);
  &.rebound { color: #ef232a; background: rgba(239, 35, 42, .1); } }
.mb-index { margin-top: 7px; }
.mb-idx { font-size: 13px; color: var(--el-text-color-regular); b { font-weight: 700; } }
.mb-idx-note { font-size: 12px; color: var(--el-text-color-secondary); }
.mb-diverge { margin-top: 7px; font-size: 13px; font-weight: 600; color: #d46b08; }
.mb-advice { margin-top: 7px; font-size: 14px; font-weight: 600; color: var(--el-text-color-primary); }
.mb-daily { margin-top: 5px; font-size: 12px; color: var(--el-text-color-secondary);
  span { margin-right: 10px; } }
.mb-evidence { margin-top: 5px; font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.6; }
.risk-pill { color: #fff; font-size: 15px; font-weight: 800; padding: 4px 11px; border-radius: 12px; }
.market-foot { margin-top: 7px; font-size: 11px; color: var(--el-text-color-placeholder); text-align: right; }
.ctx-warm { border-color: #ffb3a7; border-left-color: #ef232a; background: #fff1f0;
  .mb-tag { background: #ef232a; } .mb-state, .mb-metrics b { color: #ef232a; } }
.ctx-cold { border-color: #a7d4b4; border-left-color: #0e9f5a; background: #f0fff4;
  .mb-tag { background: #0e9f5a; } .mb-state, .mb-metrics b { color: #0e9f5a; } }
.ctx-flat { border-left-color: #d48806; background: #fffdf3; }
.ctx-loading { border-left-color: var(--el-border-color); }
.mb-idx.up b, .mb-daily .up { color: #ef232a; }
.mb-idx.down b, .mb-daily .down { color: #0e9f5a; }
.risk-pill.rk-safe { color: #fff; background: rgba(14,159,90,.85); }
.risk-pill.rk-warn { color: #fff; background: rgba(212,136,6,.85); }
.risk-pill.rk-danger { color: #fff; background: rgba(239,35,42,.85); }
.risk-pill.rk-extreme { color: #fff; background: rgba(168,7,26,.9); }

@media (max-width: 760px) {
  .risk-verdict { align-items: flex-start; flex-direction: column; gap: 6px; }
  .risk-verdict-title { min-width: 0; flex-wrap: wrap; }
  .market-head { flex-direction: column; }
  .market-actions { width: 100%; justify-content: space-between; }
}

/* ---------- Grid ---------- */
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 16px; }
.card { border: 1px solid var(--el-border-color-light); border-radius: 14px; padding: 16px 18px;
  background: var(--el-bg-color); display: flex; flex-direction: column; min-height: 200px;
  transition: box-shadow .16s, transform .16s, border-color .16s; }
.card:hover { box-shadow: 0 6px 22px rgba(0,0,0,.09); transform: translateY(-2px); }
.card-alert { border-color: #ffc9c6; }
.card header { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; cursor: pointer;
  padding-bottom: 10px; border-bottom: 1px solid var(--el-border-color-lighter); }
.card header h3 { margin: 0; font-size: 16px; flex: 1; }
.c-ico { font-size: 19px; }
.c-badge { font-size: 11px; color: var(--el-text-color-secondary); background: var(--el-fill-color);
  padding: 1px 8px; border-radius: 10px; }
.c-more { font-size: 12px; color: var(--el-color-primary); }
.card-search .c-more, .card-search header { cursor: default; }
.card-search .c-more { cursor: pointer; }
.c-body { flex: 1; display: flex; flex-direction: column; gap: 10px; }
.c-cta { margin: auto 0; font-size: 13px; color: var(--el-text-color-secondary); line-height: 1.7; }
.c-empty { margin: auto 0; display: flex; flex-direction: column; align-items: flex-start; gap: 10px;
  p { margin: 0; font-size: 13px; color: var(--el-text-color-secondary); } }
.c-note { font-size: 11px; color: var(--el-text-color-placeholder); margin-top: auto; }

/* rank list */
.rank-list { display: flex; flex-direction: column; }
.rank-row { display: flex; align-items: center; gap: 8px; padding: 5px 0; cursor: pointer;
  border-bottom: 1px dashed var(--el-border-color-lighter); font-size: 13px; }
.rank-row:last-child { border-bottom: none; }
.rank-row:hover { background: var(--el-fill-color-light); }
.rk { width: 18px; height: 18px; line-height: 18px; text-align: center; border-radius: 4px;
  font-size: 11px; font-weight: 700; background: var(--el-fill-color); color: var(--el-text-color-secondary); }
.rk-name { flex: 1; font-weight: 600; small { color: var(--el-text-color-placeholder); font-weight: 400; margin-left: 5px; } }
.rk-score { font-size: 12px; color: var(--el-text-color-secondary); }

/* kpi mini */
.kpi-mini { display: flex; gap: 8px; }
.kpi-mini > div { flex: 1; background: var(--el-fill-color-light); border-radius: 8px; padding: 8px 4px;
  text-align: center; display: flex; flex-direction: column; gap: 2px;
  b { font-size: 20px; font-weight: 800; } span { font-size: 11px; color: var(--el-text-color-secondary); } }

/* mini table */
.mini-table { width: 100%; border-collapse: collapse; font-size: 13px;
  th { font-weight: 500; color: var(--el-text-color-secondary); font-size: 11px; text-align: right; padding: 3px 4px; }
  th:first-child, td:first-child { text-align: left; }
  td { text-align: right; padding: 5px 4px; border-top: 1px solid var(--el-border-color-lighter); font-weight: 700; } }

/* industry rows */
.ind-row { display: flex; align-items: center; gap: 8px; font-size: 13px; padding: 3px 0; }
.ind-name { width: 84px; color: var(--el-text-color-regular); flex-shrink: 0; }
.ind-bar { flex: 1; height: 6px; background: var(--el-fill-color); border-radius: 3px; overflow: hidden;
  i { display: block; height: 100%; } }
.ind-row > span:last-child { width: 58px; text-align: right; font-weight: 700; }

.chip-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.chip-label { font-size: 11px; color: var(--el-text-color-placeholder); }
.chip { font-size: 12px; padding: 2px 9px; border-radius: 11px; background: var(--el-fill-color);
  b { font-weight: 700; margin-left: 2px; } }
.chip.clickable { cursor: pointer; }
.chip.clickable:hover { background: var(--el-fill-color-dark); }

.pill { font-size: 12px; font-weight: 700; padding: 1px 9px; border-radius: 10px; }
.up, .up2 { color: #ef232a; } .down, .down2 { color: #0e9f5a; } .warn { color: #d48806; } .muted { color: var(--el-text-color-placeholder); }
.up-bg { background: #ef232a; } .down-bg { background: #0e9f5a; } .mid-bg { background: #d48806; }
.rk-safe { color: #0e9f5a; } .pill.rk-safe, .tile-bar i.rk-safe { background: rgba(14,159,90,.85); }
.rk-loading { color: var(--el-text-color-placeholder); }
.rk-warn { color: #d48806; } .pill.rk-warn, .tile-bar i.rk-warn { background: rgba(212,136,6,.85); }
.rk-danger { color: #ef232a; } .pill.rk-danger, .tile-bar i.rk-danger { background: rgba(239,35,42,.85); }
.rk-extreme { color: #a8071a; } .pill.rk-extreme, .tile-bar i.rk-extreme { background: rgba(168,7,26,.9); }
.pill.rk-safe, .pill.rk-warn, .pill.rk-danger, .pill.rk-extreme { color: #fff; }
.mb-gap { color: var(--el-text-color-placeholder); font-size: 12px; }
</style>
