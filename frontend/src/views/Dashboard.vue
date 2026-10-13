<script setup>
/**
 * 审查大盘（需求 2.4.5「大盘与列表模块」）。
 *
 * 职责：任务列表渲染、多条件检索、状态标签、批量操作。
 * 权限差异：
 *   - 上传入口：需要 task.submit（业务经办人）
 *   - 批量操作：需要 task.batch（系统管理员）
 *   - 阻塞任务的「重试」：需要 task.retry（系统管理员）
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiTasks } from '../api'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()

const stats = ref(null)
const loading = ref(false)
const tasks = ref([])
const total = ref(0)
const selection = ref([])

const preset = ref({
  range: null,
  status: '',
})

const filters = reactive({
  status: '',
  risk: '',
  business_type: '',
  applicant: '',
  keyword: '',
})

const pagination = reactive({ page: 1, size: 20 })

const STATUS_OPTIONS = [
  { value: 'pending', label: '待处理' },
  { value: 'parsing', label: '解析中' },
  { value: 'reviewing', label: '审查中' },
  { value: 'blocked', label: '解析受阻' },
  { value: 'completed', label: '审查完成' },
]

const RISK_OPTIONS = [
  { value: 'high', label: '高风险' },
  { value: 'medium', label: '中风险' },
  { value: 'low', label: '低风险' },
]

const BUSINESS_OPTIONS = [
  { value: 'purchase', label: '采购合同' },
  { value: 'sales', label: '销售合同' },
  { value: 'service', label: '服务合同' },
  { value: 'labor', label: '劳动合同' },
  { value: 'other', label: '其他合同' },
]

const STATUS_TAG = {
  pending: 'info',
  parsing: 'warning',
  reviewing: 'warning',
  blocked: 'danger',
  completed: 'success',
}

const RISK_TAG = { high: 'danger', medium: 'warning', low: 'info' }
const CONCLUSION_TAG = { reject: 'danger', rectify: 'warning', pass: 'success' }
const WRITEBACK_TAG = { success: 'success', failed: 'danger', writing: 'warning', not_written: 'info' }

/** 快捷视图：一键切到运维/法务最常看的两种切片 */
const PRESETS = [
  { key: 'blocked', label: '待处理阻塞', status: 'blocked', risk: '' },
  { key: 'high', label: '高风险待复核', status: '', risk: 'high' },
  { key: 'writing', label: '审查中', status: 'reviewing', risk: '' },
]

const activePreset = ref('')

function applyPreset(p) {
  activePreset.value = p.key
  filters.status = p.status
  filters.risk = p.risk
  search()
}

async function loadStats() {
  stats.value = await apiTasks.stats()
}

function buildParams() {
  const params = {
    status: filters.status || undefined,
    risk: filters.risk || undefined,
    business_type: filters.business_type || undefined,
    applicant: filters.applicant || undefined,
    keyword: filters.keyword || undefined,
    limit: pagination.size,
    offset: (pagination.page - 1) * pagination.size,
  }
  // 创建时间区间（el-date-picker 的 daterange 给 [start, end]）
  if (preset.value.range?.length === 2) {
    params.date_from = `${preset.value.range[0]}T00:00:00`
    params.date_to = `${preset.value.range[1]}T23:59:59`
  }
  return params
}

async function loadTasks() {
  loading.value = true
  try {
    const data = await apiTasks.list(buildParams())
    tasks.value = data.items
    total.value = data.total
    selection.value = []
  } finally {
    loading.value = false
  }
}

function search() {
  pagination.page = 1
  loadTasks()
}

function reset() {
  filters.status = ''
  filters.risk = ''
  filters.business_type = ''
  filters.applicant = ''
  filters.keyword = ''
  preset.value.range = null
  activePreset.value = ''
  pagination.page = 1
  loadTasks()
}

function refresh() {
  loadStats()
  loadTasks()
}

function fmtMoney(t) {
  if (t.amount == null) return '—'
  const cur = t.currency === 'CNY' ? '¥' : (t.currency || '') + ' '
  return cur + Number(t.amount).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function fmtTime(s) {
  return s ? String(s).replace('T', ' ').slice(0, 16) : '—'
}

function goDetail(row) {
  router.push({ name: 'task-detail', params: { id: row.id } })
}

function goUpload() {
  router.push('/upload')
}

// ======================================================================
// 批量操作
// ======================================================================
const batching = ref(false)

async function batch(action) {
  const ids = selection.value.map((t) => t.id)
  if (!ids.length) {
    ElMessage.warning('请先勾选任务')
    return
  }
  const label = { retry: '重新审查', run: '执行流水线', delete: '删除' }[action]
  await ElMessageBox.confirm(
    `将对选中的 ${ids.length} 条任务执行「${label}」，确定继续？`,
    '批量操作',
    { type: action === 'delete' ? 'warning' : 'info' }
  )
  batching.value = true
  try {
    // 操作人由后端从登录态取
    const r = await apiTasks.batch(action, ids)
    if (r.ok) ElMessage.success(r.message)
    else {
      ElMessage.warning(r.message)
      // 逐条说明为什么被跳过，别让用户猜
      const lines = (r.data?.skipped || [])
        .map((s) => `${s.title || s.task_id.slice(0, 8)}：${s.reason}`)
        .join('<br/>')
      if (lines) {
        ElMessageBox.alert(lines, `跳过 ${r.data.skipped.length} 条`, {
          dangerouslyUseHTMLString: true,
        })
      }
    }
    refresh()
  } finally {
    batching.value = false
  }
}

/** 单条快捷重试（阻塞任务） */
async function retryOne(row) {
  // 操作人由后端从登录态取，这里不用再传
  await apiTasks.retry(row.id, {})
  ElMessage.success('已触发重新审查')
  refresh()
}

// ----------------------------------------------------------------------
// 角色化工作台：换个身份登录，第一眼看到的工作就不同
// ----------------------------------------------------------------------
const myCount = ref(0)

async function loadMyCount() {
  if (!auth.isLoggedIn) return
  try {
    const d = await apiTasks.list({ applicant: auth.displayName, limit: 1 })
    myCount.value = d.total
  } catch (e) {
    myCount.value = 0
  }
}

/** 每个角色的「我的待办」切片与快捷入口 */
const workspaceActions = computed(() => {
  const s = stats.value || {}
  const byStatus = s.by_status || {}
  const byRisk = s.by_risk || {}

  if (auth.roleKey === 'lawyer') {
    return [
      {
        label: '待复核（已完成审查）',
        value: byStatus.completed ?? 0,
        run: () => {
          activePreset.value = ''
          filters.status = 'completed'
          filters.risk = ''
          search()
        },
      },
      {
        label: '高风险合同',
        value: byRisk.high ?? 0,
        run: () => {
          activePreset.value = ''
          filters.status = ''
          filters.risk = 'high'
          search()
        },
      },
    ]
  }

  if (auth.roleKey === 'operator') {
    return [
      {
        label: `我的送审（${auth.displayName}）`,
        value: myCount.value,
        run: () => {
          activePreset.value = ''
          filters.applicant = auth.displayName
          filters.status = ''
          search()
        },
      },
      {
        label: '审查中',
        value: byStatus.reviewing ?? 0,
        run: () => {
          activePreset.value = ''
          filters.status = 'reviewing'
          search()
        },
      },
    ]
  }

  return [
    {
      label: '待处理阻塞',
      value: s.blocked_count ?? 0,
      run: () => applyPreset(PRESETS[0]),
    },
    {
      label: '审查中',
      value: byStatus.reviewing ?? 0,
      run: () => {
        activePreset.value = ''
        filters.status = 'reviewing'
        search()
      },
    },
  ]
})

function onSelectionChange(v) {
  selection.value = v
}

const selectedCount = computed(() => selection.value.length)

onMounted(() => {
  refresh()
  loadMyCount()
})
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h2 class="page-title">审查大盘</h2>
        <p class="sub">待审合同、风险分布与阻塞监控。</p>
      </div>
      <div class="head-btns">
        <el-button @click="refresh"><el-icon><Refresh /></el-icon>刷新</el-button>
        <el-button v-if="auth.has('task.submit')" type="primary" @click="goUpload">
          <el-icon><UploadFilled /></el-icon>上传合同
        </el-button>
        <el-tooltip
          v-else
          :content="`上传合同由业务经办人发起，当前身份为${auth.role.name}`"
          placement="bottom"
        >
          <el-button disabled><el-icon><UploadFilled /></el-icon>上传合同</el-button>
        </el-tooltip>
      </div>
    </div>

    <!-- 角色化工作台：换个账号登录，这里显示的活儿就不一样 -->
    <div v-if="auth.workspace" class="workspace-bar">
      <div class="ws-left">
        <div class="ws-title">
          {{ auth.workspace.title }}
          <el-tag size="small" effect="plain">
            {{ auth.roleName }} · {{ auth.displayName }}
          </el-tag>
        </div>
        <div class="ws-desc">{{ auth.workspace.desc }}</div>
      </div>
      <div class="ws-actions">
        <div
          v-for="a in workspaceActions"
          :key="a.label"
          class="ws-item"
          @click="a.run()"
        >
          <div class="ws-num">{{ a.value }}</div>
          <div class="ws-label">{{ a.label }}</div>
        </div>
      </div>
    </div>

    <!-- 统计卡片 -->
    <div class="stat-grid" v-if="stats">
      <div class="stat-card clickable" @click="reset">
        <div class="stat-num">{{ stats.total }}</div>
        <div class="stat-label">合同总数</div>
      </div>
      <div class="stat-card clickable" @click="filters.status = 'completed'; search()">
        <div class="stat-num">{{ stats.by_status.completed ?? 0 }}</div>
        <div class="stat-label">已完成审查</div>
      </div>
      <div class="stat-card clickable danger" @click="applyPreset(PRESETS[0])">
        <div class="stat-num">{{ stats.blocked_count }}</div>
        <div class="stat-label">解析受阻</div>
      </div>
      <div class="stat-card clickable" @click="filters.risk = 'high'; search()">
        <div class="stat-num">{{ stats.by_risk.high ?? 0 }}</div>
        <div class="stat-label">高风险合同</div>
      </div>
      <div class="stat-card clickable" @click="filters.risk = 'medium'; search()">
        <div class="stat-num">{{ stats.by_risk.medium ?? 0 }}</div>
        <div class="stat-label">中风险合同</div>
      </div>
      <div class="stat-card clickable" @click="filters.risk = 'low'; search()">
        <div class="stat-num">{{ stats.by_risk.low ?? 0 }}</div>
        <div class="stat-label">低风险合同</div>
      </div>
    </div>

    <!-- 多条件检索 -->
    <el-card class="filter-card" shadow="never">
      <div class="preset-row">
        <span class="preset-label">快捷视图</span>
        <el-button
          v-for="p in PRESETS"
          :key="p.key"
          size="small"
          :type="activePreset === p.key ? 'primary' : 'default'"
          @click="applyPreset(p)"
        >
          {{ p.label }}
        </el-button>
      </div>

      <div class="filter-row">
        <el-select v-model="filters.status" placeholder="状态" clearable style="width: 132px" @change="search">
          <el-option v-for="o in STATUS_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
        <el-select v-model="filters.risk" placeholder="风险等级" clearable style="width: 132px" @change="search">
          <el-option v-for="o in RISK_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
        <el-select v-model="filters.business_type" placeholder="业务类型" clearable style="width: 140px" @change="search">
          <el-option v-for="o in BUSINESS_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
        <el-input
          v-model="filters.applicant"
          placeholder="申请人"
          clearable
          style="width: 132px"
          @keyup.enter="search"
          @clear="search"
        />
        <el-date-picker
          v-model="preset.range"
          type="daterange"
          value-format="YYYY-MM-DD"
          range-separator="~"
          start-placeholder="创建起"
          end-placeholder="创建止"
          style="width: 240px"
          @change="search"
        />
        <el-input
          v-model="filters.keyword"
          placeholder="标题 / 编号 / 相对方 / 部门"
          clearable
          style="width: 230px"
          @keyup.enter="search"
          @clear="search"
        >
          <template #append>
            <el-button @click="search"><el-icon><Search /></el-icon></el-button>
          </template>
        </el-input>
        <el-button @click="reset">重置</el-button>
      </div>
    </el-card>

    <!-- 批量操作条 -->
    <div v-if="selectedCount" class="batch-bar">
      <span class="batch-count">已选 {{ selectedCount }} 条</span>
      <el-button v-perm="'task.retry'" size="small" :loading="batching" @click="batch('retry')">
        <el-icon><RefreshRight /></el-icon>批量重新审查
      </el-button>
      <el-button v-perm="'task.monitor'" size="small" :loading="batching" @click="batch('run')">
        批量执行流水线
      </el-button>
      <el-button v-perm="'task.batch'" size="small" type="danger" plain :loading="batching" @click="batch('delete')">
        批量删除
      </el-button>
      <span v-if="!auth.has('task.batch')" class="batch-hint">
        批量操作需要系统管理员权限
      </span>
    </div>

    <!-- 任务表格 -->
    <el-card shadow="never">
      <el-table
        v-loading="loading"
        :data="tasks"
        row-key="id"
        @row-click="goDetail"
        @selection-change="onSelectionChange"
        style="cursor: pointer"
      >
        <el-table-column type="selection" width="44" />
        <el-table-column label="合同名称" min-width="230">
          <template #default="{ row }">
            <div class="title-cell">
              <span class="title-text" :title="row.title">{{ row.title }}</span>
              <!-- 一次上传多份文件 = 多条任务 + 一个批次号，这里标出来免得看着像散落的单 -->
              <el-tag
                v-if="row.batch_total > 1"
                size="small"
                type="warning"
                effect="plain"
                class="batch-tag"
              >
                批次 {{ row.batch_seq }}/{{ row.batch_total }}
              </el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="applicant" label="申请人" width="96" show-overflow-tooltip />
        <el-table-column label="业务类型" width="106">
          <template #default="{ row }">{{ row.business_type || '—' }}</template>
        </el-table-column>
        <el-table-column label="合同金额" width="124" align="right">
          <template #default="{ row }">{{ fmtMoney(row) }}</template>
        </el-table-column>
        <el-table-column label="审查状态" width="112">
          <template #default="{ row }">
            <!-- 阻塞任务：管理员要能在列表页直接看清原因，不用先进工作台 -->
            <el-popover
              v-if="row.status === 'blocked'"
              placement="right"
              :width="360"
              trigger="click"
            >
              <template #reference>
                <el-tag :type="STATUS_TAG[row.status] || 'info'" size="small" class="clickable-tag">
                  {{ row.status_label || row.status }}
                </el-tag>
              </template>
              <div class="block-pop">
                <div class="bp-title">
                  <el-icon><WarningFilled /></el-icon>
                  解析受阻：{{ row.block_reason_label || '未知原因' }}
                </div>
                <div class="bp-body">
                  {{ row.block_detail || '（该任务没有留下更细的原因说明）' }}
                </div>
                <div class="bp-foot">
                  <span v-if="row.retry_count" class="bp-retry">已重试 {{ row.retry_count }} 次</span>
                  <el-button
                    v-perm="'task.retry'"
                    size="small"
                    type="primary"
                    @click.stop="retryOne(row)"
                  >
                    <el-icon><RefreshRight /></el-icon>重新审查
                  </el-button>
                </div>
              </div>
            </el-popover>
            <el-tag v-else :type="STATUS_TAG[row.status] || 'info'" size="small">
              {{ row.status_label || row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="综合风险" width="104">
          <template #default="{ row }">
            <span v-if="row.overall_risk_label">
              <span class="risk-dot" :class="row.overall_risk"></span>{{ row.overall_risk_label }}
            </span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="结论" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.conclusion_label" :type="CONCLUSION_TAG[row.conclusion] || 'info'" size="small">
              {{ row.conclusion_label }}
            </el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="回写" width="94">
          <template #default="{ row }">
            <el-tag :type="WRITEBACK_TAG[row.writeback_status] || 'info'" size="small" effect="plain">
              {{ row.writeback_label || row.writeback_status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }">
            <span class="nowrap">{{ fmtTime(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click.stop="goDetail(row)">查看</el-button>
            <el-button
              v-if="row.status === 'blocked'"
              v-perm="'task.retry'"
              link
              type="warning"
              size="small"
              @click.stop="retryOne(row)"
            >
              重试
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有合同任务">
            <el-button v-perm="'task.submit'" type="primary" @click="goUpload">上传第一份合同</el-button>
          </el-empty>
        </template>
      </el-table>

      <div class="pager">
        <el-pagination
          layout="total, sizes, prev, pager, next"
          :total="total"
          :page-size="pagination.size"
          :page-sizes="[20, 50, 100]"
          :current-page="pagination.page"
          @current-change="(p) => { pagination.page = p; loadTasks() }"
          @size-change="(s) => { pagination.size = s; pagination.page = 1; loadTasks() }"
        />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
/* ---- 角色化工作台条 ---- */
.workspace-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 14px 18px;
  margin-bottom: 14px;
}
/* 合同名称单元格：标题 + 批次标（多文件一次上传的几张单） */
.title-cell {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.title-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.batch-tag {
  flex: 0 0 auto;
}
.ws-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink-1);
}
.ws-desc {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--ink-3);
  max-width: 640px;
}
.ws-actions {
  display: flex;
  gap: 10px;
  flex-shrink: 0;
}
.ws-item {
  min-width: 124px;
  text-align: center;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: 8px 12px;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.ws-item:hover {
  background: var(--canvas);
  border-color: var(--el-color-primary-light-7);
}
.ws-num {
  font-size: 20px;
  font-weight: 700;
  color: var(--el-color-primary);
  line-height: 1.2;
}
.ws-label {
  margin-top: 2px;
  font-size: 11px;
  color: var(--ink-3);
}
/* ---- 阻塞原因弹层 ---- */
.clickable-tag {
  cursor: pointer;
}
.block-pop {
  font-size: 12px;
}
.bp-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  color: #b91c1c;
  margin-bottom: 6px;
}
.bp-body {
  color: #4b5563;
  line-height: 1.8;
  max-height: 200px;
  overflow: auto;
  background: #fafbfc;
  border-radius: 6px;
  padding: 8px 10px;
}
.bp-foot {
  margin-top: 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.bp-retry {
  font-size: 11px;
  color: #98a1b3;
}
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.page-title {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 600;
}
.sub {
  margin: 0;
  color: var(--ink-3);
  font-size: 12px;
}
.head-btns {
  display: flex;
  gap: 8px;
}
.stat-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}
.stat-card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 14px 12px;
  text-align: center;
  transition: border-color 0.2s;
}
.stat-card.clickable {
  cursor: pointer;
}
.stat-card.clickable:hover {
  border-color: var(--el-color-primary);
}
.stat-card.danger .stat-num {
  color: var(--risk-high);
}
.stat-num {
  font-size: 26px;
  font-weight: 700;
  color: var(--ink-1);
  line-height: 1.2;
}
.stat-label {
  margin-top: 4px;
  font-size: 12px;
  color: var(--ink-2);
}
.filter-card {
  margin-bottom: 12px;
}
.preset-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.preset-label {
  font-size: 12px;
  color: var(--ink-3);
}
.filter-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  align-items: center;
}
.batch-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: var(--radius-sm);
  padding: 8px 14px;
  margin-bottom: 12px;
}
.batch-count {
  font-size: 13px;
  color: var(--el-color-primary-dark-2);
  font-weight: 600;
}
.batch-hint {
  font-size: 12px;
  color: var(--ink-3);
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.muted {
  color: #c0c4cc;
}
.nowrap {
  white-space: nowrap;
}
</style>
