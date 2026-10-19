<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiApprovals, apiReview } from '../api'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()

const health = ref(null)
const loading = ref(false)

// 审批流对接参数（mock）—— 系统管理员职责
const mock = reactive({ fail_rate: 0, latency: 0, timeout_at: 0 })
const stats = ref(null)
const busy = ref(false)

async function load() {
  loading.value = true
  try {
    health.value = await apiReview.health()
    stats.value = await apiApprovals.mockStats().catch(() => null)
  } finally {
    loading.value = false
  }
}

async function saveMock() {
  busy.value = true
  try {
    const r = await apiApprovals.mockConfig({
      fail_rate: Number(mock.fail_rate) || 0,
      latency: Number(mock.latency) || 0,
      timeout_at: Number(mock.timeout_at) || 0,
    })
    ElMessage.success(r.message)
    stats.value = await apiApprovals.mockStats().catch(() => null)
  } finally {
    busy.value = false
  }
}

async function resetMock() {
  await ElMessageBox.confirm('会清空模拟审批单与评论区数据，确定？', '重置模拟审批系统', {
    type: 'warning',
  })
  const r = await apiApprovals.mockReset(true)
  ElMessage.success(r.message)
  await load()
}

async function pollOnce() {
  busy.value = true
  try {
    const r = await apiApprovals.poll({ run_now: false, limit: 10 })
    ElMessage.success(r.message)
    stats.value = await apiApprovals.mockStats().catch(() => null)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <div class="page-head">
      <h2 class="page-title">审查引擎状态</h2>
      <el-button @click="load"><el-icon><Refresh /></el-icon>刷新</el-button>
    </div>

    <template v-if="health">
      <el-row :gutter="16">
        <el-col :span="8">
          <el-card shadow="never" header="规则引擎">
            <div class="kv"><span>规则总数</span><strong>{{ health.rules.total }}</strong></div>
            <div class="kv"><span>已启用</span><strong>{{ health.rules.enabled }}</strong></div>
            <div class="kv"><span>规则集版本</span><strong>{{ health.rules.version }}</strong></div>
            <div class="checkers">
              <div class="kv-title">判定器（checker）</div>
              <el-tag v-for="c in health.rules.checkers" :key="c" size="small" class="checker-tag">{{ c }}</el-tag>
            </div>
          </el-card>
        </el-col>

        <el-col :span="8">
          <el-card shadow="never" header="大模型（LLM）">
            <div class="kv">
              <span>状态</span>
              <el-tag :type="health.llm.enabled ? 'success' : 'info'">
                {{ health.llm.enabled ? '已启用' : '未启用（仅规则引擎）' }}
              </el-tag>
            </div>
            <div class="kv"><span>模型</span><strong>{{ health.llm.model || '—' }}</strong></div>
            <div class="kv"><span>Base URL</span><strong class="mono">{{ health.llm.base_url || '—' }}</strong></div>
          </el-card>

          <el-card shadow="never" header="OCR 引擎" class="mt">
            <div class="kv">
              <span>状态</span>
              <el-tag :type="health.ocr.available ? 'success' : 'danger'">
                {{ health.ocr.available ? '可用' : '不可用' }}
              </el-tag>
            </div>
            <div v-if="!health.ocr.available" class="muted">{{ health.ocr.reason }}</div>
          </el-card>
        </el-col>

        <el-col :span="8">
          <el-card shadow="never" header="我方身份">
            <div class="kv"><span>代码</span><strong>{{ health.our_side.code }}</strong></div>
            <div class="kv"><span>指称</span><strong>{{ health.our_side.label }}</strong></div>
          </el-card>

          <el-card shadow="never" header="标准示范条款库" class="mt">
            <div class="kv"><span>示范条款总数</span><strong>{{ health.clause_templates.total }}</strong></div>
            <div class="kv"><span>版本</span><strong>{{ health.clause_templates.version }}</strong></div>
          </el-card>
        </el-col>
      </el-row>

      <!-- ================= 审批流对接配置（系统管理员） ================= -->
      <el-card shadow="never" header="审批系统对接配置" class="mt-block">
        <template #header>
          <div class="card-head">
            <span>审批系统对接配置</span>
            <el-tag size="small" effect="plain">需管理员权限</el-tag>
          </div>
        </template>

        <template v-if="auth.has('admin.approval')">
          <el-alert type="info" :closable="false" show-icon class="mb">
            <template #default>
              这是本机模拟通道：<strong>失败率 / 延迟 / 超时</strong>用于复现
              「接口异常 → 回写失败 → 重试成功」链路。真实对接时替换为实际网关地址、鉴权凭据与超时阈值。
            </template>
          </el-alert>

          <el-row :gutter="16">
            <el-col :span="12">
              <div class="slider-row">
                <span class="sl-label">接口失败率</span>
                <el-slider v-model="mock.fail_rate" :min="0" :max="1" :step="0.05" show-input />
              </div>
              <div class="slider-row">
                <span class="sl-label">接口延迟（秒）</span>
                <el-slider v-model="mock.latency" :min="0" :max="10" :step="0.5" show-input />
              </div>
              <div class="slider-row">
                <span class="sl-label">超时阈值（秒，0 = 不超时）</span>
                <el-slider v-model="mock.timeout_at" :min="0" :max="10" :step="0.5" show-input />
              </div>
              <div class="btn-row">
                <el-button type="primary" :loading="busy" @click="saveMock">保存对接参数</el-button>
                <el-button :loading="busy" @click="pollOnce">立即拉取待办</el-button>
                <el-button type="danger" plain @click="resetMock">重置模拟数据</el-button>
              </div>
            </el-col>

            <el-col :span="12">
              <div class="kv" v-if="stats">
                <span>审批单总数</span><strong>{{ stats.tickets ?? stats.total ?? '—' }}</strong>
              </div>
              <div class="kv" v-if="stats">
                <span>回写评论数</span><strong>{{ stats.comments ?? '—' }}</strong>
              </div>
              <div class="kv">
                <span>当前模拟配置</span>
                <strong class="mono">
                  失败率 {{ (mock.fail_rate * 100).toFixed(0) }}% / 延迟 {{ mock.latency }}s /
                  超时 {{ mock.timeout_at || '关' }}s
                </strong>
              </div>
              <div class="kv">
                <span>连通状态</span>
                <el-tag size="small" type="success">模拟通道可用</el-tag>
              </div>
              <el-button link type="primary" @click="load">刷新统计</el-button>
            </el-col>
          </el-row>
        </template>

        <el-empty v-else description="审批流对接参数由系统管理员维护" :image-size="70" />
      </el-card>
    </template>
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
.kv {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid #f5f5f5;
  font-size: 14px;
}
.kv span {
  color: var(--ink-3);
}
.kv-title {
  font-size: 13px;
  color: var(--ink-3);
  margin: 12px 0 8px;
}
.checker-tag {
  margin: 0 6px 6px 0;
}
.mt {
  margin-top: 16px;
}
.mono {
  font-family: monospace;
  font-size: 12px;
}
.muted {
  color: var(--ink-3);
  font-size: 12px;
  margin-top: 8px;
}
.mt-block {
  margin-top: 16px;
}
.mb {
  margin-bottom: 14px;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.slider-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 6px;
}
.sl-label {
  width: 190px;
  flex-shrink: 0;
  font-size: 13px;
  color: var(--ink-3);
}
.slider-row :deep(.el-slider) {
  flex: 1;
}
.btn-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 12px;
}
</style>
