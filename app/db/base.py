"""数据库引擎、会话与 Base。

异步 SQLAlchemy 2.0 风格。默认连 MySQL 8（asyncmy 驱动），
把 ``APP_DB_URL`` 换成 ``sqlite+aiosqlite:///...`` 即可无缝切到 SQLite ——
因为所有 ORM 字段都只用方言无关类型（JSON / Text / Numeric / DateTime）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import MetaData, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import settings

# 统一命名约定，让 Alembic 自动生成的迁移可预测
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def to_dict(self, exclude: set[str] | None = None) -> dict[str, Any]:
        """转 dict，便于直接塞进 Pydantic 或日志。"""
        skip = exclude or set()
        out: dict[str, Any] = {}
        for col in self.__table__.columns:
            if col.name in skip:
                continue
            val = getattr(self, col.name)
            out[col.name] = val.value if hasattr(val, "value") else val
        return out


# SQLite 用 NullPool：aiosqlite 在多事件循环下复用连接会炸；
# MySQL 走默认的连接池。
_is_sqlite = settings.db_url.startswith("sqlite")

def _engine_kwargs() -> dict:
    """按方言组装引擎参数。

    SQLite 用 NullPool：aiosqlite 在多事件循环下复用连接会炸，
    而 NullPool 不接受 pool_size / max_overflow，所以要分开传。
    """
    kw: dict = {
        "echo": settings.db_echo,
        "future": True,
        "connect_args": {"check_same_thread": False} if _is_sqlite else {
            "connect_timeout": settings.db_connect_timeout,
            "charset": "utf8mb4",
        },
    }
    if _is_sqlite:
        kw["poolclass"] = NullPool
    else:
        kw.update(
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_size=5,
            max_overflow=10,
        )
    return kw


engine: AsyncEngine = create_async_engine(settings.db_url, **_engine_kwargs())

# expire_on_commit=False：commit 后对象仍可读，避免 API 层再查一次
SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


if _is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragma(dbapi_conn, _rec):  # pragma: no cover - 仅 SQLite 分支
        """SQLite 默认不校验外键，这里打开。"""
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖注入用的会话生成器。"""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """脚本 / 服务内部用的会话上下文，自动提交或回滚。"""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db(*, drop: bool = False) -> None:
    """建表。

    开发期直接 create_all；生产建议用 Alembic 迁移（见 scripts/init_db.py --alembic 说明）。
    """
    # 必须先 import 所有模型，否则 Base.metadata 里没有表定义
    from app.models import tables  # noqa: F401

    # SQLite 需要父目录先存在，否则报 "unable to open database file"
    settings.ensure_dirs()

    async with engine.begin() as conn:
        if drop:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    await engine.dispose()


__all__ = [
    "Base",
    "engine",
    "SessionLocal",
    "get_session",
    "session_scope",
    "init_db",
    "dispose_engine",
]
