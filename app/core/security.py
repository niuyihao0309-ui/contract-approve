"""密码哈希与访问令牌。

设计取向：**零第三方依赖**。项目栈里没有 passlib / python-jose，
而这两个能力用标准库 ``hashlib`` + ``hmac`` + ``base64`` 就能做扎实，
不必为「登录」这一个功能再拖一个加密库进来（也避免装包踩沙箱网络限制）。

- 密码：PBKDF2-HMAC-SHA256，每个密码独立随机盐，存成
  ``pbkdf2_sha256$迭代次数$盐hex$摘要hex`` —— 自带算法与迭代次数，
  以后调参不影响老密码校验。
- 令牌：``base64url(payload_json).base64url(hmac_sha256)`` 的自签名结构，
  语义等价于 JWT 的 HS256，但没有 JWT 那堆可选字段与算法协商，
  校验路径只有一条，不会出 ``alg=none`` 这类经典洞。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from app.core.config import settings
from app.core.exceptions import TokenExpiredError, UnauthorizedError

# ======================================================================
# 密码
# ======================================================================
_ALGO = "pbkdf2_sha256"
_ITERATIONS = 120_000


def hash_password(password: str, *, iterations: int = _ITERATIONS) -> str:
    """生成密码摘要。同一密码每次结果都不同（盐随机），这是设计如此。"""
    if not password:
        raise ValueError("密码不能为空")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return f"{_ALGO}${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码。

    任何格式异常都返回 False 而不是抛错 —— 校验函数的失败方式不该泄漏
    「这个账号的哈希格式坏了」还是「密码不对」。
    """
    if not password or not stored:
        return False
    try:
        algo, iterations, salt, want = stored.split("$")
        if algo != _ALGO:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    # 定长比较，避免时序侧信道
    return hmac.compare_digest(digest.hex(), want)


def password_strength_error(password: str) -> str | None:
    """返回不合格原因；合格返回 None。给「建账号」接口用。"""
    if len(password) < 6:
        return "密码至少 6 位"
    if password.isdigit() or password.isalpha():
        return "密码需同时包含字母与数字"
    return None


# ======================================================================
# 令牌（自签名，HS256 语义）
# ======================================================================
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload_b64: str, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256)
    return _b64e(mac.digest())


def create_token(
    *,
    user_id: str,
    username: str,
    role: str,
    ttl_minutes: int | None = None,
    secret: str | None = None,
) -> tuple[str, int]:
    """签发令牌，返回 ``(token, 有效期秒数)``。"""
    ttl = (ttl_minutes or settings.auth_token_ttl_minutes) * 60
    now = int(time.time())
    payload = {
        "sub": user_id,
        "usr": username,
        "role": role,
        "iat": now,
        "exp": now + ttl,
    }
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signature = _sign(payload_b64, secret or settings.auth_secret)
    return f"{payload_b64}.{signature}", ttl


def decode_token(token: str, *, secret: str | None = None) -> dict:
    """校验并解出载荷。失败一律抛 401（不区分「格式错」与「签名错」）。"""
    if not token or token.count(".") != 1:
        raise UnauthorizedError("登录状态无效，请重新登录")

    payload_b64, signature = token.split(".")
    expected = _sign(payload_b64, secret or settings.auth_secret)
    if not hmac.compare_digest(signature, expected):
        raise UnauthorizedError("登录状态已失效，请重新登录")

    try:
        payload = json.loads(_b64d(payload_b64).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise UnauthorizedError("登录状态无效，请重新登录") from exc

    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        raise TokenExpiredError("登录已过期，请重新登录")
    return payload


__all__ = [
    "hash_password",
    "verify_password",
    "password_strength_error",
    "create_token",
    "decode_token",
]
