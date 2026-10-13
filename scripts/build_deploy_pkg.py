"""把项目组装成一个**可发布的单端口自包含包**（`deploy-demo/`）。

为什么需要它
------------
本项目的正式跑法是「Docker MySQL（3307）+ 后端 8000 + vite 5173」三个进程，
而「发布为应用」的沙箱只对外给**一个** HTTP 端口，且不提供 MySQL / Redis 这类外部服务。
所以发布用的版本必须是：**SQLite 内嵌库 + 后端托管前端构建产物 + 只监听一个端口**。

这个脚本只做「复制 + 生成」两件事，**不改动原项目任何文件**：

    app/            ← 项目原样复制（去掉 __pycache__ / *.pyc）
    static/         ← frontend/dist 的构建产物（`npm run build` 的成果）
    samples/        ← 演示用的样例合同（前端「示例合同（快速体验）」要读它）
    storage/        ← 运行期目录（SQLite 库、上传件、报告都在这里）
    requirements.txt, .env, run.py, README.md   ← 本脚本生成

跑法::

    .venv\\Scripts\\python.exe scripts/build_deploy_pkg.py
    # 默认输出到 ..\\deploy-demo（即 D:\\ke_cheng\\XM\\deploy-demo）
    # 换位置：--out D:\\somewhere\\contract-demo

组装完可以本地先验一遍（强烈建议，别直接发）::

    cd ../deploy-demo
    ..\\XM-contract-approve\\.venv\\Scripts\\python.exe run.py      # 读 PORT/HOST
    # 或显式指定：PORT=8099 HOST=127.0.0.1 python run.py
"""

from __future__ import annotations

import argparse
import re
import secrets
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT.parent / "deploy-demo"

#: 复制时跳过的目录名（测试、缓存、版本库、虚拟环境、依赖）
SKIP_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".git",
    ".idea",
    ".venv",
    "venv",
    "node_modules",
    "tests",
    "docs",
}

#: 不装进发布包的依赖（MySQL 驱动在沙箱里没用，还拖慢安装）
SKIP_REQUIREMENTS = {"asyncmy"}

#: 演示版也要装 OCR（图片/扫描件必须走 OCR 才能解析）。原 pyproject 里这是
#: optional extra（``[project.optional-dependencies] ocr``），不在 dependencies 里，
#: 所以这里显式补一行。别再因为「体积大」剔掉——线上已实测沙箱装得动、跑得通。
OCR_EXTRA = "rapidocr-onnxruntime>=1.3.24"

#: 发布包里的 .env。**注意这里必须是 SQLite** —— 写 localhost 的 MySQL 会被发布预检拒掉，
#: 而且沙箱里根本没有数据库服务。
ENV_TEMPLATE = """\
# ==========================================================================
# 演示版（单端口 / SQLite）—— 由 scripts/build_deploy_pkg.py 生成，不要手改
#
# 与开发版的差别：
#   1) 数据库换 SQLite（沙箱不提供 MySQL / Redis 这类外部服务）
#   2) 后端直接托管前端构建产物 → 只需一个对外端口
#   3) 关闭 LLM（沙箱里没有 API key）→ 审查走**规则引擎**；
#      打开 OCR（图片/扫描件依赖已进 requirements，见 OCR_EXTRA）→ 图片可正常解析
# ==========================================================================

# ------------------------------- 数据库 -----------------------------------
APP_DB_URL=sqlite+aiosqlite:///./storage/contract_review.db
APP_DB_AUTO_CREATE=true
APP_DB_ECHO=false

# -------------------------------- 服务 ------------------------------------
# 监听地址与端口。云平台只注入裸 PORT，config.py 里已做别名兼容（APP_PORT > PORT）
APP_HOST=0.0.0.0
APP_DEBUG=false
# 由后端托管前端：一个端口同时提供页面与 /api
APP_SERVE_FRONTEND=true
APP_FRONTEND_DIST=static

# ------------------------ 大模型关闭 / OCR 开启 ------------------------
APP_LLM_ENABLED=false
APP_OCR_ENABLED=true

# ------------------------------- 认证 -------------------------------------
# 每次组装都换一把新密钥，别用开发期那个默认值
APP_AUTH_SECRET={auth_secret}
APP_AUTH_TOKEN_TTL_MINUTES=720
APP_AUTH_SEED_USERS=true

# --------------------------- 解析完整性阈值 -------------------------------
APP_MIN_BODY_CHARS=50
APP_GIBBERISH_RATIO=0.6
"""

# 注意：APP_DEMO_SEED **不写进 .env** —— .env 只被 pydantic-settings 用来喂它自己声明的字段，
# 不会导出到 os.environ，而 run.py 读的是 os.environ。
# 所以它必须作为**真正的环境变量**传：APP_DEMO_SEED=false python run.py

SEED_PY = '''\
"""演示数据：首次启动时灌一批「已审查完成」的样例任务。

为什么要它
----------
发布链接给出去，别人第一眼看到的是审查大盘。空表格等于「这系统好像没东西」，
所以这里预先跑几份样例合同，让大盘、风险清单、原文高亮、批次切换一进去就是满的。

两条设计约束
------------
1. **走公开 HTTP 接口**（跟访客点按钮是同一条路径），不直接调内部 Service ——
   避免为了灌数据去复刻一遍建单 + 跑流水线的逻辑（那正是最容易腐化的地方）。
2. **在后台线程里跑，绝不阻塞启动**。平台要尽快看到端口在监听；
   而解析 + 审查要跑好几秒，放在启动前会把 readiness 拖爆。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: 预置的演示单：文件名 → (标题, 业务类型)。只挑**文本型**样例 ——
#: 作为门面用文本型最稳；OCR 已启用，扫描件也能解析，但灌数据选文本型更快。
SEED_FILES = [
    ("软件采购合同_风险版.docx", "软件采购合同（风险版）", "purchase"),
    ("大额销售合同_风险版.pdf", "大额销售合同（风险版）", "sales"),
    ("软件采购合同_标准版.docx", "软件采购合同（标准版）", "purchase"),
]
#: 演示账号（见 app/services/user_service.py 的 SEED_USERS），需要有建单权限
SEED_ACCOUNT = ("zhang.wei", "operator123")


def _post(url: str, payload: dict, token: str | None = None, timeout: float = 120.0):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(url: str, token: str | None = None, timeout: float = 30.0):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _seed(port: int, host: str = "127.0.0.1") -> None:
    base = f"http://{host}:{port}/api/v1"

    # ---- 1. 等接口可用（最多 60 秒）----
    token = ""
    for _ in range(60):
        try:
            token = _post(
                f"{base}/auth/login",
                {"username": SEED_ACCOUNT[0], "password": SEED_ACCOUNT[1]},
                timeout=5,
            )["token"]
            break
        except Exception:
            time.sleep(1)
    if not token:
        print("[demo-seed] 接口一直没起来，跳过演示数据", flush=True)
        return

    # ---- 2. 非空库直接跳过（幂等，重启不会重复灌）----
    try:
        if (_get(f"{base}/tasks?limit=1", token) or {}).get("total"):
            print("[demo-seed] 库里已有任务，跳过", flush=True)
            return
    except Exception as exc:
        print(f"[demo-seed] 查任务数失败：{type(exc).__name__}: {exc}", flush=True)

    # ---- 3. 逐份建单 + 跑审查（串行，别并发压 SQLite）----
    print("[demo-seed] 开始灌演示数据…", flush=True)
    for filename, title, biz in SEED_FILES:
        path = HERE / "samples" / filename
        if not path.is_file():
            print(f"[demo-seed] 缺样例，跳过：{filename}", flush=True)
            continue
        try:
            out = _post(
                f"{base}/tasks",
                {
                    "title": title,
                    "business_type": biz,
                    "file_path": str(path),
                    "run_now": True,
                },
                token,
            )
            print(
                f"[demo-seed] {title} → {out.get('status')} "
                f"风险 {out.get('risk_counts')}",
                flush=True,
            )
        except Exception as exc:  # 灌数据失败绝不能影响服务本身
            print(f"[demo-seed] {title} 失败：{type(exc).__name__}: {exc}", flush=True)
    print("[demo-seed] 演示数据就绪", flush=True)


def start(port: int, host: str = "127.0.0.1") -> None:
    """后台线程启动灌数据，立刻返回。"""
    threading.Thread(target=_seed, args=(port, host), daemon=True).start()
'''


RUN_PY = '''\
"""演示版启动入口：只监听一个端口，同时提供前端页面与 /api/v1 接口。

优先读云平台注入的裸 ``PORT`` / ``HOST``（config.py 里做了别名兼容）。
没给 HOST 时**默认绑 0.0.0.0** —— 这是发布入口，绑回环等于外部访问不到；
本地只想自己访问就显式给 ``HOST=127.0.0.1``。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 保证不管从哪个目录启动都能 import 到 app 包
sys.path.insert(0, str(HERE))

import uvicorn  # noqa: E402

from app.main import app  # noqa: E402


# ----------------------------------------------------------------------
# 诊断端点（**只存在于发布包**，项目源码里没有）：看反向代理到底转发了什么。
# 用途：排查「登录成功但下一个请求 401」这类代理剥头问题。
#
# 🔒 **默认关闭**：它是公开无鉴权的，线上没必要暴露。要排障时在环境里设
# ``APP_DEBUG_ECHO=1`` 再重启，查完记得撤掉。
# ----------------------------------------------------------------------
if (os.environ.get("APP_DEBUG_ECHO") or "").lower() in {"1", "true", "yes"}:
    from fastapi import Request  # noqa: E402

    @app.get("/debug/echo", include_in_schema=False)
    async def _debug_echo(request: Request):
        return {
            "headers": {k.lower(): v for k, v in request.headers.items()},
            "query": dict(request.query_params),
            "client": str(request.client),
            "url": str(request.url),
        }

    # ⚠️ main.py 里有一个最后注册的 SPA 兜底 ``/{full_path:path}``，而 FastAPI 是
    # **按注册顺序**匹配的 —— 现在才注册的 /debug/echo 会被它抢先吞掉。
    # 把这条路由挪到队首，保证它一定能命中。
    app.router.routes.insert(0, app.router.routes.pop())


def main() -> None:
    host = os.environ.get("HOST") or os.environ.get("APP_HOST") or "0.0.0.0"
    port = int(os.environ.get("PORT") or os.environ.get("APP_PORT") or 8000)
    print(f"[deploy-demo] 监听 http://{host}:{port}", flush=True)

    # 空库时在后台灌一批演示任务：**不阻塞启动**，平台能立刻看到端口在监听。
    # 开关见 .env 的 APP_DEMO_SEED；关掉就是一个干净的空系统。
    if (os.environ.get("APP_DEMO_SEED") or "true").lower() in {"1", "true", "yes"}:
        try:
            import demo_seed

            demo_seed.start(port)
        except Exception as exc:
            print(f"[deploy-demo] 演示数据跳过：{type(exc).__name__}: {exc}", flush=True)

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
'''

README_TEMPLATE = """\
# 合同审批智能审查系统 · 演示版（单端口 / SQLite）

由 `XM-contract-approve/scripts/build_deploy_pkg.py` 自动组装，**不要在这里手改代码** ——
改完下次组装会被覆盖。要改请改原项目，然后重新组装。

## 这是什么

正式的开发版跑「Docker MySQL + 后端 + vite」三件套；这个包是给**云平台发布**用的：
数据库换成内嵌 SQLite、后端顺便把前端构建产物一起托管，所以**只占一个端口**。

## 跑起来

```bash
pip install -r requirements.txt
python run.py                 # 读 PORT / HOST，默认绑 0.0.0.0:8000
PORT=8099 HOST=127.0.0.1 python run.py    # 本地只给自己看
```

起来后打开 `http://127.0.0.1:8000`，用演示账号登录（密码见原项目 README 的账号表，
如 `zhang.wei / operator123`）。

**首次启动会自动灌 3 份已审查完的样例合同**（后台线程里走公开接口跑，不阻塞启动；
库里已有任务就跳过）。不想要就在启动前加环境变量：

```bash
APP_DEMO_SEED=false python run.py
```

> 注意 `APP_DEMO_SEED` 是**真正的环境变量**，写进 `.env` 不生效 ——
> `.env` 只被 pydantic-settings 用来喂它自己声明的字段，不会导出到 `os.environ`。

## 与开发版的能力差异（都是刻意的）

| 项 | 开发版 | 这个演示版 | 原因 |
|---|---|---|---|
| 数据库 | Docker MySQL 8.0（3307） | SQLite（`storage/contract_review.db`） | 发布沙箱只给一个 HTTP 端口，不提供数据库服务 |
| 前端 | vite dev server（5173）+ 代理 | 后端托管 `static/` 构建产物 | 同上，只允许一个对外端口 |
| LLM 语义研判 | 可开（需 API key） | **关闭** | 沙箱里没有 key；关掉后规则引擎仍然完整可用 |
| 扫描件 OCR | 可开（`uv sync --extra ocr`） | **已启用** | 图片/扫描件走 OCR 正常解析；依赖已进 requirements |
| 数据持久化 | 长期 | 沙箱重建即重置 | 演示用途 |

## 目录

| 路径 | 说明 |
|---|---|
| `app/` | 后端源码（从原项目复制） |
| `static/` | 前端构建产物（原项目 `frontend/dist`） |
| `samples/` | 样例合同，供页面上的「示例合同（快速体验）」一键试跑 |
| `storage/` | 运行期数据：SQLite 库、上传件、导出的报告 |
| `run.py` | 启动入口（读 `PORT`，空库时后台灌演示数据） |
| `demo_seed.py` | 演示数据种子（走公开 HTTP 接口，失败不影响服务） |
| `.env` | 演示版配置（SQLite + 托管前端 + 关 LLM / 开 OCR） |
"""


def _copy_tree(src: Path, dst: Path) -> int:
    """按 SKIP_DIRS 过滤复制目录树，返回复制的文件数。"""
    n = 0
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if item.is_dir():
            continue
        if item.suffix in {".pyc", ".pyo"}:
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        n += 1
    return n


def _requirements() -> tuple[str, list[str]]:
    """从 pyproject.toml 的 dependencies 生成 requirements.txt（去掉 MySQL 驱动）。

    直接解析文本而不是用 tomllib 读结构化数据 —— 这样**保留原本的注释与分组**，
    生成出来的文件仍然是对人有用的文档，而不是一堆裸包名。
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # ⚠️ 结束括号必须锚在**行首**：依赖里存在 `uvicorn[standard]>=0.32` 这种带 extra 的写法，
    # 用非贪婪的 `\]` 会在这里被提前截断（踩过：只解析出 2 个包）。
    block = re.search(r"^dependencies\s*=\s*\[(.*?)^\]", text, re.S | re.M)
    if not block:
        raise SystemExit("pyproject.toml 里没找到 dependencies，解析失败")

    kept: list[str] = []
    dropped: list[str] = []
    for line in block.group(1).splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        m = re.match(r'"([^"]+)"', raw)
        if not m:
            continue
        spec = m.group(1)
        name = re.split(r"[<>=!\[;]", spec, 1)[0].strip()
        if name in SKIP_REQUIREMENTS:
            dropped.append(spec)
            continue
        kept.append(spec)

    lines = [
        "# 由 scripts/build_deploy_pkg.py 从 pyproject.toml 生成（演示版单端口包）",
        f"# 已剔除：{'、'.join(dropped) if dropped else '无'}（发布沙箱里用不到）",
        *kept,
        # OCR 是 optional extra，不在 dependencies 里，这里显式补上（见 OCR_EXTRA 注释）
        OCR_EXTRA,
    ]
    return "\n".join(lines) + "\n", dropped


def build(out: Path) -> int:
    dist = ROOT / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        print(f"❌ 找不到前端构建产物：{dist / 'index.html'}")
        print("   先在 frontend/ 里跑 npm run build，再回来组装。")
        return 2

    if out.exists():
        print(f"清空旧的输出目录：{out}")
        shutil.rmtree(out)
    out.mkdir(parents=True)

    n_app = _copy_tree(ROOT / "app", out / "app")
    n_samples = _copy_tree(ROOT / "samples", out / "samples")
    n_static = _copy_tree(dist, out / "static")
    (out / "storage").mkdir(exist_ok=True)

    req_text, dropped = _requirements()
    (out / "requirements.txt").write_text(req_text, encoding="utf-8")
    (out / ".env").write_text(
        ENV_TEMPLATE.format(auth_secret=secrets.token_urlsafe(32)), encoding="utf-8"
    )
    (out / "run.py").write_text(RUN_PY, encoding="utf-8")
    (out / "demo_seed.py").write_text(SEED_PY, encoding="utf-8")
    (out / "README.md").write_text(README_TEMPLATE, encoding="utf-8")

    print(f"✅ 组装完成：{out}")
    print(f"   app/     {n_app} 个文件")
    print(f"   samples/ {n_samples} 个文件")
    print(f"   static/  {n_static} 个文件（前端构建产物）")
    print(f"   requirements.txt  {len(req_text.splitlines())} 行（剔除 {dropped}）")
    print()
    print("下一步 —— 本地先验一遍再发布：")
    print(f"   cd {out}")
    print(f"   {ROOT / '.venv' / 'Scripts' / 'python.exe'} run.py")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="组装可发布的单端口演示包")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help=f"输出目录（默认 {DEFAULT_OUT}）")
    args = ap.parse_args()
    return build(Path(args.out).resolve())


if __name__ == "__main__":
    sys.exit(main())
