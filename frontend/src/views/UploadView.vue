<script setup>
/**
 * 上传合同（需求 2.4.3「合同审查大盘页 … 提供手动上传合同入口」）。
 *
 * 流程：**一次可选多份文件/图片** → 填业务信息 → 提交 → 后端落盘并跑流水线 →
 * 轮询每份文件的状态 → 完成后**在本页直接给出解析结果**：
 *   左栏 = 批次文件切换条 + 当前文件的合同原文（风险高亮叠层），
 *   右栏 = **当前文件**的风险清单 + 修改建议，
 *   两栏双向联动（点卡片 → 原文定位；点高亮 → 卡片选中）。
 *
 * 多文件的语义（与后端一致，改动前先看 app/services/batch_service.py）：
 *   **一份文件一条任务**，用批次号归组。批次只做展示分组，不参与业务计算 ——
 *   所以「点哪个文件，右侧就显示哪个文件的风险清单与建议」是本页的核心交互。
 * 完整复核动线（采纳/驳回、回写、报告）仍走「进入审查工作台」。
 *
 * 权限：需要 task.submit / task.upload（业务经办人）。管理员与法务走这里会被路由守卫拦掉。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { apiReport, apiSystem, apiTasks } from '../api'
import HighlightDocument from '../components/HighlightDocument.vue'
import SamplePicker from '../components/SamplePicker.vue'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()

const BUSINESS_TYPES = [
  { value: 'purchase', label: '采购合同' },
  { value: 'sales', label: '销售合同' },
  { value: 'service', label: '服务合同' },
  { value: 'labor', label: '劳动合同' },
  { value: 'other', label: '其他合同' },
]

const ACCEPT = '.docx,.doc,.pdf,.png,.jpg,.jpeg,.bmp,.tiff'
const MAX_MB = 50
/** 单次最多几份文件（与后端 MAX_BATCH_FILES 一致；真正的边界在后端） */
const MAX_FILES = 10

const form = reactive({
  title: '',
  business_type: 'purchase',
  applicant: '',
  department: '',
  contract_no: '',
  run_now: true,
})

const uploadRef = ref(null)
/** 已选文件：[{ uid, name, size, raw }]。**这是唯一事实来源** ——
 *  el-upload 自己的内部列表只当草稿纸用（show-file-list 关掉），
 *  这样才能干净地做「按份校验 + 按份移除」，也不受它 limit 计数的干扰。 */
const picked = ref([])
/** 自动填入的标题值，用来判断用户有没有手动改过标题 */
const autoTitle = ref('')
const submitting = ref(false)

// ----------------------------------------------------------------------
// OCR 能力感知：**在选文件那一刻**就告诉用户「本部署没开 OCR」，别等提交完才爆红。
// 背景（2026-09-19 线上演示版踩到）：图片永远走 ImageOcrParser（`ocr_enabled` 只管
// PDF 的兜底，见 app/engines/parsing/base.py::get_parser），演示包没装 rapidocr →
// 6 张 jpg 提交后全部「解析受阻：OCR 引擎不可用」。行为本身是设计内降级，
// 但提示出现得太晚、太吓人，像系统坏了。现在：
//   /health 公开下发 ocr_enabled（与 llm_ready 同级的能力位），
//   选了图片且 OCR 关着 → 文件列表上方挂黄色警告（不禁用 —— 让人能试到真实行为，
//   符合本项目「默认禁用而非隐藏」的取向反过来推：能跑就跑，跑不了说清楚）。
// ----------------------------------------------------------------------
const ocrEnabled = ref(true) // 拿不到能力位时按「开着」处理：别因为探活失败拦住正常流程
const IMAGE_EXT_RE = /\.(png|jpe?g|bmp|tiff?|webp)$/i
const imagePickedCount = computed(() => picked.value.filter((f) => IMAGE_EXT_RE.test(f.name)).length)

onMounted(async () => {
  try {
    const h = await apiSystem.health()
    if (h && h.ocr_enabled === false) ocrEnabled.value = false
  } catch {
    /* 探活失败保持默认 true —— 上传链路本身不依赖这个探活 */
  }
})

// ----------------------------------------------------------------------
// 提交进度：**分两段，传输阶段封顶 90%，跑完之前绝不显示 100%**
// ----------------------------------------------------------------------
// 为什么不能直接拿 onUploadProgress 当进度：它只反映「文件字节传完了」。
// 之后后端还要 落盘 → 解析正文/元数据/条款 → 跑规则 + 大模型审查 → 回读各份详情，
// 这段时间活儿一点没少干。老写法在字节传完就顶到 100%，
// 于是「按钮还在转圈、进度条却显示已完成」—— 属于撒谎式的进度。
//
// 现在：
//   uploading  传输阶段，走真实百分比但封顶 TRANSFER_CAP（好看且不撒谎）
//   processing 服务端解析/审查阶段，**不确定态**：条纹流动 + 整条左右扫描
//   done       整批真跑完（含首次详情回读）才落到 100%，短暂停留后收起
const TRANSFER_CAP = 90
const DONE_HOLD_MS = 1100
const submitPhase = ref('idle') // idle | uploading | processing | done
const rawPct = ref(0) // 传输原始百分比 0~100，只用来映射到前 90%
let doneTimer = null

/** 进度条百分比：done 才是 100；processing 给满宽交给 indeterminate 去扫 */
const barPct = computed(() => {
  if (submitPhase.value === 'done') return 100
  if (submitPhase.value === 'processing') return 100
  if (submitPhase.value === 'uploading') {
    return Math.min(TRANSFER_CAP, Math.round((rawPct.value / 100) * TRANSFER_CAP))
  }
  return 0
})
/** 不确定态：内层整条从 -100% 扫到 100%（见 element-plus 的 indeterminate 关键帧） */
const barIndeterminate = computed(() => submitPhase.value === 'processing')
/** 有条纹 + 条纹滚动 = 流线感 */
const barStriped = computed(
  () => submitPhase.value === 'uploading' || submitPhase.value === 'processing'
)
/** 完成态的颜色。⚠️ 这里**不能用 `status="success"`** —— element-plus 在有 status 时
 *  会用对勾图标**替换掉百分比文字**（见其 statusIcon 渲染分支），
 *  于是「跑完才显示 100%」这条需求直接落空（踩过：完成态 text 读出来是空串）。
 *  所以颜色单独用 color 给，文字继续走 format。 */
const barColor = computed(() => (submitPhase.value === 'done' ? '#16a34a' : '#2563eb'))

/** 进度条右侧文案：**只有真完成才出现 100%**，其余给阶段说明而非假数字 */
function progressFormat(p) {
  if (submitPhase.value === 'done') return '100%'
  if (submitPhase.value === 'processing') return '审查中…'
  return `${p}%`
}

const liveStageText = computed(() => {
  if (submitPhase.value === 'uploading') {
    return `正在上传 ${picked.value.length} 份文件…`
  }
  if (submitPhase.value === 'processing') {
    return '服务端解析正文与条款，正在跑规则与大模型审查…'
  }
  if (submitPhase.value === 'done') return '提交完成'
  return ''
})

function clearDoneTimer() {
  if (doneTimer) {
    clearTimeout(doneTimer)
    doneTimer = null
  }
}
function resetPhase() {
  clearDoneTimer()
  submitPhase.value = 'idle'
  rawPct.value = 0
}

// ---- 提交后的批次跟踪 ----
const batchId = ref('')
const briefs = ref([])          // 批次内每张单的摘要（TaskBrief）
const batchCtx = ref(null)      // 批次上下文（文件名 / 序号 / 各份风险统计）
const activeTaskId = ref('')    // 当前正在看哪一份
const result = ref(null)        // 当前这份的详情（含全文 blocks + 风险项）
const activeRiskId = ref('')
const polling = ref(false)
/** 详情缓存：切换文件时不必重复拉（轮询刷新后会覆盖） */
let detailCache = new Map()
let timer = null
let pollCount = 0
const MAX_POLL = 60

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

const STATUS_TAG = {
  pending: 'info',
  parsing: 'warning',
  reviewing: 'warning',
  blocked: 'danger',
  completed: 'success',
}
const RISK_TAG = { high: 'danger', medium: 'warning', low: 'info' }
const RISK_ORDER = { high: 3, medium: 2, low: 1 }
const CONCLUSION_TAG = { reject: 'danger', rectify: 'warning', pass: 'success' }

/** 多文件才叫「批次」；单文件在界面上不该出现批次概念 */
const isBatch = computed(() => briefs.value.length > 1)
const batchItems = computed(() => batchCtx.value?.items || [])
const batchSummary = computed(() => batchCtx.value?.summary || null)
const activeBrief = computed(() => briefs.value.find((b) => b.id === activeTaskId.value) || null)
const running = computed(() => briefs.value.filter((b) =>
  ['pending', 'parsing', 'reviewing'].includes(b.status)
).length)

const finished = computed(
  () => result.value?.status === 'completed' && !!result.value?.doc
)
const activeBlocked = computed(() => result.value?.status === 'blocked')
/** 结果卡：当前这份跑完了就显示（完成或受阻都要给用户反馈） */
const showResult = computed(() => finished.value || activeBlocked.value)

/** 风险清单：高风险优先，同级按编号（与工作台一致） */
const risks = computed(() => {
  return [...(result.value?.risk_items || [])].sort(
    (a, b) =>
      (RISK_ORDER[b.risk_level] || 0) - (RISK_ORDER[a.risk_level] || 0) ||
      (a.risk_no || 0) - (b.risk_no || 0)
  )
})
const doc = computed(() => result.value?.doc || null)
const metadata = computed(() => result.value?.metadata || [])

/** 当前文件的原始文件名（切换条与标题都用它） */
const activeFilename = computed(
  () =>
    batchItems.value.find((i) => i.task_id === activeTaskId.value)?.filename ||
    activeBrief.value?.title ||
    ''
)

/** 解析概况：让用户一眼看到「到底解析出了什么」 */
const parseInfo = computed(() => {
  const f = (result.value?.files || [])[0] || {}
  return {
    blocks: doc.value?.blocks?.length || 0,
    clauses: result.value?.clauses?.length || 0,
    pages: doc.value?.page_count || f.page_count || 0,
    parser: f.parser || '—',
    fromCache: !!f.from_cache,
    chars: doc.value?.full_text?.length || 0,
    metaHit: metadata.value.filter((m) => m.value).length,
    metaTotal: metadata.value.length,
  }
})

function stopPoll() {
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
  polling.value = false
}

function fmtSize(n) {
  if (!n && n !== 0) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

function stemOf(name) {
  return String(name || '').replace(/\.[^.]+$/, '')
}

/** 某份文件的风险数（批次切换条上的徽标） */
function riskTotalOf(taskId) {
  const it = batchItems.value.find((i) => i.task_id === taskId)
  if (it) return it.risk_total || 0
  const b = briefs.value.find((x) => x.id === taskId)
  const c = b?.risk_counts || {}
  return Object.values(c).reduce((a, v) => a + (v || 0), 0)
}

function filenameOf(taskId) {
  return (
    batchItems.value.find((i) => i.task_id === taskId)?.filename ||
    briefs.value.find((b) => b.id === taskId)?.title ||
    taskId.slice(0, 8)
  )
}

// ----------------------------------------------------------------------
// 文件选择（多份）
// ----------------------------------------------------------------------
function validate(raw) {
  const ext = `.${(raw.name.split('.').pop() || '').toLowerCase()}`
  if (!ACCEPT.split(',').includes(ext)) return `不支持的文件格式：${ext}`
  if (raw.size > MAX_MB * 1024 * 1024) return `「${raw.name}」超过 ${MAX_MB}MB 上限`
  return ''
}

/**
 * el-upload 每加入一份文件就回调一次。
 * 注意：这里**只从自己的 picked 取数**，不读 el-upload 的内部列表 ——
 * 否则「移除某一份」之后组件内部还留着它，会复活。
 */
function onFileChange(file) {
  const raw = file?.raw
  if (!raw) return
  if (picked.value.some((p) => p.uid === file.uid)) return

  if (picked.value.length >= MAX_FILES) {
    ElMessage.warning(`一次最多上传 ${MAX_FILES} 份文件`)
    return
  }
  const bad = validate(raw)
  if (bad) {
    ElMessage.error(bad)
    return
  }
  picked.value.push({ uid: file.uid, name: raw.name, size: raw.size, raw })
  syncAutoTitle()
}

function removeAt(i) {
  picked.value.splice(i, 1)
  syncAutoTitle()
}

/** 标题：只有一份文件时用文件名兜底；变成多份时把自动值收回去（让各份用自己的文件名） */
function syncAutoTitle() {
  if (picked.value.length === 1) {
    const stem = stemOf(picked.value[0].name)
    if (!form.title || form.title === autoTitle.value) {
      form.title = stem
      autoTitle.value = stem
    }
  } else if (form.title && form.title === autoTitle.value) {
    form.title = ''
    autoTitle.value = ''
  }
}

// ----------------------------------------------------------------------
// 提交
// ----------------------------------------------------------------------
async function submit() {
  if (!picked.value.length) {
    ElMessage.warning('请先选择合同附件')
    return
  }

  submitting.value = true
  resetPhase()
  submitPhase.value = 'uploading'
  try {
    const fd = new FormData()
    picked.value.forEach((p) => fd.append('files', p.raw))
    if (form.title.trim()) fd.append('title', form.title.trim())
    fd.append('business_type', form.business_type)
    if (form.applicant) fd.append('applicant', form.applicant)
    if (form.department) fd.append('department', form.department)
    if (form.contract_no) fd.append('contract_no', form.contract_no)
    fd.append('run_now', String(form.run_now))

    const out = await apiTasks.uploadBatch(fd, (e) => {
      if (!e.total) return
      rawPct.value = Math.min(100, Math.round((e.loaded / e.total) * 100))
      // 字节传完 ≠ 活儿干完：马上转不确定态，别把 100% 提前放出来
      if (rawPct.value >= 100 && submitPhase.value === 'uploading') {
        submitPhase.value = 'processing'
      }
    })

    // 兜底：万一 onUploadProgress 没给到 total（拿不到完整回调），
    // 响应回来就说明传输已结束，同样切到服务端处理态，别卡在 0%。
    if (submitPhase.value === 'uploading') submitPhase.value = 'processing'

    batchId.value = out.batch_id
    briefs.value = out.tasks || []
    batchCtx.value = out.batch || null
    detailCache = new Map()
    activeTaskId.value = briefs.value[0]?.id || ''
    activeRiskId.value = ''
    result.value = null
    pollCount = 0

    const n = briefs.value.length
    ElMessage.success(
      n > 1
        ? `已提交 ${n} 份文件，正在跟踪各份审查进度…`
        : briefs.value[0]?.status === 'completed'
          ? '解析与审查已完成'
          : '已提交审查申请，正在跟踪审查进度…'
    )

    if (form.run_now) {
      await refreshAll()
      if (running.value) startPoll()
    } else {
      startPoll()
    }

    // 到这一份才算「提交 + 首批回读」真的完成 → 现在才允许出现 100%
    submitPhase.value = 'done'
    clearDoneTimer()
    doneTimer = setTimeout(() => {
      if (submitPhase.value === 'done') resetPhase()
    }, DONE_HOLD_MS)
  } catch (e) {
    // 失败不能留着半截进度条骗人，直接收起（错误提示由 http 拦截器统一弹）
    resetPhase()
  } finally {
    submitting.value = false
  }
}

/** 把详情里的字段贴回批次摘要（详情就是 TaskBrief 的超集） */
function patchBrief(d) {
  const i = briefs.value.findIndex((b) => b.id === d.id)
  if (i >= 0) briefs.value.splice(i, 1, { ...briefs.value[i], ...d })
}

/** 拉当前这一份的详情；缓存命中就直接用（切换文件要快） */
async function loadActive() {
  const tid = activeTaskId.value
  if (!tid) return
  const d = detailCache.get(tid) || (await apiTasks.detail(tid))
  detailCache.set(tid, d)
  patchBrief(d)
  if (d.batch) batchCtx.value = d.batch
  result.value = d
}

/** 刷新批次内所有还没结束的单（份数 ≤ 10，串行足够） */
async function refreshAll() {
  for (const b of [...briefs.value]) {
    if (['completed', 'blocked'].includes(b.status)) continue
    try {
      const d = await apiTasks.detail(b.id)
      detailCache.set(b.id, d)
      patchBrief(d)
      if (d.batch) batchCtx.value = d.batch
    } catch {
      /* 单份拉取失败不该中断整批刷新 */
    }
  }
  await loadActive()
}

function startPoll() {
  stopPoll()
  polling.value = true
  timer = setTimeout(tick, 1200)
}

function tick() {
  pollCount += 1
  refreshAll()
    .then(() => {
      if (!running.value) {
        stopPoll()
        const bad = briefs.value.filter((b) => b.status === 'blocked').length
        if (bad) ElMessage.warning(`${bad} 份解析受阻，需要管理员处理或重传文件`)
        else ElMessage.success('全部审查完成')
        return
      }
      if (pollCount >= MAX_POLL) {
        stopPoll()
        ElMessage.warning('轮询超时，可稍后在审查大盘查看进度')
        return
      }
      timer = setTimeout(tick, 2000)
    })
    .catch(() => {
      if (pollCount < MAX_POLL) timer = setTimeout(tick, 3000)
      else stopPoll()
    })
}

/** 切换当前文件：左侧原文与右侧风险清单一起跟着换 */
async function selectFile(taskId) {
  if (taskId === activeTaskId.value) return
  activeTaskId.value = taskId
  activeRiskId.value = ''
  result.value = null
  await loadActive()
}

function reset() {
  stopPoll()
  picked.value = []
  uploadRef.value?.clearFiles()
  detailCache = new Map()
  batchId.value = ''
  briefs.value = []
  batchCtx.value = null
  activeTaskId.value = ''
  result.value = null
  activeRiskId.value = ''
  resetPhase()
  pollCount = 0
  form.title = ''
  autoTitle.value = ''
  form.contract_no = ''
}

function goWorkbench() {
  if (activeTaskId.value) router.push({ name: 'task-detail', params: { id: activeTaskId.value } })
}

async function openReport(fmt) {
  // 走 apiReport.downloadUrl：它会把访问令牌拼在查询串上
  // （window.open 是浏览器直连，带不了 Authorization 头）
  window.open(apiReport.downloadUrl(activeTaskId.value, fmt), '_blank')
}

// ----------------------------------------------------------------------
// 原文 ↔ 风险卡片 双向联动（与工作台同一套约定）
// ----------------------------------------------------------------------
/** 点卡片：只设 activeRiskId，由 HighlightDocument 滚动原文并闪烁段落 */
function selectRisk(risk) {
  activeRiskId.value = risk?.id || ''
}

/** 点原文高亮：选中对应卡片并滚进视野 */
async function onMarkClick(riskId) {
  activeRiskId.value = riskId
  await nextTick()
  document
    .querySelector(`[data-up-risk="${riskId}"]`)
    ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

/** 建议条款：法务编辑过的 > 最终稿 > 引擎建议 */
function suggestedOf(r) {
  const s = r.suggestion
  if (!s) return ''
  return s.edited_text || s.final_text || s.suggested_text || ''
}

/** 原条款：优先建议里的 original_text，退回风险项引用的原文片段 */
function originalOf(r) {
  return r.suggestion?.original_text || r.quote || ''
}

async function copyText(text) {
  if (!text) {
    ElMessage.warning('没有可复制的文本')
    return
  }
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text)
    else {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    ElMessage.success('修改建议已复制')
  } catch (e) {
    ElMessage.error('复制失败，请手动选中复制')
  }
}

onBeforeUnmount(() => {
  stopPoll()
  clearDoneTimer()
})
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h2 class="page-title">上传合同</h2>
        <p class="sub">
          支持 DOCX / PDF / 图片扫描件（单份 ≤ {{ MAX_MB }}MB，一次最多 {{ MAX_FILES }} 份）。
          提交后逐份解析、审查，并在本页给出风险清单、修改建议与原文高亮。
        </p>
      </div>
      <div class="head-right">
        <el-tag size="small" effect="plain">{{ auth.roleName }} · {{ auth.displayName }}</el-tag>
      </div>
    </div>

    <el-row :gutter="16">
      <!-- 左：上传 + 表单 -->
      <el-col :span="15">
        <el-card shadow="never" header="1. 选择合同附件">
          <el-upload
            ref="uploadRef"
            drag
            multiple
            :auto-upload="false"
            :show-file-list="false"
            :accept="ACCEPT"
            :on-change="onFileChange"
          >
            <el-icon class="up-icon"><UploadFilled /></el-icon>
            <div class="up-text">
              把合同拖到这里，或<em>点击选择文件</em>（可按住 Ctrl / Shift 多选，也可一次拖入多份）
            </div>
            <template #tip>
              <div class="up-tip">
                每份文件各生成一条审查单，同一批上传归为同一批次。文件名作为标题默认值。
                <template v-if="!ocrEnabled">（当前未启用 OCR：图片 / 扫描件无法解析，请传 DOCX / 文字版 PDF）</template>
              </div>
            </template>
          </el-upload>

          <div v-if="picked.length" class="picked-box">
            <el-alert
              v-if="!ocrEnabled && imagePickedCount > 0"
              type="warning"
              :closable="false"
              show-icon
              class="ocr-warn"
              title="本部署未启用 OCR（文字识别）"
              :description="`已选 ${imagePickedCount} 份图片 / 扫描件 —— 图片必须走 OCR 才能读出文字，提交后这几份会落到「解析受阻」。文字版请改传 DOCX 或可复制文字的 PDF，其余文件不受影响。`"
            />
            <div class="picked-head">
              已选 <strong>{{ picked.length }}</strong> 份
              <span v-if="picked.length > MAX_FILES" class="over">（超出一次上限）</span>
              <div class="spacer"></div>
              <el-button link type="primary" size="small" @click="reset">全部清空</el-button>
            </div>
            <div v-for="(f, i) in picked" :key="f.uid" class="picked">
              <el-icon><Document /></el-icon>
              <span class="picked-name" :title="f.name">{{ f.name }}</span>
              <span class="picked-size">{{ fmtSize(f.size) }}</span>
              <el-button link type="danger" size="small" @click="removeAt(i)">移除</el-button>
            </div>
          </div>
        </el-card>

        <el-card shadow="never" header="2. 填写送审信息" class="mt">
          <el-form :model="form" label-width="92px" label-position="right">
            <el-row :gutter="12">
              <el-col :span="12">
                <el-form-item label="合同标题">
                  <el-input
                    v-model="form.title"
                    :placeholder="picked.length > 1 ? '选填；留空则各份用各自文件名' : '如：XX 软件采购合同'"
                    clearable
                  />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="业务类型">
                  <el-select v-model="form.business_type" style="width: 100%">
                    <el-option v-for="t in BUSINESS_TYPES" :key="t.value" :label="t.label" :value="t.value" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="申请人">
                  <el-input v-model="form.applicant" :placeholder="`默认 ${auth.role.user}`" clearable />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="送审部门">
                  <el-input v-model="form.department" :placeholder="`默认 ${auth.role.dept}`" clearable />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="合同编号">
                  <el-input v-model="form.contract_no" placeholder="选填" clearable />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="提交后">
                  <el-switch
                    v-model="form.run_now"
                    active-text="立即执行审查"
                    inactive-text="仅建单"
                  />
                </el-form-item>
              </el-col>
            </el-row>
          </el-form>

          <!-- 提交进度：**跑完之前绝不显示 100%**。
               传输阶段走真实百分比（封顶 90%）；服务端解析/审查阶段走条纹流动的不确定态
               （整条左右扫描 + 条纹滚动 = 流线感）；整批真跑完才落到 100%，短暂停留后收起。 -->
          <div v-if="submitPhase !== 'idle'" class="up-progress" :class="{ done: submitPhase === 'done' }">
            <el-progress
              :percentage="barPct"
              :stroke-width="10"
              :indeterminate="barIndeterminate"
              :striped="barStriped"
              :striped-flow="barStriped"
              :color="barColor"
              :duration="1.6"
              :format="progressFormat"
            />
            <div class="up-progress-text">
              <el-icon v-if="submitPhase !== 'done'" class="is-loading"><Loading /></el-icon>
              <el-icon v-else><CircleCheckFilled /></el-icon>
              {{ liveStageText }}
            </div>
          </div>
          <div class="btn-row">
            <el-button
              type="primary"
              :loading="submitting"
              :disabled="!picked.length || submitting"
              @click="submit"
            >
              <el-icon><Position /></el-icon>
              提交审查申请<span v-if="picked.length > 1">（{{ picked.length }} 份）</span>
            </el-button>
            <el-button @click="reset" :disabled="submitting">清空重填</el-button>
          </div>
        </el-card>
      </el-col>

      <!-- 右：示例 + 结果 -->
      <el-col :span="9">
        <el-card shadow="never" header="审查进度与结果">
          <div v-if="!briefs.length" class="empty-side">
            <el-empty description="提交后这里会显示审查进度与结论" :image-size="70" />
          </div>

          <template v-else>
            <div class="task-line">
              <el-tag :type="STATUS_TAG[activeBrief?.status] || 'info'">
                {{ activeBrief?.status_label || activeBrief?.status }}
              </el-tag>
              <span class="task-id mono">{{ activeTaskId.slice(0, 8) }}</span>
              <el-button link type="primary" size="small" @click="refreshAll">刷新</el-button>
            </div>

            <el-progress
              :percentage="activeBrief?.progress || 0"
              :status="activeBrief?.status === 'blocked' ? 'exception' : activeBrief?.status === 'completed' ? 'success' : undefined"
              :stroke-width="12"
            />
            <div class="stage">
              {{ STAGE_LABEL[activeBrief?.stage] || activeBrief?.stage || '等待执行' }}
              <span v-if="polling" class="polling">
                <el-icon class="is-loading"><Loading /></el-icon>
                跟踪中（还有 {{ running }} 份）
              </span>
            </div>

            <!-- 批次清单：一眼看到每份文件的进度，点一行即切换当前文件 -->
            <div v-if="isBatch" class="brief-list">
              <div class="brief-title">
                本批 {{ briefs.length }} 份文件
                <span v-if="batchSummary" class="muted">
                  完成 {{ batchSummary.completed }} · 受阻 {{ batchSummary.blocked }}
                  <template v-if="batchSummary.risk_total">
                    · 合计风险 {{ batchSummary.risk_total }}
                  </template>
                </span>
              </div>
              <div
                v-for="(b, i) in briefs"
                :key="b.id"
                class="brief-row"
                :class="{ active: b.id === activeTaskId }"
                @click="selectFile(b.id)"
              >
                <span class="brief-idx">{{ i + 1 }}</span>
                <span class="brief-name" :title="filenameOf(b.id)">{{ filenameOf(b.id) }}</span>
                <span v-if="riskTotalOf(b.id)" class="brief-risk" :class="`l-${b.overall_risk || 'low'}`">
                  {{ riskTotalOf(b.id) }}
                </span>
                <el-tag size="small" :type="STATUS_TAG[b.status] || 'info'">{{ b.status_label }}</el-tag>
              </div>
            </div>

            <!-- 结论摘要（当前这一份） -->
            <div v-if="activeBrief && ['completed', 'blocked'].includes(activeBrief.status)" class="result">
              <template v-if="activeBrief.status === 'completed'">
                <div class="result-row">
                  <span class="k">综合风险</span>
                  <el-tag :type="RISK_TAG[activeBrief.overall_risk] || 'info'" size="small">
                    {{ activeBrief.overall_risk_label || '无风险' }}
                  </el-tag>
                  <el-tag :type="CONCLUSION_TAG[activeBrief.conclusion] || 'info'" size="small">
                    结论：{{ activeBrief.conclusion_label || '通过' }}
                  </el-tag>
                </div>
                <div class="result-row" v-if="activeBrief.risk_counts">
                  <span class="k">风险分布</span>
                  <span class="risk-counts">
                    <span class="rc high">高 {{ activeBrief.risk_counts.high ?? 0 }}</span>
                    <span class="rc medium">中 {{ activeBrief.risk_counts.medium ?? 0 }}</span>
                    <span class="rc low">低 {{ activeBrief.risk_counts.low ?? 0 }}</span>
                  </span>
                </div>
                <div v-if="activeBrief.risk_summary" class="summary">{{ activeBrief.risk_summary }}</div>
                <div class="hint-line">
                  <el-icon><Bottom /></el-icon>风险清单、修改建议与原文高亮见下方
                </div>
              </template>

              <el-alert
                v-else
                type="error"
                :closable="false"
                show-icon
                :title="`解析受阻：${activeBrief.block_reason_label || activeBrief.block_reason}`"
              >
                <template #default>
                  {{ activeBrief.block_detail || '需要管理员在审查大盘处理阻塞重试，或重新上传可复制文本的文档。' }}
                </template>
              </el-alert>

              <div class="btn-row">
                <el-button type="primary" @click="goWorkbench">
                  进入审查工作台<el-icon><ArrowRight /></el-icon>
                </el-button>
                <el-button
                  v-if="activeBrief.status === 'completed'"
                  v-perm="'report.download'"
                  @click="openReport('md')"
                >
                  下载报告
                </el-button>
              </div>
            </div>
          </template>
        </el-card>

        <el-card shadow="never" header="示例合同" class="mt">
          <SamplePicker />
        </el-card>
      </el-col>
    </el-row>

    <!-- ================================================================
         解析与审查结果：左「批次文件切换条 + 当前文件原文高亮」/ 右「当前文件风险与建议」
         ================================================================ -->
    <el-card v-if="showResult" shadow="never" class="mt result-card">
      <template #header>
        <div class="res-head">
          <span class="res-title">解析与审查结果</span>
          <el-tag v-if="isBatch" size="small" type="warning">
            批次 {{ batchId.slice(0, 8) }} · 共 {{ briefs.length }} 份
          </el-tag>
          <template v-if="batchSummary">
            <el-tag size="small" type="success">完成 {{ batchSummary.completed }}</el-tag>
            <el-tag v-if="batchSummary.blocked" size="small" type="danger">
              受阻 {{ batchSummary.blocked }}
            </el-tag>
            <el-tag size="small" type="info">风险合计 {{ batchSummary.risk_total }}</el-tag>
          </template>
          <template v-if="finished">
            <el-tag size="small" type="info">{{ parseInfo.blocks }} 段</el-tag>
            <el-tag size="small" type="info">{{ parseInfo.clauses }} 个条款</el-tag>
            <el-tag size="small" type="info">{{ parseInfo.pages }} 页</el-tag>
            <el-tag v-if="parseInfo.fromCache" size="small" type="success">缓存命中</el-tag>
          </template>
          <div class="spacer"></div>
          <span class="muted">点卡片定位原文，点高亮选中卡片</span>
        </div>
      </template>

      <el-row :gutter="16">
        <!-- 左：文件切换条 + 当前文件的全文高亮 -->
        <el-col :span="14">
          <!-- 批次文件切换条：一眼看清本批有哪几份、各命中几条风险 -->
          <div v-if="isBatch" class="batch-strip">
            <div
              v-for="(it, i) in batchItems"
              :key="it.task_id"
              class="batch-chip"
              :class="{ active: it.task_id === activeTaskId }"
              :data-batch-task="it.task_id"
              :data-batch-status="it.status"
              :title="it.filename"
              @click="selectFile(it.task_id)"
            >
              <span class="chip-seq">{{ i + 1 }}</span>
              <span class="chip-name">{{ it.filename }}</span>
              <span
                v-if="it.risk_total"
                class="chip-risk"
                :class="`l-${it.overall_risk || 'low'}`"
              >{{ it.risk_total }}</span>
              <el-icon
                v-if="['pending', 'parsing', 'reviewing'].includes(it.status)"
                class="is-loading chip-loading"
              ><Loading /></el-icon>
              <el-icon v-else-if="it.status === 'blocked'" class="chip-bad"><WarningFilled /></el-icon>
              <el-icon v-else class="chip-ok"><CircleCheckFilled /></el-icon>
            </div>
          </div>

          <div class="col-title">
            <template v-if="isBatch">当前文件：</template>
            {{ activeFilename || '合同原文' }}
            <span v-if="finished" class="muted">
              （{{ parseInfo.chars }} 字，风险处已高亮）
            </span>
          </div>

          <el-alert
            v-if="activeBlocked"
            type="error"
            :closable="false"
            show-icon
            title="这一份解析受阻，没有可高亮的原文"
          >
            <template #default>
              {{ result?.block_detail || '请切换其它文件查看，或让管理员在审查大盘处理阻塞重试。' }}
            </template>
          </el-alert>
          <HighlightDocument
            v-else-if="finished"
            :doc="doc"
            :risks="risks"
            :active-risk-id="activeRiskId"
            @risk-click="onMarkClick"
          />
        </el-col>

        <!-- 右：当前文件的风险清单 + 修改建议 -->
        <el-col :span="10">
          <div class="col-title">
            <span v-if="isBatch" class="rk-owner">{{ activeFilename }}</span>
            风险清单与修改要求（{{ risks.length }} 条）
          </div>
          <div class="rk-list">
            <el-empty v-if="!risks.length" description="未命中风险项" :image-size="60" />
            <el-card
              v-for="r in risks"
              :key="r.id"
              shadow="never"
              class="rk-card"
              :class="[`rk-${r.risk_level}`, { active: activeRiskId === r.id }]"
              :data-up-risk="r.id"
              @click="selectRisk(r)"
            >
              <div class="rk-head">
                <span class="rk-no">R{{ r.risk_no }}</span>
                <el-tag :type="RISK_TAG[r.risk_level] || 'info'" size="small">
                  {{ r.risk_level_label }}
                </el-tag>
                <strong class="rk-name">{{ r.name }}</strong>
                <div class="spacer"></div>
                <span class="rk-src">{{ r.rule_id ? `规则 ${r.rule_id}` : 'LLM 研判' }}</span>
              </div>

              <div class="rk-loc">
                <el-icon><Location /></el-icon>
                {{ r.location_label }} · {{ r.clause_type_label }}
              </div>

              <div v-if="originalOf(r)" class="rk-quote" :class="`q-${r.risk_level}`">
                「{{ originalOf(r) }}」
              </div>

              <div class="rk-reason">{{ r.reason }}</div>
              <div v-if="r.legal_basis" class="rk-basis">
                <el-icon><Stamp /></el-icon>{{ r.legal_basis }}
              </div>

              <div class="rk-sug">
                <div class="rk-sug-head">
                  <span class="rk-sug-title">建议修改为（示范条款）</span>
                  <div class="spacer"></div>
                  <el-button link size="small" @click.stop="copyText(suggestedOf(r))">
                    <el-icon><CopyDocument /></el-icon>复制
                  </el-button>
                </div>
                <pre v-if="suggestedOf(r)" class="rk-sug-text">{{ suggestedOf(r) }}</pre>
                <div v-else class="rk-sug-none">本条为提示类风险，暂无示范条款。</div>
                <div v-if="r.suggestion?.rationale" class="rk-sug-why">
                  依据：{{ r.suggestion.rationale }}
                </div>
              </div>
            </el-card>
          </div>
        </el-col>
      </el-row>
    </el-card>
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
  margin: 0 0 6px;
  font-size: 20px;
  font-weight: 600;
}
.sub {
  margin: 0;
  color: var(--ink-3);
  font-size: 13px;
  line-height: 1.7;
}
.up-icon {
  font-size: 46px;
  color: var(--ink-3);
  margin-top: 22px;
}
.up-text {
  color: var(--ink-3);
  font-size: 13px;
  margin: 8px 0 22px;
}
.up-text em {
  color: var(--el-color-primary);
  font-style: normal;
}
.up-tip {
  color: var(--ink-3);
  font-size: 12px;
  margin-top: 6px;
  text-align: center;
  line-height: 1.7;
}
.picked-box {
  margin-top: 12px;
}
.ocr-warn {
  margin-bottom: 8px;
}
.picked-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--ink-3);
  margin-bottom: 6px;
}
.picked-head .over {
  color: #e5484d;
}
.picked {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--bg-hover);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 6px 12px;
  font-size: 13px;
  margin-bottom: 6px;
}
.picked-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.picked-size {
  color: var(--ink-3);
  font-size: 12px;
}
.mt {
  margin-top: 16px;
}
.btn-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 12px;
}

/* ---------- 提交进度（传输→不确定态→完成） ---------- */
.up-progress {
  margin-top: 14px;
}
/* 条纹滚动：把 element 的默认 3s 调快一点，流线感更明显（inline animationDuration 生效） */
.up-progress :deep(.el-progress-bar__inner--striped-flow) {
  animation-timing-function: linear;
}
/* element 默认条纹是 rgba(0,0,0,.1)，压在蓝条上几乎看不见 —— 换成浅色高对比，
   「流线」才真的看得出来（配合 striped-flow 的滚动，就是一条在跑的斜向光带） */
.up-progress :deep(.el-progress-bar__inner--striped) {
  background-image: linear-gradient(
    45deg,
    rgba(255, 255, 255, 0.32) 25%,
    transparent 25%,
    transparent 50%,
    rgba(255, 255, 255, 0.32) 50%,
    rgba(255, 255, 255, 0.32) 75%,
    transparent 75%,
    transparent
  );
  background-size: 1.1em 1.1em;
}
/* 百分比文字：跟着进度条的蓝走，完成态转绿（用 color 而不是 status，见 script 里的注释） */
.up-progress :deep(.el-progress__text) {
  color: var(--el-color-primary);
  font-weight: 600;
}
.up-progress.done :deep(.el-progress__text) {
  color: #16a34a;
}
.up-progress-text {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--ink-3);
  line-height: 1.6;
}
.up-progress-text .is-loading {
  color: var(--el-color-primary);
}
.up-progress.done .up-progress-text {
  color: #16a34a;
}
.empty-side {
  padding: 10px 0;
}
.task-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.task-id {
  color: var(--ink-3);
  font-size: 12px;
  flex: 1;
}
.mono {
  font-family: Consolas, Monaco, monospace;
}
.stage {
  margin-top: 8px;
  font-size: 12px;
  color: var(--ink-3);
  display: flex;
  align-items: center;
  gap: 8px;
}
.polling {
  color: #f5a524;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

/* ---------- 批次清单（右侧卡片内） ---------- */
.brief-list {
  margin-top: 12px;
  border-top: 1px dashed var(--line);
  padding-top: 10px;
}
.brief-title {
  font-size: 12px;
  color: var(--ink-3);
  margin-bottom: 6px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.brief-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 6px;
  border-radius: 5px;
  cursor: pointer;
  font-size: 12px;
}
.brief-row:hover {
  background: #f5f7fa;
}
.brief-row.active {
  background: var(--el-color-primary-light-9);
}
.brief-idx {
  color: var(--ink-3);
  width: 14px;
  text-align: right;
}
.brief-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.brief-risk {
  font-weight: 700;
}
.brief-risk.l-high {
  color: var(--risk-high);
}
.brief-risk.l-medium {
  color: var(--risk-medium);
}
.brief-risk.l-low {
  color: var(--risk-low);
}

.result {
  margin-top: 14px;
  border-top: 1px dashed var(--line);
  padding-top: 12px;
}
.result-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 13px;
}
.k {
  color: var(--ink-3);
  font-size: 12px;
  width: 56px;
}
.risk-counts {
  display: flex;
  gap: 10px;
  font-size: 12px;
}
.rc.high {
  color: var(--risk-high);
}
.rc.medium {
  color: var(--risk-medium);
}
.rc.low {
  color: var(--risk-low);
}
.summary {
  background: var(--bg-hover);
  border-radius: 6px;
  padding: 10px 12px;
  font-size: 13px;
  color: var(--ink-2);
  line-height: 1.8;
  margin-bottom: 10px;
}
.hint-line {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--el-color-primary);
  margin-bottom: 6px;
}

/* ---------- 解析与审查结果 ---------- */
.result-card :deep(.el-card__header) {
  padding: 10px 16px;
}
.res-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.res-title {
  font-size: 15px;
  font-weight: 600;
}
.spacer {
  flex: 1;
}
.muted {
  color: var(--ink-3);
  font-size: 12px;
}

/* 批次文件切换条：多文件时出现在原文上方，点哪份看哪份 */
.batch-strip {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding-bottom: 8px;
  margin-bottom: 8px;
  border-bottom: 1px dashed var(--line);
}
.batch-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
  max-width: 240px;
  padding: 5px 10px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--bg-hover);
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
  color: var(--ink-3);
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

.col-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}
.rk-owner {
  color: var(--el-color-primary);
  font-weight: 600;
  margin-right: 6px;
}
.rk-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 72vh;
  overflow: auto;
  padding-right: 4px;
}
.rk-card {
  cursor: pointer;
  border-left-width: 4px;
  transition: box-shadow 0.2s;
}
.rk-card.rk-high {
  border-left-color: var(--risk-high);
}
.rk-card.rk-medium {
  border-left-color: var(--risk-medium);
}
.rk-card.rk-low {
  border-left-color: var(--risk-low);
}
.rk-card.active {
  box-shadow: 0 0 0 2px var(--el-color-primary-light-7);
}
.rk-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.rk-no {
  font-weight: 700;
}
.rk-name {
  font-size: 14px;
}
.rk-src {
  color: var(--ink-3);
  font-size: 12px;
}
.rk-loc {
  margin-top: 6px;
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--ink-3);
  font-size: 12px;
}
.rk-quote {
  margin-top: 8px;
  border-left: 3px solid var(--risk-low);
  background: var(--bg-hover);
  border-radius: 0 6px 6px 0;
  padding: 8px 12px;
  color: var(--ink-2);
  font-size: 13px;
  line-height: 1.7;
}
.rk-quote.q-high {
  border-left-color: var(--risk-high);
  background: rgba(229, 72, 77, 0.07);
}
.rk-quote.q-medium {
  border-left-color: var(--risk-medium);
  background: rgba(245, 165, 36, 0.08);
}
.rk-reason {
  margin-top: 8px;
  color: var(--ink-2);
  line-height: 1.7;
  font-size: 13px;
}
.rk-basis {
  margin-top: 6px;
  color: var(--ink-3);
  font-size: 12px;
  display: flex;
  gap: 4px;
  align-items: flex-start;
}
.rk-sug {
  margin-top: 10px;
  border-top: 1px dashed var(--line);
  padding-top: 10px;
}
.rk-sug-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.rk-sug-title {
  font-size: 13px;
  font-weight: 600;
  color: #15803d;
}
.rk-sug-text {
  margin: 6px 0 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.75;
  background: rgba(34, 197, 94, 0.12);
  border: 1px solid #bbf7d0;
  border-radius: 6px;
  padding: 8px 12px;
  color: #166534;
}
.rk-sug-none {
  margin-top: 6px;
  font-size: 12px;
  color: var(--ink-3);
}
.rk-sug-why {
  margin-top: 6px;
  font-size: 11px;
  color: var(--ink-3);
  line-height: 1.6;
}
</style>
