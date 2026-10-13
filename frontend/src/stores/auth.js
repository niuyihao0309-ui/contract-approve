/**
 * 登录态与权限判定。
 *
 * 登录态 = `{ token, user }`，存 localStorage 的 `ca.auth`（刷新不丢）。
 * `user` 来自后端 `/auth/login` 或 `/auth/me`，里面带 `role` 与 `perms` ——
 * **权限点以服务端返回的为准**，本地 `ROLE_PERMISSIONS` 只作为离线兜底
 * 与权限矩阵页的展示来源。这样以后后端加/减权限点，前端不用同步改就能生效。
 *
 * 展示层仍要收敛（隐藏入口 / 置灰按钮 / 403）。但请记住：
 * **前端权限不是安全边界**，服务端 `require_perm` 才是。
 */
import { defineStore } from 'pinia'

import { ROLES, ROLE_PERMISSIONS, ROLE_WORKSPACE, menusFor } from '../permissions'

const STORAGE_KEY = 'ca.auth'

// ======================================================================
// 本地存储（也会被回归脚本直接种值，key 别乱改）
// ======================================================================
export function readAuth() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (parsed && parsed.token && parsed.user) return parsed
    return null
  } catch (e) {
    return null
  }
}

export function writeAuth({ token, user }) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ token, user }))
  } catch (e) {
    /* 隐私模式下写不进去，忽略即可 */
  }
}

export function clearAuth() {
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch (e) {
    /* 同上 */
  }
}

/** 未登录时的占位身份，避免各处 `auth.role.name` 取到 undefined */
const ANON = {
  key: '',
  name: '未登录',
  user: '',
  dept: '',
  position: '',
  color: '#98a1b3',
  duty: '',
  scope: '',
}

export const useAuthStore = defineStore('auth', {
  state: () => {
    const saved = readAuth()
    return {
      token: saved?.token || '',
      user: saved?.user || null,
    }
  },

  getters: {
    isLoggedIn: (s) => Boolean(s.token && s.user),
    roleKey: (s) => s.user?.role || '',

    /**
     * 兼容层：老代码到处在用 `auth.role.name` / `auth.role.user` / `auth.role.dept`。
     * 这里把「角色静态配置」与「登录用户实际信息」合并成一个对象，
     * `name` 仍是角色名，`user` 换成真实姓名 —— 页面文案不用改。
     */
    role(s) {
      const base = ROLES[s.user?.role] || ANON
      if (!s.user) return ANON
      return {
        ...base,
        user: s.user.display_name || s.user.username,
        dept: s.user.dept || base.dept,
        position: s.user.position || '',
        username: s.user.username,
      }
    },

    roleName: (s) => (s.user ? (ROLES[s.user.role] || ANON).name : ANON.name),
    displayName: (s) => s.user?.display_name || s.user?.username || '',
    username: (s) => s.user?.username || '',
    dept: (s) => s.user?.dept || '',
    position: (s) => s.user?.position || '',
    /** 「法务审查人·李静」这种，写日志/批注作者用 */
    actor(s) {
      if (!s.user) return '未登录'
      const name = (ROLES[s.user.role] || ANON).name
      return `${name}·${s.user.display_name || s.user.username}`
    },
    /** 当前身份的权限点集合：优先用服务端下发的 perms */
    perms(s) {
      if (s.user?.perms?.length) return new Set(s.user.perms)
      return new Set(ROLE_PERMISSIONS[s.user?.role] || [])
    },
    /** 当前身份可见的侧栏菜单 */
    menus(s) {
      const set = this.perms
      return menusFor((p) => !p || set.has(p))
    },
    /** 角色化工作台（登录后首页按角色给不同工作内容） */
    workspace: (s) => ROLE_WORKSPACE[s.user?.role] || null,
  },

  actions: {
    /** 单个权限点 */
    has(perm) {
      if (!perm) return true
      return this.perms.has(perm)
    },
    /** 任一命中（数组入参） */
    hasAny(perms) {
      if (!perms || !perms.length) return true
      return perms.some((p) => this.has(p))
    },
    /** 全部命中 */
    hasAll(perms) {
      if (!perms || !perms.length) return true
      return perms.every((p) => this.has(p))
    },

    /** 登录成功后写入登录态 */
    setSession(token, user) {
      this.token = token
      this.user = user
      writeAuth({ token, user })
    },

    /** 用服务端最新数据刷新身份（权限点变了要能反映出来） */
    setUser(user) {
      if (!user) return
      this.user = user
      writeAuth({ token: this.token, user })
    },

    logout() {
      this.token = ''
      this.user = null
      clearAuth()
    },
  },
})
