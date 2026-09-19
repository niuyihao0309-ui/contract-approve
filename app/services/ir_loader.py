"""从数据库重建 IR。

为什么要有这一步：流水线里的解析、元数据、条款、审查是**分阶段可续跑**的，
每跑一次都重新解析一遍既浪费又可能与上次结果不一致。所以约定
**``doc_block`` 表是 IR 的唯一事实来源**，任何时候需要 IR 就从这张表重建，
保证同一条任务在多次执行之间看到的是同一份文档结构。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.models.enums import BlockType, ClauseType
from app.models.tables import ContractMetadata, DocBlock, DocClause
from app.schemas.document import Block, DocIR, SourceSpan
from app.schemas.metadata import Clause, ClauseSegmentResult, MetadataItem, MetadataResult
from app.models.enums import MetadataField


class IRLoader:
    """把持久化的产物还原成内存对象。"""

    @staticmethod
    async def load_ir(session: AsyncSession, task_id: str) -> DocIR | None:
        rows = list(
            (
                await session.execute(
                    select(DocBlock)
                    .where(DocBlock.task_id == task_id)
                    .order_by(DocBlock.char_start)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return None

        blocks = [
            Block(
                block_id=r.block_id,
                type=BlockType(r.type),
                text=r.text,
                page=r.page,
                para_index=r.para_index,
                char_start=r.char_start,
                char_end=r.char_end,
                bbox=r.bbox,
                level=r.level,
                table_index=r.table_index,
                row_index=r.row_index,
            )
            for r in rows
        ]
        ir = DocIR(
            doc_id=rows[0].doc_id,
            source_file="",
            file_kind="",
            parser="from_db",
            page_count=max((r.page for r in rows), default=1),
            blocks=blocks,
            full_text="\n".join(b.text for b in blocks),
        )
        logger.debug(f"[IR] 从数据库重建：{len(blocks)} 块 / {ir.page_count} 页")
        return ir

    # ------------------------------------------------------------------
    @staticmethod
    async def load_metadata(session: AsyncSession, task_id: str) -> MetadataResult | None:
        rows = list(
            (
                await session.execute(
                    select(ContractMetadata).where(ContractMetadata.task_id == task_id)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return None

        items: list[MetadataItem] = []
        for r in rows:
            span = None
            if r.block_id:
                span = SourceSpan(
                    block_id=r.block_id,
                    char_start=r.char_start or 0,
                    char_end=r.char_end or 0,
                    page=r.page or 1,
                    para_index=r.para_index or 0,
                    quote=r.quote or "",
                )
            items.append(
                MetadataItem(
                    field_key=MetadataField(r.field_key),
                    field_value=r.field_value,
                    normalized_value=r.normalized_value,
                    span=span,
                    source=r.source,
                    confidence=r.confidence,
                )
            )
        # 补全缺失字段（保持 8 项齐全的约定）
        have = {i.field_key for i in items}
        for f in MetadataField:
            if f not in have:
                items.append(
                    MetadataItem(field_key=f, field_value=None, source="regex", confidence=0.0)
                )
        return MetadataResult(items=items)

    # ------------------------------------------------------------------
    @staticmethod
    async def load_clauses(session: AsyncSession, task_id: str) -> ClauseSegmentResult | None:
        rows = list(
            (
                await session.execute(
                    select(DocClause).where(DocClause.task_id == task_id)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return None
        clauses = [
            Clause(
                clause_type=ClauseType(r.clause_type),
                title=r.title,
                content=r.content,
                block_ids=list(r.block_ids or []),
                char_start=r.char_start,
                char_end=r.char_end,
                page_start=r.page_start,
                page_end=r.page_end,
                para_start=r.para_start,
                confidence=r.confidence,
                method=r.method,
            )
            for r in rows
        ]
        clauses.sort(key=lambda c: c.char_start)
        return ClauseSegmentResult(clauses=clauses)


__all__ = ["IRLoader"]
