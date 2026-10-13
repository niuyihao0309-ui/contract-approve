<script setup>
/**
 * 登录页（整屏布局，不套侧栏）。
 *
 * 登录后拿到的是 `{ token, user }`，其中 user 带 role 与 perms ——
 * 之后整个前端的菜单、按钮、403 拦截都由这一个身份驱动。
 * 账号密码由服务端校验，前端不存任何明文密码。
 */
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { apiAuth } from '../api'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const form = reactive({ username: '', password: '' })
const loading = ref(false)
const accounts = ref([])
const showPwd = ref(false)

async function loadMeta() {
  // 公开接口，拿不到也不影响登录 —— 静默失败即可
  try {
    accounts.value = await apiAuth.demoAccounts()
  } catch (e) {
    accounts.value = []
  }
}

function fill(a) {
  form.username = a.username
  form.password = a.password
}

async function submit() {
  if (!form.username.trim() || !form.password) {
    ElMessage.warning('请输入账号与密码')
    return
  }
  loading.value = true
  try {
    const res = await apiAuth.login({
      username: form.username.trim(),
      password: form.password,
    })
    auth.setSession(res.token, res.user)
    ElMessage.success(res.message || '登录成功')
    const back = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    router.replace(back.startsWith('/login') ? '/' : back)
  } catch (e) {
    // 错误提示已由 axios 拦截器统一弹出
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadMeta()
  // 从「角色与权限」页跳过来时会带上要用的账号
  if (typeof route.query.username === 'string') form.username = route.query.username
})
</script>

<template>
  <div class="login-page">
    <!-- 左：品牌 -->
    <div class="hero">
      <div class="hero-inner">
        <div class="hero-mark">合</div>
        <div class="hero-title">合同审查</div>
        <p class="hero-line">合同解析 · 风险识别 · 意见回写</p>
      </div>
    </div>

    <!-- 右：登录表单 -->
    <div class="form-side">
      <div class="form-inner">
        <h1 class="form-title">登录</h1>
        <p class="form-sub">请使用系统账号登录；不同角色的菜单与可执行操作不同。</p>

        <el-form :model="form" label-position="top" @submit.prevent="submit">
          <el-form-item label="账号">
            <el-input
              v-model="form.username"
              size="large"
              placeholder="请输入账号"
              clearable
              autocomplete="username"
              @keyup.enter="submit"
            >
              <template #prefix><el-icon><User /></el-icon></template>
            </el-input>
          </el-form-item>

          <el-form-item label="密码">
            <el-input
              v-model="form.password"
              size="large"
              :type="showPwd ? 'text' : 'password'"
              placeholder="请输入密码"
              autocomplete="current-password"
              @keyup.enter="submit"
            >
              <template #prefix><el-icon><Lock /></el-icon></template>
              <template #suffix>
                <el-icon class="pwd-eye" @click="showPwd = !showPwd">
                  <component :is="showPwd ? 'View' : 'Hide'" />
                </el-icon>
              </template>
            </el-input>
          </el-form-item>

          <el-button
            type="primary"
            size="large"
            class="submit"
            :loading="loading"
            @click="submit"
          >
            {{ loading ? '正在登录…' : '登 录' }}
          </el-button>
        </el-form>

        <!-- 快捷登录：点击任意一行自动填充 -->
        <div v-if="accounts.length" class="demo">
          <div class="demo-head">快捷登录</div>
          <div
            v-for="a in accounts"
            :key="a.username"
            class="demo-item"
            :class="{ active: form.username === a.username }"
            @click="fill(a)"
          >
            <span class="di-name">{{ a.display_name }}</span>
            <span class="di-role">{{ a.display_name === a.role_name ? a.dept : a.role_name }}</span>
            <span class="di-cred">{{ a.username }} / {{ a.password }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  display: grid;
  grid-template-columns: 1fr 1fr;
  min-height: 100vh;
}
/* ---- 左侧品牌区（极简：一块底色 + 名称 + 一行说明） ---- */
.hero {
  background: #1f2937;
  color: #e5e7eb;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px;
}
.hero-inner {
  max-width: 340px;
}
.hero-mark {
  width: 40px;
  height: 40px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.16);
  color: #fff;
  font-size: 20px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}
.hero-title {
  margin: 18px 0 0;
  font-size: 22px;
  font-weight: 600;
  color: #fff;
  letter-spacing: 1px;
}
.hero-line {
  margin: 10px 0 0;
  font-size: 13px;
  color: #98a3b3;
  letter-spacing: 0.5px;
}
/* ---- 右侧表单 ---- */
.form-side {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 32px;
  background: var(--surface);
  overflow: auto;
}
.form-inner {
  width: 100%;
  max-width: 372px;
}
.form-title {
  margin: 0 0 6px;
  font-size: 20px;
  font-weight: 600;
  color: var(--ink-1);
}
.form-sub {
  margin: 0 0 24px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--ink-3);
}
.pwd-eye {
  cursor: pointer;
  color: var(--ink-3);
}
.submit {
  width: 100%;
  margin-top: 4px;
  letter-spacing: 2px;
}
/* ---- 快捷登录 ---- */
.demo {
  margin-top: 28px;
  border-top: 1px solid var(--line-soft);
  padding-top: 14px;
}
.demo-head {
  font-size: 12px;
  color: var(--ink-3);
  margin-bottom: 8px;
}
.demo-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 10px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 12px;
  transition: background 0.15s;
}
.demo-item:hover {
  background: var(--canvas);
}
.demo-item.active {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-7);
}
.di-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-1);
}
.di-role {
  color: var(--ink-3);
}
.di-cred {
  margin-left: auto;
  color: var(--ink-3);
  font-family: Consolas, Monaco, monospace;
}
@media (max-width: 900px) {
  .login-page {
    grid-template-columns: 1fr;
  }
  .hero {
    display: none;
  }
}
</style>
