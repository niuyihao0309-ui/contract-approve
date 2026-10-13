<script setup>
/**
 * 顶部用户菜单。
 *
 * 身份来自登录，所以这里只做三件事：显示当前是谁、看权限矩阵、退出登录。
 * 「切换账号」故意做成「退出 → 用另一个账号登录」，而不是原地换角色 ——
 * 原地换角色会让权限体系看起来像个开关，实际系统里不该存在这个动作。
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiAuth } from '../api'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()

const permCount = computed(() => auth.perms.size)

async function doLogout(tip = '已退出登录') {
  await apiAuth.logout().catch(() => null)
  auth.logout()
  ElMessage.success(tip)
  router.replace({ name: 'login' })
}

async function onCommand(cmd) {
  if (cmd === 'permissions') {
    router.push('/permissions')
    return
  }
  if (cmd === 'logout') {
    await doLogout()
    return
  }
  if (cmd === 'switch') {
    try {
      await ElMessageBox.confirm(
        '切换账号会先退出当前登录，然后回到登录页。继续吗？',
        '切换账号',
        { type: 'info', confirmButtonText: '去登录页', cancelButtonText: '取消' }
      )
      await doLogout('已退出，请用另一个账号登录')
    } catch (e) {
      /* 用户取消 */
    }
  }
}
</script>

<template>
  <div class="user-menu">
    <el-tag size="small" effect="plain" class="role-tag">
      {{ auth.roleName }} · {{ permCount }} 项权限
    </el-tag>

    <el-dropdown trigger="click" @command="onCommand">
      <div class="who">
        <el-avatar :size="28" class="avatar">
          {{ (auth.displayName || '?').slice(0, 1) }}
        </el-avatar>
        <div class="who-text">
          <div class="who-name">{{ auth.displayName }}</div>
          <div class="who-dept">{{ auth.dept || auth.roleName }}</div>
        </div>
        <el-icon class="caret"><ArrowDown /></el-icon>
      </div>

      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item command="permissions">
            <el-icon><UserFilled /></el-icon>角色与权限矩阵
          </el-dropdown-item>
          <el-dropdown-item command="switch">
            <el-icon><Switch /></el-icon>切换账号
          </el-dropdown-item>
          <el-dropdown-item command="logout" divided>
            <el-icon><SwitchButton /></el-icon>退出登录
          </el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>
  </div>
</template>

<style scoped>
.user-menu {
  display: flex;
  align-items: center;
  gap: 12px;
}
.role-tag {
  font-weight: 400;
  color: var(--ink-2);
}
.avatar {
  background: var(--el-color-primary-light-8);
  color: var(--el-color-primary);
  font-size: 13px;
  font-weight: 600;
}
.who {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 8px 3px 4px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background 0.15s;
}
.who:hover {
  background: #eef0f4;
}
.who-text {
  line-height: 1.3;
}
.who-name {
  font-size: 13px;
  color: var(--ink-1);
}
.who-dept {
  font-size: 11px;
  color: var(--ink-3);
}
.caret {
  color: var(--ink-3);
  font-size: 12px;
}
</style>
