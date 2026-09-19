"""``__init__.py`` 批量生成脚本。

一次性跑完即可，之后新增子包再单独补。
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]

PACKAGES = [
    "app",
    "app.core",
    "app.db",
    "app.models",
    "app.schemas",
    "app.engines",
    "app.engines.parsing",
    "app.engines.review",
    "app.engines.recommend",
    "app.llm",
    "app.services",
    "app.integrations",
    "app.api",
    "app.api.v1",
    "tests",
]

TITLES = {
    "app": "合同审批智能审查系统 —— 应用包。",
    "app.core": "核心基础设施：配置、日志、异常。",
    "app.db": "数据层：引擎、会话、建表。",
    "app.models": "ORM 模型与全局枚举（状态机）。",
    "app.schemas": "Pydantic 数据结构：IR、元数据、条款、风险、任务。",
    "app.engines": "引擎层：文档解析、智能审查、条款推荐。",
    "app.engines.parsing": "文档解析引擎：DOCX / PDF / OCR 归一为 IR。",
    "app.engines.review": "智能审查引擎：规则匹配 + LLM 语义研判。",
    "app.engines.recommend": "条款推荐引擎：标准条款库检索 + LLM 改写。",
    "app.llm": "大模型接入层（OpenAI 兼容协议）与 prompt 模板。",
    "app.services": "业务服务层：任务状态机、流水线编排、回写、报告。",
    "app.integrations": "外部系统集成：审批系统（mock）。",
    "app.api": "HTTP 接口层。",
    "app.api.v1": "v1 版本接口路由。",
    "tests": "测试。",
}


def main() -> int:
    created = 0
    for pkg in PACKAGES:
        d = BASE / pkg.replace(".", "/")
        d.mkdir(parents=True, exist_ok=True)
        init = d / "__init__.py"
        if not init.exists() or not init.read_text(encoding="utf-8").strip():
            init.write_text(f'"""{TITLES.get(pkg, pkg)}"""\n', encoding="utf-8")
            created += 1
            print(f"  + {init.relative_to(BASE)}")
    print(f"完成，新建 {created} 个 __init__.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
