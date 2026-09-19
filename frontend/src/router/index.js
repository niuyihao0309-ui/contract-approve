import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'dashboard',
    component: () => import('../views/Dashboard.vue'),
    meta: { title: '工作台' },
  },
  {
    path: '/tasks/:id',
    name: 'task-detail',
    component: () => import('../views/TaskDetail.vue'),
    meta: { title: '审查详情' },
  },
  {
    path: '/engine',
    name: 'engine',
    component: () => import('../views/EngineStatus.vue'),
    meta: { title: '引擎状态' },
  },
  {
    path: '/rules',
    name: 'rules',
    component: () => import('../views/RulesView.vue'),
    meta: { title: '规则库' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  const base = '合同审批智能审查系统'
  document.title = to.meta?.title ? `${to.meta.title} · ${base}` : base
})

export default router
