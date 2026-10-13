<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { apiAuth } from '../api'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const info = computed(() => ({
  from: route.query.from || '',
  need: route.query.need || '',
  needLabel: route.query.needLabel || '',
  role: route.query.role || auth.roleName,
}))

/**
 * 换个身份就得换个账号 —— 登录体系下不能原地换角色，
 * 所以这里的出口是「退出登录，去登录页用另一个账号登录」。
 */
async function relogin(username) {
  await apiAuth.logout().catch(() => null)
  auth.logout()
  ElMessage.success('已退出，请用有权限的账号登录')
  router.replace({ name: 'login', query: username ? { username } : {} })
}
</script>

<template>
  <div class="forbidden">
    <div class="code">403</div>
    <h2 class="title">当前身份无访问权限</h2>
    <p class="desc">
      身份 <strong>{{ info.role }}</strong> 缺少权限
      <em v-if="info.needLabel">「{{ info.needLabel }}」</em>
      <span v-if="info.need" class="mono">（{{ info.need }}）</span>。
    </p>
    <p v-if="info.from" class="from mono">目标地址：{{ info.from }}</p>

    <div class="actions">
      <el-button @click="router.replace('/')">返回审查大盘</el-button>
      <el-button @click="router.replace('/permissions')">查看角色与权限</el-button>
      <el-button type="primary" @click="relogin('admin')">
        退出并用系统管理员账号登录
      </el-button>
    </div>

    <p class="hint">
      即便绕过此提示，服务端接口同样会拒绝越权请求并返回 403。
    </p>
  </div>
</template>

<style scoped>
.forbidden {
  max-width: 620px;
  margin: 8vh auto 0;
  text-align: center;
}
.code {
  font-size: 72px;
  font-weight: 800;
  color: #dcdfe6;
  line-height: 1;
}
.title {
  margin: 12px 0 8px;
  font-size: 20px;
}
.desc {
  color: #4b5563;
  line-height: 1.8;
}
.mono {
  font-family: Consolas, Monaco, monospace;
  color: #98a1b3;
  font-size: 12px;
}
.from {
  margin-top: 6px;
}
.actions {
  margin-top: 24px;
  display: flex;
  gap: 10px;
  justify-content: center;
  flex-wrap: wrap;
}
.hint {
  margin-top: 28px;
  color: #98a1b3;
  font-size: 12px;
}
</style>
