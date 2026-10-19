<script setup>
/**
 * 协同回写模块（需求 2.4.5）。
 *
 * 职责：法务批注表单、回写结果状态轮询、审查报告导出预览。
 *
 * 回写是异步的（后端 writing → success/failed），所以提交后必须**轮询状态**，
 * 不能提交完就弹个「成功」了事 —— 失败要能看见并给重试入口。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiReport, apiTasks, apiWriteback } from '../api'
import { useAuthStore } from '../stores/auth'

const props = defineProps({
  taskId: { type: String, required: true },
  /** 任务详情里的 writeback 字段（父级每次 load 后传进来） */
  writeback: { type: Object, default: null },
  /** 任务未完成审查时不允许回写 / 导出 */
  completed: { type: Boolean, default: false },
})

const emit = defineEmits(['changed'])

const auth = useAuthStore()

const notes = ref('')
const submitting = ref(false)
const polling = ref(false)
const wbState = ref(props.writeback)

watch(
  () => props.writeback,
  (v) => {
    wbState.value = v
  }
)

const WB_TAG = { success: 'success', failed: 'danger', writing: 'warning', not_written: 'info' }
const WB_LABEL = {
  success: '回写成功',
  failed: '回写失败',
  writing: '回写中',
  not_written: '未回写',
}

const wbStatus = computed(() => wbState.value?.status || 'not_written')
const wbLabel = computed(() => wbState.value?.status_label || WB_LABEL[wbStatus.value])

// ======================================================================
// 回写 + 状态轮询
// ======================================================================
let timer = null
const pollCount = ref(0)
const MAX_POLL = 15

function stopPoll() {
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
  polling.value = false
}

async function pollOnce() {
  pollCount.value += 1
  const r = await apiWriteback.status(props.taskId).catch(() => null)
  if (r) {
    wbState.value = r
    if (r.status === 'success') {
      stopPoll()
      ElMessage.success('回写成功，审批单评论区已更新')
      emit('changed')
      return
    }
    if (r.status === 'failed') {
      stopPoll()
      ElMessage.error(r.error || '回写失败，可修改意见后重试')
      emit('changed')
      return
    }
  }
  if (pollCount.value >= MAX_POLL) {
    stopPoll()
    ElMessage.warning('回写状态轮询超时，请稍后手动刷新查看')
    return
  }
  timer = setTimeout(pollOnce, 2000)
}

async function doWriteback() {
  if (!props.completed) {
    ElMessage.warning('任务尚未完成审查，无法回写')
    return
  }
  submitting.value = true
  try {
    // 操作人（作者）由后端从登录态取，前端不传
    const r = await apiWriteback.submit(props.taskId, {
      lawyer_notes: notes.value,
    })
    if (r?.duplicated) {
      ElMessage.warning(r.message || '该内容已回写过（幂等命中，未重复写入）')
    } else {
      ElMessage.info(r?.message || '回写已提交，正在确认结果…')
    }
    emit('changed')
    // 提交后进入轮询：回写状态由后端状态机推进，前端只观察
    stopPoll()
    pollCount.value = 0
    polling.value = true
    timer = setTimeout(pollOnce, 800)
  } finally {
    submitting.value = false
  }
}

// ======================================================================
// 报告预览 / 导出
// ======================================================================
const reportVisible = ref(false)
const reportText = ref('')
const reportKind = ref('report')   // report | writeback
const reportLoading = ref(false)

async function openPreview(kind) {
  reportKind.value = kind
  reportLoading.value = true
  reportVisible.value = true
  try {
    reportText.value =
      kind === 'report'
        ? await apiReport.preview(props.taskId)
        : await apiReport.writebackText(props.taskId)
  } finally {
    reportLoading.value = false
  }
}

/** 极简 Markdown 渲染（够用即可，不引第三方依赖） */
const reportHtml = computed(() => renderMarkdown(reportText.value))
const writebackHtml = computed(() => renderMarkdown(reportText.value))

function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function renderMarkdown(md) {
  if (!md) return ''
  const lines = String(md).split(/\r?\n/)
  const out = []
  let inList = false
  let inTable = false

  const closeList = () => {
    if (inList) {
      out.push('</ul>')
      inList = false
    }
  }
  const closeTable = () => {
    if (inTable) {
      out.push('</tbody></table>')
      inTable = false
    }
  }

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '')
    const inline = (t) =>
      esc(t)
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/`(.+?)`/g, '<code>$1</code>')

    if (/^\s*$/.test(line)) {
      closeList()
      closeTable()
      continue
    }
    // 表格
    if (/^\s*\|/.test(line)) {
      const cells = line.split('|').slice(1, -1).map((c) => c.trim())
      if (cells.every((c) => /^-{2,}$/.test(c))) continue
      if (!inTable) {
        closeList()
        out.push('<table class="md-table"><tbody>')
        inTable = true
      }
      out.push(`<tr>${cells.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`)
      continue
    }
    closeTable()

    const h = line.match(/^(#{1,6})\s+(.*)$/)
    if (h) {
      closeList()
      const lv = Math.min(h[1].length + 2, 6)
      out.push(`<h${lv}>${inline(h[2])}</h${lv}>`)
      continue
    }
    if (/^\s*[-*]\s+/.test(line)) {
      if (!inList) {
        out.push('<ul>')
        inList = true
      }
      out.push(`<li>${inline(line.replace(/^\s*[-*]\s+/, ''))}</li>`)
      continue
    }
    closeList()
    out.push(`<p>${inline(line)}</p>`)
  }
  closeList()
  closeTable()
  return out.join('\n')
}

function download(fmt) {
  if (!props.completed) {
    ElMessage.warning('任务尚未完成审查，无法导出报告')
    return
  }
  window.open(apiReport.downloadUrl(props.taskId, fmt), '_blank')
}

async function loadReportFiles() {
  const r = await apiReport.files(props.taskId)
  if (!r.total) {
    ElMessage.info('还没有生成过报告文件，先导出一次')
    return
  }
  await ElMessageBox.alert(
    r.files
      .map((f) => `${f.name}（${(f.size / 1024).toFixed(1)} KB）`)
      .join('<br/>'),
    `已生成 ${r.total} 个报告文件`,
    { dangerouslyUseHTMLString: true }
  )
}

// ======================================================================
// 批注
// ======================================================================
const commentText = ref('')
const commentSaving = ref(false)

async function addComment() {
  const content = commentText.value.trim()
  if (!content) {
    ElMessage.warning('批注内容不能为空')
    return
  }
  commentSaving.value = true
  try {
    // 作者与角色由后端按登录身份落库，前端不传（传了也会被忽略）
    await apiTasks.addComment(props.taskId, { content })
    ElMessage.success('批注已保存')
    commentText.value = ''
    emit('changed')
  } finally {
    commentSaving.value = false
  }
}

onBeforeUnmount(stopPoll)
</script>

<template>
  <div class="wb-panel">
    <el-row :gutter="16">
      <!-- 报告导出预览 -->
      <el-col :span="12">
        <el-card shadow="never" header="审查报告">
          <p class="muted">
            报告内容 = 合同基本信息 + 综合结论 + 风险清单明细（含定位与示范条款）。
            先预览确认，再导出。
          </p>
          <div class="btn-row">
            <el-button
              v-perm="'report.view'"
              :disabled="!completed"
              @click="openPreview('report')"
            >
              <el-icon><View /></el-icon>预览报告
            </el-button>
            <el-button v-perm="'report.view'" :disabled="!completed" @click="openPreview('writeback')">
              预览回写文本
            </el-button>
          </div>
          <div class="btn-row">
            <el-button
              v-perm="'report.download'"
              type="primary"
              :disabled="!completed"
              @click="download('md')"
            >
              导出 Markdown
            </el-button>
            <el-button
              v-perm="'report.download'"
              type="primary"
              :disabled="!completed"
              @click="download('pdf')"
            >
              导出 PDF
            </el-button>
            <el-button v-perm="'report.view'" link @click="loadReportFiles">历史文件</el-button>
          </div>
          <el-alert
            v-if="!completed"
            type="info"
            :closable="false"
            show-icon
            title="任务完成审查后才能预览与导出报告"
            class="mt"
          />
        </el-card>
      </el-col>

      <!-- 回写 -->
      <el-col :span="12">
        <el-card shadow="never" header="回写审批系统">
          <div class="wb-status">
            <el-tag size="small" :type="WB_TAG[wbStatus] || 'info'">{{ wbLabel }}</el-tag>
            <el-tag v-if="wbState?.attempts || wbState?.attempt" size="small" type="info">
              第 {{ wbState?.attempts || wbState?.attempt }} 次
            </el-tag>
            <span v-if="wbState?.comment_id" class="mono">comment_id={{ wbState.comment_id }}</span>
            <span v-if="polling" class="polling">
              <el-icon class="is-loading"><Loading /></el-icon>轮询回写结果（{{ pollCount }}/{{ MAX_POLL }}）
            </span>
          </div>
          <div v-if="wbState?.error" class="wb-error">{{ wbState.error }}</div>

          <el-input
            v-model="notes"
            v-perm="'comment.add'"
            type="textarea"
            :rows="3"
            placeholder="法务批注（会拼进意见文本；留空则自动汇总法务评论）"
          />

          <div class="btn-row mt">
            <el-button
              v-perm="'writeback.trigger'"
              type="primary"
              :loading="submitting"
              :disabled="!completed"
              @click="doWriteback"
            >
              一键回写
            </el-button>
            <el-button v-perm="'comment.add'" :loading="commentSaving" @click="addComment">
              仅保存为批注
            </el-button>
          </div>

          <div v-if="!auth.has('writeback.trigger')" class="readonly-tip">
            <el-icon><InfoFilled /></el-icon>
            当前身份（{{ auth.role.name }}）无回写权限：可以查看回写状态，触发由法务审查人执行。
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 报告预览弹窗 -->
    <el-dialog
      v-model="reportVisible"
      :title="reportKind === 'report' ? '审查报告预览' : '回写意见文本预览'"
      width="820px"
      top="6vh"
    >
      <div v-loading="reportLoading" class="report-box">
        <div class="md-render" v-html="reportKind === 'report' ? reportHtml : writebackHtml"></div>
      </div>
      <template #footer>
        <el-button @click="reportVisible = false">关闭</el-button>
        <el-button
          v-perm="'report.download'"
          type="primary"
          :disabled="!completed"
          @click="download('md')"
        >
          导出 Markdown
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.wb-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.muted {
  color: var(--ink-3);
  font-size: 12px;
  line-height: 1.7;
  margin: 0 0 10px;
}
.btn-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.mt {
  margin-top: 10px;
}
.wb-status {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}
.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  color: var(--ink-3);
}
.polling {
  font-size: 12px;
  color: #f5a524;
  display: flex;
  align-items: center;
  gap: 4px;
}
.wb-error {
  margin-bottom: 10px;
  padding: 8px 12px;
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid #fecaca;
  border-radius: 6px;
  color: var(--risk-high);
  font-size: 12px;
}
.readonly-tip {
  margin-top: 10px;
  font-size: 12px;
  color: var(--ink-3);
  display: flex;
  align-items: center;
  gap: 6px;
}
.report-box {
  max-height: 62vh;
  overflow: auto;
  background: var(--bg-hover);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 16px 20px;
}
.md-render :deep(h3) {
  font-size: 16px;
  margin: 14px 0 6px;
}
.md-render :deep(h4) {
  font-size: 14px;
  margin: 12px 0 6px;
}
.md-render :deep(p) {
  margin: 6px 0;
  line-height: 1.8;
  font-size: 13px;
  color: var(--ink-2);
}
.md-render :deep(ul) {
  margin: 6px 0;
  padding-left: 20px;
}
.md-render :deep(li) {
  font-size: 13px;
  line-height: 1.8;
  color: var(--ink-2);
}
.md-render :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 8px 0;
}
.md-render :deep(td) {
  border: 1px solid var(--line);
  padding: 5px 8px;
  font-size: 12px;
  color: var(--ink-2);
}
.md-render :deep(code) {
  background: var(--bg-hover);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 12px;
}
</style>
