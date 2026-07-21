<template>
  <div class="auction-page">
    <div class="page-head">
      <div>
        <h1>集合竞价</h1>
        <p>从今开/昨收的竞价高开推导当日情绪、热门板块与买入候选，仅供研究参考</p>
      </div>
      <div class="head-ctrl">
        <span v-if="updatedAt" class="updated">更新于 {{ updatedAt }}</span>
        <el-button type="primary" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <div v-if="loading && !data" class="loading-hint">正在读取全市场竞价快照…</div>

    <el-alert
      v-if="data && data.available === false"
      :title="data.note || '暂无竞价数据'" type="info" :closable="false" show-icon style="margin-bottom:12px" />

    <template v-if="data && data.available">
      <!-- 竞价情绪概览 -->
      <section class="mood-band">
        <div class="mood-card" :class="moodClass(data.overview.mood)">
          <span class="mood-lbl">竞价情绪</span>
          <strong class="mood-val">{{ data.overview.mood }}</strong>
          <small>{{ data.overview.sentiment_score }} 分</small>
        </div>
        <div class="kpi-card">
          <span class="kpi-lbl">高开 / 低开</span>
          <strong class="kpi-num"><i class="up">{{ data.overview.high_open }}</i> / <i class="down">{{ data.overview.low_open }}</i></strong>
          <small>高开比 {{ data.overview.high_ratio }}%</small>
        </div>
        <div class="kpi-card">
          <span class="kpi-lbl">平均开盘</span>
          <strong class="kpi-num" :class="data.overview.avg_open_pct >= 0 ? 'up' : 'down'">
            {{ data.overview.avg_open_pct >= 0 ? '+' : '' }}{{ data.overview.avg_open_pct }}%
          </strong>
          <small>大幅高开 {{ data.overview.big_high_open }} / 低开 {{ data.overview.big_low_open }}</small>
        </div>
        <div class="kpi-card">
          <span class="kpi-lbl">竞价涨停 / 跌停</span>
          <strong class="kpi-num"><i class="up">{{ data.overview.limit_up_open }}</i> / <i class="down">{{ data.overview.limit_down_open }}</i></strong>
          <small>只（开盘价触板）</small>
        </div>
      </section>

      <div class="verdict">{{ data.overview.verdict }}</div>

      <!-- 竞价四形态（实时·不可回测） -->
      <section class="tape" v-if="data.auction_tape && data.auction_tape.available">
        <div class="tape-head">
          竞价盘口四形态
          <span class="tape-meta">跟踪 {{ data.auction_tape.tracked }} 只 · {{ data.auction_tape.sample_points }} 个采样点</span>
          <span class="tape-live">实时 · 不可回测</span>
        </div>
        <div class="tape-counts">
          <span class="tc t-acc">主力抢筹 <b>{{ data.auction_tape.pattern_counts.accumulation }}</b></span>
          <span class="tc t-dist">诱多出货 <b>{{ data.auction_tape.pattern_counts.distribution }}</b></span>
          <span class="tc t-shake">洗盘低吸 <b>{{ data.auction_tape.pattern_counts.shakeout }}</b></span>
          <span class="tc t-div">多空分歧 <b>{{ data.auction_tape.pattern_counts.divergence }}</b></span>
        </div>
        <p class="tape-note">{{ data.auction_tape.note }}</p>
      </section>
      <el-alert v-else-if="data.auction_tape && !data.auction_tape.available"
        type="info" :closable="false" show-icon style="margin:2px 0"
        title="竞价四形态需在盘前 09:15-09:25 期间采样才有数据（实时可用、无法回测）" />

      <!-- 高开幅度分布（精确口径） -->
      <section class="dist" v-if="data.overview.distribution?.length">
        <div class="dist-head">
          高开幅度分布
          <span class="caliber" :class="{ live: data.overview.is_auction_window }">
            成交额{{ data.overview.caliber }} · 总额 {{ fmtAmt(data.overview.total_amount_yi) }}
          </span>
        </div>
        <div class="dist-bar">
          <span v-for="b in data.overview.distribution" :key="b.label" v-show="b.count"
            class="dist-seg" :class="distClass(b.label)" :style="{ flexGrow: b.count }" :title="`${b.label} ${b.count}`" />
        </div>
        <div class="dist-legend">
          <span v-for="b in data.overview.distribution" :key="b.label" class="leg">
            <i :class="distClass(b.label)" />{{ b.label }} <b>{{ b.count }}</b>
          </span>
        </div>
      </section>

      <div class="cols">
        <!-- 竞价热门板块 -->
        <section class="panel">
          <div class="panel-title">热门板块 <em>近段趋势排名 · 今日竞价强度</em></div>
          <div v-if="!data.hot_sectors.length" class="empty">暂无板块高开数据</div>
          <div v-for="(s, i) in data.hot_sectors" :key="s.key" class="sector-row">
            <span class="rank">{{ i + 1 }}</span>
            <span class="s-name">{{ s.name }}</span>
            <span v-if="s.trend_pct != null" class="s-trend">近段 +{{ s.trend_pct }}%</span>
            <span class="s-pct" :class="s.avg_open_pct >= 0 ? 'up' : 'down'">
              今开{{ s.avg_open_pct >= 0 ? '+' : '' }}{{ s.avg_open_pct }}%
            </span>
            <span class="s-meta">高开 {{ s.high_count }}/{{ s.member_count }}</span>
            <span class="s-leader" @click="openStock(s.leader.code)">
              领涨 {{ s.leader.name }}
              <i :class="s.leader.open_pct >= 0 ? 'up' : 'down'">{{ s.leader.open_pct >= 0 ? '+' : '' }}{{ s.leader.open_pct }}%</i>
            </span>
          </div>
        </section>

        <!-- 竞价买入推荐 -->
        <section class="panel">
          <div class="panel-title">竞价买入候选 <em>近段强势板块 · 由强到弱排序，点击进深研</em></div>
          <div v-if="!data.buy_candidates.length" class="empty">当日近段强势板块无符合条件的高开候选（弱势竞价）</div>
          <button
            v-for="c in data.buy_candidates" :key="c.code"
            type="button" class="cand-card" :class="{ top: c.rank === 1 }" @click="openStock(c.code)"
          >
            <span class="cc-rank" :class="rankClass(c.rank)">{{ c.rank }}</span>
            <div class="cc-l">
              <div class="cc-name">{{ c.name }}<span class="cc-code">{{ c.code }}</span>
                <span v-if="c.theme" class="theme-tag">{{ c.theme }}</span>
                <span v-if="c.auction_pattern" class="pat-tag" :class="patClass(c.auction_pattern.pattern)"
                  :title="c.auction_pattern.note">{{ c.auction_pattern.label }}</span>
                <span v-if="c.tier" class="tier" :class="tierClass(c.tier)">{{ c.tier }}</span>
              </div>
              <div class="cc-reasons">{{ c.reasons.join(' · ') }}</div>
              <span v-if="c.strength != null" class="cc-bar" :title="`综合强度 ${c.strength}`">
                <i :class="tierClass(c.tier)" :style="{ width: c.strength + '%' }" />
              </span>
            </div>
            <div class="cc-r">
              <div class="cc-price">{{ c.price?.toFixed(2) }}</div>
              <div class="cc-pct up">+{{ c.open_pct }}%</div>
            </div>
          </button>
        </section>
      </div>

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

const moodClass = (mood: string) => {
  if (mood === '强' || mood === '偏强') return 'mood-strong'
  if (mood === '弱' || mood === '偏弱') return 'mood-weak'
  return 'mood-neutral'
}

const DIST_CLASS: Record<string, string> = {
  竞价涨停: 'd-limit', 大幅高开: 'd-hi2', 高开: 'd-hi1', 微高开: 'd-hi0',
  平开: 'd-flat', 低开: 'd-lo1', 大幅低开: 'd-lo2',
}
const distClass = (label: string) => DIST_CLASS[label] || 'd-flat'
const rankClass = (r: number) => (r === 1 ? 'r1' : r <= 3 ? 'r23' : 'rn')
const PAT_CLASS: Record<string, string> = {
  accumulation: 'p-acc', distribution: 'p-dist', shakeout: 'p-shake', divergence: 'p-div', neutral: 'p-neu',
}
const patClass = (p: string) => PAT_CLASS[p] || 'p-neu'
const tierClass = (t: string) => (t === '最强推荐' ? 't-top' : t === '强推荐' ? 't-strong' : 't-mid')
const fmtAmt = (yi: number) => (yi >= 10000 ? `${(yi / 10000).toFixed(2)} 万亿` : `${yi} 亿`)

const openStock = (code: string) => {
  router.push({ name: 'stock-analysis', query: { symbol: code } })
}

const load = async () => {
  loading.value = true
  try {
    const res: any = await ApiClient.get('/api/lite/call-auction', { _ts: Date.now() }, { timeout: 30000 })
    data.value = res?.data || null
    updatedAt.value = res?.data?.updated_at || ''
  } catch (e: any) {
    ElMessage.error(e?.message || '加载集合竞价数据失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped lang="scss">
.auction-page { display: flex; flex-direction: column; gap: 14px; }
.page-head { display: flex; align-items: flex-end; justify-content: space-between; flex-wrap: wrap; gap: 8px;
  h1 { margin: 0 0 4px; font-size: 24px; }
  p { margin: 0; color: var(--el-text-color-secondary); font-size: 13px; }
}
.head-ctrl { display: flex; align-items: center; gap: 10px; }
.updated { font-size: 12px; color: var(--el-text-color-secondary); }
.loading-hint { color: var(--el-text-color-secondary); font-size: 13px; padding: 20px 0; }

.up { color: #ef232a; }
.down { color: #14b143; }

.mood-band { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
.mood-card, .kpi-card {
  background: var(--el-fill-color-lighter); border-radius: 10px; padding: 14px 16px;
  display: flex; flex-direction: column; gap: 4px; border: 1px solid var(--el-border-color-lighter);
}
.mood-lbl, .kpi-lbl { font-size: 12px; color: var(--el-text-color-secondary); }
.mood-val { font-size: 26px; font-weight: 800; }
.kpi-num { font-size: 22px; font-weight: 700; i { font-style: normal; } }
.mood-card small, .kpi-card small { font-size: 12px; color: var(--el-text-color-placeholder); }
.mood-strong { border-color: #ef232a55; .mood-val { color: #ef232a; } }
.mood-weak { border-color: #14b14355; .mood-val { color: #14b143; } }
.mood-neutral { .mood-val { color: var(--el-color-warning); } }

.verdict {
  background: var(--el-color-primary-light-9); border-left: 3px solid var(--el-color-primary);
  padding: 10px 14px; border-radius: 6px; font-size: 13px; color: var(--el-text-color-primary);
}

/* 高开分布 */
.dist { background: var(--el-fill-color-blank); border: 1px solid var(--el-border-color-lighter); border-radius: 10px; padding: 14px 16px; }
.dist-head { font-size: 14px; font-weight: 700; margin-bottom: 10px; display: flex; align-items: center; gap: 10px; }
.caliber { font-size: 11px; font-weight: 400; color: var(--el-text-color-secondary); padding: 2px 8px; border-radius: 4px; background: var(--el-fill-color); }
.caliber.live { color: #fff; background: var(--el-color-success); }
.dist-bar { display: flex; height: 14px; border-radius: 7px; overflow: hidden; gap: 1px; }
.dist-seg { min-width: 2px; }
.dist-legend { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 10px; font-size: 12px; color: var(--el-text-color-secondary);
  .leg { display: inline-flex; align-items: center; gap: 4px; } i { width: 9px; height: 9px; border-radius: 2px; } b { color: var(--el-text-color-primary); } }
.d-limit { background: #b71c1c; }
.d-hi2 { background: #ef232a; }
.d-hi1 { background: #f56c6c; }
.d-hi0 { background: #fab6b6; }
.d-flat { background: #c8c9cc; }
.d-lo1 { background: #7ed0a0; }
.d-lo2 { background: #14b143; }

.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media (max-width: 900px) { .cols { grid-template-columns: 1fr; } }
.panel { background: var(--el-fill-color-blank); border: 1px solid var(--el-border-color-lighter); border-radius: 10px; padding: 14px 16px; }
.panel-title { font-size: 15px; font-weight: 700; margin-bottom: 10px; em { font-style: normal; font-size: 12px; font-weight: 400; color: var(--el-text-color-secondary); margin-left: 6px; } }
.empty { color: var(--el-text-color-secondary); font-size: 13px; padding: 16px 0; }

.sector-row { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--el-border-color-lighter); font-size: 13px; }
.sector-row:last-child { border-bottom: none; }
.rank { width: 20px; height: 20px; border-radius: 5px; background: var(--el-fill-color); text-align: center; line-height: 20px; font-size: 12px; font-weight: 700; }
.s-name { font-weight: 600; min-width: 90px; }
.s-trend { font-size: 11px; font-weight: 700; color: var(--el-color-danger); background: var(--el-color-danger-light-9); padding: 1px 6px; border-radius: 3px; font-variant-numeric: tabular-nums; }
.s-pct { font-weight: 700; font-variant-numeric: tabular-nums; min-width: 64px; color: var(--el-text-color-secondary); }
.s-meta { color: var(--el-text-color-secondary); font-size: 12px; }
.s-leader { margin-left: auto; cursor: pointer; color: var(--el-text-color-secondary); font-size: 12px; i { font-style: normal; margin-left: 4px; } }
.s-leader:hover { color: var(--el-color-primary); }

.cand-card {
  width: 100%; text-align: left; cursor: pointer; display: flex; align-items: center;
  gap: 10px; padding: 10px 12px; border-radius: 8px; border: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-lighter); margin-bottom: 8px; transition: all .15s ease;
}
.cand-card:hover { border-color: var(--el-color-primary); transform: translateX(2px); }
.cand-card.top { border-color: #b71c1c66; background: linear-gradient(90deg, rgba(183,28,28,.06), transparent 55%); }
.cc-rank {
  flex: none; width: 24px; height: 24px; border-radius: 6px; display: grid; place-items: center;
  font-size: 13px; font-weight: 800; font-variant-numeric: tabular-nums;
  background: var(--el-fill-color); color: var(--el-text-color-secondary);
}
.cc-rank.r1 { background: #b71c1c; color: #fff; }
.cc-rank.r23 { background: rgba(239,35,42,.14); color: #ef232a; }
.cc-l { flex: 1; min-width: 0; }
.cc-name { font-size: 14px; font-weight: 600; }
.cc-code { font-size: 11px; color: var(--el-text-color-secondary); margin-left: 6px; font-variant-numeric: tabular-nums; }
.theme-tag { font-size: 10px; font-weight: 600; padding: 1px 6px; border-radius: 3px; margin-left: 6px; color: var(--el-color-primary); background: var(--el-color-primary-light-9); border: 1px solid var(--el-color-primary-light-7); }
.tier { font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 3px; margin-left: 6px; color: #fff; }
.t-top { background: #b71c1c; }
.t-strong { background: #ef232a; }
.t-mid { background: #f0a020; }
.cc-reasons { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 3px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cc-bar { display: block; height: 3px; border-radius: 2px; background: var(--el-fill-color); margin-top: 6px; overflow: hidden;
  i { display: block; height: 100%; border-radius: 2px; }
}
.cc-r { flex: none; text-align: right; }
.cc-price { font-size: 15px; font-weight: 700; font-variant-numeric: tabular-nums; }
.cc-pct { font-size: 13px; font-weight: 600; }

.disclaimer { font-size: 11px; color: var(--el-text-color-placeholder); margin: 4px 0 0; }

/* 竞价四形态 */
.tape { background: var(--el-fill-color-blank); border: 1px solid var(--el-border-color-lighter); border-radius: 10px; padding: 12px 16px; }
.tape-head { font-size: 14px; font-weight: 700; display: flex; align-items: center; gap: 10px; }
.tape-meta { font-size: 12px; font-weight: 400; color: var(--el-text-color-secondary); }
.tape-live { margin-left: auto; font-size: 11px; font-weight: 600; color: #fff; background: var(--el-color-success); padding: 2px 8px; border-radius: 4px; }
.tape-counts { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; }
.tc { font-size: 12px; padding: 4px 10px; border-radius: 6px; b { font-size: 14px; margin-left: 4px; } }
.t-acc { background: rgba(183,28,28,.1); color: #b71c1c; }
.t-dist { background: rgba(240,160,32,.14); color: #b7791f; }
.t-shake { background: rgba(64,158,255,.12); color: #2f74c0; }
.t-div { background: var(--el-fill-color); color: var(--el-text-color-secondary); }
.tape-note { font-size: 11px; color: var(--el-text-color-placeholder); margin: 8px 0 0; }
.pat-tag { font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 3px; margin-left: 6px; color: #fff; }
.p-acc { background: #b71c1c; }
.p-dist { background: #f0a020; }
.p-shake { background: #409eff; }
.p-div { background: #909399; }
.p-neu { background: #c0c4cc; }
</style>
