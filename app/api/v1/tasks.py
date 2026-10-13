"""合同审查任务接口：大盘列表、创建、详情、重试、批注。

状态流转一律通过 :class:`TaskService`，路由层不直接改 ``task.status``。
写接口挂 ``require_perm`` —— 前端 `v-perm` 只是呈现层收敛，真正的边界在这里。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, Query, UploadFile

from app.api.deps import AuthDep, SessionDep, require_perm
from app.core.config import settings
from app.core.exceptions import AppError, ForbiddenError, NotFoundError
from app.models.enums import RISK_LEVEL_LABEL, TaskSource, TaskStatus
from app.schemas.task import (
    BatchUploadOut,
    CommentIn,
    DecisionIn,
    OpResult,
    RetryIn,
    TaskBrief,
    TaskCreateIn,
    TaskDetail,
    TaskListOut,
)
from app.services.batch_service import MAX_BATCH_FILES, BatchService
from app.services.task_service import TaskService

router = APIRouter(prefix="/tasks", tags=["审查任务"])

#: 多文件上传时**单次请求**的总量上限（单文件仍受 settings.max_upload_mb 约束）。
#: 上传的字节是先读进内存再落盘的，本机内存卡得死，所以多文件再加一道总量闸门。
MAX_BATCH_TOTAL_MB = 100


# ----------------------------------------------------------------------
# 大盘
# ----------------------------------------------------------------------
@router.get(
    "",
    response_model=TaskListOut,
    summary="待审合同列表",
    dependencies=[Depends(require_perm("task.view"))],
)
async def list_tasks(
    session: SessionDep,
    status: str | None = Query(default=None, description="pending/parsing/reviewing/blocked/completed"),
    risk: str | None = Query(default=None, description="high/medium/low"),
    business_type: str | None = Query(default=None),
    keyword: str | None = Query(default=None, description="按标题/编号/相对方/申请人/部门模糊搜索"),
    applicant: str | None = Query(default=None, description="按申请人模糊匹配"),
    date_from: datetime | None = Query(default=None, description="创建时间下界（含）"),
    date_to: datetime | None = Query(default=None, description="创建时间上界（含）"),
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
        applicant=applicant,
        date_from=date_from,
        date_to=date_to,
        approval_ticket_id=approval_ticket_id,
        limit=limit,
        offset=offset,
    )
    # 批次关系一次性查完（两条 IN 查询），别按行查 —— 列表最多 200 行
    batch_map = await BatchService.annotate(session, [t.id for t in rows])
    return TaskListOut(
        total=total,
        items=[TaskBrief.from_orm_task(t, batch_map.get(t.id)) for t in rows],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/stats",
    summary="大盘统计",
    dependencies=[Depends(require_perm("task.view"))],
)
async def task_stats(session: SessionDep) -> dict:
    return await TaskService.stats(session)


# ----------------------------------------------------------------------
# 创建（JSON 路径引用 或 文件上传）
# ----------------------------------------------------------------------
@router.post(
    "",
    response_model=TaskBrief,
    summary="创建审查任务（JSON）",
    dependencies=[Depends(require_perm("task.submit"))],
)
async def create_task(
    payload: TaskCreateIn, session: SessionDep, ctx: AuthDep
) -> TaskBrief:
    task = await TaskService.create(
        session,
        title=payload.title,
        business_type=payload.business_type,
        # 与上传路径同一套兜底：没填就用登录身份
        applicant=payload.applicant or ctx.display_name,
        department=payload.department or (ctx.user.dept if ctx.user else None),
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

        # ⚠️ 必须先把 id 取成普通字符串再 expire_all()：
        # expire_all() 之后连 `task.id` 都会触发**同步**惰性重载，
        # 而实参求值发生在 async 之外 → sqlalchemy.exc.MissingGreenlet → 接口 500。
        tid = task.id
        await PipelineRunner.run(tid)
        session.expire_all()
        task = await TaskService.get(session, tid)

    return TaskBrief.from_orm_task(task)


# ----------------------------------------------------------------------
# 上传：单文件（兼容入口） / 多文件（批次）
# ----------------------------------------------------------------------
def _title_for(base: str | None, filename: str, *, multi: bool) -> str:
    """任务标题。

    没填标题就用文件名（去扩展名）；**多文件时把文件名缀在后面**，
    否则一个批次里几条任务全同名，文件切换条和审查大盘上分不出来谁是谁。
    """
    stem = Path(filename).stem.strip()
    base = (base or "").strip()
    if not base:
        return stem or filename or "未命名合同"
    if not multi or not stem or stem in base:
        return base
    return f"{base} · {stem}"


async def _create_tasks_from_uploads(
    session,
    ctx,
    uploads: list[tuple[str, bytes]],
    *,
    title: str | None,
    business_type: str | None,
    applicant: str | None,
    department: str | None,
    contract_no: str | None,
) -> tuple[str, list[str]]:
    """建任务 → 挂附件 → 归入同一批次 → commit。

    **一份文件一条任务**：整条链路（IR 的 doc_id、块唯一键、风险项字符区间）
    都是「一任务一文档」的模型，多文件只能各建各的单，批次只做展示分组
    （详见 ``app/services/batch_service.py``）。

    返回 ``(batch_id, 任务 id 列表)``，其中 **id 是普通字符串** ——
    调用方随后会 ``expire_all()`` 再回查，绝不能把 ORM 属性留到 await 之后再求值。
    """
    from app.services.file_service import FileService

    multi = len(uploads) > 1
    task_ids: list[str] = []
    for filename, raw in uploads:
        if len(raw) > settings.max_upload_mb * 1024 * 1024:
            raise AppError(f"「{filename}」超过 {settings.max_upload_mb}MB 上限")
        task = await TaskService.create(
            session,
            title=_title_for(title, filename, multi=multi),
            business_type=business_type,
            # 申请人 / 送审部门留空时取登录身份 —— 比让用户手打更可信
            applicant=applicant or ctx.display_name,
            department=department or (ctx.user.dept if ctx.user else None),
            contract_no=contract_no,
            source=TaskSource.UPLOAD,
        )
        await FileService.save_bytes(session, task, filename=filename, raw=raw)
        task_ids.append(task.id)
    await session.commit()

    # 单文件也建批次（成员数 1）—— 「上传」只有一条代码路径，前端不必分叉；
    # 成员数 < 2 时 BatchService.context 不返回批次，界面上看不出「批次」这回事。
    batch_id = await BatchService.create(session, task_ids)
    await session.commit()
    return batch_id, task_ids


async def _run_pipeline_and_reload(session, task_ids: list[str]):
    """逐条跑流水线，然后回查任务对象。

    ⚠️ 必须**先把 id 落成普通字符串**（``task_ids`` 本来就是 str）再 ``expire_all()``：
    expire_all() 之后连读 ``task.id`` 都会触发**同步**惰性重载，
    而实参求值发生在 await 之外 → ``sqlalchemy.exc.MissingGreenlet`` → 接口 500。
    踩过的现场表现是「传了合同但解析不出来」（数据其实都落库了）。

    **串行**而不是并发：OCR 首次加载很吃内存，本机内存卡得死，
    并发跑多份扫描件容易把机器拖死。
    """
    from app.services.pipeline import PipelineRunner

    for tid in task_ids:
        await PipelineRunner.run(tid)
    session.expire_all()
    return [await TaskService.get(session, tid) for tid in task_ids]


@router.post(
    "/upload",
    response_model=TaskBrief,
    summary="上传单份合同附件并建任务",
    dependencies=[Depends(require_perm("task.upload"))],
)
async def upload_task(
    session: SessionDep,
    ctx: AuthDep,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    business_type: str | None = Form(default=None),
    applicant: str | None = Form(default=None),
    department: str | None = Form(default=None),
    contract_no: str | None = Form(default=None),
    run_now: bool = Form(default=True),
) -> TaskBrief:
    """单份文件上传（保留的兼容入口，响应仍是单条 ``TaskBrief``）。

    多份文件请走 ``POST /tasks/upload-batch``。
    """
    raw = await file.read()
    if not raw:
        raise AppError("上传的文件为空")

    _, task_ids = await _create_tasks_from_uploads(
        session,
        ctx,
        [(file.filename or "upload.bin", raw)],
        title=title,
        business_type=business_type,
        applicant=applicant,
        department=department,
        contract_no=contract_no,
    )

    if run_now:
        tasks = await _run_pipeline_and_reload(session, task_ids)
    else:
        tasks = [await TaskService.get(session, tid) for tid in task_ids]
    return TaskBrief.from_orm_task(tasks[0])


@router.post(
    "/upload-batch",
    response_model=BatchUploadOut,
    summary="一次上传多份文件（每份各建一条任务，同属一个批次）",
    dependencies=[Depends(require_perm("task.upload"))],
)
async def upload_batch(
    session: SessionDep,
    ctx: AuthDep,
    files: list[UploadFile] = File(..., description=f"1~{MAX_BATCH_FILES} 份文件/图片"),
    title: str | None = Form(default=None, description="批次标题，会与各文件名拼成任务标题"),
    business_type: str | None = Form(default=None),
    applicant: str | None = Form(default=None),
    department: str | None = Form(default=None),
    contract_no: str | None = Form(default=None),
    run_now: bool = Form(default=True),
) -> BatchUploadOut:
    """多文件上传：**每份文件各自一条任务，用批次号归组**。

    返回 ``batch``（批次上下文：成员文件名、序号、各自风险统计）供前端渲染
    「文件切换条」；``tasks`` 是各条任务摘要。

    任何一份文件校验不过 → **整批不建单**（先全量校验再落库），
    避免出现「传了 5 份、成功 3 份」这种半截状态让用户对不上账。
    """
    uploads: list[tuple[str, bytes]] = []
    total_bytes = 0
    for f in files:
        raw = await f.read()
        name = f.filename or "upload.bin"
        if not raw:
            raise AppError(f"文件为空：{name}")
        if len(raw) > settings.max_upload_mb * 1024 * 1024:
            raise AppError(f"「{name}」超过 {settings.max_upload_mb}MB 上限")
        total_bytes += len(raw)
        uploads.append((name, raw))

    if not uploads:
        raise AppError("请至少选择一个文件")
    if len(uploads) > MAX_BATCH_FILES:
        raise AppError(f"单次最多上传 {MAX_BATCH_FILES} 份文件，实得 {len(uploads)}")
    if total_bytes > MAX_BATCH_TOTAL_MB * 1024 * 1024:
        raise AppError(
            f"本批文件合计 {total_bytes / 1024 / 1024:.1f}MB，"
            f"超过 {MAX_BATCH_TOTAL_MB}MB 上限，请分批上传"
        )

    batch_id, task_ids = await _create_tasks_from_uploads(
        session,
        ctx,
        uploads,
        title=title,
        business_type=business_type,
        applicant=applicant,
        department=department,
        contract_no=contract_no,
    )

    if run_now:
        tasks = await _run_pipeline_and_reload(session, task_ids)
    else:
        tasks = [await TaskService.get(session, tid) for tid in task_ids]

    return BatchUploadOut(
        batch_id=batch_id,
        total=len(tasks),
        tasks=[TaskBrief.from_orm_task(t) for t in tasks],
        batch=await BatchService.context(session, task_ids[0]),
    )


# ----------------------------------------------------------------------
# 详情
# ----------------------------------------------------------------------
@router.get(
    "/{task_id}",
    response_model=TaskDetail,
    summary="任务详情（工作台）",
    dependencies=[Depends(require_perm("task.view"))],
)
async def get_task(task_id: str, session: SessionDep) -> TaskDetail:
    from app.services.task_query import TaskQueryService

    data = await TaskQueryService.detail(session, task_id)
    return TaskDetail(**data)


# ----------------------------------------------------------------------
# 状态操作
# ----------------------------------------------------------------------
@router.post(
    "/{task_id}/run",
    response_model=TaskBrief,
    summary="执行/继续流水线",
    dependencies=[Depends(require_perm("task.monitor"))],
)
async def run_pipeline(task_id: str, session: SessionDep) -> TaskBrief:
    from app.services.pipeline import PipelineRunner

    await PipelineRunner.run(task_id)
    session.expire_all()
    task = await TaskService.get(session, task_id)
    return TaskBrief.from_orm_task(task)


@router.post(
    "/{task_id}/retry",
    response_model=TaskBrief,
    summary="重新审查（阻塞重试）",
    dependencies=[Depends(require_perm("task.retry"))],
)
async def retry_task(
    task_id: str, payload: RetryIn, session: SessionDep, ctx: AuthDep
) -> TaskBrief:
    task = await TaskService.get(session, task_id)
    # 操作人以登录身份为准，不用前端传来的值
    await TaskService.retry(session, task, by=ctx.actor)

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


@router.post(
    "/{task_id}/comments",
    summary="添加法务批注",
    dependencies=[Depends(require_perm("comment.add"))],
)
async def add_comment(
    task_id: str, payload: CommentIn, session: SessionDep, ctx: AuthDep
) -> OpResult:
    task = await TaskService.get(session, task_id)
    c = await TaskService.add_comment(
        session,
        task,
        content=payload.content,
        # 作者与角色一律以登录身份为准：前端传的只是展示文案，不可信
        author=ctx.display_name,
        role=ctx.role,
        kind="comment",
        risk_item_id=payload.risk_item_id,
        block_id=payload.block_id,
        char_start=payload.char_start,
        char_end=payload.char_end,
    )
    await session.commit()
    return OpResult(message="批注已保存", data={"comment_id": c.id})


@router.post(
    "/{task_id}/risks/{risk_id}/decision",
    summary="采纳/调整/驳回风险项与建议",
    dependencies=[Depends(require_perm("risk.decide"))],
)
async def decide_risk(
    task_id: str, risk_id: str, payload: DecisionIn, session: SessionDep, ctx: AuthDep
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
            sug.edited_by = ctx.display_name

    await TaskService.add_comment(
        session,
        task,
        content=(
            f"风险项「{risk.name}」处理结果：{payload.decision}"
            + (f"；说明：{payload.note}" if payload.note else "")
            + (f"；已调整示范条款" if payload.edited_text else "")
        ),
        author=ctx.display_name,
        role=ctx.role,
        kind="decision",
        risk_item_id=risk.id,
    )
    await session.commit()
    return OpResult(message="已更新处理结果", data={"decision": payload.decision})


@router.delete(
    "/{task_id}",
    summary="删除任务",
    dependencies=[Depends(require_perm("task.batch"))],
)
async def delete_task(task_id: str, session: SessionDep) -> OpResult:
    await TaskService.delete(session, task_id)
    await session.commit()
    return OpResult(message=f"任务已删除：{task_id}")


# ----------------------------------------------------------------------
# 批量操作（大盘列表勾选后触发）
# ----------------------------------------------------------------------
#: 每个动作对应的「允许的当前状态」，不符合的会跳过并回报原因
_BATCH_ALLOWED: dict[str, set[str]] = {
    "retry": {TaskStatus.BLOCKED.value, TaskStatus.COMPLETED.value},
    "run": {TaskStatus.PENDING.value},
}

#: 每个批量动作要求的权限点（与前端 batch-bar 上各按钮的 v-perm 一一对应）
_BATCH_PERM: dict[str, str] = {
    "retry": "task.retry",
    "run": "task.monitor",
    "delete": "task.batch",
}


@router.post("/batch", summary="批量操作：重试 / 执行 / 删除")
async def batch_ops(
    session: SessionDep,
    ctx: AuthDep,
    action: str = Body(..., embed=True, description="retry / run / delete"),
    task_ids: list[str] = Body(..., embed=True),
    by: str = Body(default="", embed=True, description="已废弃：操作人取自登录身份"),
) -> OpResult:
    """大盘批量操作。

    逐条执行而不是一条 SQL 打天下，原因是**状态流转必须穿过 TaskService**，
    否则会绕过状态机校验（见状态机设计约束）。单条失败不影响其它条目，
    返回里逐条汇报 ``ok / skipped``，前端据此提示。

    权限按动作分别校验：``retry → task.retry``、``run → task.monitor``、
    ``delete → task.batch``。三个权限点管理员都有，但他没有
    ``task.submit`` / ``risk.decide`` —— 这是刻意的职责分离。
    """
    if action not in {"retry", "run", "delete"}:
        raise AppError(f"不支持的批量动作：{action}，可选 retry / run / delete")
    if not task_ids:
        raise AppError("task_ids 不能为空")
    if len(task_ids) > 100:
        raise AppError("单次批量最多 100 条")

    need = _BATCH_PERM[action]
    if not ctx.has(need):
        from app.core.permissions import perm_label

        raise ForbiddenError(
            f"当前身份「{ctx.role_name}」缺少权限：{perm_label(need)}（{need}）"
        )
    by = ctx.actor  # 操作人以登录身份为准

    done: list[str] = []
    skipped: list[dict] = []

    for tid in task_ids:
        try:
            from app.services.pipeline import PipelineRunner

            if action == "delete":
                # 走统一入口 + **必须 commit** —— 这里曾经 continue 掉、跳过循环尾部的
                # commit，导致回报「成功 N 条」而数据一条没删（假成功）。
                await TaskService.delete(session, tid)
                await session.commit()
                done.append(tid)
                continue

            task = await TaskService.get(session, tid)
            allowed = _BATCH_ALLOWED[action]
            if task.status not in allowed:
                from app.models.enums import TASK_STATUS_LABEL

                skipped.append(
                    {
                        "task_id": tid,
                        "title": task.title,
                        "reason": f"当前状态「{TASK_STATUS_LABEL.get(task.status, task.status)}」不支持该操作",
                    }
                )
                continue

            if action == "retry":
                await TaskService.retry(session, task, by=by)
            await session.commit()

            await PipelineRunner.run(tid)
            done.append(tid)
        except Exception as exc:  # 单条失败不拖垮整批
            await session.rollback()
            skipped.append({"task_id": tid, "reason": f"{type(exc).__name__}: {exc}"})

    label = {"retry": "重新审查", "run": "执行流水线", "delete": "删除"}[action]
    return OpResult(
        ok=not skipped,
        message=f"批量{label}：成功 {len(done)} 条" + (f"，跳过 {len(skipped)} 条" if skipped else ""),
        data={"action": action, "done": done, "skipped": skipped},
    )
