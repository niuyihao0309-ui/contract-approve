<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { apiTasks } from '../api'

const router = useRouter()

const stats = ref(null)
const loading = ref(false)
const tasks = ref([])
const total = ref(0)

const filters = reactive({
  status: '',
  risk: '',
  keyword: '',
})

const pagination = reactive({ page: 1, size: 20 })

const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: '待处理' },
  { value: 'parsing', label: '解析中' },
  { value: 'reviewing', label: '审查中' },
  { value: 'blocked', label: '解析受阻' },
  { value: 'completed', label: '审查完成' },
]

const RISK_OPTIONS = [
  { value: '', label: '全部风险' },
  { value: 'high', label: '高风险' },
  { value: 'medium', label: '中风险' },
  { value: 'low', label: '低风险' },
]

const STATUS_TAG = {
  pending: 'info',
  parsing: 'warning',
  reviewing: 'warning',
  blocked: 'danger',
  completed: 'success',
}

const RISK_TAG = { high: 'danger', medium: 'warning', low: 'info' }

const CONCLUSION_TAG = {
  reject: 'danger',
  rectify: 'warning',
  pass: 'success',
}

async function loadStats() {
  stats.value = await apiTasks.stats()
}

async function loadTasks() {
  loading.value = true
  try {
    const data = await apiTasks.list({
      status: filters.status || undefined,
      risk: filters.risk || undefined,
      keyword: filters.keyword || undefined,
      limit: pagination.size,
      offset: (pagination.page - 1) * pagination.size,
    })
    tasks.value = data.items
    total.value = data.total
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
  filters.keyword = ''
  pagination.page = 1
  loadTasks()
}

function fmtMoney(t) {
  if (t.amount == null) return '—'
  const cur = t.currency === 'CNY' ? '¥' : (t.currency || '') + ' '
  return cur + Number(t.amount).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function goDetail(row) {
  router.push({ name: 'task-detail', params: { id: row.id } })
}

onMounted(() => {
  loadStats()
  loadTasks()
})
</script>

<template>
  <div>
    <div class="page-head">
      <h2 class="page-title">审查工作台</h2>
      <el-button type="primary" @click="search"><el-icon><Refresh /></el-icon>刷新</el-button>
    </div>

    <!-- 统计卡片 -->
    <div class="stat-grid" v-if="stats">
      <div class="stat-card">
        <div class="stat-num">{{ stats.total }}</div>
        <div class="stat-label">合同总数</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ stats.by_status.completed ?? 0 }}</div>
        <div class="stat-label">已完成审查</div>
      </div>
      <div class="stat-card danger">
        <div class="stat-num">{{ stats.blocked_count }}</div>
        <div class="stat-label">解析受阻</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ stats.by_risk.high ?? 0 }}</div>
        <div class="stat-label">高风险合同</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ stats.by_risk.medium ?? 0 }}</div>
        <div class="stat-label">中风险合同</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ stats.by_risk.low ?? 0 }}</div>
        <div class="stat-label">低风险合同</div>
      </div>
    </div>

    <!-- 筛选 -->
    <el-card class="filter-card" shadow="never">
      <div class="filter-row">
        <el-select v-model="filters.status" placeholder="状态" clearable style="width: 140px" @change="search">
          <el-option v-for="o in STATUS_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
        <el-select v-model="filters.risk" placeholder="风险等级" clearable style="width: 140px" @change="search">
          <el-option v-for="o in RISK_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
        <el-input
          v-model="filters.keyword"
          placeholder="按标题 / 编号 / 相对方搜索"
          clearable
          style="width: 260px"
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

    <!-- 任务表格 -->
    <el-card shadow="never">
      <el-table
        v-loading="loading"
        :data="tasks"
        row-key="id"
        @row-click="goDetail"
        style="cursor: pointer"
      >
        <el-table-column prop="title" label="合同标题" min-width="220" show-overflow-tooltip />
        <el-table-column prop="contract_no" label="合同编号" width="160" show-overflow-tooltip />
        <el-table-column prop="counterparty" label="相对方" width="180" show-overflow-tooltip />
        <el-table-column label="金额" width="130" align="right">
          <template #default="{ row }">{{ fmtMoney(row) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="STATUS_TAG[row.status] || 'info'" size="small">{{ row.status_label || row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="风险" width="100">
          <template #default="{ row }">
            <span v-if="row.overall_risk_label">
              <span class="risk-dot" :class="row.overall_risk"></span>{{ row.overall_risk_label }}
            </span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="结论" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.conclusion_label" :type="CONCLUSION_TAG[row.conclusion] || 'info'" size="small">
              {{ row.conclusion_label }}
            </el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="goDetail(row)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination
          layout="total, prev, pager, next"
          :total="total"
          :page-size="pagination.size"
          :current-page="pagination.page"
          @current-change="(p) => { pagination.page = p; loadTasks() }"
        />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.page-title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.stat-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}
.stat-card {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 16px;
  text-align: center;
}
.stat-card.danger .stat-num {
  color: var(--risk-high);
}
.stat-num {
  font-size: 28px;
  font-weight: 700;
  color: #1f2937;
}
.stat-label {
  margin-top: 4px;
  font-size: 13px;
  color: #6b7280;
}
.filter-card {
  margin-bottom: 16px;
}
.filter-row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.muted {
  color: #c0c4cc;
}
</style>
