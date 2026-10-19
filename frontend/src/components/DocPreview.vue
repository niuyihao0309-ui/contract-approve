<script setup>
/**
 * 原件视图（文档视窗模块的「PDF/Word 在线预览 + 扫描件」部分）。
 *
 * 三种渲染器：
 *   pdf   → 浏览器内置 PDF 阅读器（iframe）
 *   image → 扫描件直接当图片看
 *   其它  → 浏览器不能内嵌渲染（DOCX），退化为下载 + 提示看文本层
 *
 * 高亮只做在文本层（原件是高亮的**证据来源**，不是高亮载体），
 * 两个视图用同一套 char 区间，所以能来回对照。
 */
import { computed, ref, watch } from 'vue'

import { apiDoc } from '../api'

const props = defineProps({
  taskId: { type: String, required: true },
  files: { type: Array, default: () => [] },
})

const capability = ref([])
const loading = ref(false)
const activeId = ref('')

const active = computed(() => capability.value.find((c) => c.id === activeId.value) || null)

async function load() {
  if (!props.taskId) return
  loading.value = true
  try {
    const data = await apiDoc.previewCapability(props.taskId)
    capability.value = data.items || []
    if (!activeId.value && capability.value.length) activeId.value = capability.value[0].id
  } catch (e) {
    capability.value = []
  } finally {
    loading.value = false
  }
}

watch(() => props.taskId, load, { immediate: true })

function href(c) {
  return apiDoc.rawUrl(props.taskId, c.id)
}
</script>

<template>
  <div class="doc-preview" v-loading="loading">
    <el-empty v-if="!capability.length" description="没有可预览的附件" :image-size="70" />

    <template v-else>
      <div v-if="capability.length > 1" class="tabs">
        <el-radio-group v-model="activeId" size="small">
          <el-radio-button v-for="c in capability" :key="c.id" :value="c.id">
            {{ c.filename }}
          </el-radio-button>
        </el-radio-group>
      </div>

      <div class="viewer">
        <template v-if="active?.viewer === 'pdf'">
          <iframe :src="href(active)" class="frame" title="PDF 原件预览"></iframe>
        </template>

        <template v-else-if="active?.viewer === 'image'">
          <div class="img-box">
            <img :src="href(active)" :alt="active.filename" />
            <div class="img-tip">扫描件原件（OCR 文本层见「文本层高亮」视图）</div>
          </div>
        </template>

        <template v-else>
          <el-result icon="info" :title="`${active?.filename} 无法内嵌预览`">
            <template #sub-title>
              Word 文档浏览器不能直接渲染，两种替代方式：
              <br />
              1）用「文本层高亮」视图看正文与风险高亮（推荐，定位更准）；
              <br />
              2）下载原件用本地 Word 打开对照。
            </template>
            <template #extra>
              <el-button type="primary" tag="a" :href="active ? href(active) : '#'" target="_blank">
                下载原件
              </el-button>
            </template>
          </el-result>
        </template>
      </div>
    </template>
  </div>
</template>

<style scoped>
.doc-preview {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.tabs {
  overflow-x: auto;
}
.viewer {
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
  min-height: 320px;
}
.frame {
  width: 100%;
  height: 72vh;
  border: none;
  display: block;
}
.img-box {
  padding: 12px;
  text-align: center;
  max-height: 72vh;
  overflow: auto;
}
.img-box img {
  max-width: 100%;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.12);
  border-radius: 4px;
}
.img-tip {
  margin-top: 8px;
  font-size: 12px;
  color: var(--ink-3);
}
</style>
