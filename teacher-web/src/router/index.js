import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    component: () => import('../layout/AdminLayout.vue'),
    children: [
      { path: '', redirect: '/dashboard' },
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('../views/DashboardView.vue'),
        meta: { title: '工作台' },
      },
      {
        path: 'transactions',
        name: 'transactions',
        component: () => import('../views/TransactionsView.vue'),
        meta: { title: '收款流水' },
      },
      {
        path: 'settings',
        name: 'settings',
        component: () => import('../views/SettingsView.vue'),
        meta: { title: '收单与公告' },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

/**
 * 本地 token 是否还可用：必须是三段式 JWT 且 exp 未过期。
 *
 * 守卫只看 token 是否存在等于没守卫——手工往 localStorage 写一个 "1" 就能进后台。
 * 前端无法验签，但能拦住随手伪造与过期 token；真正的鉴权仍由后端 401 兜底
 * （http.js 收到 40100/40101/40300 会清 token 跳登录）。
 */
function isTokenUsable(token) {
  if (!token) return false
  const parts = String(token).split('.')
  if (parts.length !== 3) return false
  try {
    const padded = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const payload = JSON.parse(atob(padded.padEnd(Math.ceil(padded.length / 4) * 4, '=')))
    if (payload && typeof payload.exp === 'number') {
      return payload.exp * 1000 > Date.now()
    }
    return true
  } catch {
    return false
  }
}

router.beforeEach((to) => {
  const token = localStorage.getItem('zhiyin_admin_token')
  const authed = isTokenUsable(token)
  if (token && !authed) {
    // 过期或结构不对的 token 留着只会让每个请求都 401，先清掉
    localStorage.removeItem('zhiyin_admin_token')
    localStorage.removeItem('zhiyin_admin_user')
  }
  if (!to.meta.public && !authed) {
    return { name: 'login' }
  }
  if (to.name === 'login' && authed) {
    return { name: 'dashboard' }
  }
  return true
})

export default router
