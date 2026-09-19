<script setup>
import { onMounted, ref } from 'vue'
import { apiReview } from '../api'

const health = ref(null)
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    health.value = await apiReview.health()
  } finally {
    loading.value = false
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
  color: #6b7280;
}
.kv-title {
  font-size: 13px;
  color: #6b7280;
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
  color: #98a1b3;
  font-size: 12px;
  margin-top: 8px;
}
</style>
