<template>
  <div class="capital-panel">
    <el-tabs v-model="tab">
      <!-- 资金流 -->
      <el-tab-pane label="资金流" name="flow">
        <div v-loading="flowLoading">
          <el-radio-group v-model="flowKind" @change="loadFlow" style="margin-bottom:12px">
            <el-radio-button label="industry">行业</el-radio-button>
            <el-radio-button label="concept">概念</el-radio-button>
            <el-radio-button label="individual">个股</el-radio-button>
          </el-radio-group>
          <el-empty v-if="flow.empty" :description="flow.message || '暂无数据'" />
          <el-table v-else :data="flow.rows || []" height="540" size="small" stripe>
            <el-table-column type="index" width="50" />
            <template v-if="flowKind === 'individual'">
              <el-table-column prop="symbol" label="代码" width="90" />
              <el-table-column prop="name" label="名称" width="120" />
              <el-table-column prop="price" label="最新价" width="90" />
              <el-table-column prop="pct" label="涨跌幅%" width="100" :formatter="pctFmt" />
              <el-table-column prop="net_yi" label="主力净流入(亿)" sortable />
            </template>
            <template v-else>
              <el-table-column prop="name" label="板块" width="160" />
              <el-table-column prop="pct" label="涨跌幅%" width="100" :formatter="pctFmt" />
              <el-table-column prop="net_yi" label="主力净流入(亿)" width="150" sortable />
              <el-table-column prop="companies" label="家数" width="80" />
              <el-table-column prop="leader" label="领涨股" width="120" />
              <el-table-column prop="leader_pct" label="领涨幅%" :formatter="pctFmt" />
            </template>
          </el-table>
        </div>
      </el-tab-pane>

      <!-- 龙虎榜 -->
      <el-tab-pane label="龙虎榜" name="lhb">
        <div v-loading="lhbLoading">
          <el-date-picker v-model="lhbDate" type="date" value-format="YYYY-MM-DD"
            placeholder="选择日期（默认最近交易日）" @change="loadLhb" style="margin-bottom:12px" />
          <el-empty v-if="lhb.empty" :description="lhb.message || '暂无数据'" />
          <el-table v-else :data="lhb.rows || []" height="520" size="small" stripe
            highlight-current-row @row-click="openSeats">
            <el-table-column prop="symbol" label="代码" width="90" />
            <el-table-column prop="name" label="名称" width="120" />
            <el-table-column prop="pct" label="涨跌幅%" width="100" :formatter="pctFmt" />
            <el-table-column prop="net_buy_yi" label="净买额(亿)" width="120" sortable />
            <el-table-column prop="reason" label="上榜原因" show-overflow-tooltip />
          </el-table>
          <div class="hint">点击某行查看席位明细</div>

          <el-drawer v-model="seatsOpen" :title="`${seats.symbol || ''} 龙虎榜席位`" size="42%">
            <div v-loading="seatsLoading">
              <h4>买入前列</h4>
              <el-table :data="seats.buy || []" size="small">
                <el-table-column type="index" width="44" />
                <el-table-column prop="name" label="营业部/席位" show-overflow-tooltip />
                <el-table-column prop="buy_yi" label="买入(亿)" width="100" />
              </el-table>
              <h4>卖出前列</h4>
              <el-table :data="seats.sell || []" size="small">
                <el-table-column type="index" width="44" />
                <el-table-column prop="name" label="营业部/席位" show-overflow-tooltip />
                <el-table-column prop="sell_yi" label="卖出(亿)" width="100" />
              </el-table>
            </div>
          </el-drawer>
        </div>
      </el-tab-pane>

      <!-- 财经日历 -->
      <el-tab-pane label="财经日历" name="calendar">
        <div v-loading="calLoading">
          <el-checkbox-group v-model="calTypes" @change="loadCalendar" style="margin-bottom:12px">
            <el-checkbox label="earnings">财报披露</el-checkbox>
            <el-checkbox label="unlock">限售解禁</el-checkbox>
            <el-checkbox label="ipo">新股申购</el-checkbox>
          </el-checkbox-group>
          <el-empty v-if="cal.empty" :description="cal.message || '窗口内暂无事件'" />
          <el-timeline v-else>
            <el-timeline-item v-for="(e, i) in cal.events" :key="i" :timestamp="e.date" placement="top"
              :type="dotType(e.type)">
              <el-tag size="small" :type="dotType(e.type)">{{ typeLabel(e.type) }}</el-tag>
              <span style="margin-left:8px">{{ e.title }}</span>
            </el-timeline-item>
          </el-timeline>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { quantApi } from '@/api/quant'

const tab = ref('flow')

// 资金流
const flowLoading = ref(false)
const flowKind = ref<'industry' | 'concept' | 'individual'>('industry')
const flow = ref<any>({ empty: true })
async function loadFlow() {
  flowLoading.value = true
  try {
    if (flowKind.value === 'industry') flow.value = await quantApi.capitalIndustryFlow()
    else if (flowKind.value === 'concept') flow.value = await quantApi.capitalConceptFlow()
    else flow.value = await quantApi.capitalStockFlow(50)
  } catch (e: any) {
    flow.value = { empty: true, message: '拉取失败，请重试' }
  } finally { flowLoading.value = false }
}

// 龙虎榜
const lhbLoading = ref(false)
const lhbDate = ref('')
const lhb = ref<any>({ empty: true })
const seatsOpen = ref(false)
const seatsLoading = ref(false)
const seats = ref<any>({})
async function loadLhb() {
  lhbLoading.value = true
  try { lhb.value = await quantApi.dragonTiger(lhbDate.value) }
  catch (e: any) { lhb.value = { empty: true, message: '拉取失败，请重试' } }
  finally { lhbLoading.value = false }
}
async function openSeats(row: any) {
  seatsOpen.value = true
  seatsLoading.value = true
  seats.value = { symbol: row.symbol }
  try { seats.value = await quantApi.dragonTigerSeats(row.symbol, lhb.value.date || '') }
  finally { seatsLoading.value = false }
}

// 财经日历
const calLoading = ref(false)
const calTypes = ref(['earnings', 'unlock', 'ipo'])
const cal = ref<any>({ empty: true })
async function loadCalendar() {
  calLoading.value = true
  try { cal.value = await quantApi.capitalCalendar(calTypes.value.join(','), 14) }
  catch (e: any) { cal.value = { empty: true, message: '拉取失败，请重试' } }
  finally { calLoading.value = false }
}

const pctFmt = (_r: any, _c: any, v: number) =>
  v == null ? '-' : (v > 0 ? `+${v}` : `${v}`)
const typeLabel = (t: string) =>
  (({ earnings: '财报', unlock: '解禁', ipo: '新股' }) as any)[t] || t
const dotType = (t: string) =>
  (({ earnings: 'primary', unlock: 'warning', ipo: 'success' }) as any)[t] || 'info'

onMounted(() => { loadFlow(); loadLhb(); loadCalendar() })
</script>

<style scoped>
.capital-panel { padding: 16px; }
.hint { color: var(--el-text-color-secondary); font-size: 12px; margin-top: 6px; }
h4 { margin: 14px 0 8px; }
</style>
