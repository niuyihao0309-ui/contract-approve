"""审查执行接口：跑流水线、局部重跑、查看摘要与引擎状态。"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query

from app.api.deps import SessionDep, require_perm
from app.core.config import settings
from app.core.logging import logger
from app.engines.review.checkers import CHECKERS
from app.llm.client import get_llm
from app.schemas.task import OpResult, TaskBrief
from app.services.pipeline import PipelineRunner
from app.services.task_service import TaskService

router = APIRouter(prefix="/review", tags=["审查引擎"])


@router.post(
    "/tasks/{task_id}/run",
    response_model=OpResult,
    summary="跑完整流水线",
    dependencies=[Depends(require_perm("task.monitor"))],
)
async def run(task_id: str, session: SessionDep) -> OpResult:
    summary = await PipelineRunner.run(task_id)
    return OpResult(ok=bool(summary.get("ok")), message="流水线执行完成", data=summary)


@router.post(
    "/tasks/{task_id}/reparse",
    response_model=OpResult,
    summary="强制重新解析",
    dependencies=[Depends(require_perm("task.retry"))],
)
async def reparse(task_id: str, session: SessionDep) -> OpResult:
    """忽略已有落库结果，从附件重新解析一遍（换了解析器或怀疑解析有误时用）。"""
    summary = await PipelineRunner.run(task_id, force_reparse=True)
    return OpResult(ok=bool(summary.get("ok")), message="已重新解析并审查", data=summary)


@router.get(
    "/tasks/{task_id}/summary",
    summary="审查摘要与结论",
    dependencies=[Depends(require_perm("task.view"))],
)
async def summary(task_id: str, session: SessionDep) -> dict:
    task = await TaskService.get(session, task_id)
    from app.models.enums import (
        CONCLUSION_LABEL,
        RISK_LEVEL_LABEL,
        TASK_STATUS_LABEL,
        WRITEBACK_STATUS_LABEL,
    )

    return {
        "task_id": task.id,
        "status": task.status,
        "status_label": TASK_STATUS_LABEL.get(task.status, task.status),
        "stage": task.stage,
        "progress": task.progress,
        "overall_risk": task.overall_risk,
        "overall_risk_label": (
            RISK_LEVEL_LABEL.get(task.overall_risk) if task.overall_risk else None
        ),
        "conclusion": task.conclusion,
        "conclusion_label": CONCLUSION_LABEL.get(task.conclusion or "", None),
        "risk_counts": task.risk_counts or {"high": 0, "medium": 0, "low": 0},
        "risk_summary": task.risk_summary,
        "focus_points": task.focus_points or [],
        "writeback_status": task.writeback_status,
        "writeback_label": WRITEBACK_STATUS_LABEL.get(task.writeback_status),
        "block_reason": task.block_reason,
        "block_detail": task.block_detail,
        "retry_count": task.retry_count,
        "duration_ms": task.duration_ms,
    }


@router.get("/health", summary="审查引擎自检", dependencies=[Depends(require_perm("engine.view"))])
async def engine_health() -> dict:
    """看一眼引擎装了哪些能力：规则条数、模板条数、LLM 与 OCR 是否可用。"""
    from app.services.rule_service import RuleService

    ruleset = RuleService.load_ruleset_from_file()
    library = RuleService.load_library_from_file()

    ocr_ok, ocr_reason = True, ""
    try:
        from app.engines.parsing.ocr_parser import _OcrEngineHolder

        _OcrEngineHolder.get()
    except Exception as exc:
        ocr_ok, ocr_reason = False, str(exc)[:120]

    return {
        "rules": {
            "total": len(ruleset.rules),
            "enabled": sum(1 for r in ruleset.rules if r.enabled),
            "version": ruleset.version,
            "checkers": sorted(CHECKERS),
        },
        "clause_templates": {"total": len(library.templates), "version": library.version},
        "llm": {
            "enabled": get_llm().enabled,
            "model": settings.llm_model if get_llm().enabled else None,
            "base_url": settings.llm_base_url if get_llm().enabled else None,
        },
        "ocr": {"available": ocr_ok, "reason": ocr_reason},
        "our_side": {"code": settings.our_side, "label": settings.our_side_label},
    }


@router.get("/llm/health", summary="LLM 连通性自检", dependencies=[Depends(require_perm("engine.view"))])
async def llm_health() -> dict:
    return await get_llm().health_check()


@router.post(
    "/llm/test",
    summary="用一段合同片段试跑语义研判",
    dependencies=[Depends(require_perm("engine.view"))],
)
async def llm_test(
    session: SessionDep,
    text: str = Body(..., embed=True, description="要研判的合同片段"),
    contract_type: str = Body(default="purchase", embed=True),
) -> dict:
    """不落库的 LLM 冒烟测试，用来确认 prompt 与模型配合正常。"""
    from app.engines.parsing.clause_segmenter import ClauseSegmenter
    from app.engines.review.checkers import ReviewContext
    from app.engines.review.llm_reviewer import LLMReviewer
    from app.schemas.document import DocIR
    from app.schemas.metadata import MetadataResult

    ir = DocIR.build(
        doc_id="llm-test",
        source_file="inline",
        raw_blocks=[{"type": "paragraph", "text": line, "page": 1, "para_index": i}
                    for i, line in enumerate(text.split("\n")) if line.strip()],
    )
    clauses = ClauseSegmenter().segment(ir)
    ctx = ReviewContext(
        ir=ir, clauses=clauses, metadata=MetadataResult(), contract_type=contract_type
    )
    logger.info(f"[LLM测试] 输入 {len(ir.full_text)} 字，切出 {len(clauses.clauses)} 条")
    risks = await LLMReviewer().review(
        ir, clauses=clauses, contract_type=contract_type, our_side=settings.our_side_label
    )
    return {
        "input_chars": len(ir.full_text),
        "clause_count": len(clauses.clauses),
        "risk_count": len(risks),
        "risks": [r.card() for r in risks],
    }


__all__ = ["router"]
