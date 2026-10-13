"""风险项接口：列表、详情、建议编辑、法务处理。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import AuthDep, SessionDep, require_perm
from app.core.exceptions import NotFoundError
from app.models.enums import (
    CLAUSE_TYPE_LABEL,
    ItemDecision,
    RISK_LEVEL_LABEL,
    RISK_LEVEL_LABEL as _RLL,
)
from app.schemas.task import DecisionIn, OpResult
from app.services.task_query import TaskQueryService
from app.services.task_service import TaskService

router = APIRouter(
    prefix="/tasks/{task_id}/risks",
    tags=["风险项"],
    dependencies=[Depends(require_perm("risk.view"))],
)


@router.get("", summary="风险清单（工作台卡片数据）")
async def list_risks(
    task_id: str,
    session: SessionDep,
    level: str | None = Query(default=None, description="high / medium / low"),
    clause_type: str | None = Query(default=None),
    source: str | None = Query(default=None, description="rule / llm"),
) -> dict:
    await TaskService.get(session, task_id)          # 校验任务存在
    items = await TaskQueryService._risks(session, task_id)
    if level:
        items = [r for r in items if r["risk_level"] == level]
    if clause_type:
        items = [r for r in items if r["clause_type"] == clause_type]
    if source:
        items = [r for r in items if r["source"] == source]

    counts = {"high": 0, "medium": 0, "low": 0}
    for r in await TaskQueryService._risks(session, task_id):
        counts[r["risk_level"]] = counts.get(r["risk_level"], 0) + 1

    return {
        "total": len(items),
        "counts": counts,
        "legend": RISK_LEVEL_LABEL,
        "clause_type_labels": CLAUSE_TYPE_LABEL,
        "items": items,
    }


@router.get("/{risk_id}", summary="单条风险详情")
async def get_risk(task_id: str, risk_id: str, session: SessionDep) -> dict:
    items = await TaskQueryService._risks(session, task_id)
    for r in items:
        if r["id"] == risk_id:
            return r
    raise NotFoundError(f"风险项不存在：{risk_id}")


@router.post(
    "/{risk_id}/decision",
    summary="采纳 / 调整 / 驳回",
    dependencies=[Depends(require_perm("risk.decide"))],
)
async def decide(
    task_id: str, risk_id: str, payload: DecisionIn, session: SessionDep, ctx: AuthDep
) -> OpResult:
    """法务对风险项的处理。``edited_text`` 非空时会覆盖该风险的示范条款。

    这个路由和 ``POST /tasks/{id}/risks/{risk_id}/decision`` 等价，
    保留两份是为了前端调用路径更自然（都归到 risks 命名空间下）。
    两处都挂 ``risk.decide`` —— 只有法务审查人有。
    """
    return await _decide(task_id, risk_id, payload, session, ctx=ctx)


# 与 tasks.py 的 decide_risk 同源逻辑
async def _decide(
    task_id: str, risk_id: str, payload: DecisionIn, session, *, ctx: AuthDep | None = None
) -> OpResult:
    from sqlalchemy import select

    from app.core.exceptions import AppError
    from app.models.tables import ClauseSuggestion, RiskItem

    actor = ctx.display_name if ctx and ctx.user else (payload.by or "法务审查人")
    role = ctx.role if ctx and ctx.user else "lawyer"

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

    stmt = select(ClauseSuggestion).where(ClauseSuggestion.risk_item_id == risk.id)
    for sug in (await session.execute(stmt)).scalars().all():
        sug.decision = payload.decision
        if payload.edited_text is not None:
            sug.edited_text = payload.edited_text
            sug.edited_by = actor

    action = {
        ItemDecision.ACCEPTED.value: "采纳",
        ItemDecision.REJECTED.value: "驳回",
        ItemDecision.ADJUSTED.value: "调整后采纳",
        ItemDecision.OPEN.value: "重置为待处理",
    }.get(payload.decision, payload.decision)

    await TaskService.add_comment(
        session,
        task,
        content=f"风险项「{risk.name}」处理结果：{action}"
        + (f"；法务说明：{payload.note}" if payload.note else "")
        + ("；已调整示范条款文本" if payload.edited_text else ""),
        author=actor,
        role=role,
        kind="decision",
        risk_item_id=risk.id,
        block_id=risk.block_id,
        char_start=risk.char_start,
        char_end=risk.char_end,
    )
    await session.commit()
    return OpResult(
        message=f"已更新为「{action}」",
        data={"decision": payload.decision, "risk_id": risk_id},
    )


__all__ = ["router"]
