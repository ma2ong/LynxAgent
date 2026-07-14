<template>
  <div class="today-page">
    <header class="today-head">
      <div>
        <h1>今日</h1>
        <p>{{ marketSentence }}</p>
      </div>
      <div class="head-actions">
        <el-tag v-if="marketData?.as_of" effect="plain">更新至 {{ marketData.as_of }}</el-tag>
        <el-button :loading="loading" @click="loadAll">刷新</el-button>
      </div>
    </header>

    <el-alert v-if="error" :title="error" type="warning" show-icon :closable="false" />

    <section v-if="showOnboarding" class="onboarding-panel">
      <div class="onboarding-head">
        <div>
          <span class="eyebrow">新手路径</span>
          <h2>先把三件事跑通</h2>
        </div>
        <el-button text @click="dismissOnboarding">收起</el-button>
      </div>
      <div class="onboarding-steps">
        <button @click="router.push('/quant')">
          <b>1</b>
          <span>跑一次智能选股</span>
          <small>全市场结构因子评分，选出当前候选池。</small>
        </button>
        <button @click="router.push('/favorites')">
          <b>2</b>
          <span>建立自选池</span>
          <small>把常看的股票放进同一个跟踪面板。</small>
        </button>
        <button @click="router.push('/stock-analysis')">
          <b>3</b>
          <span>跑一次深研</span>
          <small>输入股票代码，查看量化、催化和风险。</small>
        </button>
      </div>
    </section>

    <section class="kpi-grid">
      <div class="kpi-card">
        <span>情绪温度</span>
        <b :class="toneClass">{{ sentimentText }}</b>
        <small>{{ toneLabel }}</small>
      </div>
      <div class="kpi-card">
        <span>成交额</span>
        <b>{{ fmtTurnover(kpi.turnover_yi) }}</b>
        <small :class="Number(kpi.turnover_change_yi || 0) >= 0 ? 'up' : 'down'">
          {{ signed(kpi.turnover_change_yi, '亿') }}
        </small>
      </div>
      <div class="kpi-card">
        <span>涨跌比</span>
        <b>{{ kpi.adv_dec_ratio ?? '-' }}</b>
        <small>涨 {{ kpi.advancers ?? '-' }} / 跌 {{ kpi.decliners ?? '-' }}</small>
      </div>
      <div class="kpi-card">
        <span>连板高度</span>
        <b>{{ kpi.max_board_height ?? '-' }}板</b>
        <small>涨停 {{ kpi.limit_up ?? '-' }} / 跌停 {{ kpi.limit_down ?? '-' }}</small>
      </div>
    </section>

    <section class="main-grid">
      <div class="panel">
        <div class="panel-head">
          <h2>催化剂 TOP3</h2>
          <el-button text type="primary" @click="router.push('/insights/catalyst')">更多</el-button>
        </div>
        <div v-if="catalystComputing" class="soft-empty">事件引擎正在扫描，稍后自动刷新</div>
        <button
          v-for="event in topCatalysts"
          :key="event.theme + event.event"
          class="event-row"
          @click="router.push('/insights/catalyst')"
        >
          <div class="event-title">
            <span>{{ event.theme || '未命名事件' }}</span>
            <el-tag v-if="event.significance != null" size="small" type="danger" effect="plain">
              {{ event.significance }}/10
            </el-tag>
          </div>
          <p>{{ event.thesis || event.event }}</p>
          <div class="stock-tags">
            <el-tag v-for="b in (event.beneficiaries || []).slice(0, 4)" :key="b.symbol" size="small" effect="plain">
              {{ b.name }}
            </el-tag>
          </div>
        </button>
        <el-empty v-if="!catalystComputing && !topCatalysts.length" description="暂无高质量催化剂" :image-size="72">
          <el-button size="small" @click="loadCatalysts">刷新催化剂</el-button>
        </el-empty>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h2>涨停主线</h2>
          <el-button text type="primary" @click="router.push('/limit-up')">明细</el-button>
        </div>
        <button
          v-for="theme in limitThemes"
          :key="theme.cause"
          class="theme-row"
          @click="router.push('/limit-up')"
        >
          <div>
            <span>{{ theme.cause }}</span>
            <small>最高 {{ theme.max_height || 1 }} 板</small>
          </div>
          <b>{{ theme.total }}</b>
        </button>
        <el-empty v-if="!limitThemes.length" description="暂无涨停主线数据" :image-size="72">
          <el-button size="small" @click="router.push('/limit-up')">查看涨停热点</el-button>
        </el-empty>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h2>自选异动</h2>
          <el-button text type="primary" @click="router.push('/favorites')">管理</el-button>
        </div>
        <button
          v-for="item in favoriteMovers"
          :key="item.symbol || item.stock_code"
          class="favorite-row"
          @click="goStock(item.symbol || item.stock_code)"
        >
          <div>
            <span>{{ item.stock_name || item.symbol }}</span>
            <small>{{ item.symbol || item.stock_code }}</small>
          </div>
          <b :class="Number(item.change_percent || 0) >= 0 ? 'up' : 'down'">
            {{ pct(item.change_percent) }}
          </b>
        </button>
        <el-empty v-if="!favoriteMovers.length" description="暂无自选异动" :image-size="72">
          <el-button size="small" @click="router.push('/favorites')">添加自选</el-button>
        </el-empty>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ApiClient } from '@/api/request'
import { quantApi, type SerenityEvent } from '@/api/quant'
import { favoritesApi, type FavoriteItem } from '@/api/favorites'

const router = useRouter()
const loading = ref(false)
const error = ref('')
const marketData = ref<any>(null)
const limitData = ref<any>(null)
const catalystEvents = ref<SerenityEvent[]>([])
const catalystComputing = ref(false)
const favorites = ref<FavoriteItem[]>([])
const ONBOARDING_KEY = 'lynx_onboarding_done'
const showOnboarding = ref(localStorage.getItem(ONBOARDING_KEY) !== '1')

const kpi = computed(() => marketData.value?.kpi || {})

const sentimentText = computed(() => {
  const value = kpi.value.sentiment_temperature
  return value == null ? '-' : `${value}%`
})

const toneLabel = computed(() => {
  if (kpi.value.sentiment_temperature == null) return '等待数据'
  const value = Number(kpi.value.sentiment_temperature || 0)
  if (value >= 70) return '高热'
  if (value >= 55) return '偏强'
  if (value >= 40) return '中性'
  return '低温'
})

const toneClass = computed(() => {
  if (kpi.value.sentiment_temperature == null) return ''
  const value = Number(kpi.value.sentiment_temperature || 0)
  if (value >= 70) return 'hot'
  if (value >= 55) return 'warm'
  if (value >= 40) return ''
  return 'cool'
})

const marketSentence = computed(() => {
  if (!marketData.value?.kpi) return '正在读取本地行情、涨停梯队和事件引擎。'
  const temp = kpi.value.sentiment_temperature
  const up = kpi.value.limit_up
  const down = kpi.value.limit_down
  return `市场温度 ${temp}%（${toneLabel.value}），涨停 ${up} 只、跌停 ${down} 只，优先看主线强度和可验证催化。`
})

const topCatalysts = computed(() =>
  [...catalystEvents.value]
    .sort((a: any, b: any) => Number(b.significance || 0) - Number(a.significance || 0))
    .slice(0, 3),
)

const limitThemes = computed(() => {
  const ladder = marketData.value?.cause_ladder
  if (Array.isArray(ladder) && ladder.length) return ladder.slice(0, 6)
  const totals = limitData.value?.cause_total || {}
  return Object.keys(totals)
    .map((cause) => ({ cause, total: Number(totals[cause] || 0), max_height: 1 }))
    .sort((a, b) => b.total - a.total)
    .slice(0, 6)
})

const favoriteMovers = computed(() =>
  favorites.value
    .filter((item) => item.change_percent != null)
    .sort((a, b) => Math.abs(Number(b.change_percent || 0)) - Math.abs(Number(a.change_percent || 0)))
    .slice(0, 6),
)

function today() {
  return new Date().toISOString().slice(0, 10)
}

function daysAgo(n: number) {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return d.toISOString().slice(0, 10)
}

function payload(res: any) {
  return res?.data || res
}

async function loadMarket() {
  const res = await ApiClient.get('/api/lite/market-sentiment', { start: daysAgo(30), end: today() }, { timeout: 60000 })
  marketData.value = payload(res)
}

async function loadLimitUp() {
  const target = marketData.value?.as_of || today()
  const res = await ApiClient.get('/api/lite/limit-up', { date: target }, { timeout: 30000 })
  limitData.value = payload(res)
}

async function loadCatalysts() {
  const res: any = await quantApi.serenityEvents(false, 10)
  catalystComputing.value = res?.status === 'computing'
  catalystEvents.value = res?.events || []
}

async function loadFavorites() {
  const res: any = await favoritesApi.list()
  favorites.value = Array.isArray(res) ? res : Array.isArray(res?.data) ? res.data : []
}

async function loadAll() {
  loading.value = true
  error.value = ''
  try {
    await loadMarket()
    await Promise.allSettled([loadLimitUp(), loadCatalysts(), loadFavorites()])
  } catch (e: any) {
    error.value = e?.message || '今日数据加载失败'
  } finally {
    loading.value = false
  }
}

function fmtTurnover(value?: number) {
  if (value == null) return '-'
  return `${Number(value).toFixed(0)}亿`
}

function signed(value?: number, unit = '') {
  if (value == null) return '-'
  const n = Number(value)
  return `${n >= 0 ? '+' : ''}${n.toFixed(1)}${unit}`
}

function pct(value?: number | null) {
  if (value == null) return '-'
  const n = Number(value)
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

function goStock(symbol?: string) {
  if (!symbol) return
  router.push({ path: '/stock-analysis', query: { symbol } })
}

function dismissOnboarding() {
  localStorage.setItem(ONBOARDING_KEY, '1')
  showOnboarding.value = false
}

onMounted(loadAll)
</script>

<style scoped lang="scss">
.today-page { display: flex; flex-direction: column; gap: 14px; }
.today-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;

  h1 { margin: 0 0 4px; font-size: 24px; }
  p { margin: 0; color: var(--el-text-color-secondary); }
}
.head-actions { display: flex; align-items: center; gap: 8px; }
.onboarding-panel {
  background: var(--el-bg-color);
  border: 1px solid var(--el-color-primary-light-5);
  border-radius: 8px;
  padding: 14px;
}
.onboarding-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;

  h2 { margin: 3px 0 0; font-size: 18px; }
}
.eyebrow {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.onboarding-steps {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;

  button {
    display: grid;
    grid-template-columns: 28px 1fr;
    gap: 2px 9px;
    align-items: center;
    padding: 12px;
    border: 1px solid var(--el-border-color-lighter);
    border-radius: 7px;
    background: var(--el-fill-color-extra-light);
    text-align: left;
    cursor: pointer;
  }

  button:hover {
    border-color: var(--el-color-primary-light-5);
    background: var(--el-color-primary-light-9);
  }

  b {
    grid-row: span 2;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: var(--el-color-primary);
    color: #fff;
    line-height: 28px;
    text-align: center;
  }

  span {
    font-weight: 700;
    color: var(--el-text-color-primary);
  }

  small {
    color: var(--el-text-color-secondary);
    line-height: 1.45;
  }
}
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(160px, 1fr));
  gap: 10px;
}
.kpi-card {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 14px 16px;

  span, small { display: block; color: var(--el-text-color-secondary); font-size: 12px; }
  b { display: block; margin: 6px 0 3px; font-size: 26px; line-height: 1; }
}
.main-grid {
  display: grid;
  grid-template-columns: 1.35fr 1fr 1fr;
  gap: 12px;
}
.panel {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 14px;
  min-height: 300px;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;

  h2 { margin: 0; font-size: 16px; }
}
.event-row,
.theme-row,
.favorite-row {
  width: 100%;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 7px;
  background: var(--el-fill-color-extra-light);
  margin-bottom: 8px;
  padding: 10px 12px;
  text-align: left;
  cursor: pointer;
}
.event-row:hover,
.theme-row:hover,
.favorite-row:hover { border-color: var(--el-color-primary-light-5); background: var(--el-color-primary-light-9); }
.event-title,
.theme-row,
.favorite-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.event-title span,
.theme-row span,
.favorite-row span { font-weight: 700; color: var(--el-text-color-primary); }
.event-row p { margin: 6px 0 8px; line-height: 1.55; color: var(--el-text-color-regular); font-size: 13px; }
.stock-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.theme-row small,
.favorite-row small { display: block; margin-top: 4px; color: var(--el-text-color-secondary); }
.theme-row b { color: #ef232a; font-size: 20px; }
.soft-empty { color: var(--el-text-color-secondary); font-size: 13px; padding: 16px 0; }
.hot, .warm, .up { color: #ef232a; }
.cool, .down { color: #14b143; }
@media (max-width: 1100px) {
  .kpi-grid, .main-grid { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 760px) {
  .kpi-grid, .main-grid { grid-template-columns: 1fr; }
  .onboarding-steps { grid-template-columns: 1fr; }
  .today-head { flex-direction: column; }
}
</style>
