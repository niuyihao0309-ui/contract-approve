"""任务相关的 API 出入参结构。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    BLOCK_REASON_LABEL,
    CONCLUSION_LABEL,
    RISK_LEVEL_LABEL,
    TASK_STATUS_LABEL,
    WRITEBACK_STATUS_LABEL,
)


class TaskBrief(BaseModel):
    """大盘列表行。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    contract_no: str | None = None
    applicant: str | None = None
    department: str | None = None
    business_type: str | None = None
    counterparty: str | None = None
    amount: float | None = None
    currency: str | None = "CNY"

    status: str
    status_label: str = ""
    stage: str | None = None
    progress: int = 0
    block_reason: str | None = None
    block_reason_label: str | None = None
    retry_count: int = 0

    overall_risk: str | None = None
    overall_risk_label: str | None = None
    conclusion: str | None = None
    conclusion_label: str | None = None
    risk_counts: dict[str, int] | None = None

    writeback_status: str = "not_written"
    writeback_label: str = ""

    source: str | None = None
    approval_ticket_id: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None

    @classmethod
    def from_orm_task(cls, task: Any) -> "TaskBrief":
        return cls(
            id=task.id,
            title=task.title,
            contract_no=task.contract_no,
            applicant=task.applicant,
            department=task.department,
            business_type=task.business_type,
            counterparty=task.counterparty,
            amount=float(task.amount) if task.amount is not None else None,
            currency=task.currency,
            status=task.status,
            status_label=TASK_STATUS_LABEL.get(task.status, task.status),
            stage=task.stage,
            progress=task.progress or 0,
            block_reason=task.block_reason,
            block_reason_label=(
                BLOCK_REASON_LABEL.get(task.block_reason) if task.block_reason else None
            ),
            retry_count=task.retry_count or 0,
            overall_risk=task.overall_risk,
            overall_risk_label=(
                RISK_LEVEL_LABEL.get(task.overall_risk) if task.overall_risk else None
            ),
            conclusion=task.conclusion,
            conclusion_label=(
                CONCLUSION_LABEL.get(task.conclusion) if task.conclusion else None
            ),
            risk_counts=task.risk_counts,
            writeback_status=task.writeback_status,
            writeback_label=WRITEBACK_STATUS_LABEL.get(task.writeback_status, task.writeback_status),
            source=task.source,
            approval_ticket_id=task.approval_ticket_id,
            created_at=task.created_at,
            completed_at=task.completed_at,
            duration_ms=task.duration_ms,
        )


class TaskDetail(TaskBrief):
    """工作台详情。"""

    risk_summary: str | None = None
    focus_points: list[str] | None = None
    block_detail: str | None = None
    risk_items: list[dict] = Field(default_factory=list)
    files: list[dict] = Field(default_factory=list)
    metadata: list[dict] = Field(default_factory=list)
    clauses: list[dict] = Field(default_factory=list)
    comments: list[dict] = Field(default_factory=list)
    writeback: dict | None = None
    doc: dict | None = None


class TaskCreateIn(BaseModel):
    """手动创建 / 上传任务。"""

    title: str = Field(..., min_length=1, max_length=255)
    business_type: str | None = None
    applicant: str | None = None
    department: str | None = None
    counterparty: str | None = None
    contract_no: str | None = None
    amount: float | None = None
    currency: str = "CNY"
    # 直接给本地文件路径（服务端同机的场景），与上传二选一
    file_path: str | None = None
    # 建完立刻跑流水线
    run_now: bool = False


class TaskListOut(BaseModel):
    total: int
    items: list[TaskBrief]
    limit: int
    offset: int


class CommentIn(BaseModel):
    content: str = Field(..., min_length=1)
    author: str = "法务审查人"
    role: str = "lawyer"
    risk_item_id: str | None = None
    block_id: str | None = None
    char_start: int | None = None
    char_end: int | None = None


class DecisionIn(BaseModel):
    """法务对风险项 / 建议的采纳决定。"""

    decision: str = Field(..., description="accepted / adjusted / rejected / open")
    edited_text: str | None = Field(default=None, description="调整后的示范条款文本")
    by: str = "法务审查人"
    note: str | None = None


class RetryIn(BaseModel):
    by: str = "系统管理员"
    # 重新上传替换文件（可选）
    file_path: str | None = None


class OpResult(BaseModel):
    ok: bool = True
    message: str = ""
    data: dict | None = None


__all__ = [
    "TaskBrief",
    "TaskDetail",
    "TaskCreateIn",
    "TaskListOut",
    "CommentIn",
    "DecisionIn",
    "RetryIn",
    "OpResult",
]
