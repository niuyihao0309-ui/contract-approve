"""健康检查与运行时信息。"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import SessionDep
from app.core.config import settings
from app.models.enums import TASK_STATUS_LABEL, WRITEBACK_STATUS_LABEL

router = APIRouter(tags=["系统"])


@router.get("/health", summary="健康检查")
async def health() -> dict:
    return {
        "ok": True,
        "app": settings.app_name,
        "version": settings.app_version,
        "db": "configured",
        "llm_ready": settings.llm_ready,
        "llm_model": settings.llm_model if settings.llm_ready else None,
    }


@router.get("/health/db", summary="数据库连通性")
async def health_db(session: SessionDep) -> dict:
    try:
        await session.execute(text("SELECT 1"))
        dialect = session.bind.dialect.name if session.bind else "unknown"
        return {"ok": True, "dialect": dialect, "url_scheme": settings.db_url.split("://")[0]}
    except Exception as exc:  # pragma: no cover - 环境相关
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@router.get("/meta", summary="枚举字典（前端下拉/标签用）")
async def meta() -> dict:
    from app.models.enums import (
        BLOCK_REASON_LABEL,
        CLAUSE_TYPE_LABEL,
        CONTRACT_TYPE_LABEL,
        CONCLUSION_LABEL,
        METADATA_FIELD_LABEL,
        RISK_LEVEL_LABEL,
    )

    return {
        "task_status": TASK_STATUS_LABEL,
        "writeback_status": WRITEBACK_STATUS_LABEL,
        "risk_level": RISK_LEVEL_LABEL,
        "conclusion": CONCLUSION_LABEL,
        "clause_type": CLAUSE_TYPE_LABEL,
        "contract_type": CONTRACT_TYPE_LABEL,
        "metadata_field": METADATA_FIELD_LABEL,
        "block_reason": BLOCK_REASON_LABEL,
    }
