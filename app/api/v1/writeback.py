"""审批回写接口。"""

from __future__ import annotations

from fastapi import APIRouter, Body

from app.api.deps import SessionDep
from app.schemas.task import OpResult
from app.services.writeback_service import WritebackService

router = APIRouter(prefix="/tasks/{task_id}/writeback", tags=["审批回写"])


@router.get("", summary="回写状态与历史留痕")
async def get_writeback(task_id: str, session: SessionDep) -> dict:
    from app.services.task_service import TaskService
    from app.models.enums import WRITEBACK_STATUS_LABEL

    task = await TaskService.get(session, task_id)
    history = await WritebackService.history(session, task_id)
    payload = await WritebackService.build_payload(session, task_id)
    return {
        "task_id": task_id,
        "status": task.writeback_status,
        "status_label": WRITEBACK_STATUS_LABEL.get(task.writeback_status),
        "attempts": task.writeback_attempts,
        "ticket_id": task.approval_ticket_id,
        "preview_md": payload.markdown,
        "history": history,
    }


@router.get("/preview", summary="预览将要回写的意见文本")
async def preview(
    task_id: str,
    session: SessionDep,
    lawyer_notes: str = "",
) -> dict:
    payload = await WritebackService.build_payload(session, task_id, lawyer_notes=lawyer_notes)
    return {
        "payload_md": payload.markdown,
        "chars": len(payload.markdown),
        "task_id": task_id,
        "ticket_id": payload.detail.get("approval_ticket_id"),
    }


@router.post("", summary="一键回写审批意见")
async def submit(
    task_id: str,
    session: SessionDep,
    lawyer_notes: str = Body(default="", embed=True, description="法务批注，会拼进意见文本"),
    ticket_id: str | None = Body(default=None, embed=True, description="目标审批单号"),
    by: str = Body(default="法务审查人", embed=True),
    force: bool = Body(default=False, embed=True, description="强制重写（仍受幂等键约束）"),
) -> dict:
    result = await WritebackService.submit(
        session,
        task_id,
        lawyer_notes=lawyer_notes,
        ticket_id=ticket_id,
        by=by,
        force=force,
    )
    if not result.get("duplicated"):
        from app.services.task_service import TaskService

        task = await TaskService.get(session, task_id)
        if ticket_id and not task.approval_ticket_id:
            task.approval_ticket_id = ticket_id
            await session.commit()
    return result


@router.post("/retry", response_model=OpResult, summary="回写失败重试")
async def retry(
    task_id: str,
    session: SessionDep,
    by: str = Body(default="系统管理员", embed=True),
) -> OpResult:
    result = await WritebackService.retry(session, task_id, by=by)
    return OpResult(
        ok=result.get("status") == "success",
        message=result.get("message", "已重试"),
        data=result,
    )


__all__ = ["router"]
