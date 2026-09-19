"""文档视窗接口：原文、条款、元数据。

前端工作台的双栏视窗靠这里的接口拿到「原文 + 分块 + 定位」三件套，
高亮层由前端叠加渲染，不改动原文结构。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import SessionDep
from app.core.exceptions import NotFoundError
from app.services.task_query import TaskQueryService

router = APIRouter(prefix="/tasks", tags=["文档视窗"])


@router.get("/{task_id}/document", summary="文档原文与分块（视窗渲染 + 高亮定位用）")
async def get_document(task_id: str, session: SessionDep) -> dict:
    doc = await TaskQueryService._doc(session, task_id)
    if doc is None:
        raise NotFoundError("该任务还没有解析结果")
    return doc


@router.get("/{task_id}/document/blocks", summary="只要分块列表（轻量）")
async def get_blocks(
    task_id: str,
    session: SessionDep,
    page: int | None = Query(default=None, description="只看某一页"),
) -> dict:
    doc = await TaskQueryService._doc(session, task_id)
    if doc is None:
        raise NotFoundError("该任务还没有解析结果")
    blocks = doc["blocks"]
    if page is not None:
        blocks = [b for b in blocks if b["page"] == page]
    return {"total": len(blocks), "blocks": blocks}


@router.get("/{task_id}/clauses", summary="条款切分结果")
async def get_clauses(task_id: str, session: SessionDep) -> dict:
    clauses = await TaskQueryService._clauses(session, task_id)
    return {"total": len(clauses), "clauses": clauses}


@router.get("/{task_id}/metadata", summary="核心元数据（含原文定位）")
async def get_metadata(task_id: str, session: SessionDep) -> dict:
    items = await TaskQueryService._metadata(session, task_id)
    return {
        "total": len(items),
        "hit": sum(1 for i in items if i["value"]),
        "items": items,
    }


@router.get("/{task_id}/locate", summary="按字符区间反查定位（前端调试用）")
async def locate(
    task_id: str,
    session: SessionDep,
    char_start: int = Query(..., ge=0),
    char_end: int = Query(..., ge=0),
) -> dict:
    """给定全局字符区间，返回它落在哪个 block、第几页第几段，以及原文片段。"""
    doc = await TaskQueryService._doc(session, task_id)
    if doc is None:
        raise NotFoundError("该任务还没有解析结果")
    if char_end <= char_start:
        raise HTTPException(status_code=422, detail="char_end 必须大于 char_start")

    for b in doc["blocks"]:
        if b["char_start"] <= char_start < b["char_end"]:
            return {
                "block_id": b["block_id"],
                "page": b["page"],
                "para_index": b["para_index"],
                "location_label": f"第{b['page']}页 第{b['para_index'] + 1}段",
                "quote": doc["full_text"][char_start:char_end][:300],
            }
    raise NotFoundError("该字符区间不在任何分块内")


__all__ = ["router"]
