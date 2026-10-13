"""上传批次服务 —— 把「一次提交的多份文件」在展示层归成一批。

设计取舍（改动前必读）
----------------------
**一个文件 = 一条任务**，不是一个任务挂多份文档。原因：

- IR 的 ``doc_id`` 就等于任务 id，``doc_block`` 上有 ``(doc_id, block_id)`` 唯一键，
  风险项的 ``char_start~char_end`` 也是**相对本份文档全文**的偏移量。
  一个任务塞多份文档会同时打断「定位」和「高亮叠层」两条主线，
  报告、回写、批注也都得跟着改 —— 属于伤筋动骨，且没有业务必要。
- 多文件的正确抽象是：**各自独立审查 + 批次分组展示**。
  批次只参与呈现（文件切换条、批次标记），不参与任何业务计算。

成员关系存在 ``contract_batch_item``（独立表）而不是给 ``contract_task`` 加列，
理由见该模型的 docstring：开发期靠 ``create_all`` 建表，加列在老库上不生效。
"""

from __future__ import annotations

import uuid
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.enums import (
    CONCLUSION_LABEL,
    RISK_LEVEL_LABEL,
    RISK_WEIGHT,
    TASK_STATUS_LABEL,
)
from app.models.tables import ContractBatchItem, ContractFile, ContractTask

#: 单次上传最多几份文件。前后端共用这个口径（前端只做提示，真正的边界在这里）
MAX_BATCH_FILES = 10


class BatchService:
    """批次的建、查与展示上下文。"""

    # ------------------------------------------------------------------
    # 建
    # ------------------------------------------------------------------
    @staticmethod
    async def create(session: AsyncSession, task_ids: list[str]) -> str:
        """按顺序把若干任务归成一批，返回批次号。

        单文件上传也会建批次（成员数 1）—— 这样「上传」永远只有一条代码路径，
        前端不必分叉。成员数 < 2 时 :meth:`context` 不返回批次，
        所以单文件在界面上看不出「批次」这回事。
        """
        if not task_ids:
            raise AppError("批次里至少要有一个任务")
        if len(task_ids) > MAX_BATCH_FILES:
            raise AppError(f"单次最多 {MAX_BATCH_FILES} 份文件，实得 {len(task_ids)}")

        batch_id = str(uuid.uuid4())
        for seq, tid in enumerate(task_ids, start=1):
            session.add(
                ContractBatchItem(
                    id=str(uuid.uuid4()), batch_id=batch_id, task_id=tid, seq=seq
                )
            )
        await session.flush()
        return batch_id

    # ------------------------------------------------------------------
    # 查
    # ------------------------------------------------------------------
    @staticmethod
    async def members(session: AsyncSession, batch_id: str) -> list[ContractBatchItem]:
        """批次成员，按批次内序号升序。"""
        stmt = (
            select(ContractBatchItem)
            .where(ContractBatchItem.batch_id == batch_id)
            .order_by(ContractBatchItem.seq)
        )
        return list((await session.execute(stmt)).scalars().all())

    @staticmethod
    async def batch_of(session: AsyncSession, task_id: str) -> str | None:
        """这张单属于哪个批次（不属于任何批次时返回 None）。"""
        row = (
            await session.execute(
                select(ContractBatchItem)
                .where(ContractBatchItem.task_id == task_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        return row.batch_id if row else None

    # ------------------------------------------------------------------
    # 展示上下文
    # ------------------------------------------------------------------
    @staticmethod
    async def context(session: AsyncSession, task_id: str) -> dict | None:
        """给工作台 / 上传页用的批次上下文。

        成员数 < 2 时返回 ``None``：单文件上传也建批次，但界面上不该出现「批次」字样。
        历史数据（注册本功能之前建的）自然也是 ``None``，前端不用做兼容分支。
        """
        batch_id = await BatchService.batch_of(session, task_id)
        if not batch_id:
            return None

        items = await BatchService.members(session, batch_id)
        ids = [it.task_id for it in items]
        if len(ids) < 2:
            return None

        tasks = {
            t.id: t
            for t in (
                await session.execute(
                    select(ContractTask).where(ContractTask.id.in_(ids))
                )
            )
            .scalars()
            .all()
        }
        # 一个任务一份主附件（解析只吃主附件），取第一条即可
        files: dict[str, ContractFile] = {}
        for f in (
            await session.execute(
                select(ContractFile)
                .where(ContractFile.task_id.in_(ids))
                .order_by(ContractFile.created_at)
            )
        ).scalars().all():
            files.setdefault(f.task_id, f)

        out: list[dict] = []
        for it in items:
            t = tasks.get(it.task_id)
            if t is None:  # 任务被删掉了（外键级联通常已经带走成员行，这里是兜底）
                continue
            f = files.get(t.id)
            counts = t.risk_counts or {}
            out.append(
                {
                    "task_id": t.id,
                    "seq": it.seq,
                    "title": t.title,
                    "filename": f.filename if f else t.title,
                    "file_kind": f.file_kind if f else None,
                    "file_size": f.file_size if f else None,
                    "status": t.status,
                    "status_label": TASK_STATUS_LABEL.get(t.status, t.status),
                    "progress": t.progress or 0,
                    "overall_risk": t.overall_risk,
                    "overall_risk_label": (
                        RISK_LEVEL_LABEL.get(t.overall_risk) if t.overall_risk else None
                    ),
                    "conclusion": t.conclusion,
                    "conclusion_label": (
                        CONCLUSION_LABEL.get(t.conclusion) if t.conclusion else None
                    ),
                    "risk_counts": counts,
                    "risk_total": sum(int(v or 0) for v in counts.values()),
                    "active": t.id == task_id,
                }
            )

        if len(out) < 2:
            return None

        risk_counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
        for o in out:
            for k in risk_counts:
                risk_counts[k] += int((o["risk_counts"] or {}).get(k) or 0)

        worst = max(
            (o["overall_risk"] for o in out if o["overall_risk"]),
            key=lambda lv: RISK_WEIGHT.get(lv, 0),
            default=None,
        )
        return {
            "batch_id": batch_id,
            "total": len(out),
            "index": next((o["seq"] for o in out if o["active"]), 1),
            "items": out,
            "summary": {
                "total": len(out),
                "completed": sum(1 for o in out if o["status"] == "completed"),
                "blocked": sum(1 for o in out if o["status"] == "blocked"),
                "running": sum(
                    1 for o in out if o["status"] in ("pending", "parsing", "reviewing")
                ),
                "risk_counts": risk_counts,
                "risk_total": sum(risk_counts.values()),
                "overall_risk": worst,
                "overall_risk_label": (
                    RISK_LEVEL_LABEL.get(worst) if worst else "无风险"
                ),
            },
        }

    # ------------------------------------------------------------------
    @staticmethod
    async def annotate(
        session: AsyncSession, task_ids: list[str]
    ) -> dict[str, dict]:
        """批量取「批次内序号 / 批次总数」，供大盘列表打批次标。

        一次查完整个列表的批次关系（两条 IN 查询），不要按行去查 ——
        列表最多 200 行，逐行查会变成 400 次往返。
        """
        if not task_ids:
            return {}
        rows = list(
            (
                await session.execute(
                    select(ContractBatchItem).where(
                        ContractBatchItem.task_id.in_(task_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return {}

        batch_ids = {r.batch_id for r in rows}
        totals = Counter(
            r.batch_id
            for r in (
                await session.execute(
                    select(ContractBatchItem).where(
                        ContractBatchItem.batch_id.in_(batch_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        return {
            r.task_id: {
                "batch_id": r.batch_id,
                "batch_seq": r.seq,
                "batch_total": totals[r.batch_id],
            }
            for r in rows
        }


__all__ = ["BatchService", "MAX_BATCH_FILES"]
