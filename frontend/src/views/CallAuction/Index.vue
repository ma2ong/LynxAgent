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

      <div class="cols">
        <!-- 竞价热门板块 -->
        <section class="panel">
          <div class="panel-title">竞价热门板块</div>
          <div v-if="!data.hot_sectors.length" class="empty">暂无板块高开数据</div>
          <div v-for="(s, i) in data.hot_sectors" :key="s.key" class="sector-row">
            <span class="rank">{{ i + 1 }}</span>
            <span class="s-name">{{ s.name }}</span>
            <span class="s-pct" :class="s.avg_open_pct >= 0 ? 'up' : 'down'">
              {{ s.avg_open_pct >= 0 ? '+' : '' }}{{ s.avg_open_pct }}%
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
          <div class="panel-title">竞价买入候选 <em>健康高开 1.5%–7%，点击进深研</em></div>
          <div v-if="!data.buy_candidates.length" class="empty">当日无符合条件的高开候选（弱势竞价）</div>
          <button
            v-for="c in data.buy_candidates" :key="c.code"
            type="button" class="cand-card" @click="openStock(c.code)"
          >
            <div class="cc-l">
              <div class="cc-name">{{ c.name }}<span class="cc-code">{{ c.code }}</span></div>
              <div class="cc-reasons">{{ c.reasons.join(' · ') }}</div>
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

.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media (max-width: 900px) { .cols { grid-template-columns: 1fr; } }
.panel { background: var(--el-fill-color-blank); border: 1px solid var(--el-border-color-lighter); border-radius: 10px; padding: 14px 16px; }
.panel-title { font-size: 15px; font-weight: 700; margin-bottom: 10px; em { font-style: normal; font-size: 12px; font-weight: 400; color: var(--el-text-color-secondary); margin-left: 6px; } }
.empty { color: var(--el-text-color-secondary); font-size: 13px; padding: 16px 0; }

.sector-row { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--el-border-color-lighter); font-size: 13px; }
.sector-row:last-child { border-bottom: none; }
.rank { width: 20px; height: 20px; border-radius: 5px; background: var(--el-fill-color); text-align: center; line-height: 20px; font-size: 12px; font-weight: 700; }
.s-name { font-weight: 600; min-width: 120px; }
.s-pct { font-weight: 700; font-variant-numeric: tabular-nums; min-width: 56px; }
.s-meta { color: var(--el-text-color-secondary); font-size: 12px; }
.s-leader { margin-left: auto; cursor: pointer; color: var(--el-text-color-secondary); font-size: 12px; i { font-style: normal; margin-left: 4px; } }
.s-leader:hover { color: var(--el-color-primary); }

.cand-card {
  width: 100%; text-align: left; cursor: pointer; display: flex; justify-content: space-between; align-items: center;
  gap: 10px; padding: 10px 12px; border-radius: 8px; border: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-lighter); margin-bottom: 8px; transition: all .15s ease;
}
.cand-card:hover { border-color: var(--el-color-primary); transform: translateX(2px); }
.cc-name { font-size: 14px; font-weight: 600; }
.cc-code { font-size: 11px; color: var(--el-text-color-secondary); margin-left: 6px; font-variant-numeric: tabular-nums; }
.cc-reasons { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 3px; }
.cc-r { text-align: right; }
.cc-price { font-size: 15px; font-weight: 700; font-variant-numeric: tabular-nums; }
.cc-pct { font-size: 13px; font-weight: 600; }

.disclaimer { font-size: 11px; color: var(--el-text-color-placeholder); margin: 4px 0 0; }
</style>
