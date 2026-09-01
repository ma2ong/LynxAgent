<template>
  <div class="auction-page">
    <div class="page-head">
      <div class="head-copy">
        <h1>集合竞价</h1>
        <p>先筛强势板块与健康高开，再用 09:15-09:25 四形态做买入硬闸门，仅供研究参考</p>
      </div>
      <div class="head-ctrl">
        <span v-if="updatedAt" class="updated">更新于 {{ updatedAt }}</span>
        <el-button text size="small" @click="showAuctionDetails = !showAuctionDetails">
          {{ showAuctionDetails ? '收起明细' : '展开明细' }}
        </el-button>
        <el-button size="small" type="primary" :loading="loading" @click="load">刷新</el-button>
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

      <div class="auction-detail-grid">
      <!-- 竞价四形态（实时·不可回测） -->
      <section class="tape" v-if="data.auction_tape && data.auction_tape.available">
        <div class="tape-head">
          竞价盘口四形态
          <span class="tape-meta">初筛 {{ data.auction_tape.tracked }} 只 · 判出 {{ data.auction_tape.resolved }} 只</span>
          <span class="tape-live">09:15-09:25</span>
        </div>
        <div class="tape-counts">
          <span class="tc t-acc">主力抢筹 <b>{{ data.auction_tape.pattern_counts.accumulation }}</b></span>
          <span class="tc t-dist">诱多出货 <b>{{ data.auction_tape.pattern_counts.distribution }}</b></span>
          <span class="tc t-shake">洗盘低吸 <b>{{ data.auction_tape.pattern_counts.shakeout }}</b></span>
          <span class="tc t-div">多空分歧 <b>{{ data.auction_tape.pattern_counts.divergence }}</b></span>
        </div>
        <p v-show="showAuctionDetails" class="tape-note">{{ data.auction_tape.note }}</p>
      </section>
      <el-alert v-else-if="data.auction_tape && !data.auction_tape.available"
        type="info" :closable="false" show-icon
        title="当前买入候选的竞价有效报价不足，判不出盘口形态" />

      <!-- 高开幅度分布（精确口径） -->
      <section class="dist" v-if="data.overview.distribution?.length">
        <div class="dist-head">
          高开幅度分布
          <span class="caliber" :class="{ live: data.overview.is_auction_window }">
            成交额{{ data.overview.caliber }} · {{ fmtAmt(data.overview.total_amount_yi) }}
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
      </div>

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
          <!-- 上榜口径：先按档位挑出够「强推荐」的，再按名次取前 display_limit 只。
               竞价只有几分钟可操作，给十几只等于没给（2026-09-01 Allen 定上限 5）。
               被上限挡下的写进「另有 N 只」，别让用户以为今天就这点货。
               真实命中率照写——上榜不等于会涨停，最强档也只有六分之一。 -->
          <!-- 档位是相对当日最高分算的，弱势日照样会有「最强」。这条提示在整场都不强时
               出现，如实说明这是矮子里拔将军——名单照给，但不让标签替盘面吹牛。 -->
          <div v-if="data.tier_note" class="tier-note">{{ data.tier_note }}</div>
          <div v-if="data.hit_stats" class="hit-note">
            <b>达到「强推荐」档的按强弱最多取前 {{ data.display_limit || 5 }} 只（今日 {{ data.buy_candidates.length }} 只<span
              v-if="data.hidden_candidates">，另有 {{ data.hidden_candidates }} 只未展示</span>）。</b>
            {{ data.hit_stats.sessions }} 个交易日 / {{ data.hit_stats.samples }} 条留痕实测：
            前 3 名当日涨停率 <b class="hit-good">{{ data.hit_stats.top3_limit_up_rate }}%</b>，
            第 4 名以后 {{ data.hit_stats.rest_limit_up_rate }}%——名次越靠后越要减仓位。
            <span class="hit-warn">
              上榜 ≠ 会涨停：最强档也是约 6 次中 1 次，开盘买入到收盘的中位收益仅
              +{{ data.hit_stats.top3_intraday_median_pct }}%，收益全靠那 1/6。
            </span>
          </div>
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
              <!-- 竞价价是 09:25 冻结的提醒价；现价每次打开页面现取，两个一起才知道还追不追得上。 -->
              <div class="cc-price"><small>竞价</small>{{ c.price?.toFixed(2) }}</div>
              <div class="cc-pct up">+{{ c.open_pct }}%</div>
              <div v-if="c.live_price != null" class="cc-live">
                <span>现 <b>{{ c.live_price.toFixed(2) }}</b>
                  <em v-if="c.live_pct != null" :class="c.live_pct >= 0 ? 'up' : 'down'">{{ signedPct(c.live_pct) }}</em>
                </span>
                <span v-if="c.change_since_auction != null" class="cc-since">
                  较竞价 <b :class="c.change_since_auction >= 0 ? 'up' : 'down'">{{ signedPct(c.change_since_auction) }}</b>
                </span>
              </div>
            </div>
          </button>
          <p v-if="data.buy_candidates.length" class="cand-hint">
            标签为 09:15-09:25 竞价盘口形态：<span class="pat-tag p-dist">诱多出货</span
            ><span class="pat-tag p-div">多空分歧</span> 为提示而非排除项，仍按板块强度排序，自行结合开盘量价判断。
          </p>
        </section>
      </div>

      <p class="disclaimer">{{ data.note }}</p>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
defineOptions({ name: 'CallAuctionPage' })  // keep-alive 保活标识，勿改
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ApiClient } from '@/api/request'

const router = useRouter()
const loading = ref(false)
const data = ref<any>(null)
const updatedAt = ref('')
const showAuctionDetails = ref(false)

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
const signedPct = (value: number) => `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
const PAT_CLASS: Record<string, string> = {
  accumulation: 'p-acc', distribution: 'p-dist', shakeout: 'p-shake', divergence: 'p-div', neutral: 'p-neu',
}
const patClass = (p: string) => PAT_CLASS[p] || 'p-neu'
// 弱势日档位名会换成相对措辞（今日相对最强/较强），配色按同一梯度走，
// 否则整块掉进灰色，反而看不出当日的强弱次序。
const tierClass = (t: string) =>
  (t === '最强推荐' || t === '今日相对最强') ? 't-top'
    : (t === '强推荐' || t === '今日相对较强') ? 't-strong'
      : 't-mid' 
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
.auction-page { display: flex; flex-direction: column; gap: 8px; }
.page-head { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px;
  h1 { margin: 0; font-size: 21px; line-height: 1.2; }
  p { margin: 0; color: var(--el-text-color-secondary); font-size: 12px; }
}
.head-copy { display: flex; align-items: baseline; gap: 10px; min-width: 0; }
.head-ctrl { display: flex; align-items: center; gap: 6px; }
.updated { font-size: 12px; color: var(--el-text-color-secondary); }
.loading-hint { color: var(--el-text-color-secondary); font-size: 13px; padding: 20px 0; }

.up { color: #ef232a; }
.down { color: #14b143; }

.mood-band { display: grid; grid-template-columns: repeat(4, minmax(180px, 1fr)); gap: 8px; }
.mood-card, .kpi-card {
  min-width: 0; min-height: 44px;
  background: var(--el-fill-color-lighter); border-radius: 8px; padding: 7px 11px;
  display: flex; flex-direction: row; align-items: baseline; gap: 8px;
  border: 1px solid var(--el-border-color-lighter);
}
.mood-lbl, .kpi-lbl { font-size: 12px; color: var(--el-text-color-secondary); }
.mood-val { font-size: 22px; font-weight: 800; }
.kpi-num { font-size: 19px; font-weight: 700; white-space: nowrap; i { font-style: normal; } }
.mood-card small, .kpi-card small {
  margin-left: auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-size: 11px; color: var(--el-text-color-placeholder);
}
.mood-strong { border-color: #ef232a55; .mood-val { color: #ef232a; } }
.mood-weak { border-color: #14b14355; .mood-val { color: #14b143; } }
.mood-neutral { .mood-val { color: var(--el-color-warning); } }

.verdict {
  background: var(--el-color-primary-light-9); border-left: 3px solid var(--el-color-primary);
  padding: 6px 11px; border-radius: 6px; font-size: 12px; color: var(--el-text-color-primary);
}

/* 高开分布 */
.auction-detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; align-items: stretch; }
.dist { background: var(--el-fill-color-blank); border: 1px solid var(--el-border-color-lighter); border-radius: 8px; padding: 8px 11px; }
.dist-head { font-size: 13px; font-weight: 700; margin-bottom: 7px; display: flex; align-items: center; gap: 8px; }
.caliber { font-size: 11px; font-weight: 400; color: var(--el-text-color-secondary); padding: 2px 8px; border-radius: 4px; background: var(--el-fill-color); }
.caliber.live { color: #fff; background: var(--el-color-success); }
.dist-bar { display: flex; height: 10px; border-radius: 5px; overflow: hidden; gap: 1px; }
.dist-seg { min-width: 2px; }
.dist-legend { display: flex; flex-wrap: wrap; gap: 5px 9px; margin-top: 7px; font-size: 11px; color: var(--el-text-color-secondary);
  .leg { display: inline-flex; align-items: center; gap: 4px; } i { width: 9px; height: 9px; border-radius: 2px; } b { color: var(--el-text-color-primary); } }
.d-limit { background: #b71c1c; }
.d-hi2 { background: #ef232a; }
.d-hi1 { background: #f56c6c; }
.d-hi0 { background: #fab6b6; }
.d-flat { background: #c8c9cc; }
.d-lo1 { background: #7ed0a0; }
.d-lo2 { background: #14b143; }

.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.panel { background: var(--el-fill-color-blank); border: 1px solid var(--el-border-color-lighter); border-radius: 10px; padding: 14px 16px; }
.panel-title { font-size: 15px; font-weight: 700; margin-bottom: 10px; em { font-style: normal; font-size: 12px; font-weight: 400; color: var(--el-text-color-secondary); margin-left: 6px; } }
.empty { color: var(--el-text-color-secondary); font-size: 13px; padding: 16px 0; }
.hit-note {
  margin-bottom: 10px; padding: 7px 11px; border-radius: 8px; font-size: 12px; line-height: 1.65;
  background: var(--el-fill-color-light); border-left: 4px solid var(--el-color-warning);
  color: var(--el-text-color-regular);
  .hit-good { color: #ef232a; }
  .hit-warn { display: block; color: var(--el-text-color-secondary); }
}

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
.tier-note { background: #fff7e6; border: 1px solid #ffd591; color: #874d00; padding: 7px 10px; border-radius: 6px; font-size: 12px; line-height: 1.6; margin-bottom: 8px; }
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
.cc-price small { font-size: 11px; font-weight: 500; color: var(--el-text-color-placeholder); margin-right: 4px; }
.cc-pct { font-size: 13px; font-weight: 600; }
.cc-live {
  margin-top: 3px; font-size: 12px; color: var(--el-text-color-secondary);
  display: flex; flex-direction: column; gap: 1px; align-items: flex-end;
  font-variant-numeric: tabular-nums;
}
.cc-live b { font-weight: 700; }
.cc-live em { font-style: normal; margin-left: 4px; }
.cc-since { font-size: 11px; }

.cand-hint { margin: 10px 2px 0; font-size: 11px; line-height: 1.7; color: var(--el-text-color-secondary);
  .pat-tag { margin: 0 2px; vertical-align: middle; } }

.disclaimer { font-size: 11px; color: var(--el-text-color-placeholder); margin: 4px 0 0; }

/* 竞价四形态 */
.tape { background: var(--el-fill-color-blank); border: 1px solid var(--el-border-color-lighter); border-radius: 8px; padding: 8px 11px; }
.tape-head { font-size: 13px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
.tape-meta { font-size: 11px; font-weight: 400; color: var(--el-text-color-secondary); }
.tape-live { margin-left: auto; font-size: 11px; font-weight: 600; color: #fff; background: var(--el-color-success); padding: 2px 8px; border-radius: 4px; }
.tape-counts { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }
.tc { font-size: 11px; padding: 3px 8px; border-radius: 5px; b { font-size: 12px; margin-left: 3px; } }
.t-acc { background: rgba(183,28,28,.1); color: #b71c1c; }
.t-dist { background: rgba(240,160,32,.14); color: #b7791f; }
.t-shake { background: rgba(64,158,255,.12); color: #2f74c0; }
.t-div { background: var(--el-fill-color); color: var(--el-text-color-secondary); }
.tape-note { font-size: 11px; color: var(--el-text-color-placeholder); margin: 6px 0 0; }
.pat-tag { font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 3px; margin-left: 6px; color: #fff; }
.p-acc { background: #b71c1c; }
.p-dist { background: #f0a020; }
.p-shake { background: #409eff; }
.p-div { background: #909399; }
.p-neu { background: #c0c4cc; }

@media (max-width: 1100px) {
  .mood-band { grid-template-columns: repeat(2, minmax(180px, 1fr)); }
  .auction-detail-grid { grid-template-columns: 1fr; }
}

@media (max-width: 900px) {
  .cols { grid-template-columns: 1fr; }
  .head-copy { flex-wrap: wrap; }
}
</style>
