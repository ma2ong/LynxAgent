import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('@/views/Auth/Login.vue') },
  {
    path: '/',
    component: () => import('@/components/Layout/AppLayout.vue'),
    meta: { requiresAuth: true },
    children: [
      { path: '', redirect: '/market/sentiment' },
      { path: 'market/sentiment', name: 'market-sentiment', component: () => import('@/views/Market/Sentiment.vue') },
      { path: 'limit-up', name: 'limit-up', component: () => import('@/views/LimitUp/Index.vue') },
      { path: 'quant', name: 'quant', component: () => import('@/views/Quant/index.vue') },
      { path: 'factor-lab', name: 'factor-lab', component: () => import('@/views/Quant/index.vue'), meta: { initialTab: 'research' } },
      { path: 'backtest-lab', name: 'backtest-lab', component: () => import('@/views/Quant/index.vue'), meta: { initialTab: 'backtest' } },
      { path: 'stock-analysis', name: 'stock-analysis', component: () => import('@/views/StockAnalysis/index.vue') },
      { path: 'deep-research', name: 'deep-research', component: () => import('@/views/StockAnalysis/index.vue') },
      { path: 'insights/hot-news', name: 'hot-news', component: () => import('@/views/Insights/HotNews.vue') },
      { path: 'insights/catalyst', name: 'catalyst', component: () => import('@/views/Insights/CatalystMonitor.vue') },
      { path: 'favorites', name: 'favorites', component: () => import('@/views/Favorites/index.vue') },
      { path: 'portfolio-check', name: 'portfolio-check', component: () => import('@/views/Favorites/index.vue') },
      { path: 'data-center', name: 'data-center', component: () => import('@/views/DataCenter/Index.vue') },
      { path: 'account/membership', name: 'membership', component: () => import('@/views/Account/Membership.vue') },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  const token = localStorage.getItem('auth-token')
  if (to.meta.requiresAuth && !token) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && token) {
    return { name: 'quant' }
  }
  return true
})

export default router
