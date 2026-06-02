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
      { path: 'quant', name: 'quant', component: () => import('@/views/Quant/index.vue') },
      { path: 'insights/hot-news', name: 'hot-news', component: () => import('@/views/Insights/HotNews.vue') },
      { path: 'insights/catalyst', name: 'catalyst', component: () => import('@/views/Insights/CatalystMonitor.vue') },
      { path: 'analysis/single', name: 'single-analysis', component: () => import('@/views/Analysis/SingleAnalysis.vue') },
      { path: 'favorites', name: 'favorites', component: () => import('@/views/Favorites/index.vue') },
      { path: 'paper', name: 'paper', component: () => import('@/views/PaperTrading/index.vue') },
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
