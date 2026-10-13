<script setup>
/**
 * 示例合同快速体验：直接读取后端 samples/ 目录，一键建单 + 跑审查。
 *
 * 走 JSON 建单接口（``file_path`` 指向服务端本机文件）而不是 multipart —— 
 * 示例文件本来就在服务器同机上，省一次上传。真实业务走 UploadView 的上传流程。
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { apiSamples, apiTasks } from '../api'

const router = useRouter()

const loading = ref(false)
const files = ref([])
const busy = ref('')

async function load() {
  loading.value = true
  try {
    const r = await apiSamples.list()
    files.value = r.files || []
  } catch (e) {
    files.value = []
  } finally {
    loading.value = false
  }
}

function guessType(name) {
  if (name.includes('销售')) return 'sales'
  if (name.includes('劳动')) return 'labor'
  if (name.includes('服务')) return 'service'
  return 'purchase'
}

async function useOne(item) {
  busy.value = item.path
  try {
    const created = await apiTasks.create({
      title: item.name.replace(/\.[^.]+$/, ''),
      business_type: guessType(item.name),
      // 申请人不传：后端按登录身份落库，且示例文件本来就在服务器同机上
      file_path: item.path,
      run_now: true,
    })
    ElMessage.success(`已建单并完成审查（任务 ${created.id.slice(0, 8)}）`)
    router.push({ name: 'task-detail', params: { id: created.id } })
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <el-empty v-if="!files.length" description="本机 samples/ 目录下没有示例合同" :image-size="60" />
    <div v-else class="sample-list">
      <div v-for="f in files" :key="f.path" class="sample-row">
        <el-icon><Document /></el-icon>
        <span class="sample-name" :title="f.path">{{ f.name }}</span>
        <span class="sample-ext">{{ f.ext }}</span>
        <el-button
          size="small"
          type="primary"
          link
          :loading="busy === f.path"
          @click="useOne(f)"
        >
          用它建单
        </el-button>
      </div>
    </div>
    <div class="tip">包含标准版与风险版，以及空正文、模糊扫描等异常用例。</div>
  </div>
</template>

<style scoped>
.sample-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 280px;
  overflow: auto;
}
.sample-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  padding: 4px 0;
  border-bottom: 1px solid #f5f6f8;
}
.sample-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sample-ext {
  color: #98a1b3;
}
.tip {
  margin-top: 8px;
  font-size: 11px;
  color: #98a1b3;
  line-height: 1.6;
}
</style>
