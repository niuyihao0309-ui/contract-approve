<script setup>
import { computed, nextTick, ref, watch } from 'vue'

/**
 * 原文文本层高亮组件（文档视窗模块的核心）。
 *
 * 后端把全文拆成 blocks（每个 block 带 text + char_start/char_end），
 * 风险项带全局 char_start/char_end。这里按 block 渲染段落，
 * 在 block 内部计算风险区间的交集，用 <mark> 叠出高亮层 —— 不改原文结构。
 * ⚠️ 硬约束：高亮是**叠层**实现，双向跳转（卡片→原文、原文→卡片）不能破。
 *
 * 对外：
 *   props.activeRiskId 变化 → 滚动到对应段落并闪烁（卡片 → 原文）
 *   emit('risk-click')     → 点高亮片段，父级据此选中右侧卡片（原文 → 卡片）
 */
const props = defineProps({
  doc: { type: Object, default: null },
  risks: { type: Array, default: () => [] },
  activeRiskId: { type: String, default: '' },
})

const emit = defineEmits(['risk-click'])

// 记录每个 block 的 DOM 元素，用于滚动定位
const blockRefs = {}
const scroller = ref(null)

function setBlockRef(el, blockId) {
  if (el) blockRefs[blockId] = el
}

// risk.risk_level -> mark 类名
const LEVEL_CLASS = { high: 'hl-high', medium: 'hl-medium', low: 'hl-low' }

/**
 * 把一份 block 切分成若干片段（文本 / 高亮）。
 * ranges 是 block 内的相对偏移区间，需已按 start 升序、无重叠。
 */
function buildSegments(text, ranges) {
  if (!ranges.length) return [{ type: 'text', text }]
  const segs = []
  let cursor = 0
  for (const r of ranges) {
    if (r.start > cursor) segs.push({ type: 'text', text: text.slice(cursor, r.start) })
    segs.push({ type: 'mark', text: text.slice(r.start, r.end), level: r.level, riskId: r.riskId })
    cursor = r.end
  }
  if (cursor < text.length) segs.push({ type: 'text', text: text.slice(cursor) })
  return segs
}

/** 合并重叠区间（取最高等级），返回 block 内相对区间数组 */
const RISK_ORDER = { high: 3, medium: 2, low: 1 }
function mergeRanges(ranges) {
  const sorted = [...ranges].sort((a, b) => a.start - b.start)
  const out = []
  for (const r of sorted) {
    const last = out[out.length - 1]
    if (last && r.start <= last.end) {
      last.end = Math.max(last.end, r.end)
      if (RISK_ORDER[r.level] > RISK_ORDER[last.level]) {
        last.level = r.level
        last.riskId = r.riskId
      }
    } else {
      out.push({ ...r })
    }
  }
  return out
}

// 每个 block 的渲染片段（blockId -> segments），带 memo 缓存
const segmentsMap = computed(() => {
  const map = {}
  const doc = props.doc
  if (!doc || !doc.blocks) return map
  for (const b of doc.blocks) {
    const hits = props.risks
      .filter((r) => r.block_id && r.block_id === b.block_id)
      .map((r) => ({
        start: Math.max(0, (r.char_start ?? 0) - b.char_start),
        end: Math.min(b.text.length, (r.char_end ?? b.char_end) - b.char_start),
        level: r.risk_level,
        riskId: r.id,
      }))
      .filter((r) => r.end > r.start)
    map[b.block_id] = buildSegments(b.text, mergeRanges(hits))
  }
  return map
})

function blockClass(b) {
  if (b.type === 'title' || b.level) return 'doc-block is-title'
  return 'doc-block'
}

// ---- 点击风险卡片 → 滚动到对应段落（跨页平滑滚动定位） ----
const flashId = ref('')

function scrollToBlock(blockId) {
  const el = blockRefs[blockId]
  if (!el) return false
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  flashId.value = blockId
  setTimeout(() => (flashId.value = ''), 1600)
  return true
}

watch(
  () => props.activeRiskId,
  async (rid) => {
    if (!rid) return
    const risk = props.risks.find((r) => r.id === rid)
    if (!risk || !risk.block_id) return
    await nextTick()
    scrollToBlock(risk.block_id)
  }
)

function onMarkClick(riskId) {
  emit('risk-click', riskId)
}

// ======================================================================
// 定位工具条：字号缩放 + 上/下一条风险 + 页码跳转
// ======================================================================
const zoom = ref(1)
const ZOOM_STEP = 0.1
function zoomIn() {
  zoom.value = Math.min(1.8, +(zoom.value + ZOOM_STEP).toFixed(2))
}
function zoomOut() {
  zoom.value = Math.max(0.8, +(zoom.value - ZOOM_STEP).toFixed(2))
}

/** 页码列表：取 blocks 里出现过的页码（保持顺序） */
const pages = computed(() => {
  const out = []
  for (const b of props.doc?.blocks || []) if (!out.includes(b.page)) out.push(b.page)
  return out
})

const currentPage = ref(null)

function jumpToPage(page) {
  const b = (props.doc?.blocks || []).find((x) => x.page === page)
  if (b && scrollToBlock(b.block_id)) currentPage.value = page
}

/** 在风险清单里前后跳，配合卡片高亮 —— 法务逐条过风险时不用鼠标来回跑 */
const riskOrderIndex = computed(() => {
  const list = [...props.risks].sort((a, b) => (a.risk_no || 0) - (b.risk_no || 0))
  return list
})

function stepRisk(delta) {
  const list = riskOrderIndex.value
  if (!list.length) return
  let idx = list.findIndex((r) => r.id === props.activeRiskId)
  idx = idx < 0 ? (delta > 0 ? 0 : list.length - 1) : (idx + delta + list.length) % list.length
  const target = list[idx]
  emit('risk-click', target.id)
  nextTick(() => scrollToBlock(target.block_id))
}

/** 滚动时高亮当前页码（简单版：监听滚动找视口内第一个可见 block） */
function onScroll() {
  const box = scroller.value
  if (!box) return
  const top = box.getBoundingClientRect().top
  for (const b of props.doc?.blocks || []) {
    const el = blockRefs[b.block_id]
    if (!el) continue
    const rect = el.getBoundingClientRect()
    if (rect.bottom > top + 8) {
      currentPage.value = b.page
      return
    }
  }
}
</script>

<template>
  <div v-if="doc && doc.blocks" class="doc-wrap">
    <!-- 定位工具条 -->
    <div class="doc-toolbar">
      <span class="tb-label">字号</span>
      <el-button size="small" :disabled="zoom <= 0.8" @click="zoomOut">A-</el-button>
      <el-button size="small" :disabled="zoom >= 1.8" @click="zoomIn">A+</el-button>

      <el-divider direction="vertical" />

      <span class="tb-label">风险定位</span>
      <el-button size="small" @click="stepRisk(-1)"><el-icon><ArrowUp /></el-icon>上一处</el-button>
      <el-button size="small" @click="stepRisk(1)">下一处<el-icon><ArrowDown /></el-icon></el-button>

      <div class="spacer"></div>

      <span class="tb-label">
        共 {{ doc.page_count || pages.length }} 页
        <template v-if="currentPage">· 当前第 {{ currentPage }} 页</template>
      </span>
      <el-select
        v-if="pages.length > 1"
        :model-value="currentPage"
        size="small"
        placeholder="跳页"
        style="width: 96px"
        @change="jumpToPage"
      >
        <el-option v-for="p in pages" :key="p" :label="`第 ${p} 页`" :value="p" />
      </el-select>
    </div>

    <!-- 文本层：高亮叠层 + 跨页分隔 -->
    <div
      ref="scroller"
      class="highlight-doc"
      :style="{ fontSize: `${14 * zoom}px`, maxHeight: '72vh' }"
      @scroll.passive="onScroll"
    >
      <template v-for="(b, idx) in doc.blocks" :key="b.block_id">
        <!-- 分页分隔 -->
        <div v-if="idx > 0 && b.page !== doc.blocks[idx - 1].page" class="page-sep">
          第 {{ b.page }} 页
        </div>

        <div
          :ref="(el) => setBlockRef(el, b.block_id)"
          :class="[blockClass(b), { 'is-flash': flashId === b.block_id }]"
          :data-block-id="b.block_id"
        >
          <template v-for="(seg, si) in segmentsMap[b.block_id]" :key="si">
            <mark
              v-if="seg.type === 'mark'"
              :class="[LEVEL_CLASS[seg.level] || 'hl-low', { 'hl-active': activeRiskId === seg.riskId }]"
              @click.stop="onMarkClick(seg.riskId)"
              >{{ seg.text }}</mark
            >
            <template v-else>{{ seg.text }}</template>
          </template>
        </div>
      </template>
    </div>
  </div>
  <el-empty v-else description="暂无解析结果" />
</template>

<style scoped>
.doc-wrap {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.doc-toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 6px 10px;
}
.tb-label {
  font-size: 12px;
  color: #98a1b3;
}
.spacer {
  flex: 1;
}
</style>
