"""PDF 解析器。

策略（大纲 2.2）
----------------
1. 优先取**文本层**：用 PyMuPDF 按页取 block，天然带 bbox（左上原点，y 向下），
   前端叠加高亮可以直接用。
2. 有表格就调 ``page.find_tables()``，把表格行拍平成独立 block，
   并把这些区域从普通文本 block 里剔除，避免同一段文字被算两遍。
3. 单页可抽取文本少于阈值 → 判定为**扫描页**，渲染成位图走 OCR 分支，
   整篇都是扫描页时等价于「扫描件 PDF」。
4. 文档加密 → 直接抛阻塞异常（验收标准第 7 条）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pymupdf

from app.core.exceptions import DocumentBlockedError, FileParseError
from app.core.logging import logger
from app.engines.parsing.base import BaseParser, RawDoc
from app.engines.parsing.heading import clean_heading, detect_heading
from app.engines.parsing.ocr_parser import OcrMixin
from app.models.enums import BlockReason, BlockType, FileKind

#: 渲染扫描页时的 DPI。200 是中文合同的可读下限，再高 OCR 收益不大而耗时翻倍。
OCR_DPI = 200
#: 文本 block 里少于这么多字符的碎片直接丢掉（多为页眉页脚页码）
MIN_BLOCK_CHARS = 2
#: 单个 PDF 文本块最多拆出多少行（防止一页被识别成一个大块）
MAX_LINES_PER_BLOCK = 60

_WS = re.compile(r"[ \t\u3000]+")
_MULTI_NL = re.compile(r"\n{2,}")

#: 行首编号形态，是「新段落起点」的强信号
_LINE_STARTS_NUMBERED = re.compile(
    r"^\s*(?:第\s*[一二三四五六七八九十百零\d]+\s*[条章节部分款项]"
    r"|[一二三四五六七八九十]{1,3}\s*[、.．]"
    r"|\(?\d+(?:\.\d+){0,3}\)?\s*[、.．]?\s)"
)


class PdfParser(BaseParser):
    """``.pdf`` 解析器。"""

    name = "pdf_parser"
    kind = FileKind.PDF

    def __init__(self, *, ocr_fallback: bool = True) -> None:
        self.ocr_fallback = ocr_fallback
        self._ocr = OcrMixin()

    # ------------------------------------------------------------------
    def extract(self, path: Path) -> RawDoc:
        try:
            doc = pymupdf.open(str(path))
        except Exception as exc:
            raise FileParseError(f"无法打开 PDF：{type(exc).__name__}: {exc}") from exc

        try:
            return self._extract_inner(doc, path)
        finally:
            doc.close()

    # ------------------------------------------------------------------
    def _extract_inner(self, doc: Any, path: Path) -> RawDoc:
        if doc.is_encrypted:
            # 有的 PDF 空密码能打开，先试一下
            if not doc.authenticate(""):
                raise DocumentBlockedError(
                    "PDF 已加密（有打开密码），无法解析",
                    detail={"block_reason": BlockReason.ENCRYPTED.value},
                )
            logger.warning(f"[解析:pdf] {path.name} 是加密 PDF，但空密码可开，继续解析")

        from app.core.config import settings

        blocks: list[dict] = []
        warnings: list[str] = []
        para_index = 0
        ocr_pages: list[int] = []
        table_count = 0

        for pno in range(doc.page_count):
            page = doc[pno]
            page_no = pno + 1

            # --- 表格先行 ---
            table_boxes: list[tuple[float, float, float, float]] = []
            for tbl in self._find_tables(page):
                table_boxes.append(tuple(tbl.bbox))
                rows = self._table_rows(tbl)
                if not rows:
                    continue
                table_count += 1
                for row_index, (cells, row_bbox) in enumerate(rows):
                    row_text = " | ".join(c.strip() for c in cells)
                    if not row_text.strip(" |"):
                        continue
                    blocks.append(
                        {
                            "type": BlockType.TABLE.value,
                            "text": row_text,
                            "page": page_no,
                            "para_index": para_index,
                            "bbox": list(row_bbox),
                            "table_index": table_count - 1,
                            "row_index": row_index,
                            "cells": cells,
                        }
                    )
                    para_index += 1

            # --- 普通文本 ---
            page_chars = 0
            for raw in self._text_blocks(page):
                x0, y0, x1, y1, text = raw
                # 落在表格区域内的文本已经由表格分支产出，跳过
                if any(_inside((x0, y0, x1, y1), tb) for tb in table_boxes):
                    continue
                text = _clean(text)
                if len(text) < MIN_BLOCK_CHARS:
                    continue
                page_chars += len(text)
                btype, level = _classify(text)
                blocks.append(
                    {
                        "type": btype,
                        "text": text,
                        "page": page_no,
                        "para_index": para_index,
                        "bbox": [x0, y0, x1, y1],
                        "level": level,
                    }
                )
                para_index += 1

            # --- 扫描页 → OCR ---
            if page_chars < settings.pdf_page_text_threshold:
                ocr_blocks = self._ocr_page(page, page_no, para_index)
                if ocr_blocks:
                    ocr_pages.append(page_no)
                    for b in ocr_blocks:
                        b["para_index"] = para_index
                        blocks.append(b)
                        para_index += 1

        if not blocks:
            raise DocumentBlockedError(
                "PDF 中没有解析出任何文本",
                detail={"block_reason": BlockReason.EMPTY_BODY.value},
            )

        if ocr_pages:
            warnings.append(
                f"第 {', '.join(map(str, ocr_pages[:10]))} 页无文本层，已自动转 OCR 识别"
                + ("（仅显示前 10 页）" if len(ocr_pages) > 10 else "")
            )

        return RawDoc(
            blocks=blocks,
            page_count=doc.page_count,
            warnings=warnings,
            extra={
                "text_layer_pages": doc.page_count - len(ocr_pages),
                "ocr_pages": ocr_pages,
                "table_count": table_count,
                "is_encrypted": bool(doc.is_encrypted),
                "pdf_metadata": {
                    k: v for k, v in (doc.metadata or {}).items() if k in ("title", "author", "producer")
                },
            },
        )

    # ------------------------------------------------------------------
    def _ocr_page(self, page: Any, page_no: int, para_start: int) -> list[dict]:
        """把一页渲染成图再 OCR。引擎不可用时给告警而不是直接崩。"""
        if not self.ocr_fallback:
            return []
        try:
            pix = page.get_pixmap(dpi=OCR_DPI)
            img = pix.tobytes("png")
            lines = self._ocr.run_ocr(img)
        except DocumentBlockedError as exc:
            # OCR 引擎不可用：只在「整篇没有文本层」时才真正阻塞
            logger.warning(f"[解析:pdf] 第 {page_no} 页 OCR 不可用：{exc.message}")
            return []
        except Exception as exc:  # pragma: no cover
            logger.warning(f"[解析:pdf] 第 {page_no} 页 OCR 失败：{type(exc).__name__}: {exc}")
            return []

        paras = self._ocr.group_paragraphs(lines, page=page_no)
        for i, b in enumerate(paras):
            b["para_index"] = para_start + i
        return paras

    # ------------------------------------------------------------------
    @staticmethod
    def _text_blocks(page: Any) -> list[tuple[float, float, float, float, str]]:
        """按行间距把 PDF 页面重组成段落块。

        为什么不用 ``get_text("blocks")``：它的 block 是「版面区域」，
        一个区域里可能塞了整页的好几段，粒度太粗，元数据和条款都没法精确落点。

        这里的做法是走 ``get_text("dict")`` 拿到每一行，然后按两个信号切段：

        1. **行间垂直间距** —— 段内折行几乎贴着，段间留白明显更大；
        2. **行首编号形态** —— 以「第X条 / 1.1 / 一、」开头的行一定是新段起点。

        这样每个 ``(x0,y0,x1,y1,text)`` 就是一段话，与 DOCX 的段落粒度对齐。
        """
        try:
            data = page.get_text("dict")
        except Exception as exc:  # pragma: no cover
            logger.warning(f"[解析:pdf] get_text(dict) 失败：{exc}")
            return []

        lines: list[dict] = []
        for blk in data.get("blocks", []):
            if blk.get("type") != 0:      # 0=文本 1=图片
                continue
            for ln in blk.get("lines", []):
                txt = "".join(sp.get("text", "") for sp in ln.get("spans", []))
                if not txt.strip():
                    continue
                x0, y0, x1, y1 = ln.get("bbox", (0, 0, 0, 0))
                lines.append(
                    {
                        "text": txt,
                        "bbox": (float(x0), float(y0), float(x1), float(y1)),
                        "height": max(float(y1) - float(y0), 1.0),
                    }
                )
        if not lines:
            return []

        lines.sort(key=lambda l: (round(l["bbox"][1] / 3), l["bbox"][0]))

        paragraphs: list[list[dict]] = []
        cur: list[dict] = [lines[0]]
        for prev, line in zip(lines, lines[1:]):
            gap = line["bbox"][1] - prev["bbox"][3]
            new_line_h = max(prev["height"], line["height"])
            starts_numbered = _LINE_STARTS_NUMBERED.match(line["text"].strip())
            if gap > max(3.0, new_line_h * 0.25) or starts_numbered:
                paragraphs.append(cur)
                cur = [line]
            else:
                cur.append(line)
        paragraphs.append(cur)

        out: list[tuple[float, float, float, float, str]] = []
        for para_lines in paragraphs:
            text = _clean("\n".join(l["text"] for l in para_lines))
            if not text:
                continue
            x0 = min(l["bbox"][0] for l in para_lines)
            y0 = min(l["bbox"][1] for l in para_lines)
            x1 = max(l["bbox"][2] for l in para_lines)
            y1 = max(l["bbox"][3] for l in para_lines)
            out.append((x0, y0, x1, y1, text))
        return out

    @staticmethod
    def _find_tables(page: Any) -> list[Any]:
        """``find_tables`` 在旧版 PyMuPDF 里可能不存在或抛异常，全部吞掉。"""
        try:
            finder = page.find_tables()
            return list(finder.tables)
        except Exception:
            return []

    @staticmethod
    def _table_rows(tbl: Any) -> list[tuple[list[str], tuple[float, float, float, float]]]:
        rows: list[tuple[list[str], tuple[float, float, float, float]]] = []
        try:
            data = tbl.extract()
            boxes = getattr(tbl, "rows", None)
            for i, row in enumerate(data or []):
                cells = [("" if c is None else _clean(str(c))) for c in row]
                if not any(cells):
                    continue
                bbox = (0.0, 0.0, 0.0, 0.0)
                if boxes and i < len(boxes):
                    b = getattr(boxes[i], "bbox", None)
                    if b:
                        bbox = (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
                rows.append((cells, bbox))
            return rows
        except Exception as exc:  # pragma: no cover
            logger.debug(f"[解析:pdf] 表格提取失败：{exc}")
            return []


# ======================================================================
def _clean(text: str) -> str:
    """规整 PDF 抽出的文本。

    关键点：PDF 文本块里的换行**只是排版折行**，不是语义分段。
    中文之间直接拼接（不加空格），中英/英文之间补一个空格，
    否则「验收标准」会被拼成「验收 标准」，关键词就匹配不上了。
    """
    t = text.replace("\r", "\n").replace("\u3000", " ")
    lines = [_WS.sub(" ", ln).strip() for ln in t.split("\n")]
    lines = [ln for ln in lines if ln]

    out = ""
    for ln in lines:
        if not out:
            out = ln
            continue
        prev = out[-1]
        nxt = ln[0]
        if _is_cjk(prev) and _is_cjk(nxt):
            out += ln                      # 中文折行：直接接上
        elif prev in "-—" and _is_cjk(nxt):  # 中文里的连接号折行
            out += ln
        else:
            out += " " + ln
    return out.strip()


def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff" or ch in "，。、；：？！“”‘’（）《》【】"


def _inside(inner: tuple[float, float, float, float], outer: tuple[float, float, float, float]) -> bool:
    """inner 是否基本落在 outer 里（用中心点判断，避免边界误差）。"""
    cx = (inner[0] + inner[2]) / 2
    cy = (inner[1] + inner[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def _classify(text: str) -> tuple[str, int | None]:
    """粗分标题与段落。PDF 没有样式表，只能看文本形态。"""
    level = detect_heading(text)
    if level:
        return BlockType.TITLE.value, level
    return BlockType.PARAGRAPH.value, None


__all__ = ["PdfParser", "OCR_DPI"]
