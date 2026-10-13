"""认证与账号管理接口。

- ``/auth/login``          账号密码换令牌
- ``/auth/me``             拿当前登录身份（含权限点清单）
- ``/auth/logout``         登出（令牌无状态，服务端只留痕，清理由前端做）
- ``/auth/demo-accounts``  演示账号清单（公开，登录页一键填充用）
- ``/auth/roles``          角色权限矩阵（公开，登录页说明「不同角色能做什么」）
- ``/auth/users``          账号列表 / 新建 / 修改（需 ``user.manage``）

令牌是无状态的（HMAC 签名），所以「登出」本质是前端丢弃它。
这里仍然提供接口，是为了留一条可审计的痕迹，也给以后要做
令牌黑名单/单点登出留好位置。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import AuthDep, CurrentAuth, SessionDep, require_perm
from app.core.exceptions import AppError, UnauthorizedError
from app.core.logging import logger
from app.core.permissions import ROLES, ROLE_KEYS, role_perms
from app.core.security import create_token, password_strength_error
from app.schemas.auth import (
    DemoAccount,
    LoginIn,
    LoginOut,
    RoleInfo,
    UserCreateIn,
    UserOut,
    UserUpdateIn,
)
from app.schemas.task import OpResult
from app.services import user_service

router = APIRouter(prefix="/auth", tags=["认证与账号"])


# ======================================================================
# 登录 / 登出 / 当前身份
# ======================================================================
@router.post("/login", response_model=LoginOut, summary="账号密码登录")
async def login(payload: LoginIn, session: SessionDep) -> LoginOut:
    user = await user_service.authenticate(session, payload.username, payload.password)
    token, ttl = create_token(user_id=user.id, username=user.username, role=user.role)
    logger.info(f"登录成功：{user.username}（{ROLES.get(user.role, {}).get('name')}）")
    return LoginOut(
        token=token,
        expires_in=ttl,
        message=f"欢迎回来，{user.display_name or user.username}",
        user=UserOut(**user_service.user_to_dict(user)),
    )


@router.get("/me", response_model=UserOut, summary="当前登录身份")
async def me(ctx: CurrentAuth) -> UserOut:
    user = ctx.user
    if user is None:  # 只在 APP_AUTH_REQUIRED=false 且未带令牌时出现
        raise UnauthorizedError("尚未登录")
    return UserOut(**user_service.user_to_dict(user))


@router.post("/logout", response_model=OpResult, summary="登出")
async def logout(ctx: AuthDep) -> OpResult:
    if ctx.user is not None:
        logger.info(f"登出：{ctx.user.username}")
        return OpResult(message=f"已登出：{ctx.display_name}")
    return OpResult(message="已登出")


# ======================================================================
# 公开：给登录页用的元信息
# ======================================================================
@router.get("/demo-accounts", response_model=list[DemoAccount], summary="演示账号清单")
async def demo_accounts() -> list[DemoAccount]:
    rows = await user_service.demo_accounts()
    return [DemoAccount(**r) for r in rows]


@router.get("/roles", response_model=list[RoleInfo], summary="角色与权限矩阵")
async def roles() -> list[RoleInfo]:
    out: list[RoleInfo] = []
    for key in ROLE_KEYS:
        meta = ROLES[key]
        perms = role_perms(key)
        out.append(
            RoleInfo(
                key=key,
                name=meta["name"],
                duty=meta.get("duty", ""),
                scope=meta.get("scope", ""),
                perms=perms,
                perm_count=len(perms),
            )
        )
    return out


# ======================================================================
# 账号维护（管理员）
# ======================================================================
@router.get(
    "/users",
    response_model=list[UserOut],
    summary="账号列表（需 user.manage）",
    dependencies=[Depends(require_perm("user.manage"))],
)
async def list_users(session: SessionDep, role: str | None = None) -> list[UserOut]:
    rows = await user_service.list_users(session, role=role)
    return [UserOut(**user_service.user_to_dict(u)) for u in rows]


@router.post(
    "/users",
    response_model=UserOut,
    summary="新建账号（需 user.manage）",
    dependencies=[Depends(require_perm("user.manage"))],
)
async def create_user(payload: UserCreateIn, session: SessionDep) -> UserOut:
    err = password_strength_error(payload.password)
    if err:
        raise AppError(err)
    user = await user_service.create_user(
        session,
        username=payload.username,
        password=payload.password,
        display_name=payload.display_name,
        role=payload.role,
        dept=payload.dept,
        position=payload.position,
        email=payload.email,
        phone=payload.phone,
        remark=payload.remark,
    )
    logger.info(f"新建账号：{user.username}（{user.role}）")
    return UserOut(**user_service.user_to_dict(user))


@router.patch(
    "/users/{user_id}",
    response_model=UserOut,
    summary="修改账号 / 重置密码 / 启停（需 user.manage）",
    dependencies=[Depends(require_perm("user.manage"))],
)
async def update_user(user_id: str, payload: UserUpdateIn, session: SessionDep) -> UserOut:
    if payload.password:
        err = password_strength_error(payload.password)
        if err:
            raise AppError(err)
    user = await user_service.update_user(
        session,
        user_id,
        password=payload.password,
        display_name=payload.display_name,
        role=payload.role,
        dept=payload.dept,
        position=payload.position,
        email=payload.email,
        phone=payload.phone,
        remark=payload.remark,
        enabled=payload.enabled,
    )
    logger.info(f"更新账号：{user.username}")
    return UserOut(**user_service.user_to_dict(user))


__all__ = ["router"]
