<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiTasks, apiReview, apiWriteback, apiReport } from '../api'
import HighlightDocument from '../components/HighlightDocument.vue'

const route = useRoute()
const router = useRouter()
const taskId = route.params.id

const detail = ref(null)
const loading = ref(false)
const activeTab = ref('overview')
const activeRiskId = ref('')

const STATUS_TAG = { pending: 'info', parsing: 'warning', reviewing: 'warning', blocked: 'danger', completed: 'success' }
const RISK_TAG = { high: 'danger', medium: 'warning', low: 'info' }
const CONCLUSION_TAG = { reject: 'danger', rectify: 'warning', pass: 'success' }
const DECISION_OPTIONS = [
  { value: 'open', label: '待处理' },
  { value: 'accepted', label: '采纳' },
  { value: 'adjusted', label: '调整后采纳' },
  { value: 'rejected', label: '驳回' },
]

async function load() {
  loading.value = true
  try {
    detail.value = await apiTasks.detail(taskId)
  } finally {
    loading.value = false
  }
}

function goBack() {
  router.push('/')
}

function fmtMoney(t) {
  if (t.amount == null) return '—'
  const cur = t.currency === 'CNY' ? '¥' : (t.currency || '') + ' '
  return cur + Number(t.amount).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

// ---- 风险定位：点卡片 → 切原文 tab + 滚动高亮 ----
function jumpToRisk(risk) {
  activeRiskId.value = risk.id
  activeTab.value = 'document'
}

// ---- 法务处理 ----
async function saveDecision(risk) {
  const payload = {
    decision: risk.decision,
    by: '李静（法务）',
    note: risk._note || '',
    edited_text: risk._editedText || undefined,
  }
  await apiTasks.decideRisk(taskId, risk.id, payload)
  ElMessage.success('已更新处理结果')
  await load()
}

// ---- 跑流水线 / 重试 ----
async function runPipeline() {
  await apiReview.run(taskId)
  ElMessage.success('流水线执行完成')
  await load()
}

async function reparse() {
  await ElMessageBox.confirm('将忽略已有解析结果、从附件重新解析并审查，确定？', '提示', { type: 'warning' })
  await apiReview.reparse(taskId)
  ElMessage.success('已重新解析')
  await load()
}

// ---- 回写 ----
const writeback = reactive({ lawyerNotes: '', submitting: false })

async function doWriteback() {
  writeback.submitting = true
  try {
    const r = await apiWriteback.submit(taskId, {
      lawyer_notes: writeback.lawyerNotes,
      by: '李静（法务）',
    })
    if (r.duplicated) ElMessage.warning(r.message)
    else ElMessage.success(r.message || '回写成功')
    await load()
  } finally {
    writeback.submitting = false
  }
}

function downloadReport(fmt) {
  window.open(apiReport.downloadUrl(taskId, fmt), '_blank')
}

const risks = computed(() => detail.value?.risk_items || [])
const doc = computed(() => detail.value?.doc || null)
const clauses = computed(() => detail.value?.clauses || [])
const metadata = computed(() => detail.value?.metadata || [])
const comments = computed(() => detail.value?.comments || [])

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <!-- 头部 -->
    <div class="detail-head">
      <el-button @click="goBack" text><el-icon><ArrowLeft /></el-icon>返回</el-button>
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
          </div>
          <div class="sub-line">
            <span v-if="detail.contract_no">编号 {{ detail.contract_no }}</span>
            <span v-if="detail.counterparty">相对方：{{ detail.counterparty }}</span>
            <span v-if="detail.department">{{ detail.department }}</span>
            <span v-if="detail.business_type_label">{{ detail.business_type_label }}</span>
          </div>
        </div>
        <div class="head-actions">
          <el-button @click="runPipeline" :disabled="detail.status === 'parsing' || detail.status === 'reviewing'">
            跑流水线
          </el-button>
          <el-button @click="reparse">重新解析</el-button>
        </div>
      </template>
    </div>

    <template v-if="detail">
      <el-tabs v-model="activeTab" class="detail-tabs">
        <!-- ============ 概览 ============ -->
        <el-tab-pane label="概览" name="overview">
          <el-row :gutter="16">
            <el-col :span="14">
              <el-card shadow="never" header="合同基本信息">
                <el-descriptions :column="2" border size="small">
                  <el-descriptions-item label="合同标题">{{ detail.title }}</el-descriptions-item>
                  <el-descriptions-item label="合同编号">{{ detail.contract_no || '—' }}</el-descriptions-item>
                  <el-descriptions-item label="我方身份">{{ detail.metadata?.[0]?.field_label || '—' }}</el-descriptions-item>
                  <el-descriptions-item label="相对方">{{ detail.counterparty || '—' }}</el-descriptions-item>
                  <el-descriptions-item label="合同金额">{{ fmtMoney(detail) }}</el-descriptions-item>
                  <el-descriptions-item label="业务类型">{{ detail.business_type_label || '—' }}</el-descriptions-item>
                  <el-descriptions-item label="送审人">{{ detail.applicant || '—' }}</el-descriptions-item>
                  <el-descriptions-item label="送审部门">{{ detail.department || '—' }}</el-descriptions-item>
                </el-descriptions>

                <div class="meta-table" v-if="metadata.length">
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

              <el-card shadow="never" header="附件" class="mt">
                <div v-for="f in detail.files" :key="f.id" class="file-row">
                  <el-icon><Document /></el-icon>
                  <span class="file-name">{{ f.filename }}</span>
                  <el-tag size="small" type="info">{{ f.file_kind }}</el-tag>
                  <el-tag v-if="f.from_cache" size="small" type="success">指纹缓存</el-tag>
                </div>
              </el-card>
            </el-col>
          </el-row>
        </el-tab-pane>

        <!-- ============ 风险清单 ============ -->
        <el-tab-pane :label="`风险清单（${risks.length}）`" name="risks">
          <el-empty v-if="!risks.length" description="未发现风险" />
          <div v-else class="risk-list">
            <el-card
              v-for="r in risks"
              :key="r.id"
              shadow="never"
              class="risk-card"
              :class="[`risk-${r.risk_level}`]"
            >
              <div class="risk-head">
                <span class="risk-no">R{{ r.risk_no }}</span>
                <el-tag :type="RISK_TAG[r.risk_level] || 'info'" size="small">{{ r.risk_level_label }}</el-tag>
                <strong class="risk-name">{{ r.name }}</strong>
                <span class="risk-src" v-if="r.rule_id">规则 {{ r.rule_id }}</span>
                <span class="risk-src" v-else-if="r.source === 'llm'">LLM 研判</span>
                <div class="spacer"></div>
                <el-button link type="primary" size="small" @click="jumpToRisk(r)">定位原文</el-button>
              </div>

              <div class="risk-loc">{{ r.location_label }} · {{ r.clause_type_label }}</div>

              <div class="risk-quote" v-if="r.quote">「{{ r.quote }}」</div>

              <div class="risk-reason">{{ r.reason }}</div>
              <div class="risk-basis" v-if="r.legal_basis">
                <el-icon><Stamp /></el-icon>{{ r.legal_basis }}
              </div>

              <div v-if="r.suggestion?.suggested_text" class="risk-suggestion">
                <div class="sug-title">推荐修改示范条款</div>
                <pre>{{ r.suggestion.suggested_text }}</pre>
              </div>

              <div class="risk-decision">
                <span class="dec-label">法务处理：</span>
                <el-select v-model="r.decision" size="small" style="width: 130px">
                  <el-option v-for="o in DECISION_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
                </el-select>
                <el-input v-model="r._note" size="small" placeholder="备注（可选）" style="width: 180px" />
                <el-button size="small" type="primary" @click="saveDecision(r)">保存</el-button>
              </div>
            </el-card>
          </div>
        </el-tab-pane>

        <!-- ============ 原文 ============ -->
        <el-tab-pane label="原文高亮" name="document">
          <HighlightDocument
            :doc="doc"
            :risks="risks"
            :active-risk-id="activeRiskId"
          />
        </el-tab-pane>

        <!-- ============ 条款 ============ -->
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
          <el-row :gutter="16">
            <el-col :span="12">
              <el-card shadow="never" header="报告导出">
                <p class="muted">审查完成后可导出 Markdown 或 PDF 格式的审查报告。</p>
                <el-button type="primary" :disabled="detail.status !== 'completed'" @click="downloadReport('md')">
                  下载 Markdown
                </el-button>
                <el-button type="primary" :disabled="detail.status !== 'completed'" @click="downloadReport('pdf')">
                  下载 PDF
                </el-button>
                <el-alert
                  v-if="detail.status !== 'completed'"
                  type="info"
                  :closable="false"
                  show-icon
                  title="任务完成审查后才能导出报告"
                  style="margin-top: 12px"
                />
              </el-card>
            </el-col>

            <el-col :span="12">
              <el-card shadow="never" header="回写审批系统">
                <div v-if="detail.writeback" class="wb-status">
                  <el-tag size="small" :type="detail.writeback.status === 'success' ? 'success' : 'warning'">
                    {{ detail.writeback.status_label || detail.writeback.status }}
                  </el-tag>
                  <span v-if="detail.writeback.comment_id" class="muted">comment_id={{ detail.writeback.comment_id }}</span>
                </div>
                <el-input
                  v-model="writeback.lawyerNotes"
                  type="textarea"
                  :rows="3"
                  placeholder="法务批注（会拼进意见文本，留空则自动汇总法务评论）"
                />
                <el-button
                  type="primary"
                  class="mt"
                  :loading="writeback.submitting"
                  :disabled="detail.status !== 'completed'"
                  @click="doWriteback"
                >
                  一键回写
                </el-button>
              </el-card>
            </el-col>
          </el-row>

          <!-- 协同记录 -->
          <el-card shadow="never" header="协同记录" class="mt">
            <el-timeline v-if="comments.length">
              <el-timeline-item
                v-for="c in comments"
                :key="c.id"
                :timestamp="c.created_at ? c.created_at.replace('T', ' ').slice(0, 19) : ''"
                :type="c.kind === 'action' ? 'primary' : c.role === 'lawyer' ? 'success' : 'info'"
              >
                <span class="comment-author">{{ c.author }}</span>
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
}
.head-main {
  flex: 1;
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
}
.detail-tabs :deep(.el-tabs__content) {
  padding-top: 8px;
}
.meta-table {
  margin-top: 16px;
  border-top: 1px solid #f0f0f0;
}
.meta-row {
  display: flex;
  gap: 12px;
  padding: 8px 4px;
  border-bottom: 1px solid #f5f5f5;
  font-size: 13px;
}
.meta-key {
  width: 130px;
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
.mt {
  margin-top: 16px;
}
.file-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
}
.file-name {
  flex: 1;
}

/* 风险卡片 */
.risk-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.risk-card {
  border-left-width: 4px;
}
.risk-card.risk-high {
  border-left-color: var(--risk-high);
}
.risk-card.risk-medium {
  border-left-color: var(--risk-medium);
}
.risk-card.risk-low {
  border-left-color: var(--risk-low);
}
.risk-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.risk-no {
  font-weight: 700;
  color: #1f2329;
}
.risk-name {
  font-size: 15px;
}
.risk-src {
  color: #98a1b3;
  font-size: 12px;
}
.spacer {
  flex: 1;
}
.risk-loc {
  margin-top: 6px;
  color: #6b7280;
  font-size: 13px;
}
.risk-quote {
  margin-top: 8px;
  background: #f8fafc;
  border-radius: 6px;
  padding: 8px 12px;
  color: #4b5563;
  font-size: 13px;
}
.risk-reason {
  margin-top: 8px;
  color: #374151;
  line-height: 1.7;
}
.risk-basis {
  margin-top: 6px;
  color: #6b7280;
  font-size: 12px;
  display: flex;
  gap: 4px;
  align-items: flex-start;
}
.risk-suggestion {
  margin-top: 10px;
}
.sug-title {
  font-size: 13px;
  color: #6b7280;
  margin-bottom: 4px;
}
.risk-suggestion pre {
  margin: 0;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  border-radius: 6px;
  padding: 10px 12px;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  font-size: 13px;
  color: #166534;
}
.risk-decision {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed #e5e7eb;
}
.dec-label {
  font-size: 13px;
  color: #6b7280;
}
.wb-status {
  margin-bottom: 10px;
  display: flex;
  gap: 10px;
  align-items: center;
}
.comment-author {
  font-weight: 600;
  margin-right: 8px;
}
.comment-content {
  color: #4b5563;
}
.muted {
  color: #98a1b3;
  font-size: 12px;
}
</style>
