<template>
  <div class="dash">
    <!-- 大盘走向 + 攻防提示 -->
    <section class="hero" :class="`v-${verdictKey}`">
      <div class="hero-left">
        <div class="verdict">
          <span class="verdict-word">{{ verdict.word }}</span>
          <span class="verdict-sub">{{ verdict.sub }}</span>
        </div>
        <div class="hero-advice">
          <span>{{ risk?.action || marketCtx?.advice || '正在评估大盘环境…' }}</span>
        </div>
        <div v-if="marketCtx?.divergence" class="hero-diverge">⚠ {{ marketCtx.divergence }}</div>
        <div class="hero-foot">
          {{ asOfText }}
          <el-button link type="primary" size="small" :loading="heroLoading" @click="loadHero()">刷新</el-button>
          · 规则化提示，不构成投资建议
        </div>
      </div>

      <div class="hero-right">
        <div class="gauge-tiles">
          <div class="tile">
            <span class="tile-label">赚钱效应</span>
            <b class="tile-val" :class="ctxToneClass">{{ marketCtx?.state || '—' }}</b>
            <div class="tile-bar" v-if="marketCtx?.temp != null">
              <i :style="{ width: Math.min(100, marketCtx.temp) + '%' }" :class="tempBarClass" />
            </div>
            <small v-if="marketCtx?.temp != null">温度 {{ marketCtx.temp }}</small>
          </div>
          <div class="tile">
            <span class="tile-label">大盘风险</span>
            <b class="tile-val" :class="riskToneClass">{{ risk?.level || '—' }}</b>
            <div class="tile-bar" v-if="risk">
              <i :style="{ width: Math.min(100, risk.score) + '%' }" :class="riskToneClass" />
            </div>
            <small v-if="risk">风险分 {{ risk.score }}</small>
          </div>
        </div>
        <div class="idx-tiles">
          <div class="idx" v-for="idx in indices" :key="idx.name">
            <span class="idx-name">{{ idx.name }}</span>
            <b :class="idx.last_pct >= 0 ? 'up' : 'down'">{{ pct(idx.last_pct) }}</b>
          </div>
          <div v-if="!indices.length" class="idx idx-empty">指数加载中…</div>
        </div>
      </div>
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
import { computed, onActivated, onMounted, ref } from 'vue'
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

const indices = computed(() => (marketCtx.value?.index?.items || []).slice(0, 3))
const asOfText = computed(() => {
  const c = marketCtx.value
  if (!c?.as_of) return ''
  return c.intraday ? `${c.as_of} 盘中实时` : `截至 ${c.as_of} 收盘`
})
const ctxToneClass = computed(() =>
  marketCtx.value?.state === '偏暖' ? 'up' : marketCtx.value?.state === '偏冷' ? 'down' : '')
const tempBarClass = computed(() => {
  const t = marketCtx.value?.temp ?? 50
  return t >= 60 ? 'up-bg' : t <= 40 ? 'down-bg' : 'mid-bg'
})
const riskKey = computed(() => {
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
  heroLoading.value = true
  let ok = true
  await Promise.all([
    quantApi.marketContext().then((c) => { if (c) marketCtx.value = c; else ok = false }).catch(() => { ok = false }),
    quantApi.riskAlert().then((r) => { if (r) risk.value = r; else ok = false }).catch(() => { ok = false }),
  ])
  heroLoading.value = false
  if (!ok && retries > 0) setTimeout(() => loadHero(retries - 1), 1800)
}

const loadCards = () => {
  quantApi.riskScan(30).then((s) => {
    riskScan.value = s || null
    topSell.value = (s?.items || []).filter((i) => i.severity >= 2)
      .slice(0, 3).map((i) => ({ symbol: i.symbol, name: i.name, signal: i.signal }))
  }).catch(() => {})
  quantApi.picksStats(30).then((res) => {
    const stats = (res?.pools || []).filter((p) => ['smart', 'auction'].includes(p.pool))
    poolStats.value = stats.map((p) => ({
      label: POOL_LABEL[p.pool] || p.pool,
      t1: p.horizons?.t1?.win_rate == null ? null : Math.round(p.horizons.t1.win_rate * 100),
      t3: p.horizons?.t3?.win_rate == null ? null : Math.round(p.horizons.t3.win_rate * 100),
      t5: p.horizons?.t5?.win_rate == null ? null : Math.round(p.horizons.t5.win_rate * 100),
    }))
    smartWin.value = poolStats.value.find((p) => p.label === '一键智选')?.t5 ?? null
    const smartItems = (res?.items || []).filter((i) => i.pool === 'smart')
    const latest = smartItems[0]?.pick_date
    smartDate.value = latest || ''
    smartPicks.value = smartItems.filter((i) => i.pick_date === latest)
      .sort((a, b) => a.rank - b.rank).slice(0, 5)
      .map((i) => ({ symbol: i.symbol, name: i.name, score: Math.round(i.score) }))
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
}

const loadAll = () => { loadRecent(); loadHero(); loadCards() }
onMounted(loadAll)
// keep-alive 返回时若 hero 仍空（冷启动失败），补拉一次
onActivated(() => { if (!marketCtx.value || !risk.value) loadHero() })
</script>

<style scoped lang="scss">
.dash { display: flex; flex-direction: column; gap: 18px; }

/* ---------- Hero ---------- */
.hero { display: flex; gap: 24px; flex-wrap: wrap; justify-content: space-between;
  border: 1px solid var(--el-border-color-light); border-left: 6px solid var(--el-border-color);
  border-radius: 16px; padding: 22px 26px; background: var(--el-fill-color-extra-light); }
.hero-left { flex: 1 1 340px; min-width: 300px; }
.verdict { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }
.verdict-word { font-size: 38px; font-weight: 800; line-height: 1.05; letter-spacing: 1px; }
.verdict-sub { font-size: 14px; color: var(--el-text-color-secondary); }
.hero-advice { margin-top: 12px; font-size: 15px; font-weight: 600; line-height: 1.6; }
.hero-diverge { margin-top: 6px; font-size: 13px; font-weight: 600; color: #d46b08; }
.hero-foot { margin-top: 10px; font-size: 12px; color: var(--el-text-color-placeholder);
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

.hero-right { display: flex; flex-direction: column; gap: 12px; min-width: 260px; }
.gauge-tiles { display: flex; gap: 12px; }
.tile { flex: 1; background: var(--el-bg-color); border: 1px solid var(--el-border-color-lighter);
  border-radius: 12px; padding: 10px 14px; min-width: 120px; }
.tile-label { font-size: 11px; color: var(--el-text-color-secondary); }
.tile-val { display: block; font-size: 20px; font-weight: 800; margin: 2px 0 6px; }
.tile-bar { height: 5px; border-radius: 3px; background: var(--el-fill-color); overflow: hidden;
  i { display: block; height: 100%; } }
.tile small { font-size: 11px; color: var(--el-text-color-placeholder); }
.idx-tiles { display: flex; gap: 10px; flex-wrap: wrap; }
.idx { flex: 1; min-width: 78px; background: var(--el-bg-color); border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px; padding: 8px 10px; display: flex; flex-direction: column; }
.idx-name { font-size: 11px; color: var(--el-text-color-secondary); }
.idx b { font-size: 15px; font-weight: 700; }
.idx-empty { color: var(--el-text-color-placeholder); font-size: 12px; justify-content: center; }

.v-safe { border-left-color: #0e9f5a; background: #f2fdf6; .verdict-word { color: #0e9f5a; } }
.v-warn { border-left-color: #d48806; background: #fffdf3; .verdict-word { color: #d48806; } }
.v-danger { border-left-color: #ef232a; background: #fff6f5; .verdict-word { color: #ef232a; } }
.v-extreme { border-left-color: #a8071a; background: #fff5f4; .verdict-word { color: #a8071a; } }

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
.rk-warn { color: #d48806; } .pill.rk-warn, .tile-bar i.rk-warn { background: rgba(212,136,6,.85); }
.rk-danger { color: #ef232a; } .pill.rk-danger, .tile-bar i.rk-danger { background: rgba(239,35,42,.85); }
.rk-extreme { color: #a8071a; } .pill.rk-extreme, .tile-bar i.rk-extreme { background: rgba(168,7,26,.9); }
.pill.rk-safe, .pill.rk-warn, .pill.rk-danger, .pill.rk-extreme { color: #fff; }
</style>
