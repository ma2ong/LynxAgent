<template>
  <div class="smart-money-page">
    <div class="page-head">
      <h2>聪明钱</h2>
      <p class="sub">龙虎榜席位与基金持仓视角的资金追踪（东财数据，收盘后更新；北向持股已停止披露，不提供）</p>
    </div>
    <el-tabs v-model="tab">
      <el-tab-pane label="活跃席位（近30天）" name="seats">
        <el-table v-loading="loadingSeats" :data="seats" size="small" max-height="620">
          <el-table-column label="#" type="index" width="46" />
          <el-table-column prop="seat" label="营业部/席位" min-width="240" show-overflow-tooltip />
          <el-table-column prop="count" label="上榜次数" width="90" sortable />
          <el-table-column label="净买额" width="110" sortable :sort-by="'net_yi'">
            <template #default="{ row }">
              <span :class="row.net_yi > 0 ? 'up' : 'down'">{{ row.net_yi > 0 ? '+' : '' }}{{ row.net_yi }} 亿</span>
            </template>
          </el-table-column>
          <el-table-column label="买入/卖出" width="140">
            <template #default="{ row }">{{ row.buy_yi }} / {{ row.sell_yi }} 亿</template>
          </el-table-column>
          <el-table-column prop="last_date" label="最近上榜" width="110" />
          <el-table-column prop="stocks" label="买过的票" min-width="260" show-overflow-tooltip />
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="席位胜率（近一月）" name="winrate">
        <el-table v-loading="loadingWinrate" :data="winrate" size="small" max-height="620">
          <el-table-column label="#" type="index" width="46" />
          <el-table-column prop="seat" label="营业部/席位" min-width="240" show-overflow-tooltip />
          <el-table-column prop="trades_5d" label="样本数" width="90" sortable />
          <el-table-column label="5日胜率" width="100" sortable :sort-by="'win_rate_5d'">
            <template #default="{ row }"><b>{{ row.win_rate_5d }}%</b></template>
          </el-table-column>
          <el-table-column label="5日平均涨幅" width="120">
            <template #default="{ row }">
              <span :class="row.avg_chg_5d > 0 ? 'up' : 'down'">{{ row.avg_chg_5d > 0 ? '+' : '' }}{{ row.avg_chg_5d }}%</span>
            </template>
          </el-table-column>
          <el-table-column label="1日胜率/涨幅" width="140">
            <template #default="{ row }">{{ row.win_rate_1d }}% / {{ row.avg_chg_1d }}%</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
      <el-tab-pane :label="`基金重仓${fundQuarter ? '（' + fundQuarter + '）' : ''}`" name="fund">
        <el-table v-loading="loadingFund" :data="fund" size="small" max-height="620" @row-click="goStock">
          <el-table-column label="#" type="index" width="46" />
          <el-table-column prop="symbol" label="代码" width="90" />
          <el-table-column prop="name" label="名称" width="120" />
          <el-table-column prop="funds" label="持有基金数" width="110" sortable />
          <el-table-column label="持股市值" width="120" sortable :sort-by="'mv_yi'">
            <template #default="{ row }"><b>{{ row.mv_yi }} 亿</b></template>
          </el-table-column>
          <el-table-column label="较上期" width="140">
            <template #default="{ row }">
              <el-tag size="small" :type="row.change === '增仓' ? 'danger' : row.change === '减仓' ? 'success' : 'info'">
                {{ row.change || '-' }} {{ row.change_pct > 0 ? '+' : '' }}{{ row.change_pct }}%
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
        <p class="hint">点击行跳转个股深研；基金持仓为季报口径，滞后于当前。</p>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { smartMoneyApi, type SmartSeatRow, type SeatWinrateRow, type FundHoldRow } from '@/api/quant'

const router = useRouter()
const tab = ref('seats')
const seats = ref<SmartSeatRow[]>([])
const winrate = ref<SeatWinrateRow[]>([])
const fund = ref<FundHoldRow[]>([])
const fundQuarter = ref('')
const loadingSeats = ref(false)
const loadingWinrate = ref(false)
const loadingFund = ref(false)

const loadSeats = async () => {
  if (seats.value.length) return
  loadingSeats.value = true
  try { seats.value = (await smartMoneyApi.seats()).rows || [] } finally { loadingSeats.value = false }
}
const loadWinrate = async () => {
  if (winrate.value.length) return
  loadingWinrate.value = true
  try { winrate.value = (await smartMoneyApi.winrate()).rows || [] } finally { loadingWinrate.value = false }
}
const loadFund = async () => {
  if (fund.value.length) return
  loadingFund.value = true
  try {
    const res = await smartMoneyApi.fund()
    fund.value = res.rows || []
    fundQuarter.value = res.quarter || ''
  } finally { loadingFund.value = false }
}

const goStock = (row: FundHoldRow) => router.push({ path: '/stock-analysis', query: { symbol: row.symbol } })

watch(tab, (t) => { if (t === 'winrate') loadWinrate(); if (t === 'fund') loadFund() })
onMounted(loadSeats)
</script>

<style scoped lang="scss">
.page-head { margin-bottom: 6px;
  h2 { margin: 0; font-size: 20px; }
  .sub { margin: 4px 0 0; font-size: 12px; color: var(--el-text-color-secondary); }
}
.up { color: #e0402c; }
.down { color: #1e9e63; }
.hint { margin: 8px 0 0; font-size: 12px; color: var(--el-text-color-placeholder); }
</style>
