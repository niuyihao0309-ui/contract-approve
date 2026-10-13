<script setup>
/**
 * 智能审查工作台（需求 2.4.3）。
 *
 * 把前端五个模块串成一条法务复核动线：
 *   大盘/列表 → 工作台（文档视窗 + 风险卡片双栏联动）→ 协同回写 → 报告导出
 *
 * 双栏联动的两个方向（硬约束：不能破）：
 *   卡片 → 原文：jumpToRisk() 设 activeRiskId，HighlightDocument 负责滚动高亮
 *   原文 → 卡片：HighlightDocument 发 risk-click，这里选中并把卡片滚进视野
 *
 * 批次：一次上传多份文件时，每份文件各是一条任务（一任务一文档，见
 * app/services/batch_service.py）。同一批次的单会在左侧原文栏顶部出现
 * 「文件切换条」——点某一份就跳到那份的任务，右侧风险清单随之切换。
 */
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiDoc, apiReport, apiReview, apiTasks } from '../api'
import DocPreview from '../components/DocPreview.vue'
import HighlightDocument from '../components/HighlightDocument.vue'
import RiskPanel from '../components/RiskPanel.vue'
import WritebackPanel from '../components/WritebackPanel.vue'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
/** 用 computed 而不是一次性取值：批次切换是**同路由换参数**，组件不会重建 */
const taskId = computed(() => route.params.id)

const detail = ref(null)
const loading = ref(false)
const saving = ref(false)
const activeTab = ref('workbench')
const activeRiskId = ref('')
const docView = ref('text')   // text（文本层高亮） | raw（原件）
const preview = ref([])

const STATUS_TAG = { pending: 'info', parsing: 'warning', reviewing: 'warning', blocked: 'danger', completed: 'success' }
const RISK_TAG = { high: 'danger', medium: 'warning', low: 'info' }
const CONCLUSION_TAG = { reject: 'danger', rectify: 'warning', pass: 'success' }

const STAGE_LABEL = {
  received: '已接收',
  downloaded: '附件已就绪',
  parsed: '文档解析完成',
  metadata_done: '元数据抽取完成',
  clauses_segmented: '条款切分完成',
  rules_applied: '规则引擎命中完成',
  llm_reviewed: 'LLM 语义研判完成',
  suggested: '修改建议生成完成',
  aggregated: '风险等级聚合完成',
  reported: '审查完成',
}

async function load() {
  loading.value = true
  try {
    detail.value = await apiTasks.detail(taskId.value)
    preview.value = (await apiDoc.previewCapability(taskId.value).catch(() => ({ items: [] }))).items || []
  } finally {
    loading.value = false
  }
}

/** 批次内切换文件＝同路由换参数，组件会复用 → 必须自己监听并重载 */
watch(taskId, (id) => {
  if (!id) return
  activeRiskId.value = ''
  activeTab.value = 'workbench'
  docView.value = 'text'
  detail.value = null
  preview.value = []
  load()
})

function goBack() {
  router.push('/')
}

function fmtMoney(t) {
  if (t.amount == null) return '—'
  const cur = t.currency === 'CNY' ? '¥' : (t.currency || '') + ' '
  return cur + Number(t.amount).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function fmtTime(s) {
  return s ? String(s).replace('T', ' ').slice(0, 19) : '—'
}

// ======================================================================
// 双栏联动
// ======================================================================
function jumpToRisk(risk) {
  activeRiskId.value = risk?.id || ''
  activeTab.value = 'workbench'
  if (docView.value !== 'text') docView.value = 'text'
}

/** 原文点高亮 → 选中右侧卡片（把卡片滚进视野） */
async function onMarkClick(riskId) {
  activeRiskId.value = riskId
  await nextTick()
  document
    .querySelector(`[data-risk-card="${riskId}"]`)
    ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

/** 风险排序：高风险优先，供「上一处/下一处」与列表一致 */
const risks = computed(() => {
  const order = { high: 3, medium: 2, low: 1 }
  return [...(detail.value?.risk_items || [])].sort(
    (a, b) => order[b.risk_level] - order[a.risk_level] || (a.risk_no || 0) - (b.risk_no || 0)
  )
})
const doc = computed(() => detail.value?.doc || null)
const clauses = computed(() => detail.value?.clauses || [])
const metadata = computed(() => detail.value?.metadata || [])
const comments = computed(() => detail.value?.comments || [])
const parseError = computed(
  () => (detail.value?.files || []).find((f) => f.parse_error)?.parse_error || ''
)
/** 批次成员（多文件上传才有；单文件 / 历史数据为空数组） */
const batchItems = computed(() => detail.value?.batch?.items || [])

/** 批次内切换：跳到那一份的任务（同路由换参数，靠上面的 watch 重载） */
function goSibling(id) {
  if (!id || id === taskId.value) return
  router.push({ name: 'task-detail', params: { id } })
}

// ======================================================================
// 法务处理（风险卡片模块的采纳交互落到这里）
// ======================================================================
async function saveDecision({ risk, payload }) {
  saving.value = true
  try {
    const r = await apiTasks.decideRisk(taskId.value, risk.id, payload)
    ElMessage.success(r.message || '已更新处理结果')
    await load()
  } finally {
    saving.value = false
  }
}

// ======================================================================
// 管理员操作：跑流水线 / 重新解析（监控与阻塞处理）
// ======================================================================
async function runPipeline() {
  await apiReview.run(taskId.value)
  ElMessage.success('流水线执行完成')
  await load()
}

async function reparse() {
  await ElMessageBox.confirm('将忽略已有解析结果、从附件重新解析并审查，确定？', '提示', { type: 'warning' })
  await apiReview.reparse(taskId.value)
  ElMessage.success('已重新解析')
  await load()
}

/** 阻塞任务的一键重试（管理员） */
async function retryTask() {
  await ElMessageBox.confirm('确认触发重新审查？任务会回到待处理并重跑流水线。', '重新审查', {
    type: 'warning',
  })
  await apiTasks.retry(taskId.value, {})
  ElMessage.success('已重新审查')
  await load()
}

function downloadReport(fmt) {
  window.open(apiReport.downloadUrl(taskId.value, fmt), '_blank')
}

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <!-- 头部 -->
    <div class="detail-head">
      <el-button @click="goBack" text><el-icon><ArrowLeft /></el-icon>返回大盘</el-button>
      <template v-if="detail">
        <div class="head-main">
          <div class="title-line">
            <h2 class="title">{{ detail.title }}</h2>
            <el-tag :type="STATUS_TAG[detail.status] || 'info'">{{ detail.status_label || detail.status }}</el-tag>
            <el-tag v-if="detail.overall_risk_label" :type="RISK_TAG[detail.overall_risk] || 'info'">
              {{ detail.overall_risk_label }}
            </el-tag>
            <el-tag v-if="detail.conclusion_label" :type="CONCLUSION_TAG[detail.conclusion] || 'info'">
              结论：{{ detail.conclusion_label }}
            </el-tag>
            <el-tag v-if="detail.writeback_label" size="small" type="info">
              {{ detail.writeback_label }}
            </el-tag>
            <el-tag
              v-if="batchItems.length > 1"
              size="small"
              type="warning"
              effect="plain"
            >
              批次第 {{ detail.batch.index || 1 }} / {{ detail.batch.total }} 份
            </el-tag>
          </div>
          <div class="sub-line">
            <span v-if="detail.contract_no">编号 {{ detail.contract_no }}</span>
            <span v-if="detail.counterparty">相对方：{{ detail.counterparty }}</span>
            <span v-if="detail.department">{{ detail.department }}</span>
            <span v-if="detail.business_type_label">{{ detail.business_type_label }}</span>
            <span v-if="detail.created_at">创建 {{ fmtTime(detail.created_at) }}</span>
          </div>
        </div>
        <div class="head-actions">
          <el-progress
            v-if="['parsing', 'reviewing'].includes(detail.status)"
            type="circle"
            :width="46"
            :percentage="detail.progress || 0"
          />
          <el-button v-perm="'task.monitor'" @click="runPipeline" :disabled="['parsing', 'reviewing'].includes(detail.status)">
            跑流水线
          </el-button>
          <el-button v-perm="'task.retry'" @click="reparse">重新解析</el-button>
          <el-button v-perm="'task.retry'" type="warning" plain @click="retryTask">重新审查</el-button>
        </div>
      </template>
    </div>

    <!-- 阻塞提示 -->
    <el-alert
      v-if="detail?.status === 'blocked'"
      type="error"
      :closable="false"
      show-icon
      class="mb"
      :title="`任务受阻：${detail.block_reason_label || detail.block_reason || '未知原因'}`"
    >
      <template #default>
        {{ detail.block_detail || '可重传可复制文本的文档后点「重新审查」，或调整配置后重试。' }}
        <span class="muted">（处理权限：系统管理员）</span>
      </template>
    </el-alert>

    <template v-if="detail">
      <el-tabs v-model="activeTab">
        <!-- ============ 审查工作台（双栏） ============ -->
        <el-tab-pane label="审查工作台" name="workbench">
          <div class="workbench">
            <div class="col-doc">
              <!-- 批次文件切换条：本批多份文件时才有，点哪份就看哪份的原文与风险 -->
              <div v-if="batchItems.length > 1" class="batch-strip">
                <div
                  v-for="(it, i) in batchItems"
                  :key="it.task_id"
                  class="batch-chip"
                  :class="{ active: it.task_id === taskId }"
                  :title="it.filename"
                  :data-batch-task="it.task_id"
                  :data-batch-status="it.status"
                  @click="goSibling(it.task_id)"
                >
                  <span class="chip-seq">{{ i + 1 }}</span>
                  <span class="chip-name">{{ it.filename }}</span>
                  <span
                    v-if="it.risk_total"
                    class="chip-risk"
                    :class="`l-${it.overall_risk || 'low'}`"
                  >{{ it.risk_total }}</span>
                  <el-icon v-if="it.status === 'completed'" class="chip-ok"><CircleCheckFilled /></el-icon>
                  <el-icon v-else-if="it.status === 'blocked'" class="chip-bad"><WarningFilled /></el-icon>
                  <el-icon v-else class="is-loading chip-loading"><Loading /></el-icon>
                </div>
              </div>

              <div class="col-head">
                <span class="col-title">合同原文</span>
                <el-radio-group v-model="docView" size="small">
                  <el-radio-button value="text">文本层高亮</el-radio-button>
                  <el-radio-button value="raw" :disabled="!preview.length">原件视图</el-radio-button>
                </el-radio-group>
                <div class="spacer"></div>
                <span class="muted" v-if="doc">{{ doc.blocks?.length || 0 }} 段 / {{ doc.page_count || 1 }} 页</span>
              </div>

              <HighlightDocument
                v-if="docView === 'text'"
                :doc="doc"
                :risks="risks"
                :active-risk-id="activeRiskId"
                @risk-click="onMarkClick"
              />
              <DocPreview v-else :task-id="taskId" :files="detail.files || []" />
            </div>

            <div class="col-risk">
              <div class="col-head">
                <span class="col-title">风险清单与建议</span>
                <div class="spacer"></div>
                <span class="muted">{{ risks.length }} 条</span>
              </div>
              <RiskPanel
                :risks="risks"
                :active-risk-id="activeRiskId"
                :saving="saving"
                @locate="jumpToRisk"
                @decide="saveDecision"
              />
            </div>
          </div>
        </el-tab-pane>

        <!-- ============ 概览 ============ -->
        <el-tab-pane label="概览" name="overview">
          <el-row :gutter="16">
            <el-col :span="14">
              <el-card shadow="never" header="合同基本信息">
                <el-descriptions :column="2" border size="small">
                  <el-descriptions-item label="合同标题">{{ detail.title }}</el-descriptions-item>
                  <el-descriptions-item label="合同编号">{{ detail.contract_no || '—' }}</el-descriptions-item>
                  <el-descriptions-item label="相对方">{{ detail.counterparty || '—' }}</el-descriptions-item>
                  <el-descriptions-item label="合同金额">{{ fmtMoney(detail) }}</el-descriptions-item>
                  <el-descriptions-item label="业务类型">{{ detail.business_type_label || '—' }}</el-descriptions-item>
                  <el-descriptions-item label="我方身份">
                    {{ metadata.find((m) => m.field_key === 'party_a')?.value || '—' }}
                  </el-descriptions-item>
                  <el-descriptions-item label="送审人">{{ detail.applicant || '—' }}</el-descriptions-item>
                  <el-descriptions-item label="送审部门">{{ detail.department || '—' }}</el-descriptions-item>
                  <el-descriptions-item label="任务来源">{{ detail.source || '—' }}</el-descriptions-item>
                  <el-descriptions-item label="审批单号">{{ detail.approval_ticket_id || '—' }}</el-descriptions-item>
                  <el-descriptions-item label="重试次数">{{ detail.retry_count || 0 }}</el-descriptions-item>
                  <el-descriptions-item label="当前阶段">
                    {{ STAGE_LABEL[detail.stage] || detail.stage || '—' }}
                  </el-descriptions-item>
                </el-descriptions>

                <div class="meta-table" v-if="metadata.length">
                  <div class="meta-title">核心元数据（抽取自原文，可溯源）</div>
                  <div class="meta-row" v-for="m in metadata" :key="m.field_key">
                    <span class="meta-key">{{ m.field_label }}</span>
                    <span class="meta-val">{{ m.value || '—' }}</span>
                    <span class="meta-loc" v-if="m.location_label">{{ m.location_label }}</span>
                  </div>
                </div>
              </el-card>
            </el-col>

            <el-col :span="10">
              <el-card shadow="never" header="综合审查结论">
                <div class="conclusion-big" v-if="detail.risk_summary">
                  <div class="risk-badge">
                    <span v-if="detail.overall_risk_label" :class="['risk-dot', detail.overall_risk]"></span>
                    <strong>{{ detail.overall_risk_label || '无风险' }}</strong>
                    <span class="sep">·</span>
                    <strong>{{ detail.conclusion_label || '通过' }}</strong>
                  </div>
                  <p class="summary-text">{{ detail.risk_summary }}</p>
                </div>
                <el-empty v-else description="暂无审查结论" :image-size="60" />

                <div v-if="detail.focus_points?.length" class="focus-box">
                  <div class="focus-title">审批关注要点</div>
                  <ol>
                    <li v-for="(p, i) in detail.focus_points" :key="i">{{ p }}</li>
                  </ol>
                </div>
              </el-card>

              <el-card shadow="never" header="附件与原件" class="mt">
                <div v-for="f in detail.files" :key="f.id" class="file-row">
                  <el-icon><Document /></el-icon>
                  <span class="file-name">{{ f.filename }}</span>
                  <el-tag size="small" type="info">{{ f.file_kind }}</el-tag>
                  <el-tag v-if="f.from_cache" size="small" type="success">指纹缓存</el-tag>
                  <el-tag v-if="f.parsed" size="small" type="success">{{ f.parser }}</el-tag>
                  <el-tag v-else size="small" type="danger">未解析</el-tag>
                  <el-button
                    v-perm="'doc.preview'"
                    link
                    type="primary"
                    size="small"
                    tag="a"
                    :href="`/api/v1/tasks/${taskId}/files/${f.id}/raw`"
                    target="_blank"
                  >
                    打开原件
                  </el-button>
                </div>
                <div v-if="parseError" class="parse-error">解析错误：{{ parseError }}</div>
              </el-card>
            </el-col>
          </el-row>
        </el-tab-pane>

        <!-- ============ 条款切分 ============ -->
        <el-tab-pane :label="`条款切分（${clauses.length}）`" name="clauses">
          <el-table :data="clauses" size="small">
            <el-table-column prop="title" label="条款" min-width="200" show-overflow-tooltip />
            <el-table-column prop="clause_type_label" label="类型" width="130" />
            <el-table-column label="页码范围" width="110">
              <template #default="{ row }">p{{ row.page_start }}-{{ row.page_end }}</template>
            </el-table-column>
            <el-table-column label="置信度" width="100">
              <template #default="{ row }">{{ (row.confidence * 100).toFixed(0) }}%</template>
            </el-table-column>
            <el-table-column prop="content" label="内容" min-width="320" show-overflow-tooltip />
          </el-table>
        </el-tab-pane>

        <!-- ============ 报告与回写 ============ -->
        <el-tab-pane label="报告与回写" name="report">
          <WritebackPanel
            :task-id="taskId"
            :writeback="detail.writeback"
            :completed="detail.status === 'completed'"
            @changed="load"
          />

          <el-card shadow="never" header="协同记录" class="mt">
            <el-timeline v-if="comments.length">
              <el-timeline-item
                v-for="c in comments"
                :key="c.id"
                :timestamp="fmtTime(c.created_at)"
                :type="c.kind === 'action' ? 'primary' : c.role === 'lawyer' ? 'success' : 'info'"
              >
                <span class="comment-author">{{ c.author }}</span>
                <el-tag size="small" type="info" class="kind">{{ c.kind }}</el-tag>
                <span class="comment-content">{{ c.content }}</span>
              </el-timeline-item>
            </el-timeline>
            <el-empty v-else description="暂无记录" :image-size="60" />
          </el-card>
        </el-tab-pane>
      </el-tabs>
    </template>
  </div>
</template>

<style scoped>
.detail-head {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.head-main {
  flex: 1;
  min-width: 320px;
}
.title-line {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.sub-line {
  margin-top: 6px;
  color: #6b7280;
  font-size: 13px;
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.head-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.mb {
  margin-bottom: 12px;
}
.mt {
  margin-top: 16px;
}

/* 双栏工作台 */
.workbench {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(360px, 460px);
  gap: 16px;
  align-items: start;
}
@media (max-width: 1280px) {
  .workbench {
    grid-template-columns: 1fr;
  }
}
.col-doc,
.col-risk {
  min-width: 0;
}
.col-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.col-title {
  font-size: 14px;
  font-weight: 600;
}
.spacer {
  flex: 1;
}
.muted {
  color: #98a1b3;
  font-size: 12px;
}

/* 批次文件切换条（本批多份文件时出现在原文栏顶部） */
.batch-strip {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding-bottom: 8px;
  margin-bottom: 8px;
  border-bottom: 1px dashed #e5e7eb;
}
.batch-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
  max-width: 240px;
  padding: 5px 10px;
  border: 1px solid #e5e7eb;
  border-radius: 999px;
  background: #f8fafc;
  cursor: pointer;
  font-size: 12px;
  transition: all 0.15s;
}
.batch-chip:hover {
  border-color: var(--el-color-primary);
}
.batch-chip.active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  font-weight: 600;
}
.chip-seq {
  color: #98a1b3;
}
.chip-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chip-risk {
  font-weight: 700;
  padding: 0 5px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.05);
}
.chip-risk.l-high {
  color: var(--risk-high);
}
.chip-risk.l-medium {
  color: var(--risk-medium);
}
.chip-risk.l-low {
  color: var(--risk-low);
}
.chip-ok {
  color: #16a34a;
}
.chip-bad {
  color: #e5484d;
}
.chip-loading {
  color: #f5a524;
}

/* 概览 */
.meta-table {
  margin-top: 16px;
  border-top: 1px solid #f0f0f0;
}
.meta-title {
  font-size: 13px;
  font-weight: 600;
  padding: 10px 4px 2px;
}
.meta-row {
  display: flex;
  gap: 12px;
  padding: 8px 4px;
  border-bottom: 1px solid #f5f5f5;
  font-size: 13px;
}
.meta-key {
  width: 150px;
  color: #6b7280;
  flex-shrink: 0;
}
.meta-val {
  flex: 1;
  color: #1f2329;
}
.meta-loc {
  color: #98a1b3;
  font-size: 12px;
}
.conclusion-big {
  text-align: left;
}
.risk-badge {
  font-size: 16px;
  margin-bottom: 8px;
}
.risk-badge .sep {
  margin: 0 8px;
  color: #c0c4cc;
}
.summary-text {
  color: #4b5563;
  line-height: 1.7;
}
.focus-box {
  margin-top: 16px;
  background: #f8fafc;
  border-radius: 8px;
  padding: 12px 16px;
}
.focus-title {
  font-weight: 600;
  margin-bottom: 6px;
}
.focus-box ol {
  margin: 0;
  padding-left: 20px;
  color: #4b5563;
}
.focus-box li {
  margin: 4px 0;
}
.file-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
}
.file-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.parse-error {
  margin-top: 8px;
  font-size: 12px;
  color: #b91c1c;
  word-break: break-all;
}
.comment-author {
  font-weight: 600;
  margin-right: 8px;
}
.kind {
  margin-right: 8px;
}
.comment-content {
  color: #4b5563;
}
</style>
