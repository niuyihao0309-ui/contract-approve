"""合同审查任务接口：大盘列表、创建、详情、重试、批注。

状态流转一律通过 :class:`TaskService`，路由层不直接改 ``task.status``。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, Query, UploadFile

from app.api.deps import SessionDep
from app.core.config import settings
from app.core.exceptions import AppError, NotFoundError
from app.models.enums import RISK_LEVEL_LABEL, TaskSource, TaskStatus
from app.schemas.task import (
    CommentIn,
    DecisionIn,
    OpResult,
    RetryIn,
    TaskBrief,
    TaskCreateIn,
    TaskDetail,
    TaskListOut,
)
from app.services.task_service import TaskService

router = APIRouter(prefix="/tasks", tags=["审查任务"])


# ----------------------------------------------------------------------
# 大盘
# ----------------------------------------------------------------------
@router.get("", response_model=TaskListOut, summary="待审合同列表")
async def list_tasks(
    session: SessionDep,
    status: str | None = Query(default=None, description="pending/parsing/reviewing/blocked/completed"),
    risk: str | None = Query(default=None, description="high/medium/low"),
    business_type: str | None = Query(default=None),
    keyword: str | None = Query(default=None, description="按标题/编号/相对方模糊搜索"),
    approval_ticket_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> TaskListOut:
    rows, total = await TaskService.list_tasks(
        session,
        status=status,
        risk=risk,
        business_type=business_type,
        keyword=keyword,
        approval_ticket_id=approval_ticket_id,
        limit=limit,
        offset=offset,
    )
    return TaskListOut(
        total=total,
        items=[TaskBrief.from_orm_task(t) for t in rows],
        limit=limit,
        offset=offset,
    )


@router.get("/stats", summary="大盘统计")
async def task_stats(session: SessionDep) -> dict:
    return await TaskService.stats(session)


# ----------------------------------------------------------------------
# 创建（JSON 路径引用 或 文件上传）
# ----------------------------------------------------------------------
@router.post("", response_model=TaskBrief, summary="创建审查任务（JSON）")
async def create_task(payload: TaskCreateIn, session: SessionDep) -> TaskBrief:
    task = await TaskService.create(
        session,
        title=payload.title,
        business_type=payload.business_type,
        applicant=payload.applicant,
        department=payload.department,
        counterparty=payload.counterparty,
        contract_no=payload.contract_no,
        amount=payload.amount,
        currency=payload.currency,
        source=TaskSource.UPLOAD,
    )

    if payload.file_path:
        from app.services.file_service import FileService

        await FileService.attach_local_file(session, task, Path(payload.file_path))

    await session.commit()

    if payload.run_now:
        from app.services.pipeline import PipelineRunner

        await PipelineRunner.run(task.id)
        session.expire_all()
        task = await TaskService.get(session, task.id)

    return TaskBrief.from_orm_task(task)


@router.post("/upload", response_model=TaskBrief, summary="上传合同附件并建任务")
async def upload_task(
    session: SessionDep,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    business_type: str | None = Form(default=None),
    applicant: str | None = Form(default=None),
    department: str | None = Form(default=None),
    contract_no: str | None = Form(default=None),
    run_now: bool = Form(default=True),
) -> TaskBrief:
    from app.services.file_service import FileService

    raw = await file.read()
    if len(raw) > settings.max_upload_mb * 1024 * 1024:
        raise AppError(f"文件超过 {settings.max_upload_mb}MB 上限")

    task = await TaskService.create(
        session,
        title=title or file.filename or "未命名合同",
        business_type=business_type,
        applicant=applicant,
        department=department,
        contract_no=contract_no,
        source=TaskSource.UPLOAD,
    )
    await FileService.save_bytes(session, task, filename=file.filename or "upload.bin", raw=raw)
    await session.commit()

    if run_now:
        from app.services.pipeline import PipelineRunner

        await PipelineRunner.run(task.id)
        session.expire_all()
        task = await TaskService.get(session, task.id)

    return TaskBrief.from_orm_task(task)


# ----------------------------------------------------------------------
# 详情
# ----------------------------------------------------------------------
@router.get("/{task_id}", response_model=TaskDetail, summary="任务详情（工作台）")
async def get_task(task_id: str, session: SessionDep) -> TaskDetail:
    from app.services.task_query import TaskQueryService

    data = await TaskQueryService.detail(session, task_id)
    return TaskDetail(**data)


# ----------------------------------------------------------------------
# 状态操作
# ----------------------------------------------------------------------
@router.post("/{task_id}/run", response_model=TaskBrief, summary="执行/继续流水线")
async def run_pipeline(task_id: str, session: SessionDep) -> TaskBrief:
    from app.services.pipeline import PipelineRunner

    await PipelineRunner.run(task_id)
    session.expire_all()
    task = await TaskService.get(session, task_id)
    return TaskBrief.from_orm_task(task)


@router.post("/{task_id}/retry", response_model=TaskBrief, summary="重新审查（阻塞重试）")
async def retry_task(task_id: str, payload: RetryIn, session: SessionDep) -> TaskBrief:
    task = await TaskService.get(session, task_id)
    await TaskService.retry(session, task, by=payload.by)

    if payload.file_path:
        from app.services.file_service import FileService

        # 清掉旧的附件与解析产物，换上新文件
        await FileService.replace_files(session, task, Path(payload.file_path))

    await session.commit()

    from app.services.pipeline import PipelineRunner

    await PipelineRunner.run(task_id)
    session.expire_all()
    task = await TaskService.get(session, task_id)
    return TaskBrief.from_orm_task(task)


@router.post("/{task_id}/comments", summary="添加法务批注")
async def add_comment(task_id: str, payload: CommentIn, session: SessionDep) -> OpResult:
    task = await TaskService.get(session, task_id)
    c = await TaskService.add_comment(
        session,
        task,
        content=payload.content,
        author=payload.author,
        role=payload.role,
        kind="comment",
        risk_item_id=payload.risk_item_id,
        block_id=payload.block_id,
        char_start=payload.char_start,
        char_end=payload.char_end,
    )
    await session.commit()
    return OpResult(message="批注已保存", data={"comment_id": c.id})


@router.post("/{task_id}/risks/{risk_id}/decision", summary="采纳/调整/驳回风险项与建议")
async def decide_risk(
    task_id: str, risk_id: str, payload: DecisionIn, session: SessionDep
) -> OpResult:
    from app.models.enums import ItemDecision
    from app.models.tables import ClauseSuggestion, RiskItem
    from sqlalchemy import select

    task = await TaskService.get(session, task_id)
    risk = await session.get(RiskItem, risk_id)
    if risk is None or risk.task_id != task.id:
        raise NotFoundError(f"风险项不存在：{risk_id}")

    valid = {d.value for d in ItemDecision}
    if payload.decision not in valid:
        raise AppError(f"decision 必须是 {sorted(valid)} 之一")

    risk.decision = payload.decision
    if payload.note:
        risk.lawyer_note = payload.note

    # 同步到该风险下的建议：采纳后法务可直接改文本
    stmt = select(ClauseSuggestion).where(ClauseSuggestion.risk_item_id == risk.id)
    for sug in (await session.execute(stmt)).scalars().all():
        sug.decision = payload.decision
        if payload.edited_text is not None:
            sug.edited_text = payload.edited_text
            sug.edited_by = payload.by

    await TaskService.add_comment(
        session,
        task,
        content=(
            f"风险项「{risk.name}」处理结果：{payload.decision}"
            + (f"；说明：{payload.note}" if payload.note else "")
            + (f"；已调整示范条款" if payload.edited_text else "")
        ),
        author=payload.by,
        role="lawyer",
        kind="decision",
        risk_item_id=risk.id,
    )
    await session.commit()
    return OpResult(message="已更新处理结果", data={"decision": payload.decision})


@router.delete("/{task_id}", summary="删除任务")
async def delete_task(task_id: str, session: SessionDep) -> OpResult:
    task = await TaskService.get(session, task_id)
    await session.delete(task)
    await session.commit()
    return OpResult(message=f"任务已删除：{task_id}")
