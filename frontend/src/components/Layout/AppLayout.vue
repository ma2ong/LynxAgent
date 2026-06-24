<template>
  <el-container class="app-layout">
    <div class="mobile-topbar">
      <el-button text class="mobile-menu-btn" @click="mobileMenuOpen = true">
        <el-icon><Menu /></el-icon>
      </el-button>
      <div class="mobile-brand">
        <span class="logo">🐾</span>
        <span>LynxAgent</span>
      </div>
      <div v-if="billingInfo" class="mobile-quota">
        剩余 {{ billingInfo.remaining_today }}/{{ billingInfo.daily_limit }}
      </div>
    </div>
    <div v-if="mobileMenuOpen" class="mobile-mask" @click="mobileMenuOpen = false" />
    <el-aside width="216px" class="sidebar" :class="{ 'sidebar-open': mobileMenuOpen }">
      <div class="brand">
        <span class="logo">🐾</span>
        <span class="name">LynxAgent</span>
      </div>
      <el-menu
        :default-active="route.path"
        router
        class="menu"
        :default-openeds="['market', 'pick', 'stock', 'mine']"
        @select="mobileMenuOpen = false"
      >
        <el-menu-item index="/today">
          <el-icon><Calendar /></el-icon><span>今日</span>
        </el-menu-item>
        <el-sub-menu index="market">
          <template #title>
            <el-icon><Odometer /></el-icon><span>市场</span>
          </template>
        <el-menu-item index="/market/sentiment">
            <span>市场雷达</span>
        </el-menu-item>
        <el-menu-item index="/limit-up">
            <span>涨停热点</span>
        </el-menu-item>
        <el-menu-item index="/insights/hot-news">
            <span>A股热点</span>
        </el-menu-item>
        <el-menu-item index="/insights/catalyst">
            <span>催化剂监控</span>
          </el-menu-item>
        <el-menu-item index="/insights/capital">
            <span>资金面板</span>
          </el-menu-item>
        </el-sub-menu>
        <el-sub-menu index="pick">
          <template #title>
            <el-icon><TrendCharts /></el-icon><span>选股</span>
          </template>
          <el-menu-item index="/quant">
            <span>智能选股</span>
          </el-menu-item>
        </el-sub-menu>
        <el-sub-menu index="stock">
          <template #title>
            <el-icon><DocumentChecked /></el-icon><span>个股</span>
          </template>
          <el-menu-item index="/stock-analysis">
            <span>个股深研</span>
        </el-menu-item>
        </el-sub-menu>
        <el-sub-menu index="mine">
          <template #title>
            <el-icon><Star /></el-icon><span>我的</span>
          </template>
          <el-menu-item index="/favorites">
            <span>我的自选股</span>
          </el-menu-item>
          <el-menu-item index="/account/membership">
            <span>会员与用量</span>
          </el-menu-item>
          <el-menu-item v-if="currentUser?.is_admin" index="/admin/users">
            <span>用户管理</span>
          </el-menu-item>
        </el-sub-menu>
        <el-menu-item index="/data-center">
          <el-icon><Coin /></el-icon><span>数据中心</span>
        </el-menu-item>
      </el-menu>
      <div class="sidebar-foot">
        <div v-if="billingInfo" class="quota-chip" @click="$router.push('/account/membership')">
          {{ billingInfo.plan_label }} · 今日剩余 {{ billingInfo.remaining_today }}/{{ billingInfo.daily_limit }}
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
  Odometer, TrendCharts, Star, SwitchButton,
  DocumentChecked, Coin, Calendar, Menu,
} from '@element-plus/icons-vue'
import { currentUser, loadCurrentUser, clearCurrentUser } from '@/stores/user'
import { fetchBillingMe, type BillingMe } from '@/api/billing'

const route = useRoute()
const router = useRouter()

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

    .logo {
      font-size: 18px;
    }
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
