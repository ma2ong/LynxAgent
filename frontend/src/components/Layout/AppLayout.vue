<template>
  <el-container class="app-layout">
    <el-aside width="216px" class="sidebar">
      <div class="brand">
        <span class="logo">🐾</span>
        <span class="name">LynxAgent</span>
      </div>
      <el-menu :default-active="route.path" router class="menu">
        <el-menu-item index="/market/sentiment">
          <el-icon><Odometer /></el-icon><span>大盘情绪</span>
        </el-menu-item>
        <el-menu-item index="/limit-up">
          <el-icon><Histogram /></el-icon><span>涨停热点</span>
        </el-menu-item>
        <el-menu-item index="/quant">
          <el-icon><TrendCharts /></el-icon><span>智能选股</span>
        </el-menu-item>
        <el-menu-item index="/stock-report">
          <el-icon><Tickets /></el-icon><span>个股研报</span>
        </el-menu-item>
        <el-menu-item index="/insights/hot-news">
          <el-icon><Histogram /></el-icon><span>A股热点</span>
        </el-menu-item>
        <el-menu-item index="/insights/catalyst">
          <el-icon><DataLine /></el-icon><span>利好监控</span>
        </el-menu-item>
        <el-menu-item index="/analysis/single">
          <el-icon><Search /></el-icon><span>个股深研</span>
        </el-menu-item>
        <el-menu-item index="/favorites">
          <el-icon><Star /></el-icon><span>我的自选股</span>
        </el-menu-item>
        <el-menu-item index="/paper">
          <el-icon><Wallet /></el-icon><span>模拟交易</span>
        </el-menu-item>
      </el-menu>
      <div class="sidebar-foot">
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
import { useRoute, useRouter } from 'vue-router'
import {
  Odometer, TrendCharts, Histogram, DataLine, Search, Star, Wallet, SwitchButton,
  Tickets,
} from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()

const logout = () => {
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
</style>
