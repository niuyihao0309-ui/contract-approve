"""权限矩阵一致性：前端 `permissions.js` ↔ 后端 `app/core/permissions.py`。

为什么需要这个测试：权限点写了两份（前端管呈现层收敛、后端管接口边界），
两份漂移的后果不对称 —— 前端多了会「按钮点得动、接口 403」，后端多了会
「按钮点不动、接口其实能调」，两种都很难靠肉眼发现，因为它们分别只在
「真点一次」和「真调一次」时才暴露。

这里直接解析 JS 源文件比对，任何一边加了权限点忘了另一边，测试立刻红。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core import permissions as backend

JS_PATH = Path(__file__).resolve().parents[1] / "frontend" / "src" / "permissions.js"

#: 形如 'task.view': { group: ..., label: ... }
_PERM_RE = re.compile(r"'([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)':\s*\{")
#: 形如 lawyer: [ 'a.b', 'c.d', ]
_ROLE_BLOCK_RE = re.compile(r"(\w+):\s*\[(.*?)\]", re.S)
_PERM_IN_LIST_RE = re.compile(r"'([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)'")


def _read() -> str:
    assert JS_PATH.exists(), f"找不到前端权限文件：{JS_PATH}"
    return JS_PATH.read_text(encoding="utf-8")


def _block(text: str, name: str) -> str:
    """截出 `export const <name> = { ... }` 的块内容。

    块内的对象都是单行写法（`'x.y': { group: ..., label: ... }`），
    所以匹配「行首的 `}`」就是块的结束。
    """
    m = re.search(rf"export const {name} = \{{(.*?)\n\}}", text, re.S)
    assert m, f"没能从 permissions.js 里解析出 {name}"
    return m.group(1)


def js_perms() -> list[str]:
    return _PERM_RE.findall(_block(_read(), "PERMS"))


def js_role_perms() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for role, body in _ROLE_BLOCK_RE.findall(_block(_read(), "ROLE_PERMISSIONS")):
        out[role] = _PERM_IN_LIST_RE.findall(body)
    return out


def test_perm_points_match() -> None:
    """权限点清单（键名）两边必须完全一致。"""
    front, back = set(js_perms()), set(backend.PERMS)
    only_front = sorted(front - back)
    only_back = sorted(back - front)
    assert not only_front and not only_back, (
        f"权限点不一致 —— 仅前端有：{only_front}；仅后端有：{only_back}。"
        "请同步修改 frontend/src/permissions.js 与 app/core/permissions.py"
    )
    assert len(js_perms()) == len(backend.PERMS), "权限点重复定义"


def test_role_permissions_match() -> None:
    """每个角色持有的权限点两边必须完全一致。"""
    front, back = js_role_perms(), backend.ROLE_PERMISSIONS
    assert set(front) == set(back), (
        f"角色集合不一致：前端 {sorted(front)} / 后端 {sorted(back)}"
    )
    for role in sorted(back):
        f, b = sorted(front[role]), sorted(back[role])
        assert f == b, (
            f"角色 {role} 的权限不一致：\n  仅前端有 {sorted(set(f) - set(b))}\n"
            f"  仅后端有 {sorted(set(b) - set(f))}"
        )


def test_perm_labels_present() -> None:
    """每个权限点都要有中文说明 —— 403 提示与权限矩阵页都要用它。"""
    missing = [k for k, v in backend.PERMS.items() if not v.get("label") or not v.get("group")]
    assert not missing, f"这些权限点缺 label/group：{missing}"


def test_unknown_role_gets_nothing() -> None:
    """未知角色不给任何权限（失败要往紧里收，不是往松里放）。"""
    assert backend.role_perms("") == []
    assert backend.role_perms("superuser") == []
    assert not backend.has_perm("nonexistent", "task.view")


@pytest.mark.parametrize("role", backend.ROLE_KEYS)
def test_role_perm_keys_are_known(role: str) -> None:
    """角色持有的权限点必须都在 PERMS 里有定义（防止拼错权限点名）。"""
    unknown = [p for p in backend.ROLE_PERMISSIONS[role] if p not in backend.PERMS]
    assert not unknown, f"角色 {role} 引用了未定义的权限点：{unknown}"
