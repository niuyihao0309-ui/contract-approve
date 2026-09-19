"""LLM 语义研判。

定位策略（关键设计）
--------------------
大模型看不到我们的 ``block_id``，所以不能要求它直接输出 block 编号。
本模块的做法是：

1. Prompt 里要求模型 ``quote`` **逐字复制原文片段**；
2. 拿到 quote 后用 :meth:`DocIR.locate_by_text` 在全文里反查，还原出
   ``block_id + char_start/char_end + page``；
3. 反查失败的风险项**直接丢弃**（宁可少报，也不报一条点不动、无法举证的"幽灵风险"）
   —— 大纲 3.3 说得明白：没有定位的风险项等于无效。

同时把规则引擎已经命中的问题告诉模型，让它不要再重复报一遍。
"""

from __future__ import annotations

from app.core.logging import logger
from app.engines.parsing.integrity import IntegrityReport
from app.llm.client import LLMClient, get_llm
from app.models.enums import (
    CONTRACT_TYPE_LABEL,
    ClauseType,
    RiskSource,
)
from app.schemas.document import DocIR, SourceSpan
from app.schemas.metadata import ClauseSegmentResult, MetadataResult
from app.schemas.risk import LLMRiskDraft, LLMReviewPayload, RiskItemModel, SuggestionModel

#: quote 反查失败时，允许「退化匹配」的最小长度（太短容易误配到别的条款）
MIN_QUOTE_LEN = 8
#: 送审文本里每个 block 前面标的页码，帮助模型填 page 字段
_PAGE_TAG = "〔第{page}页〕"


class LLMReviewer:
    """规则引擎的补充：权责不对等、高危表述这类必须靠语义理解的判断。"""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or get_llm()

    # ------------------------------------------------------------------
    async def review(
        self,
        ir: DocIR,
        *,
        clauses: ClauseSegmentResult | None = None,
        metadata: MetadataResult | None = None,
        contract_type: str = "other",
        our_side: str = "甲方",
        known_issues: list[str] | None = None,
        integrity: IntegrityReport | None = None,
    ) -> list[RiskItemModel]:
        """跑一遍 LLM 审查。LLM 未启用或失败时返回空列表（不抛异常）。"""
        if not self.llm.enabled:
            logger.info("[LLM审查] LLM 未启用，跳过语义研判（仅使用规则引擎结果）")
            return []

        known = known_issues or []
        chunks = self._split_chunks(ir)
        logger.info(
            f"[LLM审查] 开始，{len(chunks)} 个分片，已知问题 {len(known)} 条，"
            f"合同类型={contract_type}"
        )

        all_risks: list[RiskItemModel] = []
        for i, chunk_blocks in enumerate(chunks, 1):
            chunk_text = self._render(chunk_blocks)
            drafts = await self._call(
                chunk_text,
                contract_type=contract_type,
                our_side=our_side,
                known_issues=known,
            )
            if not drafts:
                continue
            for d in drafts:
                item = self._to_item(ir, d, i)
                if item is None:
                    continue
                if self._is_duplicate(item, all_risks) or self._matches_known(item, known):
                    continue
                all_risks.append(item)

        logger.info(f"[LLM审查] 完成，产出 {len(all_risks)} 条语义风险")
        return all_risks

    # ------------------------------------------------------------------
    async def _call(
        self,
        contract_text: str,
        *,
        contract_type: str,
        our_side: str,
        known_issues: list[str],
    ) -> list[LLMRiskDraft]:
        known_txt = (
            "\n".join(f"- {k}" for k in known_issues)
            if known_issues
            else "（规则引擎未命中明确问题）"
        )
        payload = await self.llm.complete_typed(
            prompt_name="risk_review",
            variables={
                "contract_type": CONTRACT_TYPE_LABEL.get(
                    contract_type, contract_type
                ),
                "our_side": our_side,
                "known_issues": known_txt,
                "contract_text": contract_text,
            },
            schema=LLMReviewPayload,
            temperature=0.1,
        )
        if payload is None:
            return []
        if payload.summary:
            logger.info(f"[LLM审查] 模型综述：{payload.summary[:120]}")
        return payload.risks

    # ------------------------------------------------------------------
    # 合同文本准备
    # ------------------------------------------------------------------
    @staticmethod
    def _split_chunks(ir: DocIR) -> list[list]:
        """按字符预算切分，但**只在 block 边界上切**，不切断条款。"""
        from app.core.config import settings

        budget = settings.llm_chunk_chars
        chunks: list[list] = []
        cur: list = []
        size = 0
        for b in ir.non_empty_blocks:
            if size + len(b.text) > budget and cur:
                chunks.append(cur)
                cur, size = [], 0
            cur.append(b)
            size += len(b.text) + 1
        if cur:
            chunks.append(cur)
        return chunks

    @staticmethod
    def _render(blocks: list) -> str:
        """渲染成带页码标注的文本，方便模型填 page。"""
        from app.models.enums import BlockType

        out: list[str] = []
        last_page = None
        for b in blocks:
            if b.page != last_page:
                out.append(_PAGE_TAG.format(page=b.page))
                last_page = b.page
            if b.type is BlockType.TABLE:
                out.append(f"[表格] {b.text}")
            else:
                out.append(b.text)
        return "\n".join(out)

    # ------------------------------------------------------------------
    # 落位与去重
    # ------------------------------------------------------------------
    @staticmethod
    def _to_item(ir: DocIR, draft: LLMRiskDraft, chunk_no: int) -> RiskItemModel | None:
        """把 LLM 的 draft 还原成带定位的风险项。定位失败返回 None。"""
        quote = (draft.quote or "").strip()
        if len(quote) < MIN_QUOTE_LEN:
            logger.debug(f"[LLM审查] quote 过短，丢弃：{quote!r}")
            return None

        found = ir.locate_by_text(quote)
        if found is None:
            # 退化：拿 quote 的前 24 字再试一次（模型可能多带了标点或半句话）
            head = quote[:24]
            found = ir.locate_by_text(head) if len(head) >= MIN_QUOTE_LEN else None
        if found is None:
            logger.warning(f"[LLM审查] quote 无法在原文中定位，丢弃该风险：{quote[:40]}…")
            return None

        blk, start, end = found
        span = SourceSpan(
            block_id=blk.block_id,
            char_start=start,
            char_end=end,
            page=blk.page,
            para_index=blk.para_index,
            quote=ir.slice_text(start, end, 300),
        )

        suggestion = None
        if (draft.suggestion or "").strip():
            suggestion = SuggestionModel(
                original_text=ir.slice_text(start, end, 300),
                suggested_text=draft.suggestion.strip(),
                rationale=draft.suggestion_rationale.strip() or "由大模型结合本条风险生成的修改示范条款",
                source="llm",
            )

        return RiskItemModel(
            name=draft.name,
            risk_level=draft.risk_level,
            clause_type=draft.clause_type or ClauseType.OTHER,
            source=RiskSource.LLM,
            span=span,
            reason=draft.reason,
            legal_basis=draft.legal_basis,
            tags=["语义研判", f"chunk{chunk_no}"],
            probability=draft.probability,
            confidence=0.8,
            suggestion=suggestion,
        )

    @staticmethod
    def _is_duplicate(item: RiskItemModel, existing: list[RiskItemModel]) -> bool:
        """同一位置 + 同类条款 → 视为重复。"""
        for o in existing:
            overlap = not (
                item.span.char_end <= o.span.char_start or item.span.char_start >= o.span.char_end
            )
            if overlap and item.clause_type == o.clause_type:
                return True
            if item.name == o.name and overlap:
                return True
        return False

    @staticmethod
    def _matches_known(item: RiskItemModel, known_issues: list[str]) -> bool:
        """规则引擎已经报过的问题，名称高度重合就跳过。"""
        name = item.name
        for k in known_issues:
            if not k:
                continue
            # 简单包含判断：规则名「违约责任严重不对等（赔偿无上限）」vs 模型名「违约责任不对等」
            common = sum(1 for ch in set(name) if ch in k)
            if common / max(len(set(name)), 1) > 0.7:
                return True
        return False


__all__ = ["LLMReviewer", "MIN_QUOTE_LEN"]
