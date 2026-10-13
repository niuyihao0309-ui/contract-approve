"""FastAPI 应用入口。

启动::

    .venv\\Scripts\\python.exe -m uvicorn app.main:app --reload --port 8000

文档： http://127.0.0.1:8000/docs
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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

    # 演示账号：只补不存在的，绝不覆盖已有密码（幂等）
    if settings.auth_seed_users:
        try:
            from app.db.base import session_scope
            from app.services.user_service import SEED_USERS, ensure_seed_users

            async with session_scope() as session:
                created = await ensure_seed_users(session)
            if created:
                logger.info(f"已创建 {created} 个演示账号（共 {len(SEED_USERS)} 个）")
            else:
                logger.info(f"演示账号已就绪（{len(SEED_USERS)} 个）")
            if settings.auth_secret.startswith("dev-only"):
                logger.warning(
                    "正在使用开发期默认令牌密钥，上线前请在 .env 设置 APP_AUTH_SECRET"
                )
        except Exception as exc:
            logger.warning(f"演示账号写入跳过：{type(exc).__name__}: {exc}")

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
async def root():
    """根路径：托管前端时给页面，否则给一份接口索引（保持原有行为）。"""
    if settings.frontend_ready:
        return FileResponse(str(settings.frontend_dist / "index.html"))
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "api": "/api/v1",
    }


# ======================================================================
# 可选：单端口托管前端构建产物（部署用；本地开发默认关闭，见 APP_SERVE_FRONTEND）
# ======================================================================
# 为什么需要它：本地是「vite 5173 + 后端 8000」两个端口，靠 vite 的 /api 代理打通；
# 而云平台/容器只给**一个**对外端口，所以要让后端把 dist 一起托管，
# 前端才能用相对路径 `/api/v1` 同源访问接口（`src/api/index.js` 里就是这么写的）。
if settings.frontend_ready:
    _dist = settings.frontend_dist
    if (_dist / "assets").is_dir():
        # 带内容 hash 的静态资源，可以放心长缓存
        app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        """SPA 兜底：把前端路由（/upload、/tasks/:id、/login …）都交给 index.html。

        ⚠️ 这里刻意**不用** ``app.mount("/", StaticFiles(html=True))`` ——
        那样没匹配上的 ``/api/...`` 也会被静态中间件截走，接口报错会变成 404 的 HTML。
        改用一个**最后注册**的 catch-all 路由：FastAPI 按注册顺序匹配，
        先注册的 ``/api/v1/*``、``/docs``、``/openapi.json`` 优先命中，剩下的才落到这里。
        """
        # 接口前缀下的未知路径仍然是 404，别被兜底吞成「200 + HTML」
        if full_path.startswith(("api/", "docs", "redoc", "openapi.json")):
            raise HTTPException(status_code=404, detail="Not Found")

        candidate = (_dist / full_path).resolve()
        # 防目录穿越：只放行 dist 内部真实存在的文件
        if full_path and candidate.is_file() and str(candidate).startswith(str(_dist.resolve())):
            return FileResponse(str(candidate))
        return FileResponse(str(_dist / "index.html"))

    logger.info(f"前端托管已开启：{_dist}")
else:
    if settings.serve_frontend:
        logger.warning(
            f"APP_SERVE_FRONTEND 已打开，但找不到 {settings.frontend_dist / 'index.html'}，"
            "前端托管跳过（先在 frontend/ 里跑 npm run build）"
        )
