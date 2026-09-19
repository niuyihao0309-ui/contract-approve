"""日志配置：loguru 接管，同时拦截标准库 logging 与 uvicorn 的日志。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from loguru import logger

from app.core.config import settings

_configured = False


class _InterceptHandler(logging.Handler):
    """把标准库 logging 的记录转发给 loguru。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging() -> None:
    """初始化日志。幂等，多次调用只生效一次。"""
    global _configured
    if _configured:
        return
    _configured = True

    log_dir: Path = settings.storage_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()
    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )
    logger.add(sys.stderr, level="DEBUG" if settings.debug else "INFO", format=fmt, colorize=True)
    logger.add(
        log_dir / "app_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="14 days",
        encoding="utf-8",
        level="DEBUG",
        format=fmt,
        enqueue=True,
        backtrace=settings.debug,
    )

    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine"):
        lg = logging.getLogger(name)
        lg.handlers = [_InterceptHandler()]
        lg.propagate = False


__all__ = ["setup_logging", "logger"]
