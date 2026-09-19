"""持久化：把引擎产出的内存对象写进数据库。

所有表都用 ``task_id`` 关联，重新审查时先清掉该任务的旧产物，保证不串数据。
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.models.enums import ItemDecision
from app.models.tables import (
    ClauseSuggestion,
    ContractMetadata,
    DocBlock,
    DocClause,
    RiskItem,
)
from app.schemas.document import DocIR
from app.schemas.metadata import ClauseSegmentResult, MetadataResult
from app.schemas.risk import RiskAggregate, RiskItemModel


def _uid() -> str:
    return str(uuid.uuid4())


class PersistenceService:
    """IR / 条款 / 元数据 / 风险 的落库与清理。"""

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------
    @staticmethod
    async def clear_task_artifacts(session: AsyncSession, task_id: str) -> None:
        """清掉一个任务的全部解析与审查产物（重新审查时调用）。

        注意顺序：先删引用方（clause_suggestion 引 risk_item），再删被引用方。
        这里靠外键 ON DELETE CASCADE 兜底，但显式按序删更稳、也更好排查。
        """
        for model in (ClauseSuggestion, RiskItem, DocClause, ContractMetadata, DocBlock):
            await session.execute(delete(model).where(model.task_id == task_id))
        await session.flush()
        logger.debug(f"[持久化] 已清理任务 {task_id[:8]} 的解析与审查产物")

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    @staticmethod
    async def save_document(
        session: AsyncSession,
        task_id: str,
        ir: DocIR,
        *,
        file_id: str | None = None,
    ) -> int:
        """保存 IR 的全部 block。"""
        rows = [
            DocBlock(
                task_id=task_id,
                file_id=file_id,
                doc_id=ir.doc_id,
                block_id=b.block_id,
                type=b.type.value,
                text=b.text,
                page=b.page,
                para_index=b.para_index,
                char_start=b.char_start,
                char_end=b.char_end,
                bbox=b.bbox,
                level=b.level,
                table_index=b.table_index,
                row_index=b.row_index,
            )
            for b in ir.blocks
        ]
        session.add_all(rows)
        await session.flush()
        logger.debug(f"[持久化] 保存 {len(rows)} 个 block（doc={ir.doc_id[:8]}）")
        return len(rows)

    @staticmethod
    async def save_metadata(
        session: AsyncSession, task_id: str, result: MetadataResult
    ) -> int:
        rows = []
        for item in result.items:
            if not item.field_value:
                continue
            rows.append(
                ContractMetadata(
                    id=_uid(),
                    task_id=task_id,
                    field_key=item.field_key.value,
                    field_value=(item.field_value or "")[:512],
                    normalized_value=(item.normalized_value or "")[:512],
                    block_id=item.span.block_id if item.span else None,
                    char_start=item.span.char_start if item.span else None,
                    char_end=item.span.char_end if item.span else None,
                    page=item.span.page if item.span else None,
                    para_index=item.span.para_index if item.span else None,
                    quote=(item.span.quote or "")[:512] if item.span else None,
                    source=item.source,
                    confidence=item.confidence,
                )
            )
        session.add_all(rows)
        await session.flush()
        logger.debug(f"[持久化] 保存 {len(rows)} 条元数据")
        return len(rows)

    @staticmethod
    async def save_clauses(
        session: AsyncSession, task_id: str, doc_id: str, result: ClauseSegmentResult
    ) -> int:
        rows = [
            DocClause(
                id=_uid(),
                task_id=task_id,
                doc_id=doc_id,
                clause_type=c.clause_type.value,
                title=c.title[:255],
                content=c.content,
                start_block_id=c.head_block_id,
                end_block_id=c.block_ids[-1] if c.block_ids else c.head_block_id,
                block_ids=c.block_ids,
                char_start=c.char_start,
                char_end=c.char_end,
                page_start=c.page_start,
                page_end=c.page_end,
                para_start=c.para_start,
                confidence=c.confidence,
                method=c.method,
            )
            for c in result.clauses
        ]
        session.add_all(rows)
        await session.flush()
        logger.debug(f"[持久化] 保存 {len(rows)} 个条款")
        return len(rows)

    @staticmethod
    async def save_risks(
        session: AsyncSession, task_id: str, risks: list[RiskItemModel]
    ) -> list[RiskItem]:
        """保存风险项与随附的修改建议，返回 ORM 对象供后续更新。"""
        docs: list[RiskItem] = []
        for i, r in enumerate(risks, 1):
            r.risk_no = i
            row = RiskItem(
                id=_uid(),
                task_id=task_id,
                risk_no=i,
                name=r.name[:255],
                risk_level=r.risk_level.value,
                clause_type=r.clause_type.value,
                source=r.source.value,
                rule_id=r.rule_id,
                block_id=r.span.block_id,
                char_start=r.span.char_start,
                char_end=r.span.char_end,
                page=r.span.page,
                para_index=r.span.para_index,
                quote=r.span.quote,
                reason=r.reason,
                legal_basis=r.legal_basis,
                tags=r.tags,
                probability=r.probability,
                confidence=r.confidence,
                decision=ItemDecision.OPEN.value,
            )
            session.add(row)
            docs.append(row)

            if r.suggestion and (r.suggestion.suggested_text or r.suggestion.original_text):
                session.add(
                    ClauseSuggestion(
                        id=_uid(),
                        task_id=task_id,
                        risk_item_id=row.id,
                        original_text=r.suggestion.original_text,
                        suggested_text=r.suggestion.suggested_text,
                        rationale=r.suggestion.rationale,
                        source=r.suggestion.source.value
                        if hasattr(r.suggestion.source, "value")
                        else str(r.suggestion.source),
                        template_id=r.suggestion.template_id,
                        similarity=r.suggestion.similarity,
                        decision=ItemDecision.OPEN.value,
                    )
                )
        await session.flush()
        logger.debug(f"[持久化] 保存 {len(docs)} 条风险项")
        return docs

    # ------------------------------------------------------------------
    @staticmethod
    async def apply_aggregate(
        session: AsyncSession, task_id: str, agg: RiskAggregate
    ) -> None:
        """把聚合结论写回任务主表。"""
        from app.models.tables import ContractTask

        task = await session.get(ContractTask, task_id)
        if task is None:
            return
        task.overall_risk = agg.overall_risk.value if agg.overall_risk else None
        task.conclusion = agg.conclusion or None
        task.risk_summary = agg.summary
        task.focus_points = agg.focus_points
        task.risk_counts = agg.counts
        await session.flush()

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    @staticmethod
    async def load_blocks(session: AsyncSession, task_id: str) -> list[DocBlock]:
        stmt = (
            select(DocBlock)
            .where(DocBlock.task_id == task_id)
            .order_by(DocBlock.char_start)
        )
        return list((await session.execute(stmt)).scalars().all())

    @staticmethod
    async def load_risks(session: AsyncSession, task_id: str) -> list[RiskItem]:
        stmt = (
            select(RiskItem)
            .where(RiskItem.task_id == task_id)
            .order_by(RiskItem.risk_no)
        )
        return list((await session.execute(stmt)).scalars().all())

    @staticmethod
    async def load_suggestions(
        session: AsyncSession, task_id: str
    ) -> dict[str, ClauseSuggestion]:
        """返回 ``{risk_item_id: suggestion}``。"""
        stmt = select(ClauseSuggestion).where(ClauseSuggestion.task_id == task_id)
        rows = list((await session.execute(stmt)).scalars().all())
        return {s.risk_item_id: s for s in rows}


__all__ = ["PersistenceService"]
