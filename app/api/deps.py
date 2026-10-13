"""FastAPI 依赖注入：数据库会话 + 登录态 + 权限校验。

权限策略（需求 2.4.2 的服务端落地）：

- ``get_current_user`` 解令牌拿身份，**不查权限**；
- ``require_perm("xxx")`` 是依赖工厂，在需要权限的接口上挂，
  校验不过抛 403（前端 `permissions.js` 的同名权限点，两边靠
  回归测试钉住一致性）。

``APP_AUTH_REQUIRED=false`` 时整体放行 —— 这是给离线脚本与排障留的后门，
默认是开的，别把它当成「权限已经做了」的替代品。
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.permissions import ROLES, has_perm, perm_label
from app.core.security import decode_token
from app.db.base import get_session
from app.models.tables import User

#: 我们签发的令牌长相：``base64url(base64url 字符集，无填充) + "." + base64url``。
#: 用来把网关注入的 ``Authorization``（没有点）跟真令牌区分开，见 ``_extract_token``。
_TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_session_dep() -> AsyncIterator[AsyncSession]:
    async for s in get_session():
        yield s


# ======================================================================
# 登录态
# ======================================================================
@dataclass
class AuthContext:
    """当前请求的身份上下文。

    ``user`` 为 None 有两种情形：① 未带令牌（匿名）② 鉴权被显式关掉。
    用 :meth:`has` 判断权限，不要直接看 ``user`` —— 关掉鉴权时它也是 None，
    但那时所有权限都该放行。
    """

    user: User | None = None
    relaxed: bool = False          # True = APP_AUTH_REQUIRED=false，跳过校验

    @property
    def role(self) -> str:
        return self.user.role if self.user else ""

    @property
    def username(self) -> str:
        return self.user.username if self.user else "匿名"

    @property
    def display_name(self) -> str:
        return (self.user.display_name or self.user.username) if self.user else "匿名"

    @property
    def role_name(self) -> str:
        return ROLES.get(self.role, {}).get("name", self.role or "未登录")

    @property
    def actor(self) -> str:
        """写日志 / 批注作者用的「角色·姓名」，与前端文案保持一致。"""
        if not self.user:
            return "系统"
        return f"{self.role_name}·{self.display_name}"

    def has(self, perm: str) -> bool:
        if self.relaxed:
            return True
        if not self.user:
            return False
        return has_perm(self.user.role, perm)


def _extract_token(request: Request, authorization: str | None) -> str:
    """令牌来源，按**可信度**排序：

    1. ``X-Auth-Token: xxx``（前端 axios 统一带；网关**不会**改写这个头）
    2. ``?token=xxx``（**原件预览专用**：``<img src>`` / ``<iframe src>``
       由浏览器直接发请求，加不了自定义头，只能把令牌放查询串里）
    3. ``Authorization: Bearer xxx``（**放最后**，见下）

    ⚠️ **两个坑，绝不能再踩回去**（2026-09-19 线上踩过）：

    **坑一：不能见 ``Authorization`` 就认。** 发布到云平台后，反向代理会**自己注入**
    ``Authorization: Bearer <平台内部凭据>``（边缘→沙箱的凭据，哪怕客户端一个头都没带
    它也在）。旧逻辑直接拿去验签 → 报「登录状态无效」。

    **坑二：光按「长相」过滤还不够，顺序也得对。** 第一版修法是「把所有来源按
    Authorization → X-Auth-Token → ?token 收集起来，挑第一个长得像的」。问题在于
    ``Authorization`` 是**唯一会被网关覆盖**的来源，却排在队首 ——
    只要代理塞进来的值恰好也是「单点」形状，它就会抢先命中，把客户端自己带的
    ``X-Auth-Token`` 挤掉，照样 401。所以顺序必须按可信度来，
    **代理能改写的那个永远排最后**。

    我们的令牌固定是 ``base64url.base64url``（恰好一个点、两段都非空），用它分辨
    「像不像我们的」；一个都不像时退回第一个非空候选，让 ``decode_token`` 给出正常的
    401 语义（而不是「请先登录」），排障时不至于误导。
    """
    candidates: list[str] = []
    # 网关不动这两个来源 → 优先信它们
    candidates.append((request.headers.get("x-auth-token") or "").strip())
    candidates.append((request.query_params.get("token") or "").strip())
    # 可能被网关注入 → 压到最后
    if authorization:
        parts = authorization.split(None, 1)
        candidates.append(
            parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else authorization.strip()
        )

    for cand in candidates:
        if _TOKEN_SHAPE.match(cand):
            return cand
    return next((c for c in candidates if c), "")


async def get_auth_context(
    request: Request,
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthContext:
    """解令牌 → 查用户 → 组装上下文。**不做权限判断。**"""
    token = _extract_token(request, authorization)

    if not token:
        if settings.auth_required:
            # 匿名请求：交给 require_perm 决定是 401 还是 403，
            # 但需要登录态的接口会在这里就挡掉
            return AuthContext(user=None)
        return AuthContext(user=None, relaxed=True)

    payload = decode_token(token)          # 无效/过期直接抛 401
    user = await session.get(User, payload.get("sub", ""))
    if user is None:
        raise UnauthorizedError("账号不存在或已被删除，请重新登录")
    if not user.enabled:
        raise ForbiddenError("该账号已停用，请联系系统管理员")

    return AuthContext(user=user, relaxed=not settings.auth_required)


AuthDep = Annotated[AuthContext, Depends(get_auth_context)]


async def require_login(ctx: AuthDep) -> AuthContext:
    """必须已登录（不看具体权限）。"""
    if ctx.relaxed or ctx.user is not None:
        return ctx
    raise UnauthorizedError("请先登录")


CurrentAuth = Annotated[AuthContext, Depends(require_login)]


def require_perm(*perms: str):
    """依赖工厂：要求当前身份持有 ``perms`` 中**全部**权限点。

    用法::

        @router.patch("/rules/{rule_id}")
        async def patch_rule(..., _: AuthDep = Depends(require_perm("rule.edit"))):
            ...

    多个权限点传多个参数表示「都要有」。只要有一个不满足就 403，
    并在 message 里点明缺哪个 —— 排查时比「无权限」三个字有用得多。
    """

    async def _dep(ctx: AuthDep) -> AuthContext:
        if ctx.relaxed:
            return ctx
        if ctx.user is None:
            raise UnauthorizedError("请先登录")
        missing = [p for p in perms if not ctx.has(p)]
        if missing:
            need = "、".join(f"{perm_label(p)}（{p}）" for p in missing)
            raise ForbiddenError(f"当前身份「{ctx.role_name}」缺少权限：{need}")
        return ctx

    return _dep


def require_any_perm(*perms: str):
    """依赖工厂：持有 ``perms`` 中**任一**即可。"""

    async def _dep(ctx: AuthDep) -> AuthContext:
        if ctx.relaxed:
            return ctx
        if ctx.user is None:
            raise UnauthorizedError("请先登录")
        if not any(ctx.has(p) for p in perms):
            need = " 或 ".join(f"{perm_label(p)}（{p}）" for p in perms)
            raise ForbiddenError(f"当前身份「{ctx.role_name}」需要以下权限之一：{need}")
        return ctx

    return _dep


__all__ = [
    "SessionDep",
    "get_session_dep",
    "AuthContext",
    "AuthDep",
    "CurrentAuth",
    "get_auth_context",
    "require_login",
    "require_perm",
    "require_any_perm",
]
