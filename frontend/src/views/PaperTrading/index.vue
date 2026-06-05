<template>
  <div class="paper-page">
    <div class="page-head">
      <div>
        <h1>模拟交易</h1>
        <p>用虚拟资金按实时行情下单，验证选股与深研结论，不触达实盘。</p>
      </div>
      <el-button :loading="loading" @click="loadAll">
        <el-icon><Refresh /></el-icon>刷新
      </el-button>
    </div>

    <section class="summary-band">
      <div><span>总资产</span><strong>{{ fmt(account.equity?.CNY) }}</strong></div>
      <div><span>可用资金</span><strong>{{ fmt(account.cash?.CNY) }}</strong></div>
      <div><span>持仓市值</span><strong>{{ fmt(account.positions_value?.CNY) }}</strong></div>
      <div>
        <span>已实现盈亏</span>
        <strong :class="pnlClass(account.realized_pnl?.CNY)">{{ fmt(account.realized_pnl?.CNY) }}</strong>
      </div>
    </section>

    <section class="risk-panel" v-if="account.risk">
      <div class="risk-item">
        <span>总仓位</span>
        <el-progress :percentage="ratioPct(account.risk.exposure_ratio)" :status="account.risk.exposure_ratio >= 0.85 ? 'exception' : undefined" />
      </div>
      <div class="risk-item">
        <span>现金缓冲</span>
        <el-progress :percentage="ratioPct(account.risk.cash_ratio)" :status="account.risk.cash_ratio < 0.05 ? 'exception' : 'success'" />
      </div>
      <el-alert
        v-if="account.risk.flags?.length"
        :title="account.risk.flags.join('；')"
        type="warning"
        show-icon
        :closable="false"
      />
      <div v-else class="risk-ok">Paper 风控正常：单票上限 25%，总仓位上限 85%，现金缓冲下限 5%。</div>
    </section>

    <div class="grid">
      <section class="panel order-panel">
        <div class="panel-title">下单</div>
        <el-form label-position="top">
          <el-form-item label="股票代码（6 位）">
            <el-input v-model="form.code" placeholder="如 000001" maxlength="6" />
          </el-form-item>
          <el-form-item label="方向">
            <el-radio-group v-model="form.side">
              <el-radio-button value="buy">买入</el-radio-button>
              <el-radio-button value="sell">卖出</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="数量（股）">
            <el-input-number v-model="form.quantity" :min="100" :step="100" style="width: 100%" />
          </el-form-item>
          <el-button type="primary" :loading="submitting" style="width: 100%" @click="submit">
            按实时价{{ form.side === 'buy' ? '买入' : '卖出' }}
          </el-button>
          <el-button text type="danger" size="small" class="reset-btn" @click="onReset">重置模拟账户</el-button>
        </el-form>
      </section>

      <section class="panel">
        <div class="panel-title">当前持仓</div>
        <el-table :data="positions" empty-text="暂无持仓" size="small">
          <el-table-column prop="code" label="代码" width="90" />
          <el-table-column prop="quantity" label="持仓" width="80" />
          <el-table-column prop="avg_cost" label="成本" width="90">
            <template #default="{ row }">{{ row.avg_cost?.toFixed(2) }}</template>
          </el-table-column>
          <el-table-column prop="last_price" label="现价" width="90">
            <template #default="{ row }">{{ row.last_price != null ? row.last_price.toFixed(2) : '-' }}</template>
          </el-table-column>
          <el-table-column prop="market_value" label="市值">
            <template #default="{ row }">{{ fmt(row.market_value) }}</template>
          </el-table-column>
          <el-table-column label="浮动盈亏">
            <template #default="{ row }">
              <span :class="pnlClass(floatPnl(row))">{{ row.last_price != null ? fmt(floatPnl(row)) : '-' }}</span>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </div>

    <section class="panel">
      <div class="panel-title">成交记录</div>
      <el-table :data="orders" empty-text="暂无成交" size="small">
        <el-table-column prop="created_at" label="时间" width="170">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="code" label="代码" width="90" />
        <el-table-column label="方向" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.side === 'buy' ? 'danger' : 'success'">
              {{ row.side === 'buy' ? '买入' : '卖出' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="quantity" label="数量" width="90" />
        <el-table-column prop="price" label="成交价" width="100">
          <template #default="{ row }">{{ row.price?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column prop="amount" label="金额">
          <template #default="{ row }">{{ fmt(row.amount) }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="90" />
      </el-table>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { paperApi, type PaperPosition } from '@/api/paper'

const loading = ref(false)
const submitting = ref(false)
const account = ref<Record<string, any>>({})
const positions = ref<PaperPosition[]>([])
const orders = ref<any[]>([])

const form = reactive<{ code: string; side: 'buy' | 'sell'; quantity: number }>({
  code: '',
  side: 'buy',
  quantity: 100,
})

const fmt = (v?: number | null) =>
  v == null ? '-' : `¥${Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

const fmtTime = (v: string) => (v ? new Date(v).toLocaleString('zh-CN') : '-')

const pnlClass = (v?: number | null) => (v == null ? '' : v >= 0 ? 'up' : 'down')

const ratioPct = (v?: number | null) => Math.round(Number(v || 0) * 100)

const floatPnl = (row: PaperPosition) =>
  row.last_price == null ? null : Number(((row.last_price - row.avg_cost) * row.quantity).toFixed(2))

const loadAll = async () => {
  loading.value = true
  try {
    const acc: any = await paperApi.account()
    account.value = acc?.data?.account || {}
    positions.value = acc?.data?.positions || []
    const ord: any = await paperApi.orders(50)
    orders.value = ord?.data?.items || []
  } catch (error: any) {
    ElMessage.error(error?.message || '加载模拟账户失败')
  } finally {
    loading.value = false
  }
}

const submit = async () => {
  if (!/^\d{6}$/.test(form.code.trim())) {
    ElMessage.warning('请输入 6 位 A 股代码')
    return
  }
  submitting.value = true
  try {
    const res: any = await paperApi.placeOrder({ code: form.code.trim(), side: form.side, quantity: form.quantity })
    if (res?.success === false) {
      ElMessage.error(res?.message || '下单失败')
    } else {
      ElMessage.success(res?.message || '模拟成交成功')
      await loadAll()
    }
  } catch (error: any) {
    ElMessage.error(error?.message || '下单失败')
  } finally {
    submitting.value = false
  }
}

const onReset = async () => {
  try {
    await ElMessageBox.confirm('确认重置模拟账户？持仓与成交记录将清空，资金恢复 100 万。', '重置确认', {
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await paperApi.reset()
    ElMessage.success('已重置')
    await loadAll()
  } catch (error: any) {
    ElMessage.error(error?.message || '重置失败')
  }
}

onMounted(loadAll)
</script>

<style scoped lang="scss">
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;

  h1 { margin: 0 0 4px; font-size: 22px; }
  p { margin: 0; color: var(--el-text-color-secondary); }
}

.summary-band {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;

  div {
    background: var(--el-bg-color);
    border: 1px solid var(--el-border-color-light);
    border-radius: 8px;
    padding: 12px 14px;
  }
  span { display: block; font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 6px; }
  strong { font-size: 20px; }
}

.grid {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  gap: 12px;
  margin-bottom: 12px;
}

.risk-panel {
  display: grid;
  grid-template-columns: 1fr 1fr minmax(320px, 1.4fr);
  gap: 10px;
  margin-bottom: 12px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 12px 14px;
}

.risk-item span {
  display: block;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}

.risk-ok {
  color: var(--el-color-success);
  font-size: 13px;
  align-self: center;
}

.panel {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 14px;
}

.panel-title { font-weight: 700; font-size: 16px; margin-bottom: 12px; }

.reset-btn { margin-top: 10px; width: 100%; }

.up { color: #ef4444; }
.down { color: #16a34a; }

@media (max-width: 900px) {
  .summary-band, .grid { grid-template-columns: 1fr; }
}
</style>
