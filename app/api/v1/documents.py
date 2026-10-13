"""文档视窗接口：原文、条款、元数据、原件流。

前端工作台的双栏视窗靠这里的接口拿到「原文 + 分块 + 定位」三件套，
高亮层由前端叠加渲染，不改动原文结构。

``/files/{file_id}/raw`` 是「原件视图」的数据源：PDF 直接给浏览器内嵌预览，
扫描件（png/jpg）直接当图片渲染，DOCX 浏览器不能内嵌，前端会退化成下载。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.api.deps import SessionDep, require_perm
from app.core.exceptions import AppError, NotFoundError
from app.models.tables import ContractFile
from app.services.task_query import TaskQueryService

router = APIRouter(
    prefix="/tasks",
    tags=["文档视窗"],
    # 原文 / 条款 / 元数据都属「查看原文与高亮证据」这一个权限点；
    # 原件预览（PDF / 扫描件流）另需 doc.preview，见下面两个路由。
    dependencies=[Depends(require_perm("doc.view"))],
)

#: 扩展名 -> MIME。浏览器据此决定内嵌预览还是交给下载器
_RAW_MIME = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
}

#: 能直接在浏览器里内嵌预览的类型
_INLINE_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff"}


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


@router.get(
    "/{task_id}/files/{file_id}/raw",
    summary="原件流（PDF/扫描件在线预览）",
    dependencies=[Depends(require_perm("doc.preview"))],
)
async def raw_file(task_id: str, file_id: str, session: SessionDep):
    """把上传的原件喷给浏览器，用于「原件视图」。

    路径由服务端管理（都在 ``storage/uploads`` 下），这里只校验它确实属于该任务，
    不让前端拿任意路径去读服务器文件。

    注意：这个 URL 是塞进 ``<img src>`` / ``<iframe src>`` 的，浏览器直接发请求、
    带不了 ``Authorization`` 头，所以前端把令牌拼在 ``?token=`` 上（见 deps._extract_token）。
    """
    row = await session.get(ContractFile, file_id)
    if row is None or row.task_id != task_id:
        raise NotFoundError(f"附件不存在：{file_id}")

    path = Path(row.storage_path or "")
    if not path.exists() or not path.is_file():
        raise AppError(f"附件物理文件已不存在：{row.filename}")

    ext = path.suffix.lower()
    inline = ext in _INLINE_EXT
    return FileResponse(
        path,
        media_type=_RAW_MIME.get(ext, "application/octet-stream"),
        filename=row.filename,
        content_disposition_type="inline" if inline else "attachment",
    )


@router.get(
    "/{task_id}/preview-capability",
    summary="原件预览能力（前端决定用哪种视图）",
    dependencies=[Depends(require_perm("doc.preview"))],
)
async def preview_capability(task_id: str, session: SessionDep) -> dict:
    """告诉前端每个附件能不能内嵌预览，省得前端自己猜扩展名。"""
    rows = await TaskQueryService._files(session, task_id)
    return {
        "total": len(rows),
        "items": [
            {
                "id": f["id"],
                "filename": f["filename"],
                "file_kind": f["file_kind"],
                "inline": Path(f["filename"]).suffix.lower() in _INLINE_EXT,
                "viewer": _viewer_for(Path(f["filename"]).suffix.lower()),
                "url": f"/api/v1/tasks/{task_id}/files/{f['id']}/raw",
            }
            for f in rows
        ],
    }


def _viewer_for(ext: str) -> str:
    """返回前端该用的渲染器：pdf / image / unsupported。"""
    if ext == ".pdf":
        return "pdf"
    if ext in {".png", ".jpg", ".jpeg", ".bmp", ".tiff"}:
        return "image"
    return "unsupported"


__all__ = ["router"]
