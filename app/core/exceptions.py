"""业务异常定义。

统一异常基类，便于 API 层集中转成标准错误响应。
"""

from __future__ import annotations


class AppError(Exception):
    """所有业务异常的基类。"""

    code: str = "APP_ERROR"
    http_status: int = 400

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class NotFoundError(AppError):
    code = "NOT_FOUND"
    http_status = 404


class FileParseError(AppError):
    """文档解析失败（可重试）。"""

    code = "FILE_PARSE_ERROR"
    http_status = 422


class DocumentBlockedError(AppError):
    """文档触及阻塞条件（加密 / 正文为空 / 严重模糊），应把任务置为 blocked。"""

    code = "DOCUMENT_BLOCKED"
    http_status = 422


class LLMError(AppError):
    code = "LLM_ERROR"
    http_status = 502


class StateTransitionError(AppError):
    """非法状态流转。"""

    code = "INVALID_STATE_TRANSITION"
    http_status = 409


class WritebackError(AppError):
    code = "WRITEBACK_ERROR"
    http_status = 502
