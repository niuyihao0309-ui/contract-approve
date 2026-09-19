"""任务详情装配 —— 工作台一次请求拿到渲染所需的全部数据。

返回结构刻意做成「前端拿到就能渲染」：

- ``doc.blocks`` 给出全文与每个块的字符区间 → 前端据此把原文渲染成分段 DOM，
  再用 ``block_id + char_start/char_end`` 叠高亮层（**不改原文档结构**）；
- ``risk_items`` 每条都带 ``block_id + char_range``，点卡片就能跳到对应段落；
- ``doc.full_text`` 与 blocks 的偏移一致，前端口算 slice 即可。

这样前端不需要再做任何定位推理，双向联动的正确性完全由后端保证。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    BLOCK_REASON_LABEL,
    CLAUSE_TYPE_LABEL,
    CONTRACT_TYPE_LABEL,
    METADATA_FIELD_LABEL,
    METADATA_FIELD_LABEL as _MFL,
    RISK_LEVEL_LABEL,
    TASK_STATUS_LABEL,
    WRITEBACK_STATUS_LABEL,
)
from app.models.tables import (
    ClauseSuggestion,
    ContractFile,
    ContractMetadata,
    DocBlock,
    DocClause,
    ReviewComment,
    RiskItem,
    WritebackLog,
)
from app.services.task_service import TaskService


def _enum_row(row) -> dict:
    """给前端下拉/标签用的形状统一的 dict。"""
    data = row.to_dict()
    for k, v in list(data.items()):
        if hasattr(v, "value"):
            data[k] = v.value
    return data


class TaskQueryService:
    """详情与列表的读取。"""

    # ------------------------------------------------------------------
    @staticmethod
    async def detail(session: AsyncSession, task_id: str) -> dict:
        task = await TaskService.get(session, task_id)

        from app.schemas.task import TaskBrief

        base = TaskBrief.from_orm_task(task).model_dump()
        base.update(
            risk_summary=task.risk_summary,
            focus_points=task.focus_points or [],
            block_detail=task.block_detail,
            business_type_label=CONTRACT_TYPE_LABEL.get(task.business_type or "", task.business_type),
        )

        base["doc"] = await TaskQueryService._doc(session, task_id)
        base["risk_items"] = await TaskQueryService._risks(session, task_id)
        base["files"] = await TaskQueryService._files(session, task_id)
        base["metadata"] = await TaskQueryService._metadata(session, task_id)
        base["clauses"] = await TaskQueryService._clauses(session, task_id)
        base["comments"] = await TaskQueryService._comments(session, task_id)
        base["writeback"] = await TaskQueryService._writeback(session, task_id)
        base["stats"] = {
            "risk_by_level": task.risk_counts or {"high": 0, "medium": 0, "low": 0},
            "block_count": len(base["doc"]["blocks"]) if base["doc"] else 0,
        }
        return base

    # ------------------------------------------------------------------
    @staticmethod
    async def _doc(session: AsyncSession, task_id: str) -> dict | None:
        """文档视窗数据：全文 + 分块（带定位）+ 表格结构。"""
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
            {
                "block_id": r.block_id,
                "type": r.type,
                "text": r.text,
                "page": r.page,
                "para_index": r.para_index,
                "char_start": r.char_start,
                "char_end": r.char_end,
                "bbox": r.bbox,
                "level": r.level,
                "table_index": r.table_index,
                "row_index": r.row_index,
            }
            for r in rows
        ]
        full_text = "\n".join(b["text"] for b in blocks)

        pages: dict[int, int] = {}
        for b in blocks:
            pages[b["page"]] = pages.get(b["page"], 0) + 1

        return {
            "doc_id": rows[0].doc_id,
            "full_text": full_text,
            "blocks": blocks,
            "page_count": max((r.page for r in rows), default=1),
            "blocks_per_page": {str(k): v for k, v in sorted(pages.items())},
            # 前端按这个渲染高亮层的图例
            "highlight_legend": {
                "high": "#e5484d",
                "medium": "#f5a524",
                "low": "#8b8d98",
                "metadata": "#1e90ff",
            },
        }

    # ------------------------------------------------------------------
    @staticmethod
    async def _risks(session: AsyncSession, task_id: str) -> list[dict]:
        rows = list(
            (
                await session.execute(
                    select(RiskItem).where(RiskItem.task_id == task_id).order_by(RiskItem.risk_no)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return []

        sug_stmt = select(ClauseSuggestion).where(ClauseSuggestion.task_id == task_id)
        sugs = {
            s.risk_item_id: s
            for s in (await session.execute(sug_stmt)).scalars().all()
        }

        out: list[dict] = []
        for r in rows:
            s = sugs.get(r.id)
            out.append(
                {
                    # --- 定位：前端跳转与高亮全靠这三个字段 ---
                    "block_id": r.block_id,
                    "char_start": r.char_start,
                    "char_end": r.char_end,
                    "page": r.page,
                    "para_index": r.para_index,
                    "location_label": f"第{r.page}页 第{r.para_index + 1}段",
                    "quote": r.quote,
                    # --- 内容 ---
                    "id": r.id,
                    "risk_no": r.risk_no,
                    "name": r.name,
                    "risk_level": r.risk_level,
                    "risk_level_label": RISK_LEVEL_LABEL.get(r.risk_level, r.risk_level),
                    "clause_type": r.clause_type,
                    "clause_type_label": CLAUSE_TYPE_LABEL.get(r.clause_type, r.clause_type),
                    "source": r.source,
                    "rule_id": r.rule_id,
                    "reason": r.reason,
                    "legal_basis": r.legal_basis,
                    "tags": r.tags or [],
                    "probability": r.probability,
                    "confidence": r.confidence,
                    "decision": r.decision,
                    "lawyer_note": r.lawyer_note,
                    # --- 建议 ---
                    "suggestion": (
                        {
                            "id": s.id,
                            "original_text": s.original_text,
                            "suggested_text": s.suggested_text,
                            "final_text": s.final_text,
                            "rationale": s.rationale,
                            "source": s.source,
                            "template_id": s.template_id,
                            "similarity": s.similarity,
                            "edited_text": s.edited_text,
                            "edited_by": s.edited_by,
                            "decision": s.decision,
                        }
                        if s
                        else None
                    ),
                }
            )
        return out

    # ------------------------------------------------------------------
    @staticmethod
    async def _files(session: AsyncSession, task_id: str) -> list[dict]:
        rows = list(
            (
                await session.execute(
                    select(ContractFile).where(ContractFile.task_id == task_id)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": f.id,
                "filename": f.filename,
                "file_kind": f.file_kind,
                "file_size": f.file_size,
                "file_hash": f.file_hash,
                "hash_short": (f.file_hash or "")[:12],
                "parsed": f.parsed,
                "parser": f.parser,
                "page_count": f.page_count,
                "from_cache": f.from_cache,
                "parse_meta": f.parse_meta,
                "parse_error": f.parse_error,
            }
            for f in rows
        ]

    # ------------------------------------------------------------------
    @staticmethod
    async def _metadata(session: AsyncSession, task_id: str) -> list[dict]:
        rows = list(
            (
                await session.execute(
                    select(ContractMetadata).where(ContractMetadata.task_id == task_id)
                )
            )
            .scalars()
            .all()
        )
        rows.sort(key=lambda r: list(METADATA_FIELD_LABEL).index(r.field_key)
                  if r.field_key in METADATA_FIELD_LABEL else 99)
        return [
            {
                "field_key": r.field_key,
                "field_label": METADATA_FIELD_LABEL.get(r.field_key, r.field_key),
                "value": r.field_value,
                "normalized_value": r.normalized_value,
                "source": r.source,
                "confidence": r.confidence,
                # 元数据也要能跳回原文
                "block_id": r.block_id,
                "char_start": r.char_start,
                "char_end": r.char_end,
                "page": r.page,
                "para_index": r.para_index,
                "location_label": (
                    f"第{r.page}页 第{(r.para_index or 0) + 1}段" if r.block_id else None
                ),
                "quote": r.quote,
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    @staticmethod
    async def _clauses(session: AsyncSession, task_id: str) -> list[dict]:
        rows = list(
            (
                await session.execute(
                    select(DocClause)
                    .where(DocClause.task_id == task_id)
                    .order_by(DocClause.char_start)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": c.id,
                "clause_type": c.clause_type,
                "clause_type_label": CLAUSE_TYPE_LABEL.get(c.clause_type, c.clause_type),
                "title": c.title,
                "content": c.content,
                "block_ids": c.block_ids or [],
                "start_block_id": c.start_block_id,
                "char_start": c.char_start,
                "char_end": c.char_end,
                "page_start": c.page_start,
                "page_end": c.page_end,
                "confidence": c.confidence,
                "method": c.method,
            }
            for c in rows
        ]

    # ------------------------------------------------------------------
    @staticmethod
    async def _comments(session: AsyncSession, task_id: str) -> list[dict]:
        rows = list(
            (
                await session.execute(
                    select(ReviewComment)
                    .where(ReviewComment.task_id == task_id)
                    .order_by(ReviewComment.created_at)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": c.id,
                "author": c.author,
                "role": c.role,
                "kind": c.kind,
                "content": c.content,
                "risk_item_id": c.risk_item_id,
                "block_id": c.block_id,
                "char_start": c.char_start,
                "char_end": c.char_end,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in rows
        ]

    # ------------------------------------------------------------------
    @staticmethod
    async def _writeback(session: AsyncSession, task_id: str) -> dict | None:
        row = (
            await session.execute(
                select(WritebackLog)
                .where(WritebackLog.task_id == task_id)
                .order_by(WritebackLog.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "id": row.id,
            "status": row.status,
            "status_label": WRITEBACK_STATUS_LABEL.get(row.status, row.status),
            "attempt": row.attempt,
            "ticket_id": row.ticket_id,
            "comment_id": row.comment_id,
            "payload_md": row.payload_md,
            "error": row.error,
            "duration_ms": row.duration_ms,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        }


__all__ = ["TaskQueryService"]
