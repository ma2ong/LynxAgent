<template>
  <div class="radar-page">
    <header class="page-head">
      <div>
        <div class="title-line">
          <h1>盘中机会雷达</h1>
          <span class="live-dot" :class="{ active: data?.status === 'live' }" />
          <span class="phase">{{ data?.phase_label || '等待行情' }}</span>
        </div>
        <p>交易时段持续快扫；收盘后保留当天触发记录，没有实时留痕时提供收盘复盘候选。</p>
      </div>
      <div class="head-actions">
        <span v-if="data?.as_of" class="updated">扫描 {{ timeOnly(data.as_of) }}</span>
        <el-button size="small" :loading="loading" @click="load(true)">立即刷新</el-button>
      </div>
    </header>

    <el-alert
      v-if="data?.status === 'degraded'"
      type="warning"
      :title="`实时行情暂时降级：${data.error || '正在自动重试'}`"
      :closable="false"
      show-icon
    />

    <section class="market-strip">
      <div class="market-main">
        <span class="market-tone" :class="toneClass">{{ data?.market?.tone || '等待' }}</span>
        <span>全市场中位 {{ signed(data?.market?.median_pct) }}</span>
        <span>上涨家数占比 {{ percent(data?.market?.breadth_up) }}</span>
        <span v-if="data?.universe">覆盖 {{ data.universe }} 只</span>
      </div>
      <div class="scan-note">{{ scanNote }}</div>
    </section>

    <section class="stat-grid">
      <button class="stat-card entry" :class="{ selected: filter === 'entry' }" @click="toggleFilter('entry')">
        <b>{{ data?.entry_count || 0 }}</b>
        <span>{{ entryStatLabel }}</span>
        <small>{{ entryStatHint }}</small>
      </button>
      <button class="stat-card watch" :class="{ selected: filter === 'watch' }" @click="toggleFilter('watch')">
        <b>{{ data?.watch_count || 0 }}</b>
        <span>提前预警</span>
        <small>等待二次确认</small>
      </button>
      <button class="stat-card blocked" :class="{ selected: filter === 'unbuyable' }" @click="toggleFilter('unbuyable')">
        <b>{{ data?.unbuyable_count || 0 }}</b>
        <span>不可追入</span>
        <small>已涨停或空间不足</small>
      </button>
      <button class="stat-card all" :class="{ selected: filter === 'all' }" @click="filter = 'all'">
        <b>{{ data?.candidate_count || 0 }}</b>
        <span>当前全部</span>
        <small>按状态和强度排序</small>
      </button>
    </section>

    <section class="signal-section">
      <div class="section-head">
        <div>
          <h2>{{ signalSectionTitle }}</h2>
          <p>{{ data?.selection_note || data?.method_note }}</p>
        </div>
        <el-input v-model="keyword" clearable size="small" placeholder="搜索股票/代码/行业" class="search" />
      </div>

      <div v-if="filteredItems.length" class="signal-grid">
        <article v-for="item in filteredItems" :key="`${item.symbol}-${item.status}`" class="signal-card" :class="item.status">
          <div class="card-top">
            <div>
              <div class="stock-name">
                <a @click="openStock(item.symbol)">{{ item.name }}</a>
                <small>{{ item.symbol }}</small>
              </div>
              <span class="industry">{{ item.industry }}</span>
            </div>
            <el-tag :type="statusType(item.status)" effect="dark" size="small">{{ item.status_label }}</el-tag>
          </div>

          <div class="price-row">
            <div>
              <b>{{ price(item.current_price) }}</b>
              <span :class="item.pct_chg >= 0 ? 'up' : 'down'">{{ signed(item.pct_chg) }}</span>
            </div>
            <div class="score">
              <small>信号强度</small>
              <strong>{{ item.score }}</strong>
            </div>
          </div>

          <div v-if="item.signal_mode === 'close_review'" class="review-box">
            <template v-if="item.status === 'unbuyable'">
              收盘时距离涨停仅 {{ item.distance_to_limit.toFixed(2) }}%，列入复盘但不作为次日追入候选。
            </template>
            <template v-else>
              收盘快照满足量价与结构条件，仅列入下一交易日观察池；开盘后仍需等待实时确认。
            </template>
          </div>
          <div v-else-if="item.signal_mode === 'intraday_archive'" class="archive-box">
            今日 {{ timeOnly(item.triggered_at) }} 曾出现{{ item.status_label }}，当前已经收盘，原入场区间不再有效。
          </div>
          <div v-else-if="item.status === 'entry'" class="trade-box">
            <div><span>参考入场</span><b>{{ price(item.entry_low) }}—{{ price(item.entry_high) }}</b></div>
            <div><span>停止追价</span><b class="warn">{{ price(item.chase_limit) }}</b></div>
            <div><span>结构失效</span><b class="down">{{ price(item.invalidation_price) }}</b></div>
          </div>
          <div v-else-if="item.status === 'watch'" class="watch-box">
            等待量价持续确认；触发价 {{ price(item.signal_price) }}，跌破
            {{ price(item.invalidation_price) }} 视为失效。
          </div>
          <div v-else class="blocked-box">
            距离涨停仅 {{ item.distance_to_limit.toFixed(2) }}%，当前不再提供追入价格。
          </div>

          <div class="metric-row">
            <span><small>量能</small><b>{{ item.activity_ratio.toFixed(1) }}×</b></span>
            <span><small>短时涨速</small><b>{{ signed(item.speed_1m) }}/分</b></span>
            <span><small>板块</small><b :class="item.sector_change >= 0 ? 'up' : 'down'">{{ signed(item.sector_change) }}</b></span>
          </div>

          <ul class="reasons">
            <li v-for="reason in item.reasons" :key="reason">{{ reason }}</li>
          </ul>

          <div class="card-foot">
            <span>{{ signalTimeLabel(item) }}</span>
            <button @click="openStock(item.symbol)">查看个股深研 →</button>
          </div>
        </article>
      </div>
      <el-empty
        v-else-if="!loading"
        :description="emptyText"
        :image-size="86"
      />
      <el-skeleton v-else :rows="6" animated />
    </section>

    <section class="history-section">
      <div class="section-head">
        <div>
          <h2>今日状态留痕</h2>
          <p>只记录状态变化，不会因每轮扫描重复写入同一信号。</p>
        </div>
      </div>
      <el-table v-if="historyRows.length" :data="historyRows" size="small" stripe max-height="360">
        <el-table-column label="时间" width="90">
          <template #default="{ row }">{{ timeOnly(row.triggered_at) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ eventLabel(row) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="股票" min-width="150">
          <template #default="{ row }">
            <a class="stock-link" @click="openStock(row.symbol)">{{ row.name }} <small>{{ row.symbol }}</small></a>
          </template>
        </el-table-column>
        <el-table-column label="信号价" width="90">
          <template #default="{ row }">{{ price(row.signal_price) }}</template>
        </el-table-column>
        <el-table-column label="强度" width="76" prop="score" />
        <el-table-column label="触发依据" min-width="320">
          <template #default="{ row }">{{ row.item?.reasons?.join('；') || '状态变化' }}</template>
        </el-table-column>
      </el-table>
      <el-empty
        v-else
        description="当天没有盘中实时留痕；上方股票来自收盘快照复盘，不会伪造盘中触发时间"
        :image-size="72"
      />
    </section>

    <div class="calibration-note">
      {{ data?.probability_note || '信号用于研究和复盘，不构成收益保证。' }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElNotification } from 'element-plus'
import { useRouter } from 'vue-router'
import {
  fetchIntradaySignals,
  type IntradaySignalEvent,
  type IntradaySignalPayload,
  type IntradaySignalStatus,
} from '@/api/intradaySignals'

defineOptions({ name: 'IntradaySignalsPage' })

const router = useRouter()
const data = ref<IntradaySignalPayload | null>(null)
const loading = ref(false)
const keyword = ref('')
const filter = ref<'all' | IntradaySignalStatus>('all')
let timer: ReturnType<typeof setInterval> | null = null

const filteredItems = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  return (data.value?.items || []).filter((item) => {
    const statusMatch = filter.value === 'all' || item.status === filter.value
    const textMatch = !text
      || item.name.toLowerCase().includes(text)
      || item.symbol.includes(text)
      || item.industry.toLowerCase().includes(text)
    return statusMatch && textMatch
  })
})

const historyRows = computed(() => (data.value?.recent_events || []).slice(0, 100))
const isClosed = computed(() => data.value?.status === 'closed')
const isCloseReview = computed(() => data.value?.review_mode === 'close_review')
const signalSectionTitle = computed(() => {
  if (isCloseReview.value) return '今日收盘复盘候选'
  if (isClosed.value) return '今日盘中触发记录'
  return '当前信号'
})
const entryStatLabel = computed(() =>
  isCloseReview.value ? '收盘复盘候选' : isClosed.value ? '今日曾触发' : '入场触发')
const entryStatHint = computed(() =>
  isClosed.value ? '仅供复盘，等待次日确认' : '仍在有效价格区间')
const scanNote = computed(() =>
  isClosed.value
    ? '盘中实时扫描已结束 · 当前保留今日记录并展示收盘复盘结果'
    : `后台每 ${data.value?.scan_interval_sec || 15} 秒快扫 · 页面每 5 秒读取热结果`)
const emptyText = computed(() => {
  if (data.value?.status === 'closed') return '今天没有满足条件的盘中记录或收盘复盘候选'
  if (filter.value !== 'all') return '当前没有这个状态的信号'
  return '当前没有满足量价、结构和可成交条件的机会'
})
const toneClass = computed(() =>
  data.value?.market?.tone === '偏暖' ? 'warm' : data.value?.market?.tone === '偏冷' ? 'cold' : 'neutral')

const signed = (value?: number | null) => {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}
const percent = (value?: number | null) =>
  value === null || value === undefined ? '—' : `${(value * 100).toFixed(0)}%`
const price = (value?: number | null) =>
  value === null || value === undefined || !Number.isFinite(value) ? '—' : value.toFixed(value < 10 ? 3 : 2)
const timeOnly = (value?: string | null) => {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime())
    ? value.slice(11, 19)
    : parsed.toLocaleTimeString('zh-CN', { hour12: false })
}
const signalTimeLabel = (item: IntradaySignalPayload['items'][number]) => {
  if (item.signal_mode === 'close_review') return `收盘复盘 · ${timeOnly(item.reviewed_at || data.value?.as_of)} 生成`
  if (item.signal_mode === 'intraday_archive') return `${item.phase_label} · ${timeOnly(item.triggered_at)} 曾触发`
  return `${item.phase_label} · ${timeOnly(item.triggered_at)} 触发`
}
const statusType = (status: IntradaySignalStatus): 'success' | 'warning' | 'info' | 'danger' =>
  status === 'entry' ? 'success' : status === 'watch' ? 'warning' : status === 'unbuyable' ? 'info' : 'danger'
const eventLabel = (event: IntradaySignalEvent) =>
  event.item?.status_label || ({ entry: '入场触发', watch: '提前预警', unbuyable: '不可追入', invalid: '信号失效' }[event.status])
const openStock = (symbol: string) => router.push({ name: 'stock-analysis', query: { symbol } })
const toggleFilter = (next: IntradaySignalStatus) => {
  filter.value = filter.value === next ? 'all' : next
}

const load = async (refresh = false) => {
  if (refresh || !data.value) loading.value = true
  try {
    const payload = await fetchIntradaySignals(refresh)
    data.value = payload
  } catch (error: any) {
    if (refresh) {
      ElNotification({
        title: '盘中雷达暂时不可用',
        message: error?.message || '后台正在自动重试',
        type: 'warning',
      })
    }
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await load(false)
  timer = setInterval(() => {
    if (!document.hidden) void load(false)
  }, 5000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped lang="scss">
.radar-page { display: flex; flex-direction: column; gap: 16px; }
.page-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; flex-wrap: wrap;
  h1 { margin: 0; font-size: 24px; }
  p { margin: 5px 0 0; color: var(--el-text-color-secondary); font-size: 13px; }
}
.title-line { display: flex; align-items: center; gap: 8px; }
.live-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--el-text-color-placeholder);
  &.active { background: #16a34a; box-shadow: 0 0 0 5px rgb(22 163 74 / 12%); animation: pulse 1.5s infinite; }
}
.phase { color: var(--el-text-color-secondary); font-size: 13px; }
.head-actions { display: flex; align-items: center; gap: 10px; }
.updated { color: var(--el-text-color-placeholder); font-size: 12px; }
.market-strip { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap;
  padding: 11px 14px; border: 1px solid var(--el-border-color-light); border-radius: 10px; background: var(--el-bg-color); }
.market-main { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; font-size: 13px; }
.market-tone { padding: 3px 9px; border-radius: 999px; font-weight: 700;
  &.warm { color: #b91c1c; background: #fff1f0; }
  &.cold { color: #047857; background: #ecfdf5; }
  &.neutral { color: #92400e; background: #fffbeb; }
}
.scan-note { color: var(--el-text-color-placeholder); font-size: 12px; }
.stat-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.stat-card { appearance: none; display: flex; flex-direction: column; align-items: flex-start; gap: 3px; padding: 13px 15px;
  border: 1px solid var(--el-border-color-light); border-radius: 11px; background: var(--el-bg-color); cursor: pointer;
  transition: border-color .16s, transform .16s;
  &:hover, &.selected { transform: translateY(-1px); border-color: var(--el-color-primary); }
  b { font-size: 25px; }
  span { font-size: 14px; font-weight: 700; }
  small { color: var(--el-text-color-placeholder); }
  &.entry b { color: #dc2626; }
  &.watch b { color: #d97706; }
  &.blocked b { color: #64748b; }
}
.signal-section, .history-section { padding: 16px 18px; border: 1px solid var(--el-border-color-light);
  border-radius: 12px; background: var(--el-bg-color); }
.section-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 13px;
  h2 { margin: 0; font-size: 18px; }
  p { margin: 4px 0 0; color: var(--el-text-color-secondary); font-size: 12px; }
}
.search { width: 220px; }
.signal-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 13px; }
.signal-card { display: flex; flex-direction: column; gap: 12px; padding: 14px; border: 1px solid var(--el-border-color-light);
  border-top: 4px solid var(--el-border-color); border-radius: 10px; background: var(--el-fill-color-extra-light);
  &.entry { border-top-color: #dc2626; }
  &.watch { border-top-color: #d97706; }
  &.unbuyable { border-top-color: #64748b; opacity: .88; }
}
.card-top, .price-row, .card-foot { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; }
.stock-name { display: flex; align-items: baseline; gap: 7px;
  a { color: var(--el-text-color-primary); font-size: 17px; font-weight: 800; cursor: pointer; }
  small { color: var(--el-text-color-placeholder); }
}
.industry { color: var(--el-text-color-secondary); font-size: 12px; }
.price-row { align-items: center;
  > div:first-child { display: flex; align-items: baseline; gap: 8px; }
  b { font-size: 24px; }
}
.score { text-align: right;
  small { display: block; color: var(--el-text-color-placeholder); }
  strong { font-size: 20px; color: var(--el-text-color-primary); }
}
.trade-box { display: grid; grid-template-columns: repeat(3, 1fr); gap: 7px; padding: 10px; border-radius: 8px;
  background: #fff7ed;
  div { display: flex; flex-direction: column; gap: 3px; }
  span { color: #9a3412; font-size: 11px; }
  b { font-size: 13px; }
}
.watch-box, .blocked-box { padding: 9px 10px; border-radius: 7px; font-size: 12px; line-height: 1.6; }
.watch-box { color: #92400e; background: #fffbeb; }
.blocked-box { color: #475569; background: #f1f5f9; }
.review-box, .archive-box { padding: 10px 11px; border-radius: 8px; font-size: 12px; line-height: 1.65; }
.review-box { color: #92400e; background: #fffbeb; border: 1px solid #fde68a; }
.archive-box { color: #475569; background: #f8fafc; border: 1px solid #e2e8f0; }
.metric-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px;
  span { padding: 7px 8px; border: 1px solid var(--el-border-color-lighter); border-radius: 7px; background: var(--el-bg-color); }
  small { display: block; color: var(--el-text-color-placeholder); font-size: 10px; }
  b { font-size: 12px; }
}
.reasons { display: flex; flex-direction: column; gap: 5px; margin: 0; padding: 0; list-style: none;
  li { color: var(--el-text-color-regular); font-size: 12px; line-height: 1.45;
    &::before { content: '·'; margin-right: 6px; color: var(--el-color-primary); font-weight: 900; }
  }
}
.card-foot { align-items: center; padding-top: 9px; border-top: 1px solid var(--el-border-color-lighter);
  span { color: var(--el-text-color-placeholder); font-size: 11px; }
  button { padding: 0; border: 0; color: var(--el-color-primary); background: transparent; cursor: pointer; font-size: 12px; }
}
.stock-link { color: var(--el-color-primary); cursor: pointer; font-weight: 600;
  small { color: var(--el-text-color-placeholder); font-weight: 400; }
}
.calibration-note { padding: 10px 13px; border-radius: 8px; color: var(--el-text-color-secondary);
  background: var(--el-fill-color); font-size: 12px; text-align: center; }
.up { color: #dc2626; }
.down { color: #059669; }
.warn { color: #d97706; }
@keyframes pulse { 50% { opacity: .45; } }

@media (max-width: 900px) {
  .stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 560px) {
  .stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
  .stat-card { padding: 10px 11px; }
  .signal-section, .history-section { padding: 13px; }
  .signal-grid { grid-template-columns: 1fr; }
  .trade-box { grid-template-columns: 1fr; }
  .search { width: 100%; }
}

/* —— 紧凑版面 ——
   页头 + 行情条 + 四张统计卡 + 小节标题占掉了首屏一半，卡片本身也偏大，
   1080p 下只能看到 4 张信号卡。只压留白与字号，不删任何信息。 */
.page-head h1 { font-size: 19px; }
.page-head p { margin: 2px 0 0; font-size: 12px; }
.market-strip { padding: 6px 12px; }
.stat-grid { gap: 8px; }
.stat-card { padding: 7px 12px; gap: 1px;
  b { font-size: 19px; }
  span { font-size: 12px; }
  small { font-size: 11px; }
}
.section-head { margin-bottom: 8px;
  h2 { font-size: 15px; }
  p { margin: 2px 0 0; font-size: 11px; }
}

/* 卡片：数字块和留白是主因，一屏多放两三张 */
.signal-grid { gap: 9px; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); }
.signal-card { gap: 7px; padding: 10px; border-top-width: 3px; }
.price-row b { font-size: 19px; }
.score strong { font-size: 16px; }
.review-box, .archive-box { padding: 6px 9px; line-height: 1.5; }
.metric-row span { padding: 4px 7px; }
.reasons { gap: 3px;
  li { line-height: 1.35; }
}
</style>
