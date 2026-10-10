<template>
  <div class="model-picks">
    <div class="page-head">
      <div>
        <h1>模型选股 <el-tag size="small" type="warning" effect="plain">试运行 · 仅管理员可见</el-tag></h1>
        <p>统计模型每周打分、平均分仓、持有一周；周五收盘后出新名单，下周一开盘调仓。</p>
      </div>
      <el-button :loading="loading" @click="load"><el-icon><Refresh /></el-icon>刷新</el-button>
    </div>

    <el-radio-group v-model="book" @change="switchBook">
      <el-radio-button value="small">小资金版 · 10 只</el-radio-button>
      <el-radio-button value="std">标准版 · 50 只</el-radio-button>
    </el-radio-group>

    <el-alert v-if="data?.signal_date" :title="statusLine" type="info" show-icon :closable="false" />
    <el-alert v-else-if="!loading && data" title="还没有名单：第一份名单会在周五收盘后自动生成" type="info" show-icon :closable="false" />

    <section v-if="data?.items.length" class="kpi-grid">
      <div class="kpi">
        <span>{{ data.buy_date ? '本周名单 · 自买入平均' : '本周名单 · 自信号日收盘平均' }}</span>
        <strong :class="tone(data.avg_ret)">{{ pct(data.avg_ret) }}</strong>
        <em>{{ data.items.length }} 只等权，含已持有的</em>
      </div>
      <div class="kpi">
        <span>当日平均涨跌（最近交易日）</span>
        <strong :class="tone(data.avg_today)">{{ pct(data.avg_today) }}</strong>
        <em>全市场平均 <b :class="tone(data.market_today)">{{ pct(data.market_today) }}</b></em>
      </div>
      <div class="kpi">
        <span>实盘记录</span>
        <strong>{{ record?.weeks ? `${record.weeks} 周` : '刚开始' }}</strong>
        <em v-if="record?.weeks">累计 <b :class="tone(record.cum_ret! * 100)">{{ pct(record.cum_ret! * 100) }}</b>
          · 全市场 <b :class="tone(record.cum_base! * 100)">{{ pct(record.cum_base! * 100) }}</b></em>
        <em v-else>每周结算一次，买入满一周后出第一条</em>
      </div>
      <div class="kpi">
        <span>本周调仓</span>
        <strong>新进 {{ newCount }} · 卖出 {{ data.sold.length }}</strong>
        <em>续持 {{ data.items.length - newCount }} 只</em>
      </div>
    </section>

    <section v-if="book === 'small' && data?.items.length" class="panel plan">
      <div class="panel-title">本周操作
        <span class="capital">我的资金
          <el-input-number v-model="capital" :min="10000" :step="10000" :controls="!isMobile" size="small" @change="saveCapital" /> 元
        </span>
      </div>
      <p class="plan-note">每只约投 {{ yuan(capital / 10) }}，按 100 股一手向下取整；价格按{{ data.buy_date ? '现价' : '最新收盘价' }}估算，周一开盘会略有出入。
        一手都买不起的跳过，由标准版里排在后面的股票顶上。</p>
      <div class="plan-row" v-if="data.sold.length"><b class="down">卖出 {{ data.sold.length }} 只</b>
        <span v-for="s in data.sold" :key="s.symbol">{{ s.name || s.symbol }}</span></div>
      <div class="plan-row"><b class="up">买入 {{ plan.buys.length }} 只</b>
        <span v-for="r in plan.buys" :key="r.symbol">{{ r.name }} <i>{{ r.shares }} 股 ≈ {{ yuan(r.cost) }}</i><em v-if="r.reserve">替补</em></span>
        <span v-if="!plan.buys.length">无</span></div>
      <div class="plan-row" v-if="plan.holds.length"><b>继续持有 {{ plan.holds.length }} 只</b>
        <span v-for="r in plan.holds" :key="r.symbol">{{ r.name }}</span></div>
      <div class="plan-row" v-if="plan.skipped.length"><b>资金不够一手、已跳过</b>
        <span v-for="r in plan.skipped" :key="r.symbol">{{ r.name }}（一手 {{ yuan((r.price || 0) * 100) }}）</span></div>
      <p class="plan-note">合计约 {{ yuan(plan.total) }}，剩余现金约 {{ yuan(capital - plan.total - plan.holdValue) }}<template v-if="plan.holds.length">（续持的按每只 {{ yuan(capital / 10) }} 估）</template>。</p>
    </section>

    <section v-if="data?.items.length" class="panel">
      <div class="panel-title">本周名单 <small>{{ data.signal_date }} 收盘后生成</small></div>
      <el-table :data="data.items" size="small" stripe>
        <el-table-column prop="rank" label="#" min-width="44" />
        <el-table-column label="股票" min-width="140">
          <template #default="{ row }">
            <span class="name">{{ row.name }}</span> <span class="code">{{ row.symbol }}</span>
          </template>
        </el-table-column>
        <el-table-column v-if="!isMobile" prop="industry" label="行业" min-width="100" />
        <el-table-column label="模型排名" min-width="90">
          <template #default="{ row }">前 {{ topPct(row.score) }}</template>
        </el-table-column>
        <el-table-column label="状态" min-width="70">
          <template #default="{ row }">
            <el-tag size="small" :type="row.kept ? 'info' : 'danger'" effect="plain">{{ row.kept ? '续持' : '新进' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column v-if="!isMobile" :label="data.buy_date ? '买入价' : '信号价'" min-width="80" align="right">
          <template #default="{ row }">{{ num(row.buy_open ?? row.signal_close) }}</template>
        </el-table-column>
        <el-table-column label="现价" min-width="72" align="right">
          <template #default="{ row }">{{ num(row.price) }}</template>
        </el-table-column>
        <el-table-column label="当日" min-width="76" align="right">
          <template #default="{ row }"><span :class="tone(row.pct_today)">{{ pct(row.pct_today) }}</span></template>
        </el-table-column>
        <el-table-column :label="data.buy_date ? '自买入' : '自信号日'" min-width="84" align="right">
          <template #default="{ row }"><span :class="tone(row.ret)">{{ pct(row.ret) }}</span></template>
        </el-table-column>
        <el-table-column label="" min-width="56" align="right">
          <template #default="{ row }"><el-button link type="primary" size="small" @click="openChart(row)">看图</el-button></template>
        </el-table-column>
      </el-table>
      <div v-if="data.sold.length" class="sold">
        <b>本周卖出（{{ data.prev_date }} 名单里有、这周没有）：</b>
        <span v-for="s in data.sold" :key="s.symbol">{{ s.name || s.symbol }}</span>
      </div>
    </section>

    <section class="panel rules">
      <div class="panel-title">选股规则</div>
      <ol>
        <li><b>看什么：</b>每只股票算 43 个价量指标——过去 1 天到 1 年的涨跌、离均线多远、波动大小、离近期高低点多远、
          成交额放大还是萎缩、近 20 天涨停次数、所在行业近期强弱等。不看新闻、不看财报。</li>
        <li><b>怎么打分：</b>统计模型（LightGBM，量化公司常用的一种）从 2021 年以来全部 A 股里学
          「哪种指标组合的股票，下一周比其他股票涨得多」。每周用最新数据重新学一次。只比相对强弱，不预测大盘涨跌。</li>
        <li><b>从哪里选：</b>沪深两市、上市满一年、近 20 天日均成交额 ≥3000 万、不含 ST。</li>
        <li><b>选哪些：</b>模型分最高的 50 只（小资金版取前 10 只），每只投同样多的钱。</li>
        <li><b>怎么换仓：</b>每周五收盘后出新名单，下周一开盘调仓。已持有的只要仍排在全市场前 20% 就继续拿，掉出才卖，
          所以每周只换约四分之一。没有止损、止盈，只按周调仓。</li>
      </ol>
      <div class="subtitle">小资金版为什么是 10 只、不是 1~3 只</div>
      <p>同一个模型，每周按排名 3 只一组切成 16 组回测，各组年化从 0% 到 +34% 都有，排第 1~3 名那组恰好是 0%——只买两三只，结果主要看运气。
        10 只回测：2022-07~2026-09 年化 +22.8%（全市场 +8.8%），2015~2019 年化 +40.7%（全市场 −7.2%），每年都跑赢，是小资金能执行的下限。小额交易记得开「免五」，否则每笔最低 5 元佣金会吃掉不少收益。</p>
      <div class="subtitle">要知道的特点</div>
      <ul>
        <li>偏爱走势平稳、没有短期暴涨、成交活跃的股票，常见大盘蓝筹。<b>不会选</b>刚连板、短线暴涨的票——和「一键智选」是两种相反的风格。</li>
        <li>赚的是<b>整篮子跑赢市场</b>：单只股票一周跑赢的概率只比一半略高。只挑其中两三只买，结果基本靠运气。</li>
      </ul>
      <div class="subtitle">回测成绩（扣手续费，对比全市场等权）</div>
      <ul>
        <li>2015 ~ 2019（模型从没见过的老数据，含期间退市股）：年化 <b class="up">+32.0%</b>（全市场 −8.9%），
          最大回撤 −36%（全市场 −60%），五年每年都跑赢。</li>
        <li>2022-07 ~ 2026-09：年化 <b class="up">+20.2%</b>（全市场 +8.8%），最大回撤 −25%（全市场 −31%）；2025 年略输 1.7 个点。</li>
        <li>两段合起来约 450 周，统计上可靠（不是运气）。但<b>近几年超额明显变小</b>（量化基金多了，同类信号被挤），
          以后每年多赚几个点是合理预期，别按 30% 指望。</li>
      </ul>
      <div class="subtitle">怎么判断它行不行</div>
      <p>看上面的「实盘记录」：每周按真实操作结算（周一开盘买、下周一开盘卖、扣手续费），和同期全市场比。
        短期几周的输赢说明不了什么，至少看半年以上。</p>
    </section>

    <el-drawer v-model="chartDrawer" :title="chartTitle" :size="isMobile ? '100%' : '62%'" direction="rtl">
      <div v-loading="chartLoading">
        <KLineProChart v-if="chartPayload" :payload="chartPayload" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { quantApi, type ModelPickItem, type ModelPicksResult } from '@/api/quant'

const KLineProChart = defineAsyncComponent(() => import('@/components/KLineProChart.vue'))

const BOOK_KEY = 'model-picks-book'
const CAPITAL_KEY = 'model-picks-capital'
const readStore = (k: string) => { try { return localStorage.getItem(k) } catch { return null } }
const writeStore = (k: string, v: string) => { try { localStorage.setItem(k, v) } catch { /* 隐私模式 */ } }
const book = ref<'small' | 'std'>(readStore(BOOK_KEY) === 'std' ? 'std' : 'small')
const capital = ref(Number(readStore(CAPITAL_KEY)) || 50000)
const saveCapital = () => writeStore(CAPITAL_KEY, String(capital.value || 50000))
const switchBook = () => { writeStore(BOOK_KEY, book.value); data.value = null; load() }

const data = ref<ModelPicksResult | null>(null)
const loading = ref(false)
const record = computed(() => data.value?.record || null)
const newCount = computed(() => data.value?.items.filter((i) => !i.kept).length || 0)

const mobileQuery = window.matchMedia('(max-width: 760px)')
const isMobile = ref(mobileQuery.matches)
const onMobile = (e: MediaQueryListEvent) => { isMobile.value = e.matches }

const statusLine = computed(() => {
  const d = data.value
  if (!d?.signal_date) return ''
  return d.buy_date
    ? `本周名单 ${d.signal_date} 收盘后生成，${d.buy_date} 开盘买入。下一份名单本周五收盘后出，下周一开盘调仓。`
    : `本周名单 ${d.signal_date} 收盘后生成，下一个交易日开盘买入（买入前「自信号日」按 ${d.signal_date} 收盘价算）。`
})

type PlanRow = ModelPickItem & { shares: number; cost: number; reserve?: boolean }
// 新进的按每份资金算手数；买不起一手的跳过，用标准版排在后面的顶上
const plan = computed(() => {
  const d = data.value
  const per = capital.value / 10
  const lot = (r: ModelPickItem) => (r.price || r.signal_close) * 100
  const sized = (r: ModelPickItem, reserve = false): PlanRow => {
    const shares = Math.floor(per / lot(r)) * 100
    return { ...r, shares, cost: shares * (r.price || r.signal_close), reserve }
  }
  const holds = d?.items.filter((r) => r.kept) || []
  const fresh = (d?.items.filter((r) => !r.kept) || []).map((r) => sized(r))
  const buys = fresh.filter((r) => r.shares > 0)
  const skipped = fresh.filter((r) => r.shares === 0)
  const spare = (d?.reserves || []).map((r) => sized(r, true)).filter((r) => r.shares > 0)
  buys.push(...spare.slice(0, skipped.length))
  const total = buys.reduce((a, r) => a + r.cost, 0)
  return { buys, holds, skipped, total, holdValue: holds.length * per }
})
const yuan = (v: number) => `${Math.round(v).toLocaleString()} 元`
const pct = (v: number | null | undefined) => (v == null ? '-' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`)
const num = (v: number | null | undefined) => (v == null ? '-' : v.toFixed(2))
const tone = (v: number | null | undefined) => (v == null || v === 0 ? '' : v > 0 ? 'up' : 'down')
const topPct = (score: number) => {
  const p = (1 - score) * 100
  return p < 1 ? `${Math.max(p, 0.1).toFixed(1)}%` : `${Math.round(p)}%`
}

async function load() {
  loading.value = true
  try {
    data.value = await quantApi.modelPicks(book.value)
  } catch (error: any) {
    ElMessage.error(error?.message || '模型名单读取失败，稍后点刷新重试')
  } finally {
    loading.value = false
  }
}

const chartDrawer = ref(false)
const chartPayload = ref<any>(null)
const chartLoading = ref(false)
const chartTitle = ref('')
const openChart = async (row: { symbol: string; name: string }) => {
  chartTitle.value = `${row.name} ${row.symbol}`
  chartDrawer.value = true
  chartLoading.value = true
  chartPayload.value = null
  try { chartPayload.value = await quantApi.klineDetail(row.symbol, row.name) }
  finally { chartLoading.value = false }
}

// 盘中现价每分钟刷一次；页面不在前台时不刷
let timer: number | undefined
onMounted(() => {
  load()
  mobileQuery.addEventListener('change', onMobile)
  timer = window.setInterval(() => { if (!document.hidden && !loading.value) load() }, 60000)
})
onUnmounted(() => {
  mobileQuery.removeEventListener('change', onMobile)
  if (timer) window.clearInterval(timer)
})
</script>

<style scoped lang="scss">
.model-picks { display: flex; flex-direction: column; gap: 12px; }
.page-head { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
.page-head h1 { margin: 0 0 4px; font-size: 22px; display: flex; align-items: center; gap: 8px; }
.page-head p { margin: 0; color: var(--el-text-color-secondary); }
.kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.kpi { background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: 8px; padding: 12px 14px; }
.kpi span { display: block; color: var(--el-text-color-secondary); font-size: 12px; margin-bottom: 6px; }
.kpi strong { display: block; font-size: 20px; }
.kpi em { display: block; font-style: normal; font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px; }
.panel { background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: 8px; padding: 14px; overflow-x: auto; }
.panel-title { font-size: 16px; font-weight: 700; margin-bottom: 10px; }
.panel-title small { font-size: 12px; font-weight: 400; color: var(--el-text-color-secondary); margin-left: 6px; }
.name { color: var(--el-text-color-primary); }
.code { color: var(--el-text-color-secondary); font-size: 12px; }
.up { color: #f56c6c; }
.down { color: #67c23a; }
.sold { margin-top: 10px; font-size: 13px; color: var(--el-text-color-regular); display: flex; flex-wrap: wrap; gap: 4px 10px; }
.plan .panel-title { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.capital { margin-left: auto; font-size: 13px; font-weight: 400; display: flex; align-items: center; gap: 6px; }
.plan-note { margin: 0 0 8px; font-size: 13px; color: var(--el-text-color-secondary); line-height: 1.7; }
.plan-row { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 14px; padding: 8px 0; border-top: 1px solid var(--el-border-color-lighter); }
.plan-row b { min-width: 92px; }
.plan-row i { font-style: normal; color: var(--el-text-color-secondary); font-size: 12px; }
.plan-row em { font-style: normal; font-size: 12px; color: var(--el-color-warning); margin-left: 4px; }
.rules { font-size: 14px; line-height: 1.75; color: var(--el-text-color-regular); }
.rules ol, .rules ul { margin: 0; padding-left: 20px; }
.rules p { margin: 0; }
.rules .subtitle { font-weight: 700; color: var(--el-text-color-primary); margin: 12px 0 4px; }
@media (max-width: 1000px) {
  .kpi-grid { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 760px) {
  .page-head { align-items: flex-start; }
  .page-head h1 { flex-wrap: wrap; font-size: 20px; }
  .kpi strong { font-size: 17px; }
}
</style>
