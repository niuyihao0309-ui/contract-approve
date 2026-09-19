import axios from 'axios'
import { ElMessage } from 'element-plus'

// 统一走后端 FastAPI；开发期由 vite 代理 /api 到 127.0.0.1:8000
const http = axios.create({
  baseURL: '/api/v1',
  timeout: 300000,
})

// 响应拦截：后端出错时统一弹提示，并把 error 抛给调用方
http.interceptors.response.use(
  (res) => res,
  (err) => {
    const detail = err?.response?.data?.detail
    let msg = err?.message || '请求失败'
    if (typeof detail === 'string') msg = detail
    else if (detail && typeof detail === 'object') msg = detail.message || JSON.stringify(detail)
    ElMessage.error(msg)
    return Promise.reject(err)
  }
)

// ======================================================================
// 任务 / 大盘
// ======================================================================
export const apiTasks = {
  list: (params = {}) => http.get('/tasks', { params }).then((r) => r.data),
  stats: () => http.get('/tasks/stats').then((r) => r.data),
  detail: (id) => http.get(`/tasks/${id}`).then((r) => r.data),
  create: (payload) => http.post('/tasks', payload).then((r) => r.data),
  run: (id) => http.post(`/tasks/${id}/run`).then((r) => r.data),
  retry: (id, payload) => http.post(`/tasks/${id}/retry`, payload).then((r) => r.data),
  remove: (id) => http.delete(`/tasks/${id}`).then((r) => r.data),
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
  preview: (id) => http.get(`/tasks/${id}/report`).then((r) => r.data),
  files: (id) => http.get(`/tasks/${id}/report/files`).then((r) => r.data),
  // 返回文件下载 URL（前端直接 <a href> 触发下载，浏览器会带文件名）
  downloadUrl: (id, fmt) => `/api/v1/tasks/${id}/report/download?fmt=${fmt}`,
}

// ======================================================================
// 规则库
// ======================================================================
export const apiRules = {
  list: () => http.get('/rules').then((r) => r.data),
  searchTemplates: (query, topK = 3) =>
    http.get('/rules/templates/search', { params: { query, top_k: topK } }).then((r) => r.data),
}

export default http
