<script setup>
/**
 * 风险卡片模块（需求 2.4.5）。
 *
 * 职责：风险等级标签展示、条款差异对比、修改建议一键复制与采纳交互。
 *
 * 定位相关的数据来自后端 IR：``block_id + char 区间``，「定位原文」只发事件，
 * 由父级（工作台）决定怎么滚动 —— 组件不碰 DOM 定位逻辑，避免破坏双向跳转。
 */
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { useAuthStore } from '../stores/auth'

const props = defineProps({
  risks: { type: Array, default: () => [] },
  activeRiskId: { type: String, default: '' },
  /** 父级正在保存时置 true，防止重复点击 */
  saving: { type: Boolean, default: false },
})

const emit = defineEmits(['locate', 'decide'])

const auth = useAuthStore()

const RISK_TAG = { high: 'danger', medium: 'warning', low: 'info' }
const RISK_ORDER = { high: 3, medium: 2, low: 1 }

const FILTERS = [
  { value: 'all', label: '全部' },
  { value: 'high', label: '高风险' },
  { value: 'medium', label: '中风险' },
  { value: 'low', label: '低风险' },
  { value: 'open', label: '待处理' },
  { value: 'decided', label: '已处理' },
]

const filter = ref('all')
const keyword = ref('')

const DECISION_OPTIONS = [
  { value: 'accepted', label: '采纳', type: 'success' },
  { value: 'adjusted', label: '调整后采纳', type: 'warning' },
  { value: 'rejected', label: '驳回', type: 'danger' },
  { value: 'open', label: '重置待处理', type: 'info' },
]

const DECISION_LABEL = {
  open: '待处理',
  accepted: '已采纳',
  adjusted: '已调整后采纳',
  rejected: '已驳回',
}

const DECISION_TAG = { open: 'info', accepted: 'success', adjusted: 'warning', rejected: 'danger' }

/** 本地编辑态：决策下拉 / 备注 / 调整后的条款文本 */
const draft = reactive({})

/** 取（必要时惰性建）某条风险的草稿，模板里直接 d(r).note 用 */
function d(r) {
  if (!draft[r.id]) {
    draft[r.id] = {
      decision: r.decision || 'open',
      note: '',
      editing: false,
      text: r.suggestion?.edited_text || r.suggestion?.final_text || r.suggestion?.suggested_text || '',
    }
  }
  return draft[r.id]
}

/**
 * 列表行 = 风险实体 + 该行的编辑草稿（``_d``）。
 * 把草稿挂在行对象上，模板里就能直接 `row._d.note` 走 v-model，
 * 不用在模板里做函数调用（v-model 需要可赋值的成员表达式）。
 */
const rows = ref([])

// 列表变化时补齐草稿（已有草稿不覆盖，避免打断用户正在输入的内容）
watch(
  () => props.risks,
  (list) => {
    rows.value = (list || []).map((r) => ({ ...r, _d: d(r) }))
  },
  { immediate: true }
)

const counts = computed(() => {
  const c = { high: 0, medium: 0, low: 0, open: 0 }
  for (const r of props.risks) {
    c[r.risk_level] = (c[r.risk_level] || 0) + 1
    if (!r.decision || r.decision === 'open') c.open += 1
  }
  return c
})

const visible = computed(() => {
  let list = [...rows.value]
  if (filter.value === 'open') list = list.filter((r) => !r.decision || r.decision === 'open')
  else if (filter.value === 'decided') list = list.filter((r) => r.decision && r.decision !== 'open')
  else if (filter.value !== 'all') list = list.filter((r) => r.risk_level === filter.value)

  const kw = keyword.value.trim()
  if (kw) {
    list = list.filter((r) =>
      [r.name, r.reason, r.quote, r.clause_type_label, r.rule_id, r.legal_basis]
        .filter(Boolean)
        .some((t) => String(t).includes(kw))
    )
  }
  // 高风险优先，同级按编号
  return list.sort(
    (a, b) => RISK_ORDER[b.risk_level] - RISK_ORDER[a.risk_level] || a.risk_no - b.risk_no
  )
})

// ======================================================================
// 条款差异对比：行级 LCS diff（不引第三方库）
// ======================================================================
function splitLines(text) {
  return String(text || '')
    .split(/\r?\n/)
    .flatMap((l) => (l.trim() === '' ? [] : [l.trim()]))
}

/**
 * 行级 diff。返回 [{ type: 'same'|'del'|'add', text }]
 * LCS 动态规划，条款量级（几十行）足够快。
 */
function lineDiff(before, after) {
  const a = splitLines(before)
  const b = splitLines(after)
  if (!a.length && !b.length) return []
  if (!a.length) return b.map((t) => ({ type: 'add', text: t }))
  if (!b.length) return a.map((t) => ({ type: 'del', text: t }))

  const m = a.length
  const n = b.length
  const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0))
  for (let i = m - 1; i >= 0; i -= 1) {
    for (let j = n - 1; j >= 0; j -= 1) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const out = []
  let i = 0
  let j = 0
  while (i < m && j < n) {
    if (a[i] === b[j]) {
      out.push({ type: 'same', text: a[i] })
      i += 1
      j += 1
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ type: 'del', text: a[i] })
      i += 1
    } else {
      out.push({ type: 'add', text: b[j] })
      j += 1
    }
  }
  while (i < m) out.push({ type: 'del', text: a[i++] })
  while (j < n) out.push({ type: 'add', text: b[j++] })
  return out
}

/** 原条款：优先 suggestion.original_text，退回风险项的原文引用 */
function originalOf(r) {
  return r.suggestion?.original_text || r.quote || ''
}

/** 建议条款：编辑过的 > 最终稿 > LLM 建议 */
function suggestedOf(r) {
  const s = r.suggestion
  if (!s) return ''
  return d(r).text || s.edited_text || s.final_text || s.suggested_text || ''
}

function diffOf(r) {
  return lineDiff(originalOf(r), suggestedOf(r))
}

const diffCache = computed(() => {
  const map = {}
  for (const r of visible.value) map[r.id] = diffOf(r)
  return map
})

const expandedDiff = ref({})
function toggleDiff(id) {
  expandedDiff.value = { ...expandedDiff.value, [id]: !expandedDiff.value[id] }
}

// ======================================================================
// 复制 / 采纳
// ======================================================================
async function copyText(text, tip = '修改建议已复制') {
  if (!text) {
    ElMessage.warning('没有可复制的文本')
    return
  }
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    ElMessage.success(tip)
  } catch (e) {
    ElMessage.error('复制失败，请手动选中复制')
  }
}

function submitDecision(r, decision) {
  const cur = d(r)
  // 处理人由后端从登录态取，前端不传
  const payload = {
    decision,
    note: cur.note || undefined,
    edited_text: decision === 'adjusted' && cur.text ? cur.text : undefined,
  }
  emit('decide', { risk: r, payload })
}

function onLocate(r) {
  emit('locate', r)
}
</script>

<template>
  <div class="risk-panel">
    <!-- 工具条 -->
    <div class="toolbar">
      <div class="counts">
        <span class="cnt high">{{ counts.high }} 高</span>
        <span class="cnt medium">{{ counts.medium }} 中</span>
        <span class="cnt low">{{ counts.low }} 低</span>
        <span class="cnt open">{{ counts.open }} 待处理</span>
      </div>
      <el-select v-model="filter" size="small" style="width: 108px">
        <el-option v-for="f in FILTERS" :key="f.value" :label="f.label" :value="f.value" />
      </el-select>
    </div>
    <el-input
      v-model="keyword"
      size="small"
      clearable
      placeholder="筛选风险名称 / 原因 / 原文片段"
      class="kw"
    >
      <template #prefix><el-icon><Search /></el-icon></template>
    </el-input>

    <el-empty v-if="!visible.length" description="没有符合条件的风险项" :image-size="70" />

    <div v-else class="list">
      <el-card
        v-for="r in visible"
        :key="r.id"
        shadow="never"
        class="risk-card"
        :class="[`risk-${r.risk_level}`, { active: activeRiskId === r.id }]"
        :data-risk-card="r.id"
        @click="onLocate(r)"
      >
        <div class="head">
          <span class="no">R{{ r.risk_no }}</span>
          <el-tag :type="RISK_TAG[r.risk_level] || 'info'" size="small">
            {{ r.risk_level_label }}
          </el-tag>
          <strong class="name">{{ r.name }}</strong>
          <el-tag
            v-if="r.decision && r.decision !== 'open'"
            :type="DECISION_TAG[r.decision] || 'info'"
            size="small"
            effect="dark"
          >
            {{ DECISION_LABEL[r.decision] }}
          </el-tag>
          <div class="spacer"></div>
          <span class="src">{{ r.rule_id ? `规则 ${r.rule_id}` : r.source === 'llm' ? 'LLM 研判' : '' }}</span>
        </div>

        <div class="loc">
          <el-icon><Location /></el-icon>
          {{ r.location_label }} · {{ r.clause_type_label }}
          <el-button link type="primary" size="small" @click.stop="onLocate(r)">定位原文</el-button>
        </div>

        <div v-if="r.quote" class="quote">「{{ r.quote }}」</div>

        <div class="reason">{{ r.reason }}</div>
        <div v-if="r.legal_basis" class="basis">
          <el-icon><Stamp /></el-icon>{{ r.legal_basis }}
        </div>

        <!-- 条款差异对比 -->
        <div v-if="r.suggestion" class="sug">
          <div class="sug-head">
            <span class="sug-title">条款差异对比</span>
            <span class="sim" v-if="r.suggestion.source">
              来源 {{ r.suggestion.source }}
              <template v-if="r.suggestion.template_id">· 模板 {{ r.suggestion.template_id }}</template>
              <template v-if="r.suggestion.similarity != null">
                · 相似度 {{ (r.suggestion.similarity * 100).toFixed(0) }}%
              </template>
            </span>
            <div class="spacer"></div>
            <el-button link size="small" @click.stop="toggleDiff(r.id)">
              {{ expandedDiff[r.id] ? '收起差异' : '展开差异' }}
            </el-button>
            <el-button
              v-perm="'risk.copy'"
              link
              size="small"
              @click.stop="copyText(suggestedOf(r))"
            >
              <el-icon><CopyDocument /></el-icon>复制建议
            </el-button>
          </div>

          <div v-if="!expandedDiff[r.id]" class="sug-plain">
            <div class="col-label del-label">原条款</div>
            <pre class="pre del">{{ originalOf(r) || '（无原文片段）' }}</pre>
            <div class="col-label add-label">建议条款</div>
            <pre class="pre add">{{ suggestedOf(r) || '（暂无建议）' }}</pre>
          </div>

          <div v-else class="diff">
            <div class="col-label">逐行差异（<span class="del-text">- 原条款</span> / <span class="add-text">+ 建议条款</span>）</div>
            <pre class="pre diff-pre"><template v-for="(seg, i) in diffCache[r.id]" :key="i"><span :class="`d-${seg.type}`">{{ seg.type === 'add' ? '+ ' : seg.type === 'del' ? '- ' : '  ' }}{{ seg.text }}&#10;</span></template></pre>
          </div>
        </div>

        <!-- 法务处理 -->
        <div class="decide" @click.stop>
          <template v-if="auth.has('risk.decide')">
            <el-button
              size="small"
              type="success"
              :loading="saving"
              @click="submitDecision(r, 'accepted')"
            >
              <el-icon><Select /></el-icon>采纳
            </el-button>
            <el-button size="small" type="warning" :loading="saving" @click="submitDecision(r, 'adjusted')">
              调整后采纳
            </el-button>
            <el-button size="small" type="danger" plain :loading="saving" @click="submitDecision(r, 'rejected')">
              驳回
            </el-button>
            <el-input
              v-model="r._d.note"
              size="small"
              placeholder="法务说明（可选）"
              style="width: 190px"
            />
            <el-button link size="small" @click="r._d.editing = !r._d.editing">
              {{ r._d.editing ? '收起条款编辑' : '编辑示范条款' }}
            </el-button>
            <div v-if="r._d.editing" class="editor">
              <el-input
                v-model="r._d.text"
                type="textarea"
                :rows="4"
                placeholder="直接改示范条款文本，点「调整后采纳」会一并保存（edited_text）"
              />
              <div class="editor-tip">
                改动会写进建议的 edited_text，并记录修改人 —— 回写审批意见时用的是这份最终文本。
              </div>
            </div>
          </template>
          <span v-else class="readonly">
            <el-icon><View /></el-icon>
            只读视图：当前身份（{{ auth.role.name }}）无「采纳 / 调整」权限，风险处理由法务审查人完成。
          </span>
        </div>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.risk-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.counts {
  display: flex;
  gap: 10px;
  font-size: 12px;
}
.cnt.high {
  color: var(--risk-high);
}
.cnt.medium {
  color: var(--risk-medium);
}
.cnt.low {
  color: var(--risk-low);
}
.cnt.open {
  color: #6b7280;
}
.kw {
  flex-shrink: 0;
}
.list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.risk-card {
  cursor: pointer;
  border-left-width: 4px;
  transition: box-shadow 0.2s;
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
.risk-card.active {
  box-shadow: 0 0 0 2px var(--el-color-primary-light-7);
}
.head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.no {
  font-weight: 700;
}
.name {
  font-size: 14px;
}
.src {
  color: #98a1b3;
  font-size: 12px;
}
.spacer {
  flex: 1;
}
.loc {
  margin-top: 6px;
  display: flex;
  align-items: center;
  gap: 4px;
  color: #6b7280;
  font-size: 12px;
}
.quote {
  margin-top: 8px;
  background: #f8fafc;
  border-radius: 6px;
  padding: 8px 12px;
  color: #4b5563;
  font-size: 13px;
}
.reason {
  margin-top: 8px;
  color: #374151;
  line-height: 1.7;
  font-size: 13px;
}
.basis {
  margin-top: 6px;
  color: #6b7280;
  font-size: 12px;
  display: flex;
  gap: 4px;
  align-items: flex-start;
}
.sug {
  margin-top: 10px;
  border-top: 1px dashed #e5e7eb;
  padding-top: 10px;
}
.sug-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.sug-title {
  font-size: 13px;
  font-weight: 600;
}
.sim {
  font-size: 11px;
  color: #98a1b3;
}
.col-label {
  font-size: 12px;
  color: #98a1b3;
  margin: 8px 0 4px;
}
.del-label {
  color: #b91c1c;
}
.add-label {
  color: #15803d;
}
.pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  font-size: 13px;
  border-radius: 6px;
  padding: 8px 12px;
}
.pre.del {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #7f1d1d;
}
.pre.add {
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  color: #166534;
}
.diff-pre {
  background: #fff;
  border: 1px solid #e5e7eb;
  max-height: 260px;
  overflow: auto;
}
.del-text {
  color: #b91c1c;
}
.add-text {
  color: #15803d;
}
.d-del {
  background: #fef2f2;
  color: #b91c1c;
  display: block;
}
.d-add {
  background: #f0fdf4;
  color: #15803d;
  display: block;
}
.d-same {
  color: #6b7280;
  display: block;
}
.decide {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed #e5e7eb;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.editor {
  width: 100%;
  margin-top: 4px;
}
.editor-tip {
  margin-top: 4px;
  font-size: 11px;
  color: #98a1b3;
}
.readonly {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #98a1b3;
}
</style>
