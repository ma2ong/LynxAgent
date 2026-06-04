<template>
  <div class="limitup-page">
    <!-- 头部 -->
    <div class="page-head">
      <div>
        <h1>涨停热点</h1>
        <p>连板梯队 × 概念板块分布，基于本地日线，仅供研究参考</p>
      </div>
      <div class="head-ctrl">
        <el-date-picker v-model="date" type="date" value-format="YYYY-MM-DD"
          placeholder="选择日期" size="default" style="width:160px" />
        <el-button type="primary" :loading="loading" @click="load">查询</el-button>
      </div>
    </div>

    <el-alert v-if="data && data.message && !data.stocks?.length"
      :title="data.message" type="warning" :closable="false" show-icon style="margin-bottom:12px" />

    <template v-if="data && data.stocks?.length">
      <!-- KPI 栏 -->
      <section class="kpi-band">
        <div class="kpi-card accent-red">
          <span class="kpi-lbl">今日涨停</span>
          <strong class="kpi-num">{{ data.total_limit_up }}</strong>
          <small>只</small>
        </div>
        <div class="kpi-card accent-green">
          <span class="kpi-lbl">今日跌停</span>
          <strong class="kpi-num">{{ data.total_limit_down }}</strong>
          <small>只</small>
        </div>
        <div class="kpi-card accent-orange">
          <span class="kpi-lbl">最高连板</span>
          <strong class="kpi-num">{{ data.max_boards }}</strong>
          <small>板</small>
        </div>
        <div class="kpi-card accent-blue">
          <span class="kpi-lbl">2板以上</span>
          <strong class="kpi-num">{{ data.boards_2plus }}</strong>
          <small>只</small>
        </div>
        <div class="kpi-card">
          <span class="kpi-lbl">一字板</span>
          <strong class="kpi-num">{{ data.one_price_count }}</strong>
          <small>只</small>
        </div>
      </section>

      <!-- 连板梯队 × 板块矩阵 -->
      <section class="matrix-card">
        <div class="matrix-title">
          {{ fmtDate(data.date) }}　涨停热点分布
          <span class="matrix-note">按明确概念归类，无法确认的只放「其他」</span>
        </div>
        <div class="theme-strip">
          <button v-for="cause in data.causes" :key="cause" class="theme-pill" :class="`theme-${themeClass(cause)}`">
            <span>{{ cause }}</span>
            <b>{{ data.cause_total[cause] || 0 }}</b>
          </button>
        </div>
        <div class="matrix-wrap">
          <table class="matrix-table">
            <thead>
              <tr>
                <th class="col-level">连板</th>
                <th v-for="cause in data.causes" :key="cause" class="col-cause">{{ cause }}</th>
                <th class="col-total">合计</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="level in data.level_order" :key="level"
                  :class="`row-${levelClass(level)}`">
                <td class="cell-level">
                  <div class="level-badge" :class="`badge-${levelClass(level)}`">
                    {{ level }}
                    <small v-if="data.level_counts[level]">{{ data.level_counts[level] }}</small>
                  </div>
                </td>
                <td v-for="cause in data.causes" :key="cause" class="cell-stocks">
                  <div v-for="stock in (data.matrix[level]?.[cause] || [])" :key="stock.symbol"
                    class="stock-chip"
                    :class="{
                      'chip-one-price': stock.is_one_price,
                      'chip-big': stock.is_big,
                      'chip-20pct': stock.is_20pct,
                    }"
                    @click="goAnalysis(stock.symbol)"
                    :title="`${stock.symbol}｜${stock.reason || ''}`"
                  >
                    {{ stock.name }}<sup v-if="stock.is_20pct">★</sup>
                  </div>
                </td>
                <td class="cell-row-total">{{ data.level_counts[level] }}</td>
              </tr>
            </tbody>
            <tfoot>
              <tr class="row-total">
                <td class="cell-level"><b>涨停数</b></td>
                <td v-for="cause in data.causes" :key="cause" class="cell-total-num">
                  <b>{{ data.cause_total[cause] || 0 }}</b>
                </td>
                <td class="cell-row-total"><b>{{ data.total_limit_up }}</b></td>
              </tr>
            </tfoot>
          </table>
        </div>
        <div class="legend">
          <span class="leg-item"><b>粗体</b> 一字板（开盘即封，未被打开）</span>
          <span class="leg-item leg-big">橙色</span> 成交额 &gt; 5亿
          <span class="leg-item leg-20">★</span> 20% 涨幅（创业板/科创板）
        </div>
      </section>

      <!-- 涨停明细表 -->
      <section class="detail-card">
        <div class="detail-head">
          <span class="detail-title">涨停明细（{{ data.total_limit_up }} 只）</span>
          <el-input v-model="search" placeholder="搜索股票名称/代码" style="width:200px" clearable size="small" />
        </div>
        <el-table :data="filteredStocks" size="small" stripe max-height="480"
          :default-sort="{ prop: 'boards', order: 'descending' }">
          <el-table-column prop="symbol" label="代码" width="88" fixed />
          <el-table-column prop="name" label="名称" width="110" fixed />
          <el-table-column prop="boards" label="连板" width="72" sortable>
            <template #default="{ row }">
              <span class="boards-tag" :class="`boards-${Math.min(row.boards, 5)}`">
                {{ row.boards }}板
              </span>
            </template>
          </el-table-column>
          <el-table-column prop="cause" label="概念" width="110" />
          <el-table-column prop="segment" label="市场" width="80" />
          <el-table-column label="特征" width="130">
            <template #default="{ row }">
              <el-tag v-if="row.is_one_price" size="small" type="danger" effect="dark" style="margin-right:4px">一字</el-tag>
              <el-tag v-if="row.is_20pct" size="small" type="warning" effect="plain">20%</el-tag>
              <el-tag v-if="row.is_big" size="small" type="warning" effect="dark">大单</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="close" label="收盘价" width="90" sortable />
          <el-table-column prop="pct_chg" label="涨幅%" width="88" sortable>
            <template #default="{ row }"><span class="up">+{{ row.pct_chg }}%</span></template>
          </el-table-column>
          <el-table-column prop="amount_yi" label="成交额(亿)" width="100" sortable />
          <el-table-column prop="reason" label="涨停动因" min-width="360" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="reason-text">{{ row.reason }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="80" fixed="right">
            <template #default="{ row }">
              <el-button link size="small" type="primary" @click="goAnalysis(row.symbol)">深研</el-button>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </template>

    <el-empty v-else-if="!loading && !data" description="选择日期后点击查询" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ApiClient } from '@/api/request'

const router = useRouter()
const loading = ref(false)
const date = ref(new Date().toISOString().slice(0, 10))
const data = ref<any>(null)
const search = ref('')

const filteredStocks = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return data.value?.stocks || []
  return (data.value?.stocks || []).filter(
    (s: any) => s.symbol.includes(q) || (s.name || '').toLowerCase().includes(q)
  )
})

const load = async () => {
  loading.value = true
  try {
    const res: any = await ApiClient.get('/api/lite/limit-up', { date: date.value }, { timeout: 30000 })
    const d = res?.data || res
    if (!d?.success && !d?.stocks) {
      data.value = d?.data || d
    } else {
      data.value = d?.data || d
    }
    if (data.value?.total_limit_up === 0) {
      ElMessage.warning('该日无涨停数据（非交易日或尚未同步）')
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '加载失败')
  } finally {
    loading.value = false
  }
}

const fmtDate = (d: string) => {
  if (!d) return ''
  const dt = new Date(d)
  const days = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
  return `${d}　${days[dt.getDay()]}`
}

const levelClass = (level: string) => {
  if (level === '5板+') return '5p'
  if (level === '4连板') return '4'
  if (level === '3连板') return '3'
  if (level === '2连板') return '2'
  return '1'
}

const themeClass = (cause: string) => {
  if (cause.includes('光通信')) return 'optical'
  if (cause.includes('AI')) return 'ai'
  if (cause.includes('机器人')) return 'robot'
  if (cause.includes('芯片')) return 'chip'
  if (cause.includes('煤炭')) return 'coal'
  if (cause.includes('电力')) return 'power'
  if (cause.includes('消费')) return 'consumer'
  if (cause.includes('军工')) return 'aero'
  if (cause.includes('公告')) return 'notice'
  return 'other'
}

const goAnalysis = (symbol: string) => {
  router.push({ name: 'single-analysis', query: { symbol } })
}

onMounted(load)
</script>

<style scoped lang="scss">
.limitup-page { display: flex; flex-direction: column; gap: 14px; }
.page-head { display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 8px;
  h1 { margin: 0 0 4px; font-size: 22px; }
  p { margin: 0; font-size: 13px; color: var(--el-text-color-secondary); } }
.head-ctrl { display: flex; gap: 8px; align-items: center; }

/* KPI */
.kpi-band { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; }
.kpi-card { background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: 10px;
  padding: 12px 16px; display: flex; flex-direction: column; gap: 2px;
  border-top: 3px solid var(--el-border-color); }
.kpi-card.accent-red { border-top-color: #ef232a; }
.kpi-card.accent-green { border-top-color: #14b143; }
.kpi-card.accent-orange { border-top-color: #e6a23c; }
.kpi-card.accent-blue { border-top-color: #409eff; }
.kpi-lbl { font-size: 12px; color: var(--el-text-color-secondary); }
.kpi-num { font-size: 28px; font-weight: 700; line-height: 1.1; }
.kpi-card.accent-red .kpi-num { color: #ef232a; }
.kpi-card.accent-green .kpi-num { color: #14b143; }
.kpi-card.accent-orange .kpi-num { color: #e6a23c; }
.kpi-card.accent-blue .kpi-num { color: #409eff; }

/* Matrix */
.matrix-card { background: var(--el-bg-color); border: 1px solid var(--el-border-color-light);
  border-radius: 8px; padding: 14px; }
.matrix-title { font-size: 18px; font-weight: 800; margin-bottom: 8px; }
.matrix-note { font-size: 12px; font-weight: 400; color: var(--el-text-color-secondary); margin-left: 8px; }
.theme-strip { display: flex; gap: 8px; flex-wrap: wrap; margin: 0 0 12px; }
.theme-pill { border: 1px solid var(--el-border-color-light); background: var(--el-fill-color-extra-light);
  border-radius: 6px; padding: 6px 10px; display: inline-flex; gap: 8px; align-items: center;
  color: var(--el-text-color-primary); font-size: 13px; }
.theme-pill b { font-size: 16px; line-height: 1; }
.theme-optical { border-color: #409eff; background: #ecf5ff; }
.theme-ai { border-color: #8e44ad; background: #f5edff; }
.theme-robot { border-color: #00a870; background: #ecfff7; }
.theme-chip { border-color: #d35400; background: #fff4e6; }
.theme-coal { border-color: #7f8c8d; background: #f4f6f7; }
.theme-power { border-color: #f1c40f; background: #fffbe6; }
.theme-consumer { border-color: #e84393; background: #fff0f6; }
.theme-aero { border-color: #34495e; background: #eef2f7; }
.theme-notice { border-color: #16a085; background: #edfdf9; }
.matrix-wrap { overflow-x: auto; }

.matrix-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.matrix-table th, .matrix-table td {
  border: 1px solid var(--el-border-color-lighter); padding: 6px 8px; vertical-align: top; }
.matrix-table thead th { background: #eaf4ff; color: #12314f; font-weight: 800;
  text-align: center; white-space: nowrap; }
.col-level { width: 76px; min-width: 76px; }
.col-cause { min-width: 100px; text-align: center; }
.col-total { width: 56px; text-align: center; font-weight: 700; }

/* Row color by level */
.row-5p { background: #fff0f0; }
.row-4  { background: #fff5ec; }
.row-3  { background: #fffbe6; }
.row-2  { background: #f0fff4; }
.row-1  { background: var(--el-bg-color); }
.row-total td { background: var(--el-fill-color-light); font-weight: 700; text-align: center; padding: 8px; }

.cell-level { text-align: center; vertical-align: middle; }
.level-badge { display: inline-flex; flex-direction: column; align-items: center; gap: 2px;
  font-weight: 700; font-size: 13px; }
.level-badge small { font-size: 11px; font-weight: 400; color: var(--el-text-color-secondary); }
.badge-5p { color: #c0392b; }
.badge-4  { color: #d35400; }
.badge-3  { color: #d4ac0d; }
.badge-2  { color: #27ae60; }
.badge-1  { color: var(--el-text-color-regular); }

.cell-stocks { min-width: 90px; }
.cell-row-total { text-align: center; font-weight: 700; vertical-align: middle; }
.cell-total-num { text-align: center; vertical-align: middle; }

/* Stock chips in matrix */
.stock-chip { display: inline-block; padding: 2px 5px; margin: 2px 2px; border-radius: 4px;
  font-size: 12px; cursor: pointer; white-space: nowrap;
  background: var(--el-fill-color-extra-light); border: 1px solid var(--el-border-color-lighter);
  transition: background 0.15s; }
.stock-chip:hover { background: var(--el-color-primary-light-9); border-color: var(--el-color-primary-light-5); }
.stock-chip.chip-one-price { font-weight: 700; }
.stock-chip.chip-big { border-color: #e6a23c; color: #9a5a00; }
.stock-chip.chip-20pct { color: #9254de; }
sup { font-size: 9px; color: #9254de; }

/* Legend */
.legend { margin-top: 8px; font-size: 12px; color: var(--el-text-color-secondary); display: flex; gap: 16px; flex-wrap: wrap; }
.leg-item { display: inline-flex; align-items: center; gap: 3px; }
.leg-big { color: #9a5a00; }
.leg-20 { color: #9254de; }

/* Detail */
.detail-card { background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: 8px; padding: 14px; }
.detail-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.detail-title { font-size: 15px; font-weight: 700; }
.up { color: #ef232a; }
.reason-text { color: var(--el-text-color-regular); }

.boards-tag { font-size: 12px; padding: 1px 6px; border-radius: 10px; font-weight: 600; }
.boards-1 { background: #f0f9eb; color: #5a9e47; }
.boards-2 { background: #fdf6ec; color: #d4700a; }
.boards-3 { background: #fef3e2; color: #c47a00; }
.boards-4 { background: #fff0f0; color: #c0392b; }
.boards-5 { background: #fde8e8; color: #8b0000; font-weight: 700; }

@media (max-width: 900px) {
  .kpi-band { grid-template-columns: repeat(3, 1fr); }
}
</style>
