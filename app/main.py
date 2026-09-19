"""FastAPI 应用入口。

启动::

    .venv\\Scripts\\python.exe -m uvicorn app.main:app --reload --port 8000

文档： http://127.0.0.1:8000/docs
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.router import api_router
from app.core.config import settings
from app.core.logging import logger, setup_logging


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    settings.ensure_dirs()
    logger.info(f"启动 {settings.app_name} v{settings.app_version}")
    logger.info(f"数据库：{settings.db_url.split('@')[-1]}")
    logger.info(
        f"LLM：{'已启用 ' + settings.llm_model if settings.llm_ready else '未启用（仅规则引擎）'}"
    )

    if settings.db_auto_create:
        from app.db.base import init_db

        try:
            await init_db()
            logger.info("数据表检查完成")
        except Exception as exc:
            logger.error(f"建表失败（接口仍可访问，请检查数据库）：{type(exc).__name__}: {exc}")

    # 把 JSON 里的规则与标准条款同步入库，保证规则可查可改
    try:
        from app.services.rule_service import RuleService

        await RuleService.sync_from_files()
    except Exception as exc:
        logger.warning(f"规则同步跳过：{type(exc).__name__}: {exc}")

    yield

    from app.db.base import dispose_engine

    await dispose_engine()
    logger.info("已关闭")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "企业合同审批智能审查系统后端。\n\n"
        "链路：文档接入 → 解析归一（DOCX/PDF/OCR）→ 规则+LLM 双引擎审查 → "
        "原文定位证据链 → 条款推荐 → 审批回写 → 报告导出。"
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)
app.include_router(api_router, prefix="/api/v1")


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "api": "/api/v1",
    }
