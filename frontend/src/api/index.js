import axios from 'axios'
import { ElMessage } from 'element-plus'

import { clearAuth, readAuth } from '../stores/auth'

// 统一走后端 FastAPI；开发期由 vite 代理 /api 到 127.0.0.1:8000
const http = axios.create({
  baseURL: '/api/v1',
  timeout: 300000,
})

// 请求拦截：统一带上访问令牌
// ⚠️ 故意**同时带两份**（2026-09-19 上线踩过）：部署到云平台后，反向代理会自己注入
// ``Authorization: Bearer ey7DJIh9…``（平台内部凭据），会覆盖/顶掉我们这个头。
// 后端取令牌时会按「长相」过滤（必须是 xxx.yyy 两段），网关那个不像就跳过、
// 落到 X-Auth-Token —— 所以这里两份都带，哪种代理都能通。
http.interceptors.request.use((config) => {
  const auth = readAuth()
  if (auth?.token) {
    config.headers.Authorization = `Bearer ${auth.token}`
    config.headers['X-Auth-Token'] = auth.token
  }
  return config
})

/** 取出当前令牌（原件预览、报告下载这类浏览器直连的 URL 要拼在查询串上） */
export function authToken() {
  return readAuth()?.token || ''
}

/** 把令牌拼到 URL 上。给 <img src> / window.open 用 —— 它们带不了请求头。 */
export function withToken(url) {
  const t = authToken()
  if (!t) return url
  return `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(t)}`
}

let redirecting = false

// 响应拦截：后端出错时统一弹提示，并把 error 抛给调用方
http.interceptors.response.use(
  (res) => res,
  (err) => {
    const status = err?.response?.status
    const detail = err?.response?.data?.detail
    const code = err?.response?.data?.code
    let msg = err?.message || '请求失败'
    if (typeof detail === 'string') msg = detail
    else if (Array.isArray(detail)) msg = detail.map((d) => d.msg || JSON.stringify(d)).join('；')
    else if (detail && typeof detail === 'object') msg = detail.message || JSON.stringify(detail)
    else if (typeof err?.response?.data?.message === 'string') msg = err.response.data.message

    // 登录态失效：清掉本地身份并回登录页。
    // 用 location.replace 而不是 router.push —— 拦截器在 store 之外，
    // 拿不到 router 实例，硬跳一次比留个坏掉的页面好。
    if (status === 401 && !redirecting) {
      const onLoginPage = window.location.pathname.startsWith('/login')
      clearAuth()
      if (!onLoginPage) {
        redirecting = true
        ElMessage.warning(code === 'TOKEN_EXPIRED' ? '登录已过期，请重新登录' : '请先登录')
        const back = encodeURIComponent(window.location.pathname + window.location.search)
        window.location.replace(`/login?redirect=${back}`)
      }
    } else {
      ElMessage.error(msg)
    }
    return Promise.reject(err)
  }
)

export const API_BASE = '/api/v1'

// ======================================================================
// 认证与账号
// ======================================================================
export const apiAuth = {
  login: (payload) => http.post('/auth/login', payload).then((r) => r.data),
  me: () => http.get('/auth/me').then((r) => r.data),
  logout: () => http.post('/auth/logout').then((r) => r.data),
  /** 登录页用：演示账号清单（公开接口） */
  demoAccounts: () => http.get('/auth/demo-accounts').then((r) => r.data),
  /** 登录页用：角色权限矩阵（公开接口） */
  roles: () => http.get('/auth/roles').then((r) => r.data),
  users: (params = {}) => http.get('/auth/users', { params }).then((r) => r.data),
  createUser: (body) => http.post('/auth/users', body).then((r) => r.data),
  updateUser: (id, body) => http.patch(`/auth/users/${id}`, body).then((r) => r.data),
}

// ======================================================================
// 任务 / 大盘
// ======================================================================
export const apiTasks = {
  list: (params = {}) => http.get('/tasks', { params }).then((r) => r.data),
  stats: () => http.get('/tasks/stats').then((r) => r.data),
  detail: (id) => http.get(`/tasks/${id}`).then((r) => r.data),
  create: (payload) => http.post('/tasks', payload).then((r) => r.data),
  /**
   * 多文件上传：FormData 里字段名 `files`（可重复 append）。
   * 后端**一份文件建一条任务**，再用 batch_id 归组（批次只做展示分组）。
   * 返回 { batch_id, total, tasks: TaskBrief[], batch: 批次上下文|null }。
   */
  uploadBatch: (formData, onProgress) =>
    http
      .post('/tasks/upload-batch', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        onUploadProgress: onProgress,
      })
      .then((r) => r.data),
  run: (id) => http.post(`/tasks/${id}/run`).then((r) => r.data),
  retry: (id, payload) => http.post(`/tasks/${id}/retry`, payload).then((r) => r.data),
  remove: (id) => http.delete(`/tasks/${id}`).then((r) => r.data),
  /** 批量操作：action = retry | run | delete */
  batch: (action, taskIds, by = '系统管理员') =>
    http.post('/tasks/batch', { action, task_ids: taskIds, by }).then((r) => r.data),
  addComment: (id, payload) => http.post(`/tasks/${id}/comments`, payload).then((r) => r.data),
  decideRisk: (id, riskId, payload) =>
    http.post(`/tasks/${id}/risks/${riskId}/decision`, payload).then((r) => r.data),
}

// ======================================================================
// 文档视窗
// ======================================================================
export const apiDoc = {
  document: (id) => http.get(`/tasks/${id}/document`).then((r) => r.data),
  blocks: (id, page) => http.get(`/tasks/${id}/document/blocks`, { params: { page } }).then((r) => r.data),
  clauses: (id) => http.get(`/tasks/${id}/clauses`).then((r) => r.data),
  metadata: (id) => http.get(`/tasks/${id}/metadata`).then((r) => r.data),
  locate: (id, charStart, charEnd) =>
    http.get(`/tasks/${id}/locate`, { params: { char_start: charStart, char_end: charEnd } }).then((r) => r.data),
  /** 原件预览能力（每个附件能不能内嵌看、该用哪个渲染器） */
  previewCapability: (id) => http.get(`/tasks/${id}/preview-capability`).then((r) => r.data),
  /** 原件流 URL：PDF 塞 iframe、扫描件塞 img、docx 直接下载。
   *  浏览器直连带不了请求头，令牌拼在查询串上（后端 deps._extract_token 支持）。 */
  rawUrl: (id, fileId) => withToken(`${API_BASE}/tasks/${id}/files/${fileId}/raw`),
}

// ======================================================================
// 风险
// ======================================================================
export const apiRisks = {
  list: (id, params = {}) => http.get(`/tasks/${id}/risks`, { params }).then((r) => r.data),
  get: (id, riskId) => http.get(`/tasks/${id}/risks/${riskId}`).then((r) => r.data),
}

// ======================================================================
// 审查引擎
// ======================================================================
export const apiReview = {
  summary: (id) => http.get(`/review/tasks/${id}/summary`).then((r) => r.data),
  health: () => http.get('/review/health').then((r) => r.data),
  run: (id) => http.post(`/review/tasks/${id}/run`).then((r) => r.data),
  reparse: (id) => http.post(`/review/tasks/${id}/reparse`).then((r) => r.data),
}

// ======================================================================
// 回写
// ======================================================================
export const apiWriteback = {
  preview: (id, lawyerNotes = '') =>
    http.get(`/tasks/${id}/writeback/preview`, { params: { lawyer_notes: lawyerNotes } }).then((r) => r.data),
  status: (id) => http.get(`/tasks/${id}/writeback`).then((r) => r.data),
  submit: (id, payload) => http.post(`/tasks/${id}/writeback`, payload).then((r) => r.data),
}

// ======================================================================
// 报告
// ======================================================================
export const apiReport = {
  /** 预览：后端返回 text/markdown 纯文本 */
  preview: (id) => http.get(`/tasks/${id}/report`).then((r) => r.data),
  writebackText: (id) => http.get(`/tasks/${id}/report/writeback-text`).then((r) => r.data),
  files: (id) => http.get(`/tasks/${id}/report/files`).then((r) => r.data),
  // 返回文件下载 URL（前端直接 window.open / <a href> 触发下载，浏览器会带文件名）。
  // 同样是浏览器直连，令牌拼查询串。
  downloadUrl: (id, fmt) => withToken(`${API_BASE}/tasks/${id}/report/download?fmt=${fmt}`),
}

// ======================================================================
// 规则 / 模板
// ======================================================================
export const apiRules = {
  list: (params = {}) => http.get('/rules', { params }).then((r) => r.data),
  /** 条件编辑表单保存（也用于列表上的启停开关） */
  patch: (ruleId, body) => http.patch(`/rules/${ruleId}`, body).then((r) => r.data),
  sync: () => http.post('/rules/sync').then((r) => r.data),
  templates: (params = {}) => http.get('/rules/templates', { params }).then((r) => r.data),
  createTemplate: (body) => http.post('/rules/templates', body).then((r) => r.data),
  updateTemplate: (templateId, body) =>
    http.patch(`/rules/templates/${templateId}`, body).then((r) => r.data),
  searchTemplates: (query, topK = 3) =>
    http.get('/rules/templates/search', { params: { query, top_k: topK } }).then((r) => r.data),
}

// ======================================================================
// 审批集成（mock）—— 管理员配置检修用
// ======================================================================
export const apiApprovals = {
  tickets: (onlyPending = false) =>
    http.get('/approvals/tickets', { params: { only_pending: onlyPending } }).then((r) => r.data),
  mockStats: () => http.get('/approvals/mock/stats').then((r) => r.data),
  mockConfig: (payload) => http.post('/approvals/mock/config', payload).then((r) => r.data),
  mockReset: (keepTickets = false) =>
    http.post('/approvals/mock/reset', { keep_tickets: keepTickets }).then((r) => r.data),
  push: (payload) => http.post('/approvals/push', payload).then((r) => r.data),
  poll: (payload = {}) => http.post('/approvals/poll', payload).then((r) => r.data),
}

// ======================================================================
// 示例合同（上传页「一键试跑」用；全角色可读）
// ======================================================================
export const apiSamples = {
  list: () => http.get('/samples').then((r) => r.data),
}

// ======================================================================
// 系统能力（公开；上传页用来在**选文件那一刻**提示「OCR 没开，图片传了也会受阻」）
// ======================================================================
export const apiSystem = {
  health: () => http.get('/health').then((r) => r.data),
}

export default http
