"""统一异常处理：把所有业务异常转成固定结构的 JSON 响应。"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.exceptions import AppError
from app.core.logging import logger


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        logger.warning(f"业务异常 {exc.code}: {exc.message} {exc.detail or ''}")
        body = exc.to_dict()
        body["ok"] = False
        return JSONResponse(status_code=exc.http_status, content=body)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "ok": False,
                "code": "VALIDATION_ERROR",
                "message": "请求参数校验失败",
                "detail": {"errors": exc.errors()},
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(f"未捕获异常 {request.method} {request.url.path}: {exc}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "ok": False,
                "code": "INTERNAL_ERROR",
                "message": f"{type(exc).__name__}: {exc}",
                "detail": {},
            },
        )


__all__ = ["register_exception_handlers"]
