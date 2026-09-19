"""审批回写服务。

两条硬性要求（大纲阶段五）
--------------------------
1. **幂等**：用 ``writeback_log`` 防重复写。幂等键 = ``task_id + 意见内容 sha256``，
   有唯一索引。同一份意见重复提交只会命中已有记录，不会往审批单里灌两条一样的评论。
2. **状态机独立**：回写状态（``not_written → writing → success/failed``）
   与任务状态机完全分离，由 :meth:`TaskService.set_writeback` 统一驱动。

评论内容变了（例如法务改了批注）会得到新的幂等键，所以「改完再写一次」是允许的，
而且两次都会留痕。
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, WritebackError
from app.core.logging import logger
from app.db.base import session_scope
from app.integrations.approval_mock import get_approval_mock
from app.models.enums import WritebackStatus
from app.models.tables import WritebackLog
from app.services.report_service import build_writeback_markdown
from app.services.task_query import TaskQueryService
from app.services.task_service import TaskService


@dataclass
class WritebackPayload:
    """一次回写的内容。把「发什么」和「凭什么判重」拆开。

    :attr markdown:     实际写进审批系统评论区的正文（含生成时间，给人看的）
    :attr digest_basis: 算幂等键用的正文（**不含**生成时间，保证可复现）
    :attr detail:       任务详情快照
    :attr lawyer_notes: 最终生效的法务批注（可能是从任务评论里自动拼的）
    """

    markdown: str
    digest_basis: str
    detail: dict = field(default_factory=dict)
    lawyer_notes: str = ""


def _idempotency_key(task_id: str, payload: str) -> str:
    return hashlib.sha256(f"{task_id}::{payload}".encode("utf-8")).hexdigest()[:48]


class WritebackService:
    """构建意见文本 → 调用审批系统评论接口 → 记录留痕。"""

    # ------------------------------------------------------------------
    @staticmethod
    async def build_payload(
        session: AsyncSession, task_id: str, *, lawyer_notes: str = ""
    ) -> WritebackPayload:
        """生成回写用的 Markdown 意见与任务详情快照。"""
        detail = await TaskQueryService.detail(session, task_id)
        risks = detail.get("risk_items", [])

        if not lawyer_notes:
            # 没显式传批注就用任务里的法务评论拼一份
            notes = [
                c["content"]
                for c in detail.get("comments", [])
                if c.get("kind") == "comment" and c.get("role") == "lawyer"
            ]
            lawyer_notes = "\n".join(f"- {n}" for n in notes[-5:])

        basis = build_writeback_markdown(
            detail, risks, lawyer_notes=lawyer_notes, with_timestamp=False
        )
        md = build_writeback_markdown(detail, risks, lawyer_notes=lawyer_notes)
        return WritebackPayload(
            markdown=md, digest_basis=basis, detail=detail, lawyer_notes=lawyer_notes
        )

    # ------------------------------------------------------------------
    @staticmethod
    async def submit(
        session: AsyncSession,
        task_id: str,
        *,
        lawyer_notes: str = "",
        ticket_id: str | None = None,
        by: str = "法务审查人",
        force: bool = False,
    ) -> dict:
        """执行回写。

        :param ticket_id: 目标审批单。不传则用任务上记录的 ``approval_ticket_id``。
        :param force:     跳过「已成功则不再写」的短路（仍然幂等，不会重复插入同内容）
        """
        task = await TaskService.get(session, task_id)

        # ---- 前置校验：要有审查结果才允许回写 ----
        if not task.overall_risk and task.status != "completed":
            raise WritebackError("任务尚未完成审查，无法回写审批意见")

        target_ticket = ticket_id or task.approval_ticket_id
        if not target_ticket:
            raise WritebackError(
                "任务没有关联审批单号，无法回写。请先指定 ticket_id 或由审批系统拉取建单"
            )

        payload = await WritebackService.build_payload(
            session, task_id, lawyer_notes=lawyer_notes
        )
        payload_md, detail = payload.markdown, payload.detail
        # 幂等键只吃 digest_basis（不含生成时间戳），否则同一份意见每次算出的哈希都不同
        content_hash = hashlib.sha256(payload.digest_basis.encode("utf-8")).hexdigest()
        key = _idempotency_key(task_id, payload.digest_basis)

        # ---- 幂等：同一份内容已经写成功过，直接返回 ----
        existing = (
            await session.execute(select(WritebackLog).where(WritebackLog.idempotency_key == key))
        ).scalar_one_or_none()
        if existing is not None and existing.status == WritebackStatus.SUCCESS.value and not force:
            logger.info(
                f"[回写] 命中幂等键，跳过重复回写（任务 {task_id[:8]}，"
                f"comment_id={existing.comment_id}）"
            )
            return {
                "duplicated": True,
                "task_id": task_id,
                "ticket_id": existing.ticket_id,
                "comment_id": existing.comment_id,
                "message": "该版本审查意见已成功回写，本次为重复提交，已幂等拦截",
                "payload_md": payload_md,
            }

        # ---- 建日志 → 进入 writing ----
        log = existing or WritebackLog(
            id=str(uuid.uuid4()),
            task_id=task_id,
            idempotency_key=key,
        )
        log.content_hash = content_hash
        log.payload_md = payload_md
        log.ticket_id = target_ticket
        log.status = WritebackStatus.WRITING.value
        log.attempt = (log.attempt or 0) + 1
        log.error = None
        if existing is None:
            session.add(log)
        await TaskService.set_writeback(session, task, WritebackStatus.WRITING)

        # 记一条协同动作，方便在评论区看到「谁在什么时候触发了回写」
        await TaskService.add_comment(
            session,
            task,
            content=f"触发审查意见回写（第 {log.attempt} 次尝试，目标审批单 {target_ticket}）",
            author=by,
            role="lawyer",
            kind="action",
        )
        await session.commit()

        # ---- 调审批系统评论接口 ----
        t0 = time.perf_counter()
        mock = get_approval_mock()
        try:
            comment = await mock.post_comment(target_ticket, payload_md, author="合同智能审查系统")
            elapsed = int((time.perf_counter() - t0) * 1000)
        except Exception as exc:
            elapsed = int((time.perf_counter() - t0) * 1000)
            msg = f"{type(exc).__name__}: {exc}"

            async with session_scope() as s2:
                _log = (
                    await s2.execute(
                        select(WritebackLog).where(WritebackLog.idempotency_key == key)
                    )
                ).scalar_one()
                _log.status = WritebackStatus.FAILED.value
                _log.error = msg[:2000]
                _log.finished_at = datetime.now(timezone.utc)
                _log.duration_ms = elapsed
                _task2 = await TaskService.get(s2, task_id)
                await TaskService.set_writeback(s2, _task2, WritebackStatus.FAILED, detail=msg)

            logger.error(f"[回写] 失败（{elapsed}ms）：{msg}")
            raise WritebackError(f"回写审批系统失败：{msg}", detail={"retryable": True}) from exc

        # ---- 成功 ----
        async with session_scope() as s2:
            _log = (
                await s2.execute(
                    select(WritebackLog).where(WritebackLog.idempotency_key == key)
                )
            ).scalar_one()
            _log.status = WritebackStatus.SUCCESS.value
            _log.comment_id = comment["comment_id"]
            _log.response_body = {
                "comment_id": comment["comment_id"],
                "ticket_id": comment["ticket_id"],
                "content_length": comment["content_length"],
            }
            _log.finished_at = datetime.now(timezone.utc)
            _log.duration_ms = elapsed
            _task2 = await TaskService.get(s2, task_id)
            await TaskService.set_writeback(s2, _task2, WritebackStatus.SUCCESS)
            await TaskService.add_comment(
                s2,
                _task2,
                content=f"审查意见已成功写回审批单 {target_ticket} 评论区（comment_id={comment['comment_id']}）",
                author="系统",
                role="system",
                kind="action",
            )

        logger.info(
            f"[回写] 成功：任务 {task_id[:8]} → {target_ticket}，"
            f"comment_id={comment['comment_id']}，耗时 {elapsed}ms"
        )
        return {
            "duplicated": False,
            "task_id": task_id,
            "ticket_id": target_ticket,
            "comment_id": comment["comment_id"],
            "status": WritebackStatus.SUCCESS.value,
            "duration_ms": elapsed,
            "payload_md": payload_md,
            "message": f"审查意见已写回审批单 {target_ticket}",
        }

    # ------------------------------------------------------------------
    @staticmethod
    async def retry(session: AsyncSession, task_id: str, *, by: str = "系统管理员") -> dict:
        """失败重试：沿用同一份意见重新调接口（幂等键不变）。"""
        task = await TaskService.get(session, task_id)
        if task.writeback_status not in (
            WritebackStatus.FAILED.value,
            WritebackStatus.NOT_WRITTEN.value,
        ):
            raise WritebackError(
                f"当前回写状态为 {task.writeback_status}，只有失败或未回写才需要重试"
            )
        return await WritebackService.submit(session, task_id, by=by, force=True)

    # ------------------------------------------------------------------
    @staticmethod
    async def history(session: AsyncSession, task_id: str) -> list[dict]:
        rows = list(
            (
                await session.execute(
                    select(WritebackLog)
                    .where(WritebackLog.task_id == task_id)
                    .order_by(WritebackLog.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": r.id,
                "status": r.status,
                "attempt": r.attempt,
                "ticket_id": r.ticket_id,
                "comment_id": r.comment_id,
                "idempotency_key": r.idempotency_key,
                "content_hash": r.content_hash[:12],
                "error": r.error,
                "duration_ms": r.duration_ms,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in rows
        ]


__all__ = ["WritebackService"]
