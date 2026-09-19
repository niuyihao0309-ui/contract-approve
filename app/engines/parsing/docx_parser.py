"""DOCX 解析器。

关键点（大纲 2.1）
-----------------
1. 按**文档流顺序**遍历段落与表格 —— 不能先跑完段落再跑表格，否则条款顺序全乱。
2. **表格按行拍平成独立 block**（``type=table``，``text`` 用 ``" | "`` 连接各单元格），
   否则跨行条款切不干净、也无法单独定位。
3. ``para_index`` 是全文递增的块序号；DOCX 本身没有页码概念，所以 ``page`` 用
   三重信号估算：``w:lastRenderedPageBreak``（Word 保存时写入的真实分页）
   → 显式 ``w:br type=page`` 分页符 → 按每页字数经验值兜底。
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

from app.core.exceptions import DocumentBlockedError, FileParseError
from app.core.logging import logger
from app.engines.parsing.base import BaseParser, RawDoc
from app.engines.parsing.heading import clean_heading, detect_heading
from app.models.enums import BlockReason, BlockType, FileKind

#: OLE 复合文档魔数。加密的 docx 与旧版 doc 都是这个头。
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

#: A4 中文单页大致字数，用于无分页信号时估算页码
_CHARS_PER_PAGE = 1400

#: 标题样式名到层级的映射（中英文两种 Word 界面）
_HEADING_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"^(?:Heading|标题)\s*([1-9])$", re.I), 0),          # 用捕获组
    (re.compile(r"^Title$|^题注$|^文档标题$", re.I), 1),
]

#: 中文公文的条号在 heading.detect_heading 里统一处理，这里不再维护第二套规则

#: 分页符在 OOXML 里的两种写法
_PAGE_BREAK = re.compile(r'w:type="page"|lastRenderedPageBreak')


def _split_lines(text: str) -> list[str]:
    """把一个段落按内嵌换行拆成多行。

    同一个段落里用 ``Shift+Enter`` 换行时，python-docx 读出来是一整段带 ``\\n`` 的文本。
    如果不拆开，甲方/乙方/金额/期限会挤在同一个 block 里 —— 元数据定位精度会大打折扣。
    """
    if "\n" not in text:
        return [text]
    return [line for line in (ln.strip() for ln in text.split("\n")) if line]


def _style_level(paragraph: Paragraph) -> int | None:
    """从段落样式推断标题层级。"""
    try:
        name = (paragraph.style.name or "").strip()
    except Exception:  # pragma: no cover - 异常样式表
        return None
    for pattern, static_level in _HEADING_PATTERNS:
        m = pattern.match(name)
        if m:
            if static_level:
                return static_level
            try:
                return int(m.group(1))
            except (IndexError, ValueError):
                return 1
    # 大纲级别：Word 里设置了大纲级别的段落
    try:
        pPr = paragraph._p.pPr
        if pPr is not None and pPr.find(qn("w:outlineLvl")) is not None:
            lvl = int(pPr.find(qn("w:outlineLvl")).get(qn("w:val")))
            return min(lvl + 1, 9)
    except Exception:  # pragma: no cover
        pass
    return None


def _iter_block_items(parent: DocxDocument | _Cell):
    """按文档流顺序产出 Paragraph / Table。"""
    if isinstance(parent, DocxDocument):
        parent_elm = parent.element.body
    elif isinstance(parent, _Cell):
        parent_elm = parent._tc
    else:  # pragma: no cover
        raise ValueError(f"不支持的父节点类型：{type(parent)}")

    for child in parent_elm.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


class DocxParser(BaseParser):
    """``.docx`` 解析器。"""

    name = "docx_parser"
    kind = FileKind.DOCX

    # ------------------------------------------------------------------
    def extract(self, path: Path) -> RawDoc:
        self._guard_encrypted(path)

        try:
            doc = Document(str(path))
        except Exception as exc:
            raise FileParseError(f"无法打开 DOCX：{type(exc).__name__}: {exc}") from exc

        blocks: list[dict] = []
        warnings: list[str] = []
        tables: list[list[str]] = []

        para_index = 0
        cursor_chars = 0
        page = 1
        table_seq = -1

        for item in _iter_block_items(doc):
            if isinstance(item, Paragraph):
                raw_text = self._paragraph_text(item)
                style_level = _style_level(item)

                for text in _split_lines(raw_text):
                    if not text.strip():
                        continue
                    level = detect_heading(text, style_level=style_level)
                    btype = BlockType.TITLE if level else BlockType.PARAGRAPH

                    blocks.append(
                        {
                            "type": btype.value,
                            "text": clean_heading(text) if level else text,
                            "page": page,
                            "para_index": para_index,
                            "level": level,
                        }
                    )
                    para_index += 1
                    cursor_chars += len(text)
                    page = self._advance_page(item, page, cursor_chars)

                # 空段落不占 para_index，避免「第N段」跟人肉数出来的差太多
                if not raw_text.strip():
                    page = self._advance_page(item, page, cursor_chars)

            else:  # Table
                table_seq += 1
                row_texts: list[str] = []
                for row_index, row in enumerate(item.rows):
                    cells = [self._cell_text(c) for c in row.cells]
                    if not any(c.strip() for c in cells):
                        continue
                    row_text = " | ".join(c.strip() for c in cells)
                    row_texts.append(row_text)
                    blocks.append(
                        {
                            "type": BlockType.TABLE.value,
                            "text": row_text,
                            "page": page,
                            "para_index": para_index,
                            "table_index": table_seq,
                            "row_index": row_index,
                            "cells": cells,
                        }
                    )
                    para_index += 1
                    cursor_chars += len(row_text)
                    page = max(page, (cursor_chars // _CHARS_PER_PAGE) + 1)
                tables.append(row_texts)
                if row_texts:
                    warnings.append(f"第 {table_seq + 1} 张表格已按行拆为 {len(row_texts)} 个文本块")

        if not blocks:
            raise DocumentBlockedError(
                "DOCX 中没有可解析的文本内容",
                detail={"block_reason": BlockReason.EMPTY_BODY.value},
            )

        return RawDoc(
            blocks=blocks,
            page_count=max(page, 1),
            warnings=warnings[:5],
            extra={
                "table_count": max(table_seq + 1, 0),
                "paragraph_source": "python-docx",
                "page_estimation": "lastRenderedPageBreak > 显式分页符 > 字数估算",
            },
        )

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _guard_encrypted(path: Path) -> None:
        """识别加密文档 / 旧版 doc / 损坏包。"""
        head = path.read_bytes()[:8] if path.stat().st_size >= 8 else b""
        if head.startswith(_OLE_MAGIC):
            if path.suffix.lower() == ".doc":
                raise DocumentBlockedError(
                    "这是旧版 .doc 二进制格式，无法直接解析，请用 Word 另存为 .docx 后重传",
                    detail={"block_reason": BlockReason.UNSUPPORTED_FORMAT.value},
                )
            raise DocumentBlockedError(
                "DOCX 已被加密（设有打开密码），无法解析",
                detail={"block_reason": BlockReason.ENCRYPTED.value},
            )
        if not zipfile.is_zipfile(path):
            raise DocumentBlockedError(
                "文件不是有效的 DOCX（可能已损坏或被加密）",
                detail={"block_reason": BlockReason.PARSE_FAILED.value},
            )

    @staticmethod
    def _paragraph_text(p: Paragraph) -> str:
        """取段落纯文本，并把制表符规整成空格。"""
        try:
            return p.text.replace("\t", " ").strip()
        except Exception:  # pragma: no cover
            return ""

    @staticmethod
    def _cell_text(cell: _Cell) -> str:
        """取单元格文本，多个段落用空格连起来。"""
        try:
            return " ".join(t.strip() for t in cell.text.split("\n") if t.strip())
        except Exception:  # pragma: no cover
            return ""

    @staticmethod
    def _advance_page(p: Paragraph, page: int, cursor_chars: int) -> int:
        """根据分页信号推进页码。"""
        try:
            xml = p._p.xml
        except Exception:  # pragma: no cover
            return page
        if _PAGE_BREAK.search(xml):
            return page + 1
        return max(page, (cursor_chars // _CHARS_PER_PAGE) + 1)


__all__ = ["DocxParser"]
