"""数据库初始化 / 体检脚本。

用法::

    # 建表（幂等，已存在不会报错）
    .venv\\Scripts\\python.exe scripts/init_db.py

    # 只看连通性
    .venv\\Scripts\\python.exe scripts/init_db.py --check

    # 删表重建（危险，只在开发期用）
    .venv\\Scripts\\python.exe scripts/init_db.py --drop

    # 灌入规则与标准条款库
    .venv\\Scripts\\python.exe scripts/init_db.py --seed
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from app.core.config import settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402

LINE = "─" * 70


def _mask(url: str) -> str:
    """隐去密码，日志里打印。"""
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    cred, host = rest.split("@", 1)
    user = cred.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"


async def main() -> int:
    ap = argparse.ArgumentParser(description="数据库初始化")
    ap.add_argument("--check", action="store_true", help="只检查连通性")
    ap.add_argument("--drop", action="store_true", help="先删表再建（危险）")
    ap.add_argument("--seed", action="store_true", help="同时灌入规则与标准条款库")
    args = ap.parse_args()

    setup_logging()
    print(f"\n{LINE}\n  目标数据库\n{LINE}")
    print(f"  {_mask(settings.db_url)}")
    dialect = settings.db_url.split("://")[0]
    print(f"  方言：{dialect}")

    from sqlalchemy import text

    from app.db.base import Base, dispose_engine, engine, init_db

    # ---- 连通性 ----
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(text("SELECT VERSION()"))).scalar()
            print(f"  连接成功，服务端版本：{row}")
    except Exception as exc:
        print(f"\n  ✗ 连接失败：{type(exc).__name__}: {exc}")
        _print_hint(dialect, exc)
        await dispose_engine()
        return 1

    if args.check:
        await dispose_engine()
        return 0

    # ---- 建表 ----
    try:
        await init_db(drop=args.drop)
    except Exception as exc:
        print(f"\n  ✗ 建表失败：{type(exc).__name__}: {exc}")
        await dispose_engine()
        return 1

    tables = sorted(Base.metadata.tables.keys())
    print(f"\n{LINE}\n  数据表（{len(tables)} 张）\n{LINE}")
    async with engine.connect() as conn:
        for name in tables:
            try:
                n = (await conn.execute(text(f"SELECT COUNT(*) FROM `{name}`"))).scalar()
                print(f"  {name:22s} {n:>6} 行")
            except Exception:
                print(f"  {name:22s}      ? ")
    print(f"\n  ✓ 建表完成{'（已先删表）' if args.drop else ''}")

    # ---- 灌规则 ----
    if args.seed:
        from app.services.rule_service import RuleService

        stats = await RuleService.sync_from_files()
        print(f"\n{LINE}\n  规则与标准条款库\n{LINE}")
        print(f"  审查规则    ：{stats['rules']} 条")
        print(f"  示范条款模板：{stats['templates']} 条")

    await dispose_engine()
    return 0


def _print_hint(dialect: str, exc: Exception) -> None:
    msg = str(exc)
    print(f"\n{LINE}\n  排查建议\n{LINE}")
    if "mysql" in dialect:
        if "Can't connect" in msg or "refused" in msg or "timed out" in msg:
            print("  1. Docker Desktop 是否已启动（托盘图标变绿）")
            print("  2. 容器是否在跑： docker compose ps")
            print("  3. 没起就执行：   docker compose up -d mysql")
            print("  4. 首次启动 MySQL 要 20-40 秒初始化，等一下再试")
            print("  5. 端口对不对：本机 3307（容器内是 3306）")
        elif "Access denied" in msg:
            print("  账号密码不对。默认 contract / contract123；")
            print("  若你改了 docker-compose.yml，请同步改 .env 里的 APP_DB_URL")
        elif "Unknown database" in msg:
            print("  库不存在。执行： docker compose down -v && docker compose up -d mysql")
            print("  或手工建库： CREATE DATABASE contract_review DEFAULT CHARSET utf8mb4;")
        elif "cryptography" in msg or "sha256_password" in msg:
            print("  认证插件不兼容，装一下： uv add cryptography")
    else:
        print("  检查 APP_DB_URL 是否写对；SQLite 的话确认 storage/ 目录存在")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
