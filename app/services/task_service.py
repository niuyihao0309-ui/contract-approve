"""任务状态机服务。

大纲「地基③」明确要求：**状态流转集中在一个 service 里，不要在多个地方改状态。**
所以本模块是唯一允许写 ``ContractTask.status`` / ``writeback_status`` 的地方，
其它模块要改状态必须调这里的函数。

状态图::

    pending ──► parsing ──► reviewing ──► completed
       ▲           │            │            │
       │           ▼            ▼            │ (重新审查)
       └────── blocked ◄────────┘            │
       ▲                                    │
       └────────────────────────────────────┘
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, StateTransitionError
from app.core.logging import logger
from app.models.enums import (
    BLOCK_REASON_LABEL,
    RETRYABLE_TASK_STATUS,
    TASK_STATUS_LABEL,
    TASK_TRANSITIONS,
    WRITEBACK_STATUS_LABEL,
    WRITEBACK_TRANSITIONS,
    BlockReason,
    ReviewStage,
    TaskSource,
    TaskStatus,
    WritebackStatus,
)
from app.models.tables import ContractTask, ReviewComment

#: 各阶段对应的进度百分比，让前端进度条有连续感
STAGE_PROGRESS: dict[str, int] = {
    ReviewStage.RECEIVED.value: 5,
    ReviewStage.DOWNLOADED.value: 15,
    ReviewStage.PARSED.value: 35,
    ReviewStage.METADATA_DONE.value: 45,
    ReviewStage.CLAUSES_SEGMENTED.value: 55,
    ReviewStage.RULES_APPLIED.value: 70,
    ReviewStage.LLM_REVIEWED.value: 80,
    ReviewStage.SUGGESTED.value: 88,
    ReviewStage.AGGREGATED.value: 95,
    ReviewStage.REPORTED.value: 100,
}


def new_id() -> str:
    return str(uuid.uuid4())


class TaskService:
    """任务状态机 + 基础 CRUD。所有方法都接收外部 session，不自己开事务。"""

    # ------------------------------------------------------------------
    # 创建
    # ------------------------------------------------------------------
    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        title: str,
        business_type: str | None = None,
        applicant: str | None = None,
        department: str | None = None,
        counterparty: str | None = None,
        contract_no: str | None = None,
        amount: float | None = None,
        currency: str = "CNY",
        source: TaskSource = TaskSource.UPLOAD,
        approval_ticket_id: str | None = None,
        status: TaskStatus = TaskStatus.PENDING,
    ) -> ContractTask:
        """建一条任务。初始状态固定为 ``pending``（除非显式指定）。"""
        task = ContractTask(
            id=new_id(),
            title=title,
            business_type=business_type,
            applicant=applicant,
            department=department,
            counterparty=counterparty,
            contract_no=contract_no,
            amount=amount,
            currency=currency,
            source=source.value,
            approval_ticket_id=approval_ticket_id,
            status=status.value,
            writeback_status=WritebackStatus.NOT_WRITTEN.value,
            stage=ReviewStage.RECEIVED.value,
            progress=0,
        )
        session.add(task)
        await session.flush()
        logger.info(f"[任务 {task.id[:8]}] 创建：{title}（{TASK_STATUS_LABEL[task.status]}）")
        return task

    @staticmethod
    async def get(session: AsyncSession, task_id: str) -> ContractTask:
        task = await session.get(ContractTask, task_id)
        if task is None:
            raise NotFoundError(f"任务不存在：{task_id}")
        return task

    @staticmethod
    async def delete(session: AsyncSession, task_id: str) -> ContractTask:
        """删除任务（关联行由外键 ``ON DELETE CASCADE`` 带走）。

        抽成统一入口的理由：批量删除那条路径曾经**漏了 commit** ——
        ``session.delete()`` 之后直接 ``continue``，跳过了循环尾部的 ``await session.commit()``，
        于是接口回报「批量删除：成功 N 条」而数据一条没少（假成功，前端还提示成功）。
        删除逻辑收到这一处，调用方只需 ``await session.commit()``，就不会再漏。

        注意：本方法**不自己开事务**（与其它方法一致），只 flush。
        """
        task = await TaskService.get(session, task_id)
        title = task.title
        await session.delete(task)
        await session.flush()
        logger.info(f"[任务 {task_id[:8]}] 已删除：{title}")
        return task

    # ------------------------------------------------------------------
    # 状态流转（唯一入口）
    # ------------------------------------------------------------------
    @staticmethod
    def can_transition(current: str, target: str) -> bool:
        cur = TaskStatus(current)
        nxt = TaskStatus(target)
        return nxt in TASK_TRANSITIONS.get(cur, set())

    @staticmethod
    async def transition(
        session: AsyncSession,
        task: ContractTask,
        to: TaskStatus,
        *,
        reason: str | None = None,
        detail: str | None = None,
        force: bool = False,
    ) -> ContractTask:
        """执行一次任务状态流转。

        :param reason: 进入 blocked 时的 ``BlockReason`` 值
        :param detail: 阻塞详情 / 备注，写进 ``block_detail`` 并落一条系统备注
        :param force:  跳过流转表校验（仅用于数据修复，业务代码不要用）
        """
        current = TaskStatus(task.status)
        target = TaskStatus(to)

        if not force and not TaskService.can_transition(task.status, target.value):
            raise StateTransitionError(
                f"非法状态流转：{TASK_STATUS_LABEL[current.value]} → {TASK_STATUS_LABEL[target.value]}",
                detail={"from": current.value, "to": target.value, "allowed": sorted(
                    s.value for s in TASK_TRANSITIONS.get(current, set())
                )},
            )

        task.status = target.value
        logger.info(
            f"[任务 {task.id[:8]}] 状态流转：{TASK_STATUS_LABEL[current.value]} → "
            f"{TASK_STATUS_LABEL[target.value]}" + (f"（{detail}）" if detail else "")
        )

        if target is TaskStatus.BLOCKED:
            task.block_reason = reason or BlockReason.PARSE_FAILED.value
            task.block_detail = detail or BLOCK_REASON_LABEL.get(task.block_reason, "")
            task.progress = 0
            await TaskService.add_comment(
                session,
                task,
                author="系统",
                role="system",
                kind="action",
                content=(
                    f"任务进入阻塞：{BLOCK_REASON_LABEL.get(task.block_reason, task.block_reason)}"
                    + (f"。{detail}" if detail and detail != task.block_detail else "")
                ),
            )
        elif target in (TaskStatus.PARSING, TaskStatus.REVIEWING, TaskStatus.PENDING):
            # 离开 blocked 时清掉阻塞信息
            task.block_reason = None
            task.block_detail = None

        if target is TaskStatus.COMPLETED:
            task.completed_at = datetime.now(timezone.utc)
            if task.created_at:
                delta = task.completed_at - task.created_at.replace(tzinfo=timezone.utc)
                task.duration_ms = int(delta.total_seconds() * 1000)

        await session.flush()
        return task

    # ------------------------------------------------------------------
    # 语义化包装（业务代码用这些，不要直接拼 TaskStatus）
    # ------------------------------------------------------------------
    @staticmethod
    async def start_parsing(session: AsyncSession, task: ContractTask) -> ContractTask:
        return await TaskService.transition(session, task, TaskStatus.PARSING)

    @staticmethod
    async def start_reviewing(session: AsyncSession, task: ContractTask) -> ContractTask:
        return await TaskService.transition(session, task, TaskStatus.REVIEWING)

    @staticmethod
    async def complete(
        session: AsyncSession, task: ContractTask, *, summary: str | None = None
    ) -> ContractTask:
        task = await TaskService.transition(session, task, TaskStatus.COMPLETED)
        task.progress = 100
        task.stage = ReviewStage.REPORTED.value
        if summary:
            task.risk_summary = summary
        return task

    @staticmethod
    async def block(
        session: AsyncSession,
        task: ContractTask,
        reason: BlockReason | str,
        detail: str | None = None,
    ) -> ContractTask:
        """置为阻塞。已经是 blocked 时只更新原因，不重复流转。"""
        code = reason.value if isinstance(reason, BlockReason) else reason
        if task.status == TaskStatus.BLOCKED.value:
            task.block_reason = code
            task.block_detail = detail or BLOCK_REASON_LABEL.get(code, code)
            await TaskService.add_comment(
                session,
                task,
                author="系统",
                role="system",
                kind="action",
                content=f"阻塞原因更新：{task.block_detail}",
            )
            await session.flush()
            return task
        return await TaskService.transition(
            session, task, TaskStatus.BLOCKED, reason=code, detail=detail
        )

    @staticmethod
    async def retry(
        session: AsyncSession, task: ContractTask, *, by: str = "系统管理员"
    ) -> ContractTask:
        """一键重试：清阻塞 → 回到 pending → 计数 +1。

        只允许 ``blocked`` 和 ``completed`` 触发（后者用于「重新审查」）。
        """
        if TaskStatus(task.status) not in RETRYABLE_TASK_STATUS:
            raise StateTransitionError(
                f"当前状态（{TASK_STATUS_LABEL[task.status]}）不支持重试，"
                f"仅 {'/'.join(TASK_STATUS_LABEL[s.value] for s in RETRYABLE_TASK_STATUS)} 可重试"
            )
        prev_reason = task.block_reason
        task.retry_count = (task.retry_count or 0) + 1
        # blocked -> pending 是合法流转；completed -> pending 也在流转表里
        await TaskService.transition(session, task, TaskStatus.PENDING)
        task.progress = 0
        task.stage = ReviewStage.RECEIVED.value
        task.block_reason = None
        task.block_detail = None
        await TaskService.add_comment(
            session,
            task,
            author=by,
            role="admin",
            kind="action",
            content=f"触发重新审查（第 {task.retry_count} 次重试）"
            + (f"，原阻塞原因：{BLOCK_REASON_LABEL.get(prev_reason or '', prev_reason)}" if prev_reason else ""),
        )
        return task

    # ------------------------------------------------------------------
    # 进度
    # ------------------------------------------------------------------
    @staticmethod
    async def set_stage(
        session: AsyncSession, task: ContractTask, stage: ReviewStage | str, progress: int | None = None
    ) -> ContractTask:
        """更新流水线阶段与进度。"""
        s = stage.value if isinstance(stage, ReviewStage) else stage
        task.stage = s
        pct = progress if progress is not None else STAGE_PROGRESS.get(s, task.progress or 0)
        task.progress = max(0, min(100, pct))
        await session.flush()
        logger.debug(f"[任务 {task.id[:8]}] 阶段：{s} ({task.progress}%)")
        return task

    # ------------------------------------------------------------------
    # 回写状态机（与任务状态机分离）
    # ------------------------------------------------------------------
    @staticmethod
    async def set_writeback(
        session: AsyncSession,
        task: ContractTask,
        to: WritebackStatus,
        *,
        detail: str | None = None,
        force: bool = False,
    ) -> ContractTask:
        cur = WritebackStatus(task.writeback_status)
        tgt = WritebackStatus(to)
        if not force and tgt not in WRITEBACK_TRANSITIONS.get(cur, set()):
            raise StateTransitionError(
                f"非法回写状态流转：{WRITEBACK_STATUS_LABEL[cur.value]} → "
                f"{WRITEBACK_STATUS_LABEL[tgt.value]}"
            )
        task.writeback_status = tgt.value
        if tgt is WritebackStatus.WRITING:
            task.writeback_attempts = (task.writeback_attempts or 0) + 1
        await session.flush()
        logger.info(
            f"[任务 {task.id[:8]}] 回写状态：{WRITEBACK_STATUS_LABEL[cur.value]} → "
            f"{WRITEBACK_STATUS_LABEL[tgt.value]}" + (f"（{detail}）" if detail else "")
        )
        return task

    # ------------------------------------------------------------------
    # 备注 / 批注
    # ------------------------------------------------------------------
    @staticmethod
    async def add_comment(
        session: AsyncSession,
        task: ContractTask,
        *,
        content: str,
        author: str = "法务审查人",
        role: str = "lawyer",
        kind: str = "comment",
        risk_item_id: str | None = None,
        block_id: str | None = None,
        char_start: int | None = None,
        char_end: int | None = None,
    ) -> ReviewComment:
        c = ReviewComment(
            id=new_id(),
            task_id=task.id,
            author=author,
            role=role,
            kind=kind,
            content=content,
            risk_item_id=risk_item_id,
            block_id=block_id,
            char_start=char_start,
            char_end=char_end,
        )
        session.add(c)
        await session.flush()
        return c

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    @staticmethod
    async def list_tasks(
        session: AsyncSession,
        *,
        status: str | None = None,
        risk: str | None = None,
        business_type: str | None = None,
        keyword: str | None = None,
        applicant: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        approval_ticket_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ContractTask], int]:
        """大盘页列表：多条件筛选 + 总数。"""
        stmt = select(ContractTask)
        count_stmt = select(func.count(ContractTask.id))

        conditions = []
        if status:
            conditions.append(ContractTask.status == status)
        if risk:
            conditions.append(ContractTask.overall_risk == risk)
        if business_type:
            conditions.append(ContractTask.business_type == business_type)
        if applicant:
            conditions.append(ContractTask.applicant.like(f"%{applicant}%"))
        if date_from is not None:
            conditions.append(ContractTask.created_at >= date_from)
        if date_to is not None:
            conditions.append(ContractTask.created_at <= date_to)
        if approval_ticket_id:
            conditions.append(ContractTask.approval_ticket_id == approval_ticket_id)
        if keyword:
            like = f"%{keyword}%"
            conditions.append(
                ContractTask.title.like(like)
                | ContractTask.contract_no.like(like)
                | ContractTask.counterparty.like(like)
                | ContractTask.applicant.like(like)
                | ContractTask.department.like(like)
            )

        for c in conditions:
            stmt = stmt.where(c)
            count_stmt = count_stmt.where(c)

        stmt = stmt.order_by(ContractTask.created_at.desc()).limit(limit).offset(offset)
        rows = list((await session.execute(stmt)).scalars().all())
        total = int((await session.execute(count_stmt)).scalar() or 0)
        return rows, total

    @staticmethod
    async def stats(session: AsyncSession) -> dict[str, Any]:
        """大盘统计卡片数据。"""
        by_status: dict[str, int] = {}
        for st in TaskStatus:
            n = await session.scalar(
                select(func.count(ContractTask.id)).where(ContractTask.status == st.value)
            )
            by_status[st.value] = int(n or 0)

        by_risk: dict[str, int] = {}
        for rk in ("high", "medium", "low"):
            n = await session.scalar(
                select(func.count(ContractTask.id)).where(ContractTask.overall_risk == rk)
            )
            by_risk[rk] = int(n or 0)

        total = int(await session.scalar(select(func.count(ContractTask.id))) or 0)
        return {
            "total": total,
            "by_status": by_status,
            "by_status_label": {k: TASK_STATUS_LABEL[k] for k in by_status},
            "by_risk": by_risk,
            "blocked_count": by_status.get(TaskStatus.BLOCKED.value, 0),
        }


__all__ = ["TaskService", "STAGE_PROGRESS", "new_id"]
