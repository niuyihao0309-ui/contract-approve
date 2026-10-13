import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '../stores/auth'
import { permLabel } from '../permissions'

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
    // public：未登录也允许访问，且渲染时不套侧栏布局（见 App.vue）
    meta: { title: '登录', public: true },
  },
  {
    path: '/',
    name: 'dashboard',
    component: () => import('../views/Dashboard.vue'),
    meta: { title: '审查大盘', perm: 'task.view' },
  },
  {
    path: '/upload',
    name: 'upload',
    component: () => import('../views/UploadView.vue'),
    meta: { title: '上传合同', perm: 'task.submit' },
  },
  {
    path: '/tasks/:id',
    name: 'task-detail',
    component: () => import('../views/TaskDetail.vue'),
    meta: { title: '审查工作台', perm: 'task.view' },
  },
  {
    path: '/rules',
    name: 'rules',
    component: () => import('../views/RulesView.vue'),
    meta: { title: '规则与模板', perm: 'rule.view' },
  },
  {
    path: '/engine',
    name: 'engine',
    component: () => import('../views/EngineStatus.vue'),
    meta: { title: '引擎状态', perm: 'engine.view' },
  },
  {
    path: '/permissions',
    name: 'permissions',
    component: () => import('../views/PermissionsView.vue'),
    meta: { title: '角色与权限' },
  },
  {
    path: '/403',
    name: 'forbidden',
    component: () => import('../views/NoPermission.vue'),
    meta: { title: '无访问权限' },
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/',
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  const base = '合同审查'
  document.title = to.meta?.title ? `${to.meta.title} · ${base}` : base
})

/**
 * 守卫分三层：
 * 1. 公开页（登录页）：未登录放行；已登录就没必要停在登录页，直接送首页。
 * 2. 未登录：一律送登录页，并记住原目标以便登录后回跳。
 * 3. 已登录但当前角色无该权限：跳 403 页并说明缺哪个权限点。
 *
 * 仍然只是呈现层行为 —— 后端 `require_perm` 才是真边界。
 */
router.beforeEach((to) => {
  const auth = useAuthStore()

  if (to.meta?.public) {
    return auth.isLoggedIn ? { path: '/' } : true
  }

  if (!auth.isLoggedIn) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  const perm = to.meta?.perm
  if (!perm || auth.has(perm)) return true

  return {
    name: 'forbidden',
    query: {
      from: to.fullPath,
      need: perm,
      needLabel: permLabel(perm),
      role: auth.roleName,
    },
  }
})

export default router
