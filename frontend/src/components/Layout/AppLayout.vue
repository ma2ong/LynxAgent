<template>
  <el-container class="app-layout">
    <el-aside width="216px" class="sidebar">
      <div class="brand">
        <span class="logo">🐾</span>
        <span class="name">LynxAgent</span>
      </div>
      <el-menu :default-active="route.path" router class="menu">
        <el-menu-item index="/market/sentiment">
          <el-icon><Odometer /></el-icon><span>市场雷达</span>
        </el-menu-item>
        <el-menu-item index="/limit-up">
          <el-icon><Histogram /></el-icon><span>涨停热点</span>
        </el-menu-item>
        <el-menu-item index="/insights/hot-news">
          <el-icon><Histogram /></el-icon><span>A股热点</span>
        </el-menu-item>
        <el-menu-item index="/insights/catalyst">
          <el-icon><DataLine /></el-icon><span>利好监控</span>
        </el-menu-item>
        <el-menu-item index="/quant">
          <el-icon><TrendCharts /></el-icon><span>智能选股</span>
        </el-menu-item>
        <el-menu-item index="/portfolio-check">
          <el-icon><PieChart /></el-icon><span>组合体检</span>
        </el-menu-item>
        <el-menu-item index="/factor-lab">
          <el-icon><Operation /></el-icon><span>AI因子实验室</span>
        </el-menu-item>
        <el-menu-item index="/backtest-lab">
          <el-icon><DataAnalysis /></el-icon><span>策略回测实验台</span>
        </el-menu-item>
        <el-menu-item index="/stock-analysis">
          <el-icon><DocumentChecked /></el-icon><span>个股深研</span>
        </el-menu-item>
        <el-menu-item index="/deep-research">
          <el-icon><ChatLineRound /></el-icon><span>深研辩论</span>
        </el-menu-item>
        <el-menu-item index="/favorites">
          <el-icon><Star /></el-icon><span>我的自选股</span>
        </el-menu-item>
        <el-menu-item index="/data-center">
          <el-icon><Coin /></el-icon><span>数据中心</span>
        </el-menu-item>
        <el-menu-item index="/account/membership">
          <el-icon><Medal /></el-icon><span>会员与用量</span>
        </el-menu-item>
        <el-menu-item v-if="currentUser?.is_admin" index="/admin/users">
          <el-icon><Setting /></el-icon><span>用户管理</span>
        </el-menu-item>
      </el-menu>
      <div class="sidebar-foot">
        <div v-if="billingInfo" class="quota-chip" @click="$router.push('/account/membership')">
          {{ billingInfo.plan_label }} · 今日 AI {{ billingInfo.remaining_today }}/{{ billingInfo.daily_limit }}
        </div>
        <el-button text size="small" @click="logout">
          <el-icon><SwitchButton /></el-icon>退出登录
        </el-button>
      </div>
    </el-aside>
    <el-main class="content">
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Odometer, TrendCharts, Histogram, DataLine, Star, SwitchButton,
  DocumentChecked, Coin, PieChart, Operation, DataAnalysis, ChatLineRound,
  Medal, Setting,
} from '@element-plus/icons-vue'
import { currentUser, loadCurrentUser, clearCurrentUser } from '@/stores/user'
import { fetchBillingMe, type BillingMe } from '@/api/billing'

const route = useRoute()
const router = useRouter()

const billingInfo = ref<BillingMe | null>(null)

onMounted(async () => {
  await loadCurrentUser()
  try {
    const res = await fetchBillingMe()
    billingInfo.value = (res?.data as BillingMe) ?? null
  } catch { /* 配额信息拉不到不阻塞页面 */ }
})

const logout = () => {
  clearCurrentUser()
  localStorage.removeItem('auth-token')
  router.push('/login')
}
</script>

<style scoped lang="scss">
.app-layout {
  height: 100vh;
}

.sidebar {
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--el-border-color-light);
  background: var(--el-bg-color);
}

.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px 18px;
  font-size: 18px;
  font-weight: 700;

  .logo {
    font-size: 22px;
  }
}

.menu {
  flex: 1;
  border-right: none;
}

.sidebar-foot {
  padding: 12px 18px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.content {
  padding: 18px 20px;
  background: var(--el-fill-color-lighter);
  overflow-y: auto;
}

.quota-chip {
  font-size: 12px;
  color: #909399;
  padding: 4px 8px;
  border: 1px solid #e4e7ed;
  border-radius: 10px;
  cursor: pointer;
  margin-bottom: 6px;
  text-align: center;
  &:hover { color: #409eff; border-color: #409eff; }
}
</style>
