<template>
  <div class="dash">
    <!-- 大盘走向 + 攻防提示 -->
    <section class="hero" :class="`v-${verdictKey}`">
      <div class="hero-main">
        <div class="verdict">
          <span class="verdict-word">{{ verdict.word }}</span>
          <span class="verdict-sub">{{ verdict.sub }}</span>
        </div>
        <div class="hero-metrics">
          <div class="hm">
            <span class="hm-label">赚钱效应</span>
            <b :class="ctxToneClass">{{ marketCtx?.state || '—' }}</b>
            <small v-if="marketCtx?.temp != null">温度 {{ marketCtx.temp }}</small>
          </div>
          <div class="hm">
            <span class="hm-label">风险分</span>
            <b :class="riskToneClass">{{ risk?.level || '—' }}</b>
            <small v-if="risk">{{ risk.score }}</small>
          </div>
          <div class="hm hm-idx" v-for="idx in indices" :key="idx.name">
            <span class="hm-label">{{ idx.name }}</span>
            <b :class="idx.last_pct >= 0 ? 'up' : 'down'">{{ pct(idx.last_pct) }}</b>
          </div>
        </div>
      </div>
      <div class="hero-advice">
        <span>{{ risk?.action || marketCtx?.advice || '正在评估大盘环境…' }}</span>
        <span v-if="marketCtx?.divergence" class="hero-diverge">⚠ {{ marketCtx.divergence }}</span>
      </div>
      <div class="hero-foot">
        {{ marketCtx?.intraday ? `${marketCtx.as_of} 盘中实时` : marketCtx?.as_of ? `截至 ${marketCtx.as_of} 收盘` : '' }}
        · 数据为规则化提示，不构成投资建议
      </div>
    </section>

    <!-- 模块预览 -->
    <section class="grid">
      <!-- 智能选股 -->
      <article class="card" @click="go('/quant')">
        <header><span class="c-ico">🎯</span><h3>智能选股</h3><span class="c-more">进入 →</span></header>
        <div class="c-body">
          <template v-if="smartPicks.length">
            <div class="chip-row">
              <span v-for="s in smartPicks" :key="s.symbol" class="chip">{{ s.name }}</span>
            </div>
            <small class="c-note">最近一批一键智选留痕{{ smartWin != null ? ` · T+5胜率 ${smartWin}%` : '' }}</small>
          </template>
          <p v-else class="c-cta">一键横向比较全市场 A 股，输出量化推荐池</p>
        </div>
      </article>

      <!-- 风险预警 -->
      <article class="card" @click="go('/risk-alert')">
        <header><span class="c-ico">⚠️</span><h3>风险预警</h3><span class="c-more">进入 →</span></header>
        <div class="c-body">
          <template v-if="risk">
            <div class="stat-row">
              <span class="pill" :class="riskToneClass">{{ risk.level }} {{ risk.score }}</span>
              <span v-if="riskScan" class="mini">退出 <b class="down2">{{ riskScan.recommendation_counts?.exit || 0 }}</b>
                · 减仓 <b class="warn">{{ riskScan.recommendation_counts?.reduce || 0 }}</b></span>
            </div>
            <div v-if="topSell.length" class="chip-row">
              <span v-for="s in topSell" :key="s.symbol" class="chip chip-danger">{{ s.name }} {{ s.signal }}</span>
            </div>
          </template>
          <p v-else class="c-cta">大盘仓位红绿灯 + 全市场卖出信号</p>
        </div>
      </article>

      <!-- 集合竞价 -->
      <article class="card" @click="go('/call-auction')">
        <header><span class="c-ico">🌅</span><h3>集合竞价</h3><span class="c-more">进入 →</span></header>
        <div class="c-body">
          <template v-if="auction">
            <div class="stat-row">
              <span class="mini">抢筹 <b class="up2">{{ auction.pc.accumulation }}</b>
                · 洗盘 <b class="up2">{{ auction.pc.shakeout }}</b>
                · 出货 <b class="down2">{{ auction.pc.distribution }}</b></span>
            </div>
            <div v-if="auction.top.length" class="chip-row">
              <span v-for="c in auction.top" :key="c.code" class="chip">{{ c.name }} +{{ c.open_pct }}%</span>
            </div>
            <small v-else class="c-note">当日无符合闸门的高开候选</small>
          </template>
          <p v-else class="c-cta">竞价高开 + 四形态硬闸门筛买入候选</p>
        </div>
      </article>

      <!-- 涨停热点 -->
      <article class="card" @click="go('/limit-up')">
        <header><span class="c-ico">🔥</span><h3>涨停热点</h3><span class="c-more">进入 →</span></header>
        <div class="c-body">
          <template v-if="limitUp">
            <div class="stat-row">
              <span class="mini">涨停 <b class="up2">{{ limitUp.up }}</b>
                · 跌停 <b class="down2">{{ limitUp.down }}</b>
                · 最高 <b>{{ limitUp.maxBoards }}</b> 板</span>
            </div>
            <div v-if="limitUp.causes.length" class="chip-row">
              <span v-for="c in limitUp.causes" :key="c.name" class="chip">{{ c.name }} {{ c.n }}</span>
            </div>
          </template>
          <p v-else class="c-cta">涨停家数、连板高度与题材分布</p>
        </div>
      </article>

      <!-- 行业热力 -->
      <article class="card" @click="go('/heatmap')">
        <header><span class="c-ico">📊</span><h3>行业热力</h3><span class="c-more">进入 →</span></header>
        <div class="c-body">
          <template v-if="hotIndustries.length">
            <div class="ind-row" v-for="i in hotIndustries" :key="i.name">
              <span class="ind-name">{{ i.name }}</span>
              <span :class="i.pct >= 0 ? 'up' : 'down'">{{ pct(i.pct) }}</span>
            </div>
          </template>
          <p v-else class="c-cta">全市场行业涨跌热力，红涨绿跌</p>
        </div>
      </article>

      <!-- 选股复盘 -->
      <article class="card" @click="go('/review')">
        <header><span class="c-ico">📈</span><h3>选股复盘</h3><span class="c-more">进入 →</span></header>
        <div class="c-body">
          <template v-if="poolStats.length">
            <div class="ind-row" v-for="p in poolStats" :key="p.label">
              <span class="ind-name">{{ p.label }}</span>
              <span :class="(p.win ?? 0) >= 50 ? 'up' : 'down'">T+5 {{ p.win == null ? '—' : p.win + '%' }}</span>
            </div>
          </template>
          <p v-else class="c-cta">各池 T+1/T+3/T+5 真实胜率与超额</p>
        </div>
      </article>

      <!-- 我的自选股 -->
      <article class="card" @click="go('/favorites')">
        <header><span class="c-ico">⭐</span><h3>我的自选股</h3><span class="c-more">进入 →</span></header>
        <div class="c-body">
          <template v-if="favs.length">
            <div class="ind-row" v-for="f in favTop" :key="f.symbol">
              <span class="ind-name">{{ f.name }}</span>
              <span :class="(f.pct ?? 0) >= 0 ? 'up' : 'down'">{{ pct(f.pct ?? 0) }}</span>
            </div>
            <small class="c-note">共 {{ favs.length }} 只自选</small>
          </template>
          <p v-else class="c-cta">收藏个股，实时跟踪涨跌与预警</p>
        </div>
      </article>

      <!-- 个股深研 -->
      <article class="card card-search">
        <header><span class="c-ico">🔍</span><h3>个股深研</h3><span class="c-more" @click="go('/stock-analysis')">进入 →</span></header>
        <div class="c-body">
          <el-input v-model="searchKw" size="small" clearable placeholder="输入名称或代码，回车研究"
            @keyup.enter="doSearch" @click.stop />
          <div v-if="recent.length" class="chip-row">
            <span v-for="r in recent" :key="r.code" class="chip" @click.stop="goStock(r.code)">
              {{ r.name || r.code }}
            </span>
          </div>
        </div>
      </article>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ApiClient } from '@/api/request'
import { quantApi, heatmapApi, type MarketContext, type RiskAlert, type RiskScan } from '@/api/quant'
import { favoritesApi } from '@/api/favorites'
defineOptions({ name: 'DashboardPage' })

const router = useRouter()
const go = (path: string) => router.push(path)
const pct = (v: number) => `${v > 0 ? '+' : ''}${(v ?? 0).toFixed(2)}%`

const marketCtx = ref<MarketContext | null>(null)
const risk = ref<RiskAlert | null>(null)
const riskScan = ref<RiskScan | null>(null)

const indices = computed(() => (marketCtx.value?.index?.items || []).slice(0, 3))
const ctxToneClass = computed(() =>
  marketCtx.value?.state === '偏暖' ? 'up' : marketCtx.value?.state === '偏冷' ? 'down' : '')
const riskKey = computed(() => {
  const l = risk.value?.level
  return l === '极危' ? 'extreme' : l === '危险' ? 'danger' : l === '警惕' ? 'warn' : 'safe'
})
const riskToneClass = computed(() => `rk-${riskKey.value}`)

// 攻防结论：以风险等级为主导，回答「能进攻还是要防守」
const verdict = computed(() => {
  const l = risk.value?.level
  if (l === '极危') return { word: '清仓观望', sub: '流动性踩踏风险，停止买入' }
  if (l === '危险') return { word: '防守减仓', sub: '停止新增买入，只留最强主线' }
  if (l === '警惕') return { word: '谨慎参与', sub: '控制仓位，只打强势方向' }
  if (l === '安全') return { word: '可以进攻', sub: '赚钱效应尚可，按纪律选股' }
  return { word: '评估中', sub: '正在加载大盘环境' }
})
const verdictKey = riskKey

// —— 各卡片预览（独立拉取，互不阻塞，失败降级为 CTA）——
const smartPicks = ref<{ symbol: string; name: string }[]>([])
const smartWin = ref<number | null>(null)
const poolStats = ref<{ label: string; win: number | null }[]>([])
const topSell = ref<{ symbol: string; name: string; signal: string }[]>([])
const auction = ref<{ pc: Record<string, number>; top: any[] } | null>(null)
const limitUp = ref<{ up: number; down: number; maxBoards: number; causes: { name: string; n: number }[] } | null>(null)
const hotIndustries = ref<{ name: string; pct: number }[]>([])
const favs = ref<{ symbol: string; name: string; pct: number | null }[]>([])
const favTop = computed(() =>
  [...favs.value].sort((a, b) => Math.abs(b.pct ?? 0) - Math.abs(a.pct ?? 0)).slice(0, 3))

const POOL_LABEL: Record<string, string> = { smart: '一键智选', auction: '竞价优选' }
const searchKw = ref('')
const recent = ref<{ code: string; name: string }[]>([])
const doSearch = () => {
  const kw = searchKw.value.trim()
  if (kw) router.push({ name: 'stock-analysis', query: { symbol: kw } })
}
const goStock = (code: string) => router.push({ name: 'stock-analysis', query: { symbol: code } })

const loadRecent = () => {
  try {
    recent.value = (JSON.parse(localStorage.getItem('stock_analysis_history') || '[]') || []).slice(0, 6)
  } catch { recent.value = [] }
}

onMounted(() => {
  loadRecent()
  quantApi.marketContext().then((c) => (marketCtx.value = c || null)).catch(() => {})
  quantApi.riskAlert().then((r) => (risk.value = r || null)).catch(() => {})
  quantApi.riskScan(30).then((s) => {
    riskScan.value = s || null
    topSell.value = (s?.items || []).filter((i) => i.severity >= 2)
      .slice(0, 3).map((i) => ({ symbol: i.symbol, name: i.name, signal: i.signal }))
  }).catch(() => {})
  quantApi.picksStats(30).then((res) => {
    const stats = (res?.pools || []).filter((p) => ['smart', 'auction'].includes(p.pool))
    poolStats.value = stats.map((p) => ({ label: POOL_LABEL[p.pool] || p.pool, win: p.horizons?.t5?.win_rate == null ? null : Math.round(p.horizons.t5.win_rate * 100) }))
    smartWin.value = poolStats.value.find((p) => p.label === '一键智选')?.win ?? null
    const smartItems = (res?.items || []).filter((i) => i.pool === 'smart')
    const latest = smartItems[0]?.pick_date
    smartPicks.value = smartItems.filter((i) => i.pick_date === latest).slice(0, 4)
      .map((i) => ({ symbol: i.symbol, name: i.name }))
  }).catch(() => {})
  heatmapApi.fetch('industry').then((d) => {
    const items = (d?.items || [])
    const hot = [...items].sort((a, b) => b.pct - a.pct).slice(0, 3)
    const cold = [...items].sort((a, b) => a.pct - b.pct).slice(0, 2)
    hotIndustries.value = [...hot, ...cold].map((i) => ({ name: i.name, pct: i.pct }))
  }).catch(() => {})
  favoritesApi.list().then((raw: any) => {
    const list = (raw?.data ?? raw) || []
    favs.value = list.map((f: any) => ({
      symbol: f.symbol || f.stock_code, name: f.stock_name, pct: f.change_percent ?? null,
    }))
  }).catch(() => {})
  ApiClient.get<any>('/api/lite/call-auction', { _ts: Date.now() }, { timeout: 30000 }).then((raw) => {
    const d = raw?.data ?? raw
    const pc = d?.auction_tape?.pattern_counts
    if (d) auction.value = {
      pc: { accumulation: pc?.accumulation || 0, shakeout: pc?.shakeout || 0, distribution: pc?.distribution || 0 },
      top: (d.buy_candidates || []).slice(0, 3),
    }
  }).catch(() => {})
  ApiClient.get<any>('/api/lite/limit-up', {}, { timeout: 45000 }).then((raw) => {
    const d = raw?.data ?? raw
    if (d?.total_limit_up != null) limitUp.value = {
      up: d.total_limit_up, down: d.total_limit_down, maxBoards: d.max_boards,
      causes: (d.causes || []).slice(0, 3).map((c: string) => ({ name: c, n: d.cause_total?.[c] || 0 })),
    }
  }).catch(() => {})
})
</script>

<style scoped lang="scss">
.dash { display: flex; flex-direction: column; gap: 16px; }

/* Hero */
.hero { border: 1px solid var(--el-border-color-light); border-left: 6px solid var(--el-border-color);
  border-radius: 14px; padding: 18px 22px; background: var(--el-fill-color-extra-light); }
.hero-main { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 16px; }
.verdict { display: flex; flex-direction: column; }
.verdict-word { font-size: 30px; font-weight: 800; line-height: 1.1; }
.verdict-sub { font-size: 13px; color: var(--el-text-color-secondary); margin-top: 2px; }
.hero-metrics { display: flex; gap: 20px; flex-wrap: wrap; }
.hm { display: flex; flex-direction: column; align-items: flex-end; }
.hm-label { font-size: 11px; color: var(--el-text-color-secondary); }
.hm b { font-size: 17px; font-weight: 700; }
.hm small { font-size: 11px; color: var(--el-text-color-placeholder); }
.hero-advice { margin-top: 12px; font-size: 14px; font-weight: 600; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.hero-diverge { font-size: 12px; font-weight: 600; color: #d46b08; }
.hero-foot { margin-top: 6px; font-size: 11px; color: var(--el-text-color-placeholder); }

.v-safe { border-left-color: #0e9f5a; background: #f0fff4; .verdict-word { color: #0e9f5a; } }
.v-warn { border-left-color: #d48806; background: #fffbe6; .verdict-word { color: #d48806; } }
.v-danger { border-left-color: #ef232a; background: #fff1f0; .verdict-word { color: #ef232a; } }
.v-extreme { border-left-color: #a8071a; background: #fff1f0; .verdict-word { color: #a8071a; } }

/* Grid */
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }
.card { border: 1px solid var(--el-border-color-light); border-radius: 12px; padding: 14px 16px;
  background: var(--el-bg-color); cursor: pointer; transition: box-shadow .15s, transform .15s;
  display: flex; flex-direction: column; min-height: 130px; }
.card:hover { box-shadow: 0 4px 16px rgba(0,0,0,.08); transform: translateY(-2px); }
.card-search { cursor: default; }
.card header { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.card header h3 { margin: 0; font-size: 15px; flex: 1; }
.c-ico { font-size: 18px; }
.c-more { font-size: 12px; color: var(--el-color-primary); }
.card-search .c-more { cursor: pointer; }
.c-body { flex: 1; display: flex; flex-direction: column; gap: 8px; }
.c-cta { margin: 0; font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.6; }
.c-note { font-size: 11px; color: var(--el-text-color-placeholder); }
.chip-row { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font-size: 12px; padding: 2px 8px; border-radius: 10px; background: var(--el-fill-color); }
.chip-danger { background: rgba(239,35,42,.1); color: #ef232a; }
.stat-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.mini { font-size: 12px; color: var(--el-text-color-regular); }
.pill { font-size: 13px; font-weight: 700; padding: 1px 8px; border-radius: 10px; }
.ind-row { display: flex; justify-content: space-between; font-size: 13px; padding: 2px 0; }
.ind-name { color: var(--el-text-color-regular); }

.up, .up2 { color: #ef232a; }
.down, .down2 { color: #0e9f5a; }
.warn { color: #d48806; }
b.up2, b.down2, b.warn { font-weight: 700; }
.rk-safe { color: #0e9f5a; } .pill.rk-safe { background: rgba(14,159,90,.12); }
.rk-warn { color: #d48806; } .pill.rk-warn { background: rgba(212,136,6,.14); }
.rk-danger { color: #ef232a; } .pill.rk-danger { background: rgba(239,35,42,.12); }
.rk-extreme { color: #a8071a; } .pill.rk-extreme { background: rgba(168,7,26,.16); }
</style>
