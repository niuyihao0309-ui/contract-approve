<script setup>
import { computed, nextTick, ref, watch } from 'vue'

/**
 * 原文高亮组件。
 *
 * 后端把全文拆成 blocks（每个 block 带 text + char_start/char_end），
 * 风险项带全局 char_start/char_end。这里按 block 渲染段落，
 * 在 block 内部计算风险区间的交集，用 <mark> 叠出高亮层 —— 不改原文结构。
 *
 * 对外：
 *   props.activeRiskId 变化时，滚动到对应段落并高亮闪烁。
 */
const props = defineProps({
  doc: { type: Object, default: null },
  risks: { type: Array, default: () => [] },
  activeRiskId: { type: String, default: '' },
})

const emit = defineEmits(['risk-click'])

// 记录每个 block 的 DOM 元素，用于滚动定位
const blockRefs = {}

function setBlockRef(el, blockId) {
  if (el) blockRefs[blockId] = el
}

// risk.riskLevel -> mark 类名
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

// ---- 点击风险卡片 → 滚动到对应段落 ----
const flashId = ref('')

watch(
  () => props.activeRiskId,
  async (rid) => {
    if (!rid) return
    const risk = props.risks.find((r) => r.id === rid)
    if (!risk || !risk.block_id) return
    const el = blockRefs[risk.block_id]
    if (!el) return
    await nextTick()
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    flashId.value = risk.block_id
    setTimeout(() => (flashId.value = ''), 1600)
  }
)

function onMarkClick(riskId) {
  emit('risk-click', riskId)
}
</script>

<template>
  <div class="highlight-doc" v-if="doc && doc.blocks">
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
  <el-empty v-else description="暂无解析结果" />
</template>

<style scoped>
.highlight-doc {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 12px 16px;
  max-height: 72vh;
  overflow: auto;
  font-size: 14px;
}
</style>
