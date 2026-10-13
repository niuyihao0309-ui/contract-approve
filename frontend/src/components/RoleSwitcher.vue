<script setup>
/**
 * 身份切换器（演示态）。
 *
 * 真实上线时这里换成登录后的用户信息展示即可 —— 角色的来源是登录态，
 * 不应该由用户自己切。保留它是因为本期没有认证体系，需要能演示三个角色的差异。
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { ROLE_KEYS, ROLES, ROLE_PERMISSIONS } from '../permissions'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()

const options = computed(() =>
  ROLE_KEYS.map((k) => ({
    key: k,
    ...ROLES[k],
    permCount: (ROLE_PERMISSIONS[k] || []).length,
  }))
)

function onChange(key) {
  auth.switchRole(key)
  ElMessage.success(`已切换身份：${ROLES[key].name}（${ROLES[key].user}）`)
  // 当前页面若新身份进不去，退回大盘，避免停在 403
  const cur = router.currentRoute.value
  if (cur.meta?.perm && !auth.has(cur.meta.perm)) router.replace('/')
}
</script>

<template>
  <div class="switcher">
    <span class="label">当前身份</span>
    <el-select
      :model-value="auth.roleKey"
      size="small"
      style="width: 168px"
      @change="onChange"
    >
      <el-option v-for="o in options" :key="o.key" :label="o.name" :value="o.key">
        <span class="dot" :style="{ background: o.color }"></span>
        <span>{{ o.name }}</span>
        <span class="cnt">{{ o.permCount }} 权限</span>
      </el-option>
    </el-select>

    <div class="who">
      <el-avatar :size="26" :style="{ background: auth.role.color }">
        {{ auth.role.name.slice(0, 1) }}
      </el-avatar>
      <div class="who-text">
        <div class="who-name">{{ auth.role.name }} · {{ auth.role.user }}</div>
        <div class="who-dept">{{ auth.role.dept }}｜{{ auth.role.scope }}</div>
      </div>
    </div>

    <el-tooltip content="查看角色权限矩阵" placement="bottom">
      <el-button link @click="router.push('/permissions')">
        <el-icon><UserFilled /></el-icon>
      </el-button>
    </el-tooltip>
  </div>
</template>

<style scoped>
.switcher {
  display: flex;
  align-items: center;
  gap: 12px;
}
.label {
  color: #98a1b3;
  font-size: 12px;
}
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: middle;
}
.cnt {
  float: right;
  color: #98a1b3;
  font-size: 12px;
}
.who {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-left: 12px;
  border-left: 1px solid #e5e7eb;
}
.who-name {
  font-size: 13px;
  color: #1f2329;
  line-height: 1.2;
}
.who-dept {
  font-size: 11px;
  color: #98a1b3;
}
</style>
