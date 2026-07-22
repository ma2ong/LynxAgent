import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('@/views/Auth/Login.vue') },
  { path: '/legal/terms', name: 'terms', component: () => import('@/views/Legal/Terms.vue') },
  { path: '/legal/privacy', name: 'privacy', component: () => import('@/views/Legal/Privacy.vue') },
  {
    path: '/',
    component: () => import('@/components/Layout/AppLayout.vue'),
    meta: { requiresAuth: true },
    children: [
      { path: '', redirect: '/quant' },
      { path: 'limit-up', name: 'limit-up', component: () => import('@/views/LimitUp/Index.vue') },
      { path: 'heatmap', name: 'heatmap', component: () => import('@/views/Heatmap/Index.vue') },
      { path: 'call-auction', name: 'call-auction', component: () => import('@/views/CallAuction/Index.vue') },
      { path: 'quant', name: 'quant', component: () => import('@/views/Quant/index.vue') },
      { path: 'review', name: 'picks-review', component: () => import('@/views/Review/Index.vue') },
      { path: 'stock-analysis', name: 'stock-analysis', component: () => import('@/views/StockAnalysis/index.vue') },
      { path: 'insights/catalyst', name: 'catalyst', component: () => import('@/views/Insights/CatalystMonitor.vue') },
      { path: 'favorites', name: 'favorites', component: () => import('@/views/Favorites/index.vue') },
      { path: 'data-center', name: 'data-center', component: () => import('@/views/DataCenter/Index.vue') },
      { path: 'account/membership', name: 'membership', component: () => import('@/views/Account/Membership.vue') },
      { path: 'admin/users', name: 'admin-users', component: () => import('@/views/Admin/Users.vue'), meta: { requiresAdmin: true } },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/quant' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  const token = localStorage.getItem('auth-token')
  if (to.meta.requiresAuth && !token) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && token) {
    return { name: 'quant' }
  }
  if (to.meta.requiresAdmin) {
    const { loadCurrentUser } = await import('@/stores/user')
    const user = await loadCurrentUser()
    if (!user?.is_admin) return { path: '/' }
  }
  return true
})

export default router
