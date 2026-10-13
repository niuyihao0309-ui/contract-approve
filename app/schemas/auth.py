"""登录与账号管理的出入参结构。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserOut(BaseModel):
    """账号信息（**永远不含密码**）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: str = ""
    role: str
    role_name: str = ""
    dept: str = ""
    position: str = ""
    email: str | None = None
    phone: str | None = None
    remark: str | None = None
    enabled: bool = True
    is_demo: bool = False
    scope: str = ""
    duty: str = ""
    last_login_at: datetime | None = None
    login_count: int = 0
    perms: list[str] = Field(default_factory=list)


class LoginIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class LoginOut(BaseModel):
    ok: bool = True
    token: str
    token_type: str = "bearer"
    expires_in: int = 0
    message: str = ""
    user: UserOut


class DemoAccount(BaseModel):
    """登录页的演示账号（含明文密码，仅用于演示便捷填充）。"""

    username: str
    password: str
    display_name: str
    role: str
    role_name: str
    dept: str = ""
    position: str = ""
    remark: str = ""
    perm_count: int = 0


class UserCreateIn(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    display_name: str = ""
    role: str = "operator"
    dept: str = ""
    position: str = ""
    email: str | None = None
    phone: str | None = None
    remark: str | None = None


class UserUpdateIn(BaseModel):
    """改账号。字段留空表示不改。"""

    password: str | None = Field(default=None, min_length=6, max_length=128)
    display_name: str | None = None
    role: str | None = None
    dept: str | None = None
    position: str | None = None
    email: str | None = None
    phone: str | None = None
    remark: str | None = None
    enabled: bool | None = None


class RoleInfo(BaseModel):
    key: str
    name: str
    duty: str = ""
    scope: str = ""
    perms: list[str] = Field(default_factory=list)
    perm_count: int = 0


__all__ = [
    "UserOut",
    "LoginIn",
    "LoginOut",
    "DemoAccount",
    "UserCreateIn",
    "UserUpdateIn",
    "RoleInfo",
]
