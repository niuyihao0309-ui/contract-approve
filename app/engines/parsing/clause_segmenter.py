"""条款切分。

目标（大纲 2.6）：按标题层级 + 关键词切出标的物 / 付款 / 验收 / 违约 / 保密 /
数据安全 / 管辖等条款，每条绑定 ``block_id`` 范围，保证能「指回原文」。

算法
----
**第一层：按标题切段。**
识别「第X条 / 一、 / 1.1 / 整套标题样式」这类块作为分节起点，
每个分节覆盖从该标题块到下一个标题块之前的全部块。

**第二层：给分节定性。**
对每个分节的文本做关键词加权打分，取分数超过阈值的类型。
一个分节可以同时属于多个类型（例如「付款与验收」既算 payment 也算 acceptance）。

**第三层：无标题文档兜底。**
OCR 出来的文档常常没有标题结构。这时退化为「段落级关键词打点」：
按关键词首次出现的位置把连续段落切成分节。

**相邻同类合并**：切完之后把类型相同且位置相邻的分节合并，避免出现一堆碎条款。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.logging import logger
from app.engines.parsing.heading import clean_heading, detect_heading
from app.models.enums import BlockType, ClauseType
from app.schemas.document import Block, DocIR
from app.schemas.metadata import Clause, ClauseSegmentResult

# ======================================================================
# 关键词表：词 -> 权重
# ======================================================================
CLAUSE_KEYWORDS: dict[ClauseType, dict[str, float]] = {
    ClauseType.SUBJECT_MATTER: {
        "标的": 3.0, "标的物": 3.0, "货物名称": 3.0, "产品名称": 2.5, "服务内容": 3.0,
        "采购内容": 3.0, "规格型号": 3.0, "型号规格": 3.0, "服务范围": 2.5,
        "工作内容": 2.0, "数量": 1.2, "品名": 2.0, "型号": 1.5, "技术参数": 1.5,
        "供货范围": 2.5, "产品": 1.5, "货物": 1.5, "设备": 1.0, "供货": 1.5,
        "销售": 1.0, "采购": 1.0, "标的额": 2.0, "清单": 1.0,
    },
    ClauseType.PAYMENT: {
        "付款": 3.0, "支付": 2.0, "价款": 2.0, "结算": 2.0, "预付款": 3.0, "进度款": 3.0,
        "尾款": 2.5, "账期": 2.5, "汇款": 1.5, "付款方式": 3.0, "开票": 1.5, "发票": 1.5,
        "首付款": 2.5, "分期": 2.0, "款项": 2.0, "对公账户": 2.0, "押金": 1.5, "保证金": 1.2,
    },
    ClauseType.ACCEPTANCE: {
        "验收": 3.0, "交付": 1.5, "交货": 2.0, "到货": 2.0, "检测": 2.0, "签收": 2.0,
        "质量保证": 2.5, "质保期": 2.5, "合格": 1.5, "检验": 2.0, "试运行": 2.5,
        "验收标准": 3.0, "验收合格": 3.0, "开箱": 1.5, "安装调试": 2.0,
    },
    ClauseType.BREACH: {
        "违约": 3.0, "违约金": 3.5, "赔偿责任": 3.0, "逾期": 2.0, "滞纳金": 2.5,
        "损害赔偿": 2.5, "违约责任": 3.5, "解除合同": 2.0, "终止合同": 1.5, "赔偿责任上限": 3.0,
    },
    ClauseType.CONFIDENTIALITY: {
        "保密": 3.5, "商业秘密": 3.0, "保密义务": 3.5, "保密期限": 3.5, "不可披露": 2.5,
        "机密": 2.0, "保密信息": 3.0, "不得向第三方披露": 2.5,
    },
    ClauseType.DATA_SECURITY: {
        "数据安全": 3.5, "个人信息": 3.0, "数据保护": 3.0, "网络安全": 2.5, "数据出境": 3.0,
        "隐私": 2.0, "个人数据": 2.5, "数据合规": 3.0, "GDPR": 2.0, "数据泄露": 2.5,
    },
    ClauseType.JURISDICTION: {
        "争议": 2.5, "仲裁": 3.0, "诉讼": 3.0, "管辖": 3.0, "法院": 2.5, "适用法律": 3.0,
        "仲裁委员会": 3.0, "管辖法院": 3.5, "争议解决": 3.5, "调解": 1.5,
    },
    ClauseType.IP: {
        "知识产权": 3.5, "著作权": 3.0, "专利权": 3.0, "商标": 2.5, "源代码": 3.0,
        "成果归属": 3.0, "技术成果": 2.5, "版权": 2.5, "发明创造": 2.5, "许可使用": 2.0,
        "所有权归": 2.5, "归属": 1.5,
    },
    ClauseType.FORCE_MAJEURE: {
        "不可抗力": 3.5, "自然灾害": 2.5, "战争": 2.0, "疫情": 2.0, "政府行为": 1.5,
        "通知义务": 1.2, "免责": 1.5,
    },
    ClauseType.TERM: {
        "期限": 2.0, "有效期": 3.0, "服务期": 2.5, "生效": 2.0, "终止": 1.5, "续期": 2.5,
        "自动续约": 3.0, "合同期": 2.5, "履行期": 2.5,
    },
}

#: 分节定性阈值
CLAUSE_SCORE_THRESHOLD = 3.0
#: 标题定性阈值。标题很短但指示性极强（「第一条 产品与数量」），阈值要低一些
TITLE_SCORE_THRESHOLD = 2.0
#: 标题命中在总分里的放大倍数 —— 标题比正文里的零散关键词可信得多
TITLE_SCORE_BOOST = 2.0
#: 单个分节最多归属几个类型（防止一条长段落全命中）
MAX_TYPES_PER_SECTION = 3
#: 表格里的关键词打折。表格常出现「验收」「交付」这类词，但不是条款正文
TABLE_SCORE_DISCOUNT = 0.35
#: 送进关键词打分的分节文本上限（超长分节截断，避免尾部噪声主导）
SCORE_TEXT_LIMIT = 4000


@dataclass
class _Section:
    """一个分节（尚未定性）。"""

    blocks: list[Block] = field(default_factory=list)
    title: str = ""
    heading_block: Block | None = None

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)

    @property
    def block_ids(self) -> list[str]:
        return [b.block_id for b in self.blocks]

    @property
    def char_start(self) -> int:
        return self.blocks[0].char_start if self.blocks else 0

    @property
    def char_end(self) -> int:
        return self.blocks[-1].char_end if self.blocks else 0


def _is_heading(block: Block) -> bool:
    """块是否是分节标题。判断逻辑统一在 heading.detect_heading 里。"""
    if block.type is BlockType.TITLE:
        return True
    return detect_heading(block.text) is not None


def _score(text: str, *, threshold: float = CLAUSE_SCORE_THRESHOLD) -> dict[ClauseType, float]:
    """对一个分节文本做关键词打分，只返回达到阈值的类型。"""
    text = text[:SCORE_TEXT_LIMIT]
    scores: dict[ClauseType, float] = {}
    for ctype, keywords in CLAUSE_KEYWORDS.items():
        total = 0.0
        for kw, weight in keywords.items():
            n = text.count(kw)
            if n:
                # 同一关键词出现多次只做对数增益，避免长段落靠堆词刷分
                total += weight * (1 + 0.4 * min(n - 1, 3))
        if total >= threshold:
            scores[ctype] = total
    return scores


def _score_section(sec: "_Section") -> dict[ClauseType, float]:
    """分节打分：表格内容降权，正文按原权重。"""
    plain, tabular = [], []
    for b in sec.blocks:
        (tabular if b.type is BlockType.TABLE else plain).append(b.text)

    scores = _score("\n".join(plain))
    if tabular:
        for ctype, s in _score("\n".join(tabular)).items():
            scores[ctype] = scores.get(ctype, 0.0) + s * TABLE_SCORE_DISCOUNT
    return {t: s for t, s in scores.items() if s >= CLAUSE_SCORE_THRESHOLD}


class ClauseSegmenter:
    """条款切分器。"""

    # ------------------------------------------------------------------
    def segment(self, ir: DocIR) -> ClauseSegmentResult:
        blocks = [b for b in ir.blocks if not b.is_empty]
        if not blocks:
            return ClauseSegmentResult(warnings=["文档没有可切分的文本块"])

        sections = self._split_by_heading(blocks)
        used_heading = len(sections) > 1

        if not used_heading:
            # 没有标题结构（OCR 文档常见）→ 段落级关键词打点
            sections = self._split_by_keyword(blocks)
            method = "keyword"
        else:
            method = "heading"

        clauses = self._classify(sections, method=method)
        clauses = self._merge_adjacent(ir, clauses)

        warnings: list[str] = []
        missing_required = [
            t for t in (
                ClauseType.SUBJECT_MATTER, ClauseType.PAYMENT,
                ClauseType.ACCEPTANCE, ClauseType.BREACH,
            )
            if not any(c.clause_type is t for c in clauses)
        ]
        if missing_required:
            from app.models.enums import CLAUSE_TYPE_LABEL

            warnings.append(
                "未切出以下条款：" + "、".join(CLAUSE_TYPE_LABEL[t.value] for t in missing_required)
            )

        logger.info(
            f"[条款切分] {method} 模式：{len(sections)} 个分节 → {len(clauses)} 个条款"
            + (f"；{warnings[0]}" if warnings else "")
        )
        return ClauseSegmentResult(clauses=clauses, warnings=warnings)

    # ------------------------------------------------------------------
    # 第一层 A：按标题切
    # ------------------------------------------------------------------
    def _split_by_heading(self, blocks: list[Block]) -> list[_Section]:
        sections: list[_Section] = []
        cur: _Section | None = None

        for b in blocks:
            level = detect_heading(b.text)
            if level is not None and len(b.text.strip()) <= 80:
                if cur and cur.blocks:
                    sections.append(cur)
                cur = _Section(blocks=[b], title=clean_heading(b.text), heading_block=b)
            else:
                if cur is None:
                    cur = _Section(blocks=[], title="")
                cur.blocks.append(b)

        if cur and cur.blocks:
            sections.append(cur)
        return sections

    # ------------------------------------------------------------------
    # 第一层 B：按关键词切（无标题结构时的兜底）
    # ------------------------------------------------------------------
    def _split_by_keyword(self, blocks: list[Block]) -> list[_Section]:
        # 找出每一块命中的类型
        block_hits: list[dict[ClauseType, float]] = [_score(b.text) for b in blocks]
        if not any(block_hits):
            return [_Section(blocks=list(blocks))]

        sections: list[_Section] = []
        cur: _Section = _Section(blocks=[])
        cur_hits: dict[ClauseType, float] = {}

        for b, hits in zip(blocks, block_hits):
            # 命中了「当前分节之外的新类型」→ 从此处断节
            new_types = {t for t in hits if t not in cur_hits}
            if cur.blocks and hits and new_types:
                sections.append(cur)
                cur = _Section(blocks=[])
                cur_hits = {}
            cur.blocks.append(b)
            for t, s in hits.items():
                cur_hits[t] = cur_hits.get(t, 0.0) + s
        if cur.blocks:
            sections.append(cur)
        return sections

    # ------------------------------------------------------------------
    # 第二层：定性
    # ------------------------------------------------------------------
    def _classify(self, sections: list[_Section], *, method: str) -> list[Clause]:
        clauses: list[Clause] = []
        for sec in sections:
            # 标题本身就是最强的类型信号：「第六条 违约责任」不需要靠正文猜
            title_scores = _score(sec.title, threshold=TITLE_SCORE_THRESHOLD) if sec.title else {}
            scores = dict(_score_section(sec))
            for t, s in title_scores.items():
                scores[t] = scores.get(t, 0.0) + s * TITLE_SCORE_BOOST

            if not scores:
                types: list[ClauseType] = [ClauseType.OTHER]
            else:
                ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
                top = ranked[0][1]
                # 只保留与头名差距不超过 50% 的类型，避免一条分节被打上四五个标签
                types = [t for t, s in ranked[:MAX_TYPES_PER_SECTION] if s >= top * 0.5]

            for ctype in types:
                conf = 0.5 + min(scores.get(ctype, 0.0) / 30, 0.45)
                if method == "heading" and sec.heading_block is not None:
                    conf = min(conf + 0.05, 0.95)
                clauses.append(
                    Clause(
                        clause_type=ctype,
                        title=sec.title or (sec.blocks[0].text[:40] if sec.blocks else ""),
                        content=sec.text,
                        block_ids=sec.block_ids,
                        char_start=sec.char_start,
                        char_end=sec.char_end,
                        page_start=sec.blocks[0].page if sec.blocks else 1,
                        page_end=sec.blocks[-1].page if sec.blocks else 1,
                        para_start=sec.blocks[0].para_index if sec.blocks else 0,
                        confidence=round(min(conf, 0.95), 3),
                        method=method,
                    )
                )
        return clauses

    # ------------------------------------------------------------------
    # 第三层：相邻同类合并
    # ------------------------------------------------------------------
    @staticmethod
    def _merge_adjacent(ir: DocIR, clauses: list[Clause]) -> list[Clause]:
        """类型相同且 block 区间紧邻的条款合并，减少碎块。"""
        if not clauses:
            return []
        ordered = sorted(clauses, key=lambda c: (c.char_start, c.clause_type.value))
        merged: list[Clause] = [ordered[0].model_copy(deep=True)]

        for c in ordered[1:]:
            last = merged[-1]
            # 两块紧邻时，中间只隔一个 Block 连接符（"\n"）
            between = ir.slice_text(last.char_end, c.char_start)
            tight = not between.strip()
            if c.clause_type is last.clause_type and tight:
                last.content = f"{last.content}\n{c.content}"
                new_ids = [b for b in c.block_ids if b not in last.block_ids]
                last.block_ids = last.block_ids + new_ids
                last.char_end = c.char_end
                last.page_end = c.page_end
                last.confidence = max(last.confidence, c.confidence)
            else:
                merged.append(c.model_copy(deep=True))
        return merged


__all__ = ["ClauseSegmenter", "CLAUSE_KEYWORDS"]
