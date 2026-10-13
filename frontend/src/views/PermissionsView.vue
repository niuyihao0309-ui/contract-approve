<script setup>
/**
 * 角色与权限（需求 2.4.2 的可视化落地）。
 *
 * 权限的唯一事实来源是 `src/permissions.js`（服务端对应 `app/core/permissions.py`），
 * 本页把它渲染出来，并列出**数据库里真实的登录账号** —— 让「不同职位的人
 * 拥有不同权限」这件事看得见，而不是只停留在矩阵表上。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiAuth } from '../api'
import {
  PERMS,
  ROLES,
  ROLE_KEYS,
  ROLE_PERMISSIONS,
  permLabel,
  permsByGroup,
} from '../permissions'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()

const groups = computed(() => permsByGroup())

/** 每个角色对某权限点的持有情况 */
function holds(roleKey, perm) {
  return (ROLE_PERMISSIONS[roleKey] || []).includes(perm)
}

/** 需求原文里的职责，逐条映射到权限点 —— 验收时要能对得上 */
const DUTY_PERMS = {
  lawyer: [
    ['查看合同审查详情', ['task.view']],
    ['核对原文高亮证据', ['doc.view', 'doc.preview']],
    ['采纳或调整修改建议', ['risk.view', 'risk.copy', 'risk.decide']],
    ['补充法务意见并触发审批回写', ['comment.add', 'writeback.trigger']],
    ['（工作台内导出审查报告）', ['report.view', 'report.download']],
  ],
  operator: [
    ['提交合同审查申请', ['task.submit', 'task.upload']],
    ['查看合同风险点与整改建议', ['task.view', 'doc.view', 'doc.preview', 'risk.view', 'risk.copy']],
    ['下载审查报告', ['report.view', 'report.download']],
  ],
  admin: [
    ['配置审批流对接参数', ['admin.approval']],
    ['维护合规审查规则与标准条款库', ['rule.view', 'rule.edit', 'template.edit']],
    ['监控审查任务状态并处理阻塞重试', ['task.monitor', 'task.retry', 'task.batch', 'engine.view']],
    ['维护登录账号与角色', ['user.manage']],
  ],
}

// ----------------------------------------------------------------------
// 登录账号（来自数据库 sys_user 表）
// ----------------------------------------------------------------------
const accounts = ref([])
const accountsLoading = ref(false)
const accountsDenied = ref(false)

async function loadAccounts() {
  if (!auth.has('user.manage')) {
    accountsDenied.value = true
    return
  }
  accountsLoading.value = true
  try {
    accounts.value = await apiAuth.users()
  } catch (e) {
    accounts.value = []
  } finally {
    accountsLoading.value = false
  }
}

function accountsOf(roleKey) {
  return accounts.value.filter((a) => a.role === roleKey)
}

/** 演示账号兜底：拿不到 DB 数据时（非管理员）用权限配置里的静态信息 */
function displayUsers(roleKey) {
  const list = accountsOf(roleKey)
  if (list.length) return list.map((a) => a.display_name).join('、')
  return ROLES[roleKey].user
}

/** 「以此身份进入」：登录体系下不能原地换角色，只能退出后换账号登录 */
async function reloginAs(roleKey) {
  const first = accountsOf(roleKey)[0]
  const username = first?.username || (roleKey === 'admin' ? 'admin' : '')
  const name = first?.display_name || ROLES[roleKey].user
  try {
    await ElMessageBox.confirm(
      `当前已以「${auth.roleName}·${auth.displayName}」登录。` +
        `要体验「${ROLES[roleKey].name}」的工作台，需要退出后用 ${name} 的账号重新登录。`,
      '切换身份需要重新登录',
      { type: 'info', confirmButtonText: '退出并去登录页', cancelButtonText: '取消' }
    )
  } catch (e) {
    return
  }
  await apiAuth.logout().catch(() => null)
  auth.logout()
  ElMessage.success('已退出，请用目标账号登录')
  router.replace({ name: 'login', query: username ? { username } : {} })
}

onMounted(loadAccounts)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h2 class="page-title">角色与权限</h2>
        <p class="sub">
          三个角色共 {{ Object.keys(PERMS).length }} 个权限点：查看类权限三家共有，
          差异集中在决定权（法务）、发起权（经办人）与维护权（管理员）。
        </p>
      </div>
    </div>

    <!-- 角色卡 -->
    <div class="role-grid">
      <div
        v-for="k in ROLE_KEYS"
        :key="k"
        class="role-card"
        :class="{ active: auth.roleKey === k }"
        :style="{ '--role-color': ROLES[k].color }"
      >
        <div class="rc-head">
          <div class="rc-name">{{ ROLES[k].name }}</div>
          <el-tag v-if="auth.roleKey === k" size="small" type="success">当前身份</el-tag>
        </div>
        <div class="rc-meta">
          账号 {{ displayUsers(k) }} · {{ ROLES[k].dept }} · 数据范围 {{ ROLES[k].scope }}
        </div>
        <div class="rc-duty">{{ ROLES[k].duty }}</div>

        <div class="rc-block">
          <div class="rc-block-title">职责与权限映射</div>
          <ul class="duty-list">
            <li v-for="(item, i) in DUTY_PERMS[k]" :key="i">
              <span class="duty-text">{{ item[0] }}</span>
              <span class="duty-perms">
                <el-tag v-for="p in item[1]" :key="p" size="small" effect="plain">
                  {{ p }}
                </el-tag>
              </span>
            </li>
          </ul>
        </div>

        <div class="rc-block">
          <div class="rc-block-title">权限点清单（{{ (ROLE_PERMISSIONS[k] || []).length }}）</div>
          <div class="chip-wrap">
            <el-tag v-for="p in ROLE_PERMISSIONS[k]" :key="p" size="small" class="chip">
              {{ permLabel(p) }}
            </el-tag>
          </div>
        </div>

        <el-button
          class="rc-btn"
          type="primary"
          plain
          :disabled="auth.roleKey === k"
          @click="reloginAs(k)"
        >
          {{ auth.roleKey === k ? '当前登录角色' : '以该角色重新登录' }}
        </el-button>
      </div>
    </div>

    <!-- 登录账号 -->
    <el-card shadow="never" class="matrix-card">
      <template #header>
        <div class="matrix-head">
          <span>登录账号</span>
        </div>
      </template>

      <el-alert
        v-if="accountsDenied"
        type="info"
        :closable="false"
        show-icon
        title="账号管理需要「查看与维护登录账号（user.manage）」权限，当前身份无此权限"
      />
      <el-table v-else v-loading="accountsLoading" :data="accounts" size="small" stripe>
        <el-table-column prop="username" label="账号" width="130">
          <template #default="{ row }"><span class="mono">{{ row.username }}</span></template>
        </el-table-column>
        <el-table-column prop="display_name" label="姓名" width="110" />
        <el-table-column label="角色" width="120">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.role_name }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="position" label="职位" width="140" />
        <el-table-column prop="dept" label="部门" width="120" />
        <el-table-column label="权限点" width="90" align="center">
          <template #default="{ row }">{{ (row.perms || []).length }}</template>
        </el-table-column>
        <el-table-column prop="remark" label="职责说明" min-width="260" show-overflow-tooltip />
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.enabled ? 'success' : 'info'">
              {{ row.enabled ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 权限矩阵 -->
    <el-card shadow="never" class="matrix-card">
      <template #header>
        <div class="matrix-head">
          <span>权限矩阵</span>
          <span class="muted">✓ 持有　– 不持有</span>
        </div>
      </template>

      <div v-for="(items, group) in groups" :key="group" class="group">
        <div class="group-name">{{ group }}</div>
        <table class="matrix">
          <thead>
            <tr>
              <th class="col-perm">权限点</th>
              <th class="col-desc">说明</th>
              <th v-for="k in ROLE_KEYS" :key="k" class="col-role">{{ ROLES[k].name }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in items" :key="p.key">
              <td class="mono">{{ p.key }}</td>
              <td>{{ p.label }}</td>
              <td v-for="k in ROLE_KEYS" :key="k" class="cell">
                <span v-if="holds(k, p.key)" class="yes">✓</span>
                <span v-else class="no">–</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </el-card>

    <p class="footnote">
      权限在界面与服务端两层执行：界面按身份收敛入口（隐藏菜单 / 置灰按钮 / 403 拦截），
      服务端接口对越权请求直接返回 403。
    </p>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.page-title {
  margin: 0 0 6px;
  font-size: 20px;
  font-weight: 600;
}
.sub {
  margin: 0;
  color: #6b7280;
  font-size: 13px;
  line-height: 1.7;
  max-width: 900px;
}
.role-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
}
.role-card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-top: 2px solid var(--role-color);
  border-radius: var(--radius);
  padding: 16px;
  display: flex;
  flex-direction: column;
}
.role-card.active {
  box-shadow: 0 0 0 2px var(--el-color-primary-light-7);
}
.rc-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.rc-name {
  font-size: 16px;
  font-weight: 700;
}
.rc-meta {
  margin-top: 4px;
  font-size: 12px;
  color: #98a1b3;
}
.rc-duty {
  margin-top: 10px;
  font-size: 13px;
  color: #4b5563;
  line-height: 1.7;
}
.rc-block {
  margin-top: 14px;
}
.rc-block-title {
  font-size: 12px;
  color: #98a1b3;
  margin-bottom: 6px;
}
.duty-list {
  margin: 0;
  padding-left: 16px;
  font-size: 12px;
  color: #374151;
}
.duty-list li {
  margin-bottom: 6px;
  line-height: 1.6;
}
.duty-text {
  display: block;
}
.duty-perms {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 3px;
}
.chip-wrap {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.chip {
  margin: 0;
}
.rc-btn {
  margin-top: 16px;
}
.matrix-card {
  margin-top: 16px;
}
.matrix-head {
  display: flex;
  justify-content: space-between;
  font-weight: 600;
}
.muted {
  color: #98a1b3;
  font-size: 12px;
  font-weight: 400;
}
.group {
  margin-bottom: 18px;
}
.group-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-color-primary);
  margin-bottom: 6px;
}
.matrix {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.matrix th,
.matrix td {
  border: 1px solid #eef0f3;
  padding: 6px 10px;
  text-align: left;
}
.matrix thead th {
  background: #f8fafc;
  color: #6b7280;
  font-weight: 600;
}
.col-perm {
  width: 190px;
}
.col-desc {
  min-width: 240px;
}
.col-role {
  width: 110px;
  text-align: center;
}
.cell {
  text-align: center;
}
.yes {
  color: #16a34a;
  font-weight: 700;
}
.no {
  color: #d1d5db;
}
.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  color: #475569;
}
.mt {
  margin-top: 16px;
}
.footnote {
  margin: 16px 0 0;
  font-size: 12px;
  line-height: 1.8;
  color: var(--ink-3);
}
</style>
