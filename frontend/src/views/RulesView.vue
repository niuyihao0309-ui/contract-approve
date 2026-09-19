<script setup>
import { onMounted, ref } from 'vue'
import { apiRules } from '../api'

const data = ref(null)
const loading = ref(false)
const opened = ref([])

const RISK_TAG = { high: 'danger', medium: 'warning', low: 'info' }

async function load() {
  loading.value = true
  try {
    data.value = await apiRules.list()
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <div class="page-head">
      <h2 class="page-title">规则库</h2>
      <span class="muted" v-if="data">共 {{ data.total }} 条规则，按分类组织</span>
    </div>

    <template v-if="data">
      <el-collapse v-model="opened">
        <el-collapse-item v-for="cat in data.categories" :key="cat" :name="cat">
          <template #title>
            <span class="cat-title">{{ cat }}</span>
            <el-tag size="small" type="info">{{ data.tree[cat].length }} 条</el-tag>
          </template>

          <el-table :data="data.tree[cat]" size="small">
            <el-table-column prop="rule_id" label="编号" width="140" />
            <el-table-column prop="name" label="规则名" min-width="200" show-overflow-tooltip />
            <el-table-column label="等级" width="90">
              <template #default="{ row }">
                <el-tag :type="RISK_TAG[row.risk_level] || 'info'" size="small">{{ row.risk_level_label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="触发方式" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="row.trigger === 'checker' ? 'success' : ''">{{ row.trigger }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="适用范围" min-width="180">
              <template #default="{ row }">
                <el-tag v-for="t in row.contract_type_labels" :key="t" size="small" type="info" class="mr">{{ t }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="80">
              <template #default="{ row }">
                <el-tag size="small" :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="description" label="说明" min-width="220" show-overflow-tooltip />
          </el-table>
        </el-collapse-item>
      </el-collapse>
    </template>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.page-title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.cat-title {
  font-weight: 600;
  margin-right: 8px;
}
.mr {
  margin-right: 4px;
}
.muted {
  color: #98a1b3;
  font-size: 13px;
}
</style>
