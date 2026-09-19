"""FastAPI 依赖注入。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_session_dep() -> AsyncIterator[AsyncSession]:
    async for s in get_session():
        yield s


__all__ = ["SessionDep", "get_session_dep"]
