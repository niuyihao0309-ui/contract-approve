<script setup>
/**
 * 规则配置模块（需求 2.4.5 + 2.4.3「合规规则与模板页」）。
 *
 * 三件事：
 *   1. 规则树状列表（按规则分类 / 按合同类型 两种分组）
 *   2. 条件编辑表单（触发方式、命中模式、参数、适用范围、风险等级…）
 *   3. 示范条款模板库维护（新增 / 编辑 / 启停）
 *
 * ⚠️ 重要语义：``app/rules/*.json`` 是唯一事实来源，DB 是运行期副本。
 * 界面上改的是运行期副本，立即对审查生效；但执行「从 JSON 同步」会把内容刷回去
 * （只有 enabled 开关会保留）。页面顶部有明确提示，别让用户以为改的是源文件。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiRules } from '../api'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()

const tab = ref('rules')
const loading = ref(false)
const saving = ref(false)

const RISK_TAG = { high: 'danger', medium: 'warning', low: 'info' }

const TRIGGER_OPTIONS = [
  { value: 'keyword', label: '关键词命中', hint: 'patterns 里任一关键词出现在文本中即命中' },
  { value: 'regex', label: '正则匹配', hint: 'patterns 是 Python 正则，逐个 re.search' },
  { value: 'checker', label: 'Python 判定器', hint: '调用 checkers.py 里的函数，参数走 params' },
  { value: 'required', label: '必备条款缺失', hint: '找不到该条款类型才算命中' },
  { value: 'forbidden', label: '禁止性表述', hint: '命中即风险（与 required 相反）' },
  { value: 'threshold', label: '数值阈值', hint: '配合 params 做比例/金额比较' },
]

const RISK_OPTIONS = [
  { value: 'high', label: '高风险' },
  { value: 'medium', label: '中风险' },
  { value: 'low', label: '低风险' },
]

// ======================================================================
// 规则
// ======================================================================
const data = ref(null)
const opened = ref([])
const groupBy = ref('category')      // category | contract_type
const ruleKw = ref('')
const ruleStatus = ref('')           // '' | 'on' | 'off'

const CONTRACT_TYPES = [
  { value: 'purchase', label: '采购合同' },
  { value: 'sales', label: '销售合同' },
  { value: 'service', label: '服务合同' },
  { value: 'labor', label: '劳动合同' },
  { value: 'other', label: '其他合同' },
]

const clauseTypeDict = computed(() => data.value?.dictionaries?.clause_type || {})

/** 原始规则列表 */
const allRules = computed(() => {
  const out = []
  const tree = data.value?.tree || {}
  for (const cat of Object.keys(tree)) out.push(...tree[cat])
  return out
})

/** 前端筛选（规则的量级小，不做服务端分页） */
const filteredRules = computed(() => {
  const kw = ruleKw.value.trim()
  return allRules.value.filter((r) => {
    if (ruleStatus.value === 'on' && !r.enabled) return false
    if (ruleStatus.value === 'off' && r.enabled) return false
    if (!kw) return true
    return [r.rule_id, r.name, r.description, r.legal_basis, ...(r.tags || [])]
      .filter(Boolean)
      .some((t) => String(t).includes(kw))
  })
})

/** 分组后的树 */
const grouped = computed(() => {
  const out = {}
  for (const r of filteredRules.value) {
    if (groupBy.value === 'category') {
      ;(out[r.category || '未分类'] ||= []).push(r)
    } else {
      const types = r.contract_types?.length ? r.contract_types : ['__all__']
      for (const t of types) {
        const label = t === '__all__' ? '全类型通用' : CONTRACT_TYPES.find((c) => c.value === t)?.label || t
        ;(out[label] ||= []).push(r)
      }
    }
  }
  return out
})

const groupNames = computed(() => Object.keys(grouped.value))

async function loadRules() {
  loading.value = true
  try {
    data.value = await apiRules.list()
    opened.value = groupNames.value.slice(0, 3)
  } finally {
    loading.value = false
  }
}

async function toggleRule(row, val) {
  saving.value = true
  try {
    const r = await apiRules.patch(row.rule_id, { enabled: val })
    row.enabled = val
    ElMessage.success(r.message || '已保存')
  } catch (e) {
    row.enabled = !val
  } finally {
    saving.value = false
  }
}

async function syncFromJson() {
  await ElMessageBox.confirm(
    '将以规则源文件为准覆盖规则与模板内容（界面上改过的字段会丢失，启停开关保留）。确定同步？',
    '从 JSON 同步',
    { type: 'warning' }
  )
  const r = await apiRules.sync()
  ElMessage.success(r.message)
  await loadRules()
}

// ---- 条件编辑表单（抽屉） ----
const editVisible = ref(false)
const editForm = reactive({
  rule_id: '',
  name: '',
  category: '',
  description: '',
  risk_level: 'medium',
  trigger: 'keyword',
  patterns: [],
  exclude_patterns: [],
  paramsText: '{}',
  contract_types: [],
  clause_types: [],
  reason: '',
  legal_basis: '',
  suggestion_template: '',
  tags: [],
  priority: 100,
  enabled: true,
})

const triggerHint = computed(
  () => TRIGGER_OPTIONS.find((t) => t.value === editForm.trigger)?.hint || ''
)

function openEdit(row) {
  Object.assign(editForm, {
    rule_id: row.rule_id,
    name: row.name,
    category: row.category || '',
    description: row.description || '',
    risk_level: row.risk_level,
    trigger: row.trigger,
    patterns: [...(row.patterns || [])],
    exclude_patterns: [...(row.exclude_patterns || [])],
    paramsText: JSON.stringify(row.params || {}, null, 2),
    contract_types: [...(row.contract_types || [])],
    clause_types: [...(row.clause_types || [])],
    reason: row.reason || '',
    legal_basis: row.legal_basis || '',
    suggestion_template: row.suggestion_template || '',
    tags: [...(row.tags || [])],
    priority: row.priority ?? 100,
    enabled: row.enabled,
  })
  editVisible.value = true
}

async function saveRule() {
  let params
  try {
    params = editForm.paramsText.trim() ? JSON.parse(editForm.paramsText) : {}
  } catch (e) {
    ElMessage.error('params 不是合法 JSON')
    return
  }
  saving.value = true
  try {
    const r = await apiRules.patch(editForm.rule_id, {
      name: editForm.name,
      category: editForm.category,
      description: editForm.description,
      risk_level: editForm.risk_level,
      trigger: editForm.trigger,
      patterns: editForm.patterns,
      exclude_patterns: editForm.exclude_patterns,
      params,
      contract_types: editForm.contract_types,
      clause_types: editForm.clause_types,
      reason: editForm.reason,
      legal_basis: editForm.legal_basis,
      suggestion_template: editForm.suggestion_template || null,
      tags: editForm.tags,
      priority: Number(editForm.priority) || 100,
      enabled: editForm.enabled,
    })
    ElMessage.success(r.message || '规则已保存')
    editVisible.value = false
    await loadRules()
  } finally {
    saving.value = false
  }
}

// ======================================================================
// 示范条款模板库
// ======================================================================
const templates = ref([])
const tplKw = ref('')
const tplLoading = ref(false)
const tplVisible = ref(false)
const tplForm = reactive({
  isNew: false,
  template_id: '',
  title: '',
  clause_type: 'other',
  contract_types: [],
  content: '',
  tags: [],
  keywords: [],
  scenario: '',
  risk_level: 'medium',
  legal_basis: '',
  version: '1.0',
  enabled: true,
})

const filteredTemplates = computed(() => {
  const kw = tplKw.value.trim()
  if (!kw) return templates.value
  return templates.value.filter((t) =>
    [t.template_id, t.title, t.content, t.scenario, ...(t.keywords || [])]
      .filter(Boolean)
      .some((x) => String(x).includes(kw))
  )
})

async function loadTemplates() {
  tplLoading.value = true
  try {
    const r = await apiRules.templates()
    templates.value = r.items || []
  } finally {
    tplLoading.value = false
  }
}

function openTpl(t = null) {
  if (t) {
    Object.assign(tplForm, {
      isNew: false,
      template_id: t.template_id,
      title: t.title,
      clause_type: t.clause_type,
      contract_types: [...(t.contract_types || [])],
      content: t.content || '',
      tags: [...(t.tags || [])],
      keywords: [...(t.keywords || [])],
      scenario: t.scenario || '',
      risk_level: t.risk_level,
      legal_basis: t.legal_basis || '',
      version: t.version || '1.0',
      enabled: t.enabled,
    })
  } else {
    Object.assign(tplForm, {
      isNew: true,
      template_id: '',
      title: '',
      clause_type: 'other',
      contract_types: [],
      content: '',
      tags: [],
      keywords: [],
      scenario: '',
      risk_level: 'medium',
      legal_basis: '',
      version: '1.0',
      enabled: true,
    })
  }
  tplVisible.value = true
}

async function saveTpl() {
  if (!tplForm.title.trim()) {
    ElMessage.warning('模板标题不能为空')
    return
  }
  const body = {
    title: tplForm.title,
    clause_type: tplForm.clause_type,
    contract_types: tplForm.contract_types,
    content: tplForm.content,
    tags: tplForm.tags,
    keywords: tplForm.keywords,
    scenario: tplForm.scenario,
    risk_level: tplForm.risk_level,
    legal_basis: tplForm.legal_basis,
    version: tplForm.version,
    enabled: tplForm.enabled,
  }
  saving.value = true
  try {
    const r = tplForm.isNew
      ? await apiRules.createTemplate({ ...body, template_id: tplForm.template_id || null })
      : await apiRules.updateTemplate(tplForm.template_id, body)
    ElMessage.success(r.message || '模板已保存')
    tplVisible.value = false
    await loadTemplates()
  } finally {
    saving.value = false
  }
}

async function previewHtml(content) {
  await ElMessageBox.alert(
    `<pre style="white-space:pre-wrap;font-family:inherit;margin:0">${String(content)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')}</pre>`,
    '示范条款全文',
    { dangerouslyUseHTMLString: true, customClass: 'tpl-preview' }
  )
}

onMounted(async () => {
  await loadRules()
  await loadTemplates()
})
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h2 class="page-title">规则与模板</h2>
        <p class="sub">
          审查规则 {{ allRules.length }} 条 · 示范条款 {{ templates.length }} 条。
          按合同类型维护触发条件、风险分级与标准示范条款库。
        </p>
      </div>
      <div class="head-btns">
        <el-button v-perm="'rule.edit'" @click="syncFromJson">
          <el-icon><Refresh /></el-icon>从 JSON 同步
        </el-button>
        <el-button v-perm="'rule.edit'" @click="loadRules">刷新</el-button>
      </div>
    </div>

    <el-alert type="info" :closable="false" show-icon class="mb">
      <template #default>
        页面上的编辑落在运行期副本上，<strong>立即对审查生效</strong>；
        执行「从 JSON 同步」会以规则源文件为准覆盖内容（启停开关保留）。
      </template>
    </el-alert>

    <el-tabs v-model="tab">
      <!-- ================= 审查规则 ================= -->
      <el-tab-pane :label="`审查规则（${allRules.length}）`" name="rules">
        <el-card shadow="never" class="toolbar-card">
          <div class="toolbar">
            <span class="tb-label">分组方式</span>
            <el-radio-group v-model="groupBy" size="small">
              <el-radio-button value="category">按规则分类</el-radio-button>
              <el-radio-button value="contract_type">按合同类型</el-radio-button>
            </el-radio-group>

            <el-select v-model="ruleStatus" size="small" style="width: 120px">
              <el-option label="全部状态" value="" />
              <el-option label="仅启用" value="on" />
              <el-option label="仅停用" value="off" />
            </el-select>

            <el-input
              v-model="ruleKw"
              size="small"
              clearable
              placeholder="搜索规则编号 / 名称 / 法律依据 / 标签"
              style="width: 300px"
            >
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>

            <div class="spacer"></div>
            <span class="muted">命中 {{ filteredRules.length }} 条</span>
          </div>
        </el-card>

        <el-collapse v-model="opened" class="mt">
          <el-collapse-item v-for="name in groupNames" :key="name" :name="name">
            <template #title>
              <span class="cat-title">{{ name }}</span>
              <el-tag size="small" type="info">{{ grouped[name].length }} 条</el-tag>
            </template>

            <el-table :data="grouped[name]" size="small">
              <el-table-column prop="rule_id" label="编号" width="150" />
              <el-table-column prop="name" label="规则名" min-width="190" show-overflow-tooltip />
              <el-table-column label="等级" width="86">
                <template #default="{ row }">
                  <el-tag :type="RISK_TAG[row.risk_level] || 'info'" size="small">{{ row.risk_level_label }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="触发方式" width="112">
                <template #default="{ row }">
                  <el-tag size="small" :type="row.trigger === 'checker' ? 'success' : ''">{{ row.trigger }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="条件" min-width="220" show-overflow-tooltip>
                <template #default="{ row }">
                  <span v-if="row.patterns?.length" class="mono">{{ row.patterns.join(' | ') }}</span>
                  <span v-else-if="row.checker" class="mono">{{ row.checker }}({{ JSON.stringify(row.params) }})</span>
                  <span v-else class="muted">—</span>
                </template>
              </el-table-column>
              <el-table-column label="适用范围" min-width="150">
                <template #default="{ row }">
                  <template v-if="row.contract_type_labels?.length">
                    <el-tag v-for="t in row.contract_type_labels" :key="t" size="small" type="info" class="mr">{{ t }}</el-tag>
                  </template>
                  <span v-else class="muted">全类型</span>
                </template>
              </el-table-column>
              <el-table-column label="优先级" width="76" align="center">
                <template #default="{ row }">{{ row.priority }}</template>
              </el-table-column>
              <el-table-column label="启用" width="76">
                <template #default="{ row }">
                  <el-switch
                    :model-value="row.enabled"
                    size="small"
                    :disabled="!auth.has('rule.edit')"
                    @update:model-value="(v) => toggleRule(row, v)"
                  />
                </template>
              </el-table-column>
              <el-table-column label="操作" width="80" fixed="right">
                <template #default="{ row }">
                  <el-button
                    v-perm="'rule.edit'"
                    link
                    type="primary"
                    size="small"
                    @click="openEdit(row)"
                  >
                    编辑条件
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-collapse-item>
        </el-collapse>
      </el-tab-pane>

      <!-- ================= 示范条款模板库 ================= -->
      <el-tab-pane :label="`示范条款模板库（${templates.length}）`" name="templates">
        <el-card shadow="never" class="toolbar-card">
          <div class="toolbar">
            <el-input
              v-model="tplKw"
              size="small"
              clearable
              placeholder="搜索模板编号 / 标题 / 正文 / 关键词"
              style="width: 340px"
            >
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
            <div class="spacer"></div>
            <el-button v-perm="'template.edit'" type="primary" size="small" @click="openTpl(null)">
              <el-icon><Plus /></el-icon>新建模板
            </el-button>
          </div>
        </el-card>

        <el-table :data="filteredTemplates" size="small" v-loading="tplLoading" class="mt">
          <el-table-column prop="template_id" label="模板编号" width="130" />
          <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
          <el-table-column prop="clause_type_label" label="条款类型" width="130" />
          <el-table-column label="适用合同类型" width="170">
            <template #default="{ row }">
              <template v-if="row.contract_type_labels?.length">
                <el-tag v-for="t in row.contract_type_labels" :key="t" size="small" type="info" class="mr">{{ t }}</el-tag>
              </template>
              <span v-else class="muted">全类型</span>
            </template>
          </el-table-column>
          <el-table-column label="风险等级" width="92">
            <template #default="{ row }">
              <el-tag :type="RISK_TAG[row.risk_level] || 'info'" size="small">{{ row.risk_level_label }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="正文预览" min-width="240">
            <template #default="{ row }">
              <span class="ellipsis">{{ (row.content || '').slice(0, 60) }}…</span>
              <el-button link type="primary" size="small" @click="previewHtml(row.content)">全文</el-button>
            </template>
          </el-table-column>
          <el-table-column prop="version" label="版本" width="70" />
          <el-table-column label="启用" width="76">
            <template #default="{ row }">
              <el-tag size="small" :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="80" fixed="right">
            <template #default="{ row }">
              <el-button v-perm="'template.edit'" link type="primary" size="small" @click="openTpl(row)">
                编辑
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- 条件编辑表单 -->
    <el-drawer v-model="editVisible" title="编辑审查规则" size="600px" destroy-on-close>
      <el-form :model="editForm" label-width="112px" label-position="right">
        <el-form-item label="规则编号">
          <el-input v-model="editForm.rule_id" disabled />
        </el-form-item>
        <el-form-item label="规则名">
          <el-input v-model="editForm.name" />
        </el-form-item>
        <el-form-item label="分类">
          <el-input v-model="editForm.category" placeholder="如 违约责任 / 主体资质" />
        </el-form-item>
        <el-form-item label="风险等级">
          <el-radio-group v-model="editForm.risk_level">
            <el-radio-button v-for="o in RISK_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="优先级">
          <el-input-number v-model="editForm.priority" :min="1" :max="999" />
          <span class="form-hint">数字越小越先执行（规则引擎按此排序）</span>
        </el-form-item>

        <el-divider content-position="left">触发条件</el-divider>

        <el-form-item label="触发方式">
          <el-select v-model="editForm.trigger" style="width: 100%">
            <el-option v-for="o in TRIGGER_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
          <div class="form-hint">{{ triggerHint }}</div>
        </el-form-item>

        <el-form-item label="命中模式">
          <el-select
            v-model="editForm.patterns"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="输入后回车添加（关键词 / 正则）"
            style="width: 100%"
          />
        </el-form-item>

        <el-form-item label="排除模式">
          <el-select
            v-model="editForm.exclude_patterns"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="命中即排除，用于降噪"
            style="width: 100%"
          />
        </el-form-item>

        <el-form-item label="判定参数">
          <el-input v-model="editForm.paramsText" type="textarea" :rows="4" placeholder='JSON，如 {"max_ratio": 0.3}' />
          <div class="form-hint">checker / threshold 类规则的参数，必须是合法 JSON</div>
        </el-form-item>

        <el-divider content-position="left">适用范围</el-divider>

        <el-form-item label="合同类型">
          <el-select v-model="editForm.contract_types" multiple clearable style="width: 100%" placeholder="留空 = 全类型适用">
            <el-option v-for="t in CONTRACT_TYPES" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
        </el-form-item>

        <el-form-item label="条款类型">
          <el-select v-model="editForm.clause_types" multiple clearable style="width: 100%" placeholder="留空 = 全文搜索">
            <el-option v-for="(label, key) in clauseTypeDict" :key="key" :label="label" :value="key" />
          </el-select>
        </el-form-item>

        <el-divider content-position="left">输出</el-divider>

        <el-form-item label="风险说明">
          <el-input v-model="editForm.reason" type="textarea" :rows="3" placeholder="为什么会构成风险（会写进风险卡片）" />
        </el-form-item>
        <el-form-item label="法律依据">
          <el-input v-model="editForm.legal_basis" placeholder="如《民法典》第五百七十七条" />
        </el-form-item>
        <el-form-item label="示范条款模板">
          <el-input v-model="editForm.suggestion_template" type="textarea" :rows="3" placeholder="可选，兜底建议文本" />
        </el-form-item>
        <el-form-item label="标签">
          <el-select v-model="editForm.tags" multiple filterable allow-create default-first-option style="width: 100%" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="editForm.enabled" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveRule">保存</el-button>
      </template>
    </el-drawer>

    <!-- 模板编辑 -->
    <el-drawer
      v-model="tplVisible"
      :title="tplForm.isNew ? '新建示范条款模板' : `编辑模板 ${tplForm.template_id}`"
      size="620px"
      destroy-on-close
    >
      <el-form :model="tplForm" label-width="112px" label-position="right">
        <el-form-item label="模板编号" v-if="tplForm.isNew">
          <el-input v-model="tplForm.template_id" placeholder="留空自动生成（T-时间戳）" />
        </el-form-item>
        <el-form-item label="标题" required>
          <el-input v-model="tplForm.title" />
        </el-form-item>
        <el-form-item label="条款类型">
          <el-select v-model="tplForm.clause_type" style="width: 100%">
            <el-option v-for="(label, key) in clauseTypeDict" :key="key" :label="label" :value="key" />
          </el-select>
        </el-form-item>
        <el-form-item label="适用合同类型">
          <el-select v-model="tplForm.contract_types" multiple clearable style="width: 100%" placeholder="留空 = 全类型">
            <el-option v-for="t in CONTRACT_TYPES" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="风险等级">
          <el-radio-group v-model="tplForm.risk_level">
            <el-radio-button v-for="o in RISK_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="示范条款正文">
          <el-input v-model="tplForm.content" type="textarea" :rows="7" placeholder="可直接落进合同的标准化条款文本" />
        </el-form-item>
        <el-form-item label="适用场景">
          <el-input v-model="tplForm.scenario" placeholder="如 供应商标准采购合同 / 大额销售合同" />
        </el-form-item>
        <el-form-item label="检索关键词">
          <el-select v-model="tplForm.keywords" multiple filterable allow-create default-first-option style="width: 100%" />
          <div class="form-hint">BM25 检索用它匹配风险条款，关键词直接决定能不能召回</div>
        </el-form-item>
        <el-form-item label="法律依据">
          <el-input v-model="tplForm.legal_basis" />
        </el-form-item>
        <el-form-item label="标签">
          <el-select v-model="tplForm.tags" multiple filterable allow-create default-first-option style="width: 100%" />
        </el-form-item>
        <el-form-item label="版本">
          <el-input v-model="tplForm.version" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="tplForm.enabled" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="tplVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveTpl">保存</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.page-title {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 600;
}
.sub {
  margin: 0;
  color: var(--ink-3);
  font-size: 12px;
}
.head-btns {
  display: flex;
  gap: 8px;
}
.mb {
  margin-bottom: 12px;
}
.mt {
  margin-top: 12px;
}
.toolbar-card {
  margin-bottom: 4px;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.tb-label {
  font-size: 12px;
  color: var(--ink-3);
}
.spacer {
  flex: 1;
}
.cat-title {
  font-weight: 600;
  margin-right: 8px;
}
.mr {
  margin-right: 4px;
}
.muted {
  color: var(--ink-3);
}
.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  color: #475569;
}
.ellipsis {
  color: var(--ink-3);
}
.form-hint {
  font-size: 11px;
  color: var(--ink-3);
  line-height: 1.6;
  margin-top: 2px;
}
</style>
