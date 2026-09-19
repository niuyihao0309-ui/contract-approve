"""Prompt 模板加载器。

沿用项目里 ``prompt_loader.py`` 的做法：prompt 与代码分离，放在 ``prompts/`` 下，
用 ``.prompt`` 后缀。好处是调 prompt 不用动代码、不用重启（带 mtime 缓存失效）。

文件首行可写 ``---`` 分隔的元信息（可选）::

    ---
    name: risk_review
    description: 合同风险语义研判
    ---
    你是一名资深企业法务……

正文里用 ``{变量名}`` 占位，用 :meth:`PromptTemplate.render` 填充。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.exceptions import AppError

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
PROMPT_SUFFIX = ".prompt"

_FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
#: 只允许 {identifier} 形式的占位符，避免 JSON 花括号被误判
_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class PromptTemplate:
    """一个 prompt 模板。"""

    __slots__ = ("name", "meta", "body", "path")

    def __init__(self, name: str, meta: dict[str, str], body: str, path: Path) -> None:
        self.name = name
        self.meta = meta
        self.body = body
        self.path = path

    @property
    def variables(self) -> list[str]:
        return sorted(set(_PLACEHOLDER.findall(self.body)))

    def render(self, **kwargs: Any) -> str:
        """填充占位符。缺失的变量保持原样（便于排查是哪一项没传）。"""
        def _sub(m: re.Match[str]) -> str:
            key = m.group(1)
            if key not in kwargs:
                return m.group(0)
            val = kwargs[key]
            return val if isinstance(val, str) else str(val)

        return _PLACEHOLDER.sub(_sub, self.body)

    def render_strict(self, **kwargs: Any) -> str:
        """严格模式：缺失变量直接报错，防止把 ``{contract_text}`` 发给大模型。"""
        missing = [v for v in self.variables if v not in kwargs]
        if missing:
            raise AppError(
                f"prompt「{self.name}」缺少变量：{missing}（需要 {self.variables}）"
            )
        return self.render(**kwargs)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PromptTemplate {self.name} vars={self.variables}>"


def _parse(text: str, name: str, path: Path) -> PromptTemplate:
    meta: dict[str, str] = {}
    body = text
    m = _FRONT_MATTER.match(text)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip()
        body = text[m.end():]
    return PromptTemplate(name=name, meta=meta, body=body.strip(), path=path)


@lru_cache(maxsize=64)
def _load(name: str, mtime: float) -> PromptTemplate:
    """mtime 参与缓存键，改文件后自动重新加载。"""
    path = PROMPT_DIR / f"{name}{PROMPT_SUFFIX}"
    if not path.exists():
        raise AppError(f"prompt 文件不存在：{path}")
    return _parse(path.read_text(encoding="utf-8"), name, path)


def load_prompt(name: str) -> PromptTemplate:
    """按名字加载 prompt（不含后缀）。"""
    path = PROMPT_DIR / f"{name}{PROMPT_SUFFIX}"
    if not path.exists():
        available = [p.stem for p in PROMPT_DIR.glob(f"*{PROMPT_SUFFIX}")]
        raise AppError(f"prompt 文件不存在：{path.name}，可用：{available}")
    return _load(name, path.stat().st_mtime)


def list_prompts() -> list[dict[str, str]]:
    if not PROMPT_DIR.exists():
        return []
    return [
        {
            "name": p.stem,
            "meta_name": (t := _parse(p.read_text(encoding="utf-8"), p.stem, p)).meta.get("name", ""),
            "variables": ",".join(t.variables),
        }
        for p in sorted(PROMPT_DIR.glob(f"*{PROMPT_SUFFIX}"))
    ]


__all__ = ["PromptTemplate", "load_prompt", "list_prompts", "PROMPT_DIR"]
