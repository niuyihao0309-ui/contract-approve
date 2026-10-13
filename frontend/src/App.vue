<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import UserMenu from './components/UserMenu.vue'
import { useAuthStore } from './stores/auth'
import { menusFor } from './permissions'

const route = useRoute()
const auth = useAuthStore()

/** 登录页自带整屏布局，不套侧栏（否则会出现「未登录却有侧栏」的怪画面） */
const standalone = computed(() => Boolean(route.meta?.public))

// 侧栏菜单只渲染当前身份能进的页面（菜单级收敛）
const menus = computed(() => menusFor((p) => auth.has(p)))

const activeMenu = computed(() => {
  if (route.name === 'task-detail') return '/'
  return route.path
})
</script>

<template>
  <!-- 公开页（登录）：独立整屏 -->
  <router-view v-if="standalone" />

  <el-container v-else class="layout">
    <el-aside width="212px" class="aside">
      <div class="brand">
        <div class="brand-logo">合</div>
        <div class="brand-title">合同审查</div>
      </div>
      <el-menu :default-active="activeMenu" router class="menu">
        <el-menu-item v-for="m in menus" :key="m.path" :index="m.path">
          <el-icon><component :is="m.icon" /></el-icon>
          <span>{{ m.title }}</span>
        </el-menu-item>
      </el-menu>

      <div class="role-foot">
        <div class="role-line">{{ auth.roleName }}</div>
        <div class="role-dept">{{ auth.dept || auth.role.dept }}</div>
      </div>
    </el-aside>

    <el-main class="main">
      <div class="topbar">
        <UserMenu />
      </div>
      <router-view />
    </el-main>
  </el-container>
</template>

<style scoped>
.layout {
  height: 100%;
}
.aside {
  background: var(--surface);
  border-right: 1px solid var(--line);
  display: flex;
  flex-direction: column;
}
.brand {
  display: flex;
  align-items: center;
  gap: 9px;
  height: 56px;
  padding: 0 18px;
  border-bottom: 1px solid var(--line-soft);
}
.brand-logo {
  width: 26px;
  height: 26px;
  border-radius: 6px;
  background: var(--el-color-primary);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.brand-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink-1);
  letter-spacing: 0.5px;
}
.menu {
  border-right: none;
  background: transparent;
  flex: 1;
  padding: 8px 0;
  --el-menu-bg-color: transparent;
  --el-menu-text-color: var(--ink-2);
  --el-menu-hover-bg-color: var(--canvas);
  --el-menu-active-color: var(--el-color-primary);
  --el-menu-item-height: 42px;
}
.menu :deep(.el-menu-item) {
  margin: 0 8px;
  border-radius: var(--radius-sm);
  font-size: 13px;
}
.menu :deep(.el-menu-item.is-active) {
  background: var(--el-color-primary-light-9);
  font-weight: 600;
}
.role-foot {
  padding: 12px 18px 16px;
  border-top: 1px solid var(--line-soft);
}
.role-line {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-2);
}
.role-dept {
  margin-top: 2px;
  font-size: 11px;
  color: var(--ink-3);
}
.main {
  padding: 0 20px 20px;
  overflow: auto;
}
.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  justify-content: flex-end;
  align-items: center;
  padding: 10px 0;
  margin-bottom: 8px;
  background: var(--canvas);
  border-bottom: 1px solid var(--line-soft);
}
</style>
