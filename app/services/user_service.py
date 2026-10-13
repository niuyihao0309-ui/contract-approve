"""用户与登录：种子账号、认证、账号维护。

演示账号在这里定义（**唯一事实来源**），启动时幂等写入 DB。
「不同职位 + 不同权限」的示例就是这 5 个账号：3 个角色覆盖 3 套权限，
法务与经办人各有两个人，用来说明「同角色不同部门/职位的人，
能做的事一样但数据范围不一样」。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError, ForbiddenError, NotFoundError, UnauthorizedError
from app.core.permissions import ROLE_KEYS, ROLES, role_perms
from app.core.security import hash_password, verify_password
from app.models.tables import User

# ======================================================================
# 演示账号（登录页会把这些直接列出来，免去手输）
# ======================================================================
SEED_USERS: list[dict] = [
    {
        "username": "li.jing",
        "password": "lawyer123",
        "display_name": "李静",
        "role": "lawyer",
        "dept": "法务部",
        "position": "法务审查专员",
        "email": "li.jing@example.com",
        "remark": "复核拍板：采纳/调整建议、补充法务意见、触发回写",
    },
    {
        "username": "wang.fang",
        "password": "lawyer123",
        "display_name": "王芳",
        "role": "lawyer",
        "dept": "法务部",
        "position": "法务经理",
        "email": "wang.fang@example.com",
        "remark": "同角色不同职位：权限与李静一致，数据范围同为全部合同",
    },
    {
        "username": "zhang.wei",
        "password": "operator123",
        "display_name": "张伟",
        "role": "operator",
        "dept": "采购部",
        "position": "采购经办人",
        "email": "zhang.wei@example.com",
        "remark": "发起送审：提交合同、跟踪进度、下载报告；对风险点无决定权",
    },
    {
        "username": "liu.yang",
        "password": "operator123",
        "display_name": "刘洋",
        "role": "operator",
        "dept": "销售部",
        "position": "销售经办人",
        "email": "liu.yang@example.com",
        "remark": "同角色不同部门：能做的事同张伟，但数据范围限本部门送审",
    },
    {
        "username": "admin",
        "password": "admin123",
        "display_name": "系统管理员",
        "role": "admin",
        "dept": "信息中心",
        "position": "系统管理员",
        "email": "admin@example.com",
        "remark": "修管道：规则与模板维护、任务监控、阻塞重试、审批流对接",
    },
]


# ======================================================================
# 序列化
# ======================================================================
def user_to_dict(user: User, *, with_perms: bool = True) -> dict:
    """出参统一走这里 —— 别把 ``password_hash`` 漏出去。"""
    data = {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "role_name": ROLES.get(user.role, {}).get("name", user.role),
        "dept": user.dept,
        "position": user.position,
        "email": user.email,
        "phone": user.phone,
        "remark": user.remark,
        "enabled": user.enabled,
        "is_demo": user.is_demo,
        "scope": ROLES.get(user.role, {}).get("scope", ""),
        "duty": ROLES.get(user.role, {}).get("duty", ""),
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "login_count": user.login_count or 0,
    }
    if with_perms:
        data["perms"] = role_perms(user.role)
    return data


# ======================================================================
# 种子
# ======================================================================
async def ensure_seed_users(session: AsyncSession) -> int:
    """写入演示账号（幂等）。

    只补不存在的，**已存在的绝不覆盖密码** —— 否则用户改过的密码
    会在每次重启后被重置回文档里的值，那种 bug 很难被发现。
    返回新建条数。
    """
    existing = set(
        (await session.execute(select(User.username))).scalars().all()
    )
    created = 0
    for item in SEED_USERS:
        if item["username"] in existing:
            continue
        session.add(
            User(
                id=str(uuid.uuid4()),
                username=item["username"],
                password_hash=hash_password(item["password"]),
                display_name=item["display_name"],
                role=item["role"],
                dept=item["dept"],
                position=item["position"],
                email=item.get("email"),
                remark=item.get("remark"),
                enabled=True,
                is_demo=True,
            )
        )
        created += 1
    if created:
        await session.commit()
    return created


# ======================================================================
# 认证
# ======================================================================
async def authenticate(session: AsyncSession, username: str, password: str) -> User:
    """账号密码认证。失败一律抛 401（不区分账号不存在与密码错误）。"""
    username = (username or "").strip()
    if not username or not password:
        raise UnauthorizedError("请输入账号与密码")

    stmt = select(User).where(func.lower(User.username) == username.lower())
    user = (await session.execute(stmt)).scalars().first()

    if user is None or not verify_password(password, user.password_hash):
        # 账号不存在与密码错误给同一句话，避免被用来枚举账号
        raise UnauthorizedError("账号或密码错误")
    if not user.enabled:
        raise ForbiddenError("该账号已停用，请联系系统管理员")

    user.last_login_at = datetime.now(timezone.utc)
    user.login_count = (user.login_count or 0) + 1
    await session.commit()
    return user


async def get_user(session: AsyncSession, user_id: str) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(f"用户不存在：{user_id}")
    return user


# ======================================================================
# 账号维护（管理员）
# ======================================================================
async def list_users(session: AsyncSession, *, role: str | None = None) -> list[User]:
    stmt = select(User).order_by(User.role, User.username)
    if role:
        stmt = stmt.where(User.role == role)
    return list((await session.execute(stmt)).scalars().all())


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    display_name: str,
    role: str,
    dept: str = "",
    position: str = "",
    email: str | None = None,
    phone: str | None = None,
    remark: str | None = None,
) -> User:
    username = (username or "").strip()
    if not username:
        raise AppError("账号不能为空")
    if role not in ROLE_KEYS:
        raise AppError(f"角色必须是 {ROLE_KEYS} 之一")

    dup = (
        await session.execute(select(User).where(func.lower(User.username) == username.lower()))
    ).scalars().first()
    if dup is not None:
        raise AppError(f"账号已存在：{username}")

    user = User(
        id=str(uuid.uuid4()),
        username=username,
        password_hash=hash_password(password),
        display_name=display_name or username,
        role=role,
        dept=dept,
        position=position,
        email=email,
        phone=phone,
        remark=remark,
        enabled=True,
        is_demo=False,
    )
    session.add(user)
    await session.commit()
    return user


async def update_user(
    session: AsyncSession,
    user_id: str,
    *,
    password: str | None = None,
    display_name: str | None = None,
    role: str | None = None,
    dept: str | None = None,
    position: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    remark: str | None = None,
    enabled: bool | None = None,
) -> User:
    """改账号。只更新传进来的字段（None = 不动）。"""
    user = await get_user(session, user_id)
    if role is not None:
        if role not in ROLE_KEYS:
            raise AppError(f"角色必须是 {ROLE_KEYS} 之一")
        user.role = role
    if password:
        user.password_hash = hash_password(password)
    for field, value in (
        ("display_name", display_name),
        ("dept", dept),
        ("position", position),
        ("email", email),
        ("phone", phone),
        ("remark", remark),
    ):
        if value is not None:
            setattr(user, field, value)
    if enabled is not None:
        user.enabled = enabled
    await session.commit()
    return user


async def demo_accounts() -> list[dict]:
    """给登录页用的演示账号清单。

    这里**返回明文密码**是刻意的：它们是写在本文档里的演示常量，
    登录页要能一键填充才好演示。真实账号的密码永远不经过这个函数。
    """
    if not settings.auth_seed_users:
        return []
    out: list[dict] = []
    for u in SEED_USERS:
        out.append(
            {
                "username": u["username"],
                "password": u["password"],
                "display_name": u["display_name"],
                "role": u["role"],
                "role_name": ROLES.get(u["role"], {}).get("name", u["role"]),
                "dept": u["dept"],
                "position": u["position"],
                "remark": u.get("remark", ""),
                "perm_count": len(role_perms(u["role"])),
            }
        )
    return out


__all__ = [
    "SEED_USERS",
    "user_to_dict",
    "ensure_seed_users",
    "authenticate",
    "get_user",
    "list_users",
    "create_user",
    "update_user",
    "demo_accounts",
]
