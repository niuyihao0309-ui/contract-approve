"""附件接入服务：保存、校验、指纹去重、解析结果复用。

大纲 5.2：按文件 hash 去重，重复提交复用已解析结果 —— 省 LLM 成本。

实现方式：``contract_file.file_hash`` 建索引，解析成功后置 ``parsed=True``。
新任务上传同一个文件时，直接把它上次解析出的 ``doc_block`` / ``contract_metadata`` /
``doc_clause`` 复制到本次任务下，跳过解析与 OCR，也跳过元数据抽取。
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import logger
from app.engines.parsing.base import kind_of
from app.engines.parsing.heading import looks_like_org
from app.models.enums import FileKind
from app.models.tables import (
    ContractFile,
    ContractMetadata,
    ContractTask,
    DocBlock,
    DocClause,
)

CHUNK = 1024 * 1024


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


class FileService:
    """附件的存取与去重。"""

    # ------------------------------------------------------------------
    @staticmethod
    async def attach_local_file(
        session: AsyncSession, task: ContractTask, source: Path
    ) -> ContractFile:
        """把本机上的文件挂到任务下（复制进 storage/uploads）。"""
        source = Path(source).expanduser().resolve()
        if not source.exists():
            raise AppError(f"文件不存在：{source}")
        data = source.read_bytes()
        return await FileService.save_bytes(
            session, task, filename=source.name, raw=data
        )

    # ------------------------------------------------------------------
    @staticmethod
    async def save_bytes(
        session: AsyncSession,
        task: ContractTask,
        *,
        filename: str,
        raw: bytes,
    ) -> ContractFile:
        """校验 → 落盘 → 建记录。不负责解析。"""
        if not raw:
            raise AppError("上传的文件为空")

        settings.ensure_dirs()
        ext = Path(filename).suffix.lower()

        # 扩展名校验（先于写盘，避免脏文件落盘）
        allowed = {e.lower() for e in settings.allowed_extensions}
        if ext not in allowed:
            raise AppError(
                f"不支持的文件格式：{ext or '（无扩展名）'}，允许：{', '.join(sorted(allowed))}"
            )

        kind = kind_of(filename)          # 可能抛 DocumentBlockedError（不可解析格式）
        file_hash = sha256_of(raw)

        # 落盘：用 hash 前缀命名，天然去重、也不会被同名文件覆盖
        target = settings.upload_dir / f"{file_hash[:16]}{ext}"
        if not target.exists():
            target.write_bytes(raw)
            logger.info(f"[附件] 写入 {target.name}（{len(raw)} bytes）")
        else:
            logger.info(f"[附件] 文件已存在，复用磁盘副本 {target.name}")

        row = ContractFile(
            id=str(uuid.uuid4()),
            task_id=task.id,
            filename=filename,
            file_hash=file_hash,
            file_size=len(raw),
            ext=ext,
            file_kind=kind.value,
            storage_path=str(target),
            parsed=False,
        )
        session.add(row)
        await session.flush()
        logger.info(
            f"[附件] 任务 {task.id[:8]} ← {filename}（{kind.value}, sha256={file_hash[:12]}…）"
        )
        return row

    # ------------------------------------------------------------------
    # 去重与复用
    # ------------------------------------------------------------------
    @staticmethod
    async def find_cache(
        session: AsyncSession, file_hash: str, *, exclude_task_id: str | None = None
    ) -> ContractFile | None:
        """找一个已成功解析过同一文件的记录（指纹缓存）。"""
        stmt = (
            select(ContractFile)
            .where(ContractFile.file_hash == file_hash, ContractFile.parsed.is_(True))
            .order_by(ContractFile.created_at.desc())
        )
        for row in (await session.execute(stmt)).scalars().all():
            if exclude_task_id and row.task_id == exclude_task_id:
                continue
            if await session.get(ContractTask, row.task_id) is None:
                continue
            return row
        return None

    @staticmethod
    async def reuse_parse(
        session: AsyncSession, task: ContractTask, cached: ContractFile
    ) -> dict:
        """把缓存任务的解析产物复制到当前任务下。

        注意 ``doc_id``：库里 ``doc_block`` 有唯一约束 ``(doc_id, block_id)``，
        而 ``doc_id`` 就等于所属任务的 id。所以复制时必须把 ``doc_id`` 改写成
        当前任务的 id，否则新任务的 block 会和源任务的记录撞唯一键
        （MySQL 报 Duplicate entry）。
        """
        new_doc_id = task.id
        n_blocks = await FileService._copy(
            session,
            DocBlock,
            src_task_id=cached.task_id,
            dst_task_id=task.id,
            extra={"file_id": None, "doc_id": new_doc_id},
        )
        n_meta = await FileService._copy(
            session, ContractMetadata, src_task_id=cached.task_id, dst_task_id=task.id
        )
        n_clause = await FileService._copy(
            session,
            DocClause,
            src_task_id=cached.task_id,
            dst_task_id=task.id,
            extra={"doc_id": new_doc_id},
        )
        await session.flush()
        logger.info(
            f"[附件] 命中指纹缓存（源任务 {cached.task_id[:8]}）："
            f"复用 {n_blocks} blocks / {n_meta} 元数据 / {n_clause} 条款，跳过解析与 OCR"
        )
        return {"blocks": n_blocks, "metadata": n_meta, "clauses": n_clause}

    @staticmethod
    async def _copy(
        session: AsyncSession,
        model,
        *,
        src_task_id: str,
        dst_task_id: str,
        extra: dict | None = None,
    ) -> int:
        """通用行复制：换掉主键与 task_id 后插回。

        ``id`` 可能是 UUID 字符串（要重新生成）或自增整数（要置空让数据库分配）。
        """
        rows = list(
            (await session.execute(select(model).where(model.task_id == src_task_id)))
            .scalars()
            .all()
        )
        for r in rows:
            data = {c.name: getattr(r, c.name) for c in r.__table__.columns}
            if isinstance(data.get("id"), str):
                data["id"] = str(uuid.uuid4())
            else:
                data.pop("id", None)
            data["task_id"] = dst_task_id
            data.update(extra or {})
            session.add(model(**data))
        return len(rows)

    # ------------------------------------------------------------------
    @staticmethod
    async def mark_parsed(
        session: AsyncSession,
        file: ContractFile,
        *,
        parser: str,
        page_count: int,
        meta: dict,
        from_cache: bool = False,
    ) -> None:
        file.parsed = True
        file.parser = parser
        file.page_count = page_count
        file.parse_meta = meta
        file.parse_error = None
        file.from_cache = from_cache
        await session.flush()

    @staticmethod
    async def mark_failed(session: AsyncSession, file: ContractFile, error: str) -> None:
        file.parsed = False
        file.parse_error = error[:2000]
        await session.flush()

    # ------------------------------------------------------------------
    @staticmethod
    async def replace_files(
        session: AsyncSession, task: ContractTask, new_path: Path
    ) -> list[ContractFile]:
        """一键重试时替换附件：旧记录清掉，换上新文件。

        旧记录必须**真的删掉**，不能只标记 ``parsed=False``：
        否则同一个任务下会同时存在新旧两条 ``contract_file``，
        而 :meth:`primary_file` 按 ``created_at`` 取第一条 —— 拿到的正是那条
        物理文件已被删除的旧记录，后续解析直接报「文件不存在」。
        没有任何表用外键引用 ``contract_file.id``，所以删除是安全的。
        """
        from app.services.persistence import PersistenceService

        await PersistenceService.clear_task_artifacts(session, task.id)
        old = list(
            (await session.execute(
                select(ContractFile).where(ContractFile.task_id == task.id)
            )).scalars().all()
        )

        # 物理文件删掉（路径由我们管理，均在 upload_dir 下），再删记录
        for f in old:
            p = Path(f.storage_path)
            if p.exists() and settings.upload_dir in p.parents:
                try:
                    p.unlink()
                except OSError as exc:  # pragma: no cover
                    logger.warning(f"[附件] 删除旧文件失败 {p}：{exc}")
            await session.delete(f)
        await session.flush()

        return [await FileService.attach_local_file(session, task, new_path)]

    # ------------------------------------------------------------------
    @staticmethod
    async def load_for_task(session: AsyncSession, task_id: str) -> list[ContractFile]:
        stmt = (
            select(ContractFile)
            .where(ContractFile.task_id == task_id)
            .order_by(ContractFile.created_at)
        )
        return list((await session.execute(stmt)).scalars().all())

    @staticmethod
    async def primary_file(session: AsyncSession, task_id: str) -> ContractFile | None:
        """取主附件（当前只支持单附件主流程，多附件取第一个）。"""
        files = await FileService.load_for_task(session, task_id)
        return files[0] if files else None


__all__ = ["FileService", "sha256_of", "sha256_of_file"]
