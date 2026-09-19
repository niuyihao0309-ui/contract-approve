"""解析器基类与调度。

三种格式（DOCX / PDF / 扫描件）的实现只负责吐出「裸 block」，
编号、字符偏移、全文拼接统一由 :meth:`DocIR.build` 完成 ——
这样保证三条路径产出的 IR **结构完全一致**（大纲阶段二验收要求）。
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.exceptions import DocumentBlockedError, FileParseError
from app.core.logging import logger
from app.models.enums import EXT_TO_KIND, PARSER_BY_KIND, BlockReason, FileKind
from app.schemas.document import DocIR


class RawDoc(BaseModel):
    """解析器的原始输出（还没编号、还没算偏移）。"""

    blocks: list[dict] = Field(default_factory=list)
    page_count: int = 1
    warnings: list[str] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)


class BaseParser(ABC):
    """所有解析器的基类。"""

    #: 解析器名，落库到 ``contract_file.parser``
    name: str = "base"
    #: 处理的文件类型
    kind: FileKind = FileKind.DOCX

    # ------------------------------------------------------------------
    @abstractmethod
    def extract(self, path: Path) -> RawDoc:
        """同步解析，在子类实现。

        约定要产出的 block 字段：``type / text / page / para_index``，
        PDF 与 OCR 还要给 ``bbox``。**不要**自己填 ``block_id`` 和 ``char_*``。
        """

    # ------------------------------------------------------------------
    async def parse(self, path: Path, *, doc_id: str, filename: str | None = None) -> DocIR:
        """异步入口：把同步解析丢到线程池，避免阻塞事件循环。"""
        path = Path(path)
        if not path.exists():
            raise FileParseError(f"文件不存在：{path}")

        logger.info(f"[解析:{self.name}] 开始 {path.name}（{path.stat().st_size} bytes）")
        try:
            raw = await asyncio.to_thread(self.extract, path)
        except (DocumentBlockedError, FileParseError):
            raise
        except Exception as exc:
            logger.exception(f"[解析:{self.name}] 失败 {path.name}")
            raise FileParseError(f"{self.name} 解析失败：{type(exc).__name__}: {exc}") from exc

        ir = DocIR.build(
            doc_id=doc_id,
            source_file=filename or path.name,
            file_kind=self.kind.value,
            parser=self.name,
            raw_blocks=raw.blocks,
            page_count=raw.page_count,
        )
        ir.warnings.extend(raw.warnings)
        ir.extra.update(raw.extra)

        logger.info(
            f"[解析:{self.name}] 完成 {path.name}：{len(ir.blocks)} 块 / "
            f"{ir.page_count} 页 / 正文 {ir.body_char_count} 字"
            + (f" / 告警 {len(ir.warnings)}" if ir.warnings else "")
        )
        return ir


# ======================================================================
# 注册表与调度
# ======================================================================
def kind_of(path: str | Path) -> FileKind:
    """按扩展名判断文件类型。"""
    ext = Path(path).suffix.lower()
    kind = EXT_TO_KIND.get(ext)
    if kind is None:
        raise DocumentBlockedError(
            f"不支持的文件格式：{ext or '（无扩展名）'}",
            detail={"block_reason": BlockReason.UNSUPPORTED_FORMAT.value, "ext": ext},
        )
    return kind


def get_parser(path: str | Path, *, ocr_enabled: bool | None = None) -> BaseParser:
    """按文件类型拿到解析器实例。

    延迟 import：装了 PyMuPDF 不代表要装 OCR，反之亦然。
    """
    from app.core.config import settings

    kind = kind_of(path)
    use_ocr = settings.ocr_enabled if ocr_enabled is None else ocr_enabled

    if kind is FileKind.DOCX:
        from app.engines.parsing.docx_parser import DocxParser

        return DocxParser()

    if kind is FileKind.PDF:
        from app.engines.parsing.pdf_parser import PdfParser

        return PdfParser(ocr_fallback=use_ocr)

    # IMAGE
    from app.engines.parsing.ocr_parser import ImageOcrParser

    return ImageOcrParser()


def parser_name_of(path: str | Path) -> str:
    return PARSER_BY_KIND.get(kind_of(path), "unknown")


__all__ = ["BaseParser", "RawDoc", "get_parser", "kind_of", "parser_name_of"]
