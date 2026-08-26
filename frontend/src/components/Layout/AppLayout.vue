<template>
  <el-container class="app-layout">
    <div class="mobile-topbar">
      <el-button text class="mobile-menu-btn" @click="mobileMenuOpen = true">
        <el-icon><Menu /></el-icon>
      </el-button>
      <div class="mobile-brand">
        <BrandLogo :size="22" /><span class="wordmark"><i>A</i>StockPick</span>
      </div>
      <div v-if="billingInfo && !billingInfo.unlimited" class="mobile-quota">
        剩余 {{ billingInfo.remaining_today }}/{{ billingInfo.daily_limit }}
      </div>
    </div>
    <div v-if="mobileMenuOpen" class="mobile-mask" @click="mobileMenuOpen = false" />
    <el-aside width="216px" class="sidebar" :class="{ 'sidebar-open': mobileMenuOpen }">
      <div class="brand">
        <BrandLogo :size="26" /><span class="wordmark name"><i>A</i>StockPick</span>
      </div>
      <el-menu
        :default-active="route.path"
        router
        class="menu"
        @select="mobileMenuOpen = false"
      >
        <el-menu-item index="/dashboard"><el-icon><Odometer /></el-icon><span>盘面总览</span></el-menu-item>
        <el-menu-item index="/intraday-signals"><el-icon><Bell /></el-icon><span>盘中信号</span></el-menu-item>
        <el-menu-item index="/quant"><el-icon><MagicStick /></el-icon><span>智能选股</span></el-menu-item>
        <el-menu-item index="/call-auction"><el-icon><Sunrise /></el-icon><span>集合竞价</span></el-menu-item>
        <el-menu-item index="/heatmap"><el-icon><Histogram /></el-icon><span>行业热力</span></el-menu-item>
        <el-menu-item index="/limit-up"><el-icon><TrendCharts /></el-icon><span>涨停热点</span></el-menu-item>
        <el-menu-item index="/risk-alert"><el-icon><Warning /></el-icon><span>风险预警</span></el-menu-item>
        <el-menu-item index="/stock-analysis"><el-icon><DocumentChecked /></el-icon><span>个股深研</span></el-menu-item>
        <el-menu-item index="/review"><el-icon><DataAnalysis /></el-icon><span>选股复盘</span></el-menu-item>
        <el-menu-item index="/favorites"><el-icon><Star /></el-icon><span>我的自选股</span></el-menu-item>
        <el-menu-item index="/account/membership"><el-icon><Tools /></el-icon><span>设置</span></el-menu-item>
        <el-menu-item v-if="currentUser?.is_admin" index="/admin/users"><el-icon><Setting /></el-icon><span>用户管理</span></el-menu-item>
        <el-menu-item index="/data-center"><el-icon><DataLine /></el-icon><span>数据中心</span></el-menu-item>
      </el-menu>
      <div class="sidebar-foot">
        <!-- 不限档没有额度可报，不显示任何标识；只有限额档才提示今日剩余。 -->
        <div v-if="billingInfo && !billingInfo.unlimited" class="quota-chip" @click="$router.push('/account/membership')">
          {{ billingInfo.plan_label }} · 今日剩余 {{ billingInfo.remaining_today }}/{{ billingInfo.daily_limit }}
        </div>
        <el-button text size="small" @click="logout">
          <el-icon><SwitchButton /></el-icon>退出登录
        </el-button>
      </div>
    </el-aside>
    <el-main class="content">
      <MacroBar />
      <!-- 搜索类页面保活：点进个股详情再返回时，保留已搜出的结果不重跑 -->
      <router-view v-slot="{ Component }">
        <keep-alive :include="CACHED_VIEWS">
          <component :is="Component" />
        </keep-alive>
      </router-view>
    </el-main>
  </el-container>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  TrendCharts, Star, SwitchButton,
  DocumentChecked, Menu,
  MagicStick, Tools, Setting, DataLine, Sunrise, DataAnalysis, Histogram, Warning, Odometer, Bell,
} from '@element-plus/icons-vue'
import { currentUser, loadCurrentUser, clearCurrentUser } from '@/stores/user'
import { fetchBillingMe, type BillingMe } from '@/api/billing'
import BrandLogo from './BrandLogo.vue'
import MacroBar from '@/components/MacroBar.vue'

const route = useRoute()
const router = useRouter()

// 需要保活的搜索类页面（组件 name 必须与此一致）。个股深研/复盘不保活：
// 深研每次点开都要跟着新代码重新分析，不能复用上一只票的旧结果。
const CACHED_VIEWS = ['DashboardPage', 'QuantPage', 'CallAuctionPage', 'LimitUpPage', 'HeatmapPage', 'RiskAlertPage']

const billingInfo = ref<BillingMe | null>(null)
const mobileMenuOpen = ref(false)

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

.mobile-topbar,
.mobile-mask {
  display: none;
}

.sidebar {
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--el-border-color-light);
  background: var(--el-bg-color);
}

.brand svg, .mobile-brand svg { display: block; flex: none; }

.wordmark {
  font-weight: 800;
  letter-spacing: -0.025em;
  color: var(--el-text-color-primary);

  i {
    font-style: normal;
    color: #e11d2f;
  }
}

.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px 18px;
  font-size: 19px;
  font-weight: 700;

  svg { display: block; flex: none; }
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

@media (max-width: 760px) {
  .app-layout {
    min-height: 100vh;
    height: auto;
  }

  .mobile-topbar {
    position: fixed;
    inset: 0 0 auto 0;
    z-index: 30;
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 52px;
    padding: 0 12px;
    border-bottom: 1px solid var(--el-border-color-light);
    background: var(--el-bg-color);
  }

  .mobile-menu-btn {
    width: 36px;
    height: 36px;
  }

  .mobile-brand {
    display: flex;
    align-items: center;
    gap: 7px;
    font-weight: 700;

    svg { display: block; flex: none; }
  }

  .mobile-quota {
    min-width: 66px;
    padding: 3px 7px;
    border: 1px solid var(--el-border-color-light);
    border-radius: 999px;
    color: var(--el-text-color-secondary);
    font-size: 11px;
    text-align: center;
  }

  .mobile-mask {
    position: fixed;
    inset: 0;
    z-index: 35;
    display: block;
    background: rgb(0 0 0 / 26%);
  }

  .sidebar {
    position: fixed;
    inset: 0 auto 0 0;
    z-index: 40;
    width: 216px !important;
    max-width: 82vw;
    transform: translateX(-100%);
    transition: transform .18s ease;
  }

  .sidebar.sidebar-open {
    transform: translateX(0);
  }

  .content {
    width: 100%;
    min-height: 100vh;
    padding: 66px 12px 16px;
  }
}
</style>
