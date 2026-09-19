"""报告导出接口（Markdown / PDF）。"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, PlainTextResponse

from app.api.deps import SessionDep
from app.core.exceptions import AppError
from app.services.report_service import (
    build_report_markdown,
    build_writeback_markdown,
    export_report,
)
from app.services.task_query import TaskQueryService
from app.services.task_service import TaskService

router = APIRouter(prefix="/tasks/{task_id}/report", tags=["报告导出"])

_MIME = {
    "md": "text/markdown; charset=utf-8",
    "pdf": "application/pdf",
}


@router.get("", summary="预览审查报告（Markdown 文本）")
async def preview(task_id: str, session: SessionDep) -> PlainTextResponse:
    detail = await TaskQueryService.detail(session, task_id)
    md = build_report_markdown(detail, detail.get("risk_items", []))
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")


@router.get("/writeback-text", summary="预览回写用的意见文本")
async def writeback_text(task_id: str, session: SessionDep) -> PlainTextResponse:
    detail = await TaskQueryService.detail(session, task_id)
    md = build_writeback_markdown(detail, detail.get("risk_items", []))
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")


@router.get("/download", summary="下载报告文件（fmt=md|pdf）")
async def download(
    task_id: str,
    session: SessionDep,
    fmt: str = Query(default="md", pattern="^(md|pdf)$"),
):
    task = await TaskService.get(session, task_id)
    if task.status != "completed":
        raise AppError(f"任务尚未完成审查（当前状态 {task.status}），无法导出报告")

    detail = await TaskQueryService.detail(session, task_id)
    try:
        path = export_report(detail, detail.get("risk_items", []), fmt=fmt)
    except Exception as exc:
        raise AppError(f"报告生成失败：{type(exc).__name__}: {exc}") from exc

    return FileResponse(
        path,
        media_type=_MIME.get(fmt, "application/octet-stream"),
        filename=path.name,
    )


@router.get("/files", summary="已生成的报告文件列表")
async def list_files(task_id: str, session: SessionDep) -> dict:
    from app.core.config import settings

    await TaskService.get(session, task_id)
    detail = await TaskQueryService.detail(session, task_id)
    import re

    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", detail.get("title") or "合同")[:40]
    files = sorted(
        (
            p
            for p in settings.report_dir.glob(f"{safe}_审查报告_*")
            if p.suffix in (".md", ".pdf")
        ),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return {
        "total": len(files),
        "dir": str(settings.report_dir),
        "files": [
            {
                "name": p.name,
                "size": p.stat().st_size,
                "ext": p.suffix.lstrip("."),
                "path": str(p),
            }
            for p in files
        ],
    }


__all__ = ["router"]
