/**
 * 角色与权限的**前端那一份**（对应需求 2.4.2）。
 *
 * ⚠️ 服务端对应物是 `app/core/permissions.py`。两份必须一致，
 * 否则会出现「界面点不动、接口却调得通」或反过来的错位。
 * 一致性由 `scripts/verify_perms_sync.py` + `tests/test_permissions_sync.py` 钉住，
 * 改了一边没改另一边会直接测试红。
 *
 * 设计原则：
 * 1. 前端只做「呈现层的收敛」——按角色隐藏/禁用入口，让界面不出现用不了的功能；
 *    真正的安全边界在服务端（`app/api/deps.py` 的 `require_perm`）。
 * 2. 权限点用「资源.动作」命名，一个权限点对应一个可被察觉的界面能力，
 *    不搞「菜单级」这种粗粒度（否则法务和经办人的区别只剩菜单，等于没做）。
 * 3. 三个角色的差异落在**决定权**上：谁能拍板（法务）、谁能发起（经办人）、
 *    谁能修管道（管理员）。查看类权限三家都有，构成共同底座。
 */

// ======================================================================
// 角色
// ======================================================================
export const ROLES = {
  lawyer: {
    key: 'lawyer',
    name: '法务审查人',
    user: '李静',
    dept: '法务部',
    color: '#e5484d',
    duty: '核对原文证据、采纳或调整修改建议、触发审批回写',
    scope: '全部合同（不限部门）',
  },
  operator: {
    key: 'operator',
    name: '业务经办人',
    user: '张伟',
    dept: '采购部',
    color: '#3b82f6',
    duty: '提交合同送审、跟踪审查进度、下载审查报告',
    scope: '本部门送审的合同',
  },
  admin: {
    key: 'admin',
    name: '系统管理员',
    user: 'admin',
    dept: '信息中心',
    color: '#6366f1',
    duty: '维护审查规则与条款库、监控任务并处理阻塞重试、配置审批流对接',
    scope: '全部合同 + 系统配置',
  },
}

export const ROLE_KEYS = ['lawyer', 'operator', 'admin']

/** 每个角色的工作台标题与"不同权限进去做不同事"的一句话说明 */
export const ROLE_WORKSPACE = {
  lawyer: {
    title: '法务复核台',
    desc: '对着原文高亮核对风险点，采纳或调整建议并回写审批意见。',
    todo: 'completed',
  },
  operator: {
    title: '送审跟踪台',
    desc: '提交合同送审，跟进审查进度，拿到整改报告后推进下一步。',
    todo: 'mine',
  },
  admin: {
    title: '系统监控台',
    desc: '盯阻塞任务与重试，维护审查规则和示范条款库，配置审批流对接。',
    todo: 'blocked',
  },
}

// ======================================================================
// 权限点
// ======================================================================
export const PERMS = {
  'task.view': { group: '审查任务', label: '查看任务列表与审查详情' },
  'task.submit': { group: '审查任务', label: '提交合同审查申请' },
  'task.upload': { group: '审查任务', label: '上传合同附件' },
  'task.monitor': { group: '审查任务', label: '监控任务状态（进度 / 阻塞原因）' },
  'task.retry': { group: '审查任务', label: '处理阻塞重试 / 重新解析' },
  'task.batch': { group: '审查任务', label: '批量操作（重试 / 执行 / 删除）' },

  'doc.view': { group: '文档视窗', label: '查看原文与高亮证据' },
  'doc.preview': { group: '文档视窗', label: '原件预览（PDF / 扫描件）' },

  'risk.view': { group: '风险与建议', label: '查看风险点与整改建议' },
  'risk.copy': { group: '风险与建议', label: '复制修改建议' },
  'risk.decide': { group: '风险与建议', label: '采纳 / 调整 / 驳回修改建议' },

  'comment.add': { group: '协同回写', label: '补充法务意见（批注）' },
  'writeback.trigger': { group: '协同回写', label: '触发审批回写' },
  'report.view': { group: '协同回写', label: '预览审查报告' },
  'report.download': { group: '协同回写', label: '下载审查报告（Markdown / PDF）' },

  'rule.view': { group: '规则配置', label: '查看审查规则与标准条款库' },
  'rule.edit': { group: '规则配置', label: '维护审查规则（启停 / 条件编辑）' },
  'template.edit': { group: '规则配置', label: '维护标准示范条款模板库' },

  'admin.approval': { group: '系统配置', label: '配置审批流对接参数与故障演练' },
  'engine.view': { group: '系统配置', label: '查看引擎状态与自检结果' },
  'user.manage': { group: '系统配置', label: '查看与维护登录账号' },
}

// ======================================================================
// 角色 → 权限（最小权限原则：需求里没写的就不给）
// ======================================================================
export const ROLE_PERMISSIONS = {
  // 法务审查人：复核 + 拍板 + 回写。不参与建单，也不碰系统配置。
  lawyer: [
    'task.view',
    'doc.view',
    'doc.preview',
    'risk.view',
    'risk.copy',
    'risk.decide',
    'comment.add',
    'writeback.trigger',
    'report.view',
    'report.download',
    'rule.view',
  ],
  // 业务经办人：提交 + 只读跟踪 + 下载。对风险点没有决定权，不能回写。
  operator: [
    'task.view',
    'task.submit',
    'task.upload',
    'doc.view',
    'doc.preview',
    'risk.view',
    'risk.copy',
    'report.view',
    'report.download',
  ],
  // 系统管理员：管道与规则。不做业务决定，也不代替经办人建单。
  admin: [
    'task.view',
    'task.monitor',
    'task.retry',
    'task.batch',
    'doc.view',
    'doc.preview',
    'risk.view',
    'engine.view',
    'rule.view',
    'rule.edit',
    'template.edit',
    'admin.approval',
    'user.manage',
  ],
}

// ======================================================================
// 菜单（perm 为空 = 所有已登录角色可见）
// ======================================================================
export const MENUS = [
  { path: '/', name: 'dashboard', title: '审查大盘', icon: 'Grid', perm: 'task.view' },
  { path: '/upload', name: 'upload', title: '上传合同', icon: 'UploadFilled', perm: 'task.submit' },
  { path: '/rules', name: 'rules', title: '规则与模板', icon: 'Document', perm: 'rule.view' },
  { path: '/engine', name: 'engine', title: '引擎状态', icon: 'Monitor', perm: 'engine.view' },
  { path: '/permissions', name: 'permissions', title: '角色与权限', icon: 'UserFilled', perm: '' },
]

// ======================================================================
// 辅助
// ======================================================================
/** 权限点 → 中文标签（无权限时给用户看「你缺什么权限」） */
export function permLabel(perm) {
  return PERMS[perm]?.label || perm
}

/** 把权限点按 group 分组，权限矩阵页按组渲染 */
export function permsByGroup() {
  const out = {}
  for (const [key, meta] of Object.entries(PERMS)) {
    ;(out[meta.group] ||= []).push({ key, label: meta.label })
  }
  return out
}

/** 某角色可见菜单 */
export function menusFor(has) {
  return MENUS.filter((m) => has(m.perm))
}
