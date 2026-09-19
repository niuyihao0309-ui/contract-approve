"""扫描件 OCR 解析器。

用 RapidOCR（onnxruntime 版）而不是 PaddleOCR：同样支持中英文，
但依赖体积约 100MB 级别，不需要装 2GB+ 的 paddlepaddle ——
在 16G 内存、无独显的机器上这是更务实的选择。

实现要点
--------
1. OCR 引擎**懒加载**：没装 OCR 依赖时不影响其它格式的解析。
2. 引擎不可用 → 抛 :class:`DocumentBlockedError`（原因 ``OCR_UNAVAILABLE``），
   任务落到 ``blocked``，管理员可重传可复制文本的文档（验收标准第 7 条）。
3. 识别结果先按行拿到，再按「同页 + 垂直间距小 + 上一行没结束标点」合并成段落块，
   同时把多行的 bbox 取并集 —— 这样前端既能在段落级高亮，也能精确到行。
4. 平均置信度过低 → 视为严重模糊，同样走 blocked。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.exceptions import DocumentBlockedError
from app.core.logging import logger
from app.engines.parsing.base import BaseParser, RawDoc
from app.models.enums import BlockReason, BlockType, FileKind

#: 平均置信度低于此值 → 严重模糊
MIN_AVG_SCORE = 0.55
#: 识别出的有效字符少于这个数 → 空/模糊
MIN_TOTAL_CHARS = 20
#: 两行 y 方向间距小于「行高 × 该系数」视为同一段
PARA_GAP_RATIO = 0.75
#: 行尾出现这些标点时，不把下一行并入本段
_SENTENCE_END = "。！？；：.!?;:"


class _OcrEngineHolder:
    """OCR 引擎单例。首次调用才加载模型。"""

    _engine: Any = None
    _error: str | None = None

    @classmethod
    def get(cls) -> Any:
        if cls._engine is not None:
            return cls._engine
        if cls._error:
            raise DocumentBlockedError(
                f"OCR 引擎不可用：{cls._error}",
                detail={"block_reason": BlockReason.OCR_UNAVAILABLE.value},
            )
        try:
            # 新包名优先，回退旧包名
            try:
                from rapidocr import RapidOCR  # type: ignore

                cls._engine = RapidOCR()
            except ImportError:
                from rapidocr_onnxruntime import RapidOCR  # type: ignore

                cls._engine = RapidOCR()
            logger.info("[OCR] RapidOCR 引擎已加载")
            return cls._engine
        except Exception as exc:
            cls._error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                f"[OCR] 引擎加载失败：{cls._error}。"
                f"安装方式：uv sync --extra ocr"
            )
            raise DocumentBlockedError(
                f"OCR 引擎不可用（{cls._error}），请安装后重试或人工重传文本版文档",
                detail={"block_reason": BlockReason.OCR_UNAVAILABLE.value},
            ) from exc


class OcrMixin:
    """OCR 调用逻辑，图片解析器与 PDF 扫描页共用。"""

    @staticmethod
    def run_ocr(image: Any) -> list[dict]:
        """对一张图（路径 / ndarray / bytes）跑 OCR。

        返回 ``[{"text", "score", "box": [x0,y0,x1,y1]}, ...]``，已按阅读顺序排序。
        """
        engine = _OcrEngineHolder.get()
        try:
            raw = engine(image)
        except Exception as exc:
            raise DocumentBlockedError(
                f"OCR 识别失败：{type(exc).__name__}: {exc}",
                detail={"block_reason": BlockReason.OCR_UNAVAILABLE.value},
            ) from exc

        lines = OcrMixin._normalize_output(raw)
        lines.sort(key=lambda x: (round(x["box"][1] / 10), x["box"][0]))
        return lines

    @staticmethod
    def _normalize_output(raw: Any) -> list[dict]:
        """兼容 RapidOCR 各版本 / 两种包名的输出格式。"""
        lines: list[dict] = []

        # 格式 A：RapidOCROutput 对象，带 .boxes / .txts / .scores
        txts = getattr(raw, "txts", None)
        if txts is not None:
            boxes = getattr(raw, "boxes", []) or []
            scores = getattr(raw, "scores", []) or []
            for i, text in enumerate(txts):
                box = boxes[i] if i < len(boxes) else None
                score = float(scores[i]) if i < len(scores) else 0.0
                lines.append(
                    {"text": str(text).strip(), "score": score, "box": _to_xyxy(box)}
                )
            return [x for x in lines if x["text"]]

        # 格式 B：(result, elapse) 元组，result 是 [[box, text, score], ...]
        result = raw[0] if isinstance(raw, tuple) else raw
        if not result:
            return []
        for item in result:
            try:
                box, text, score = item[0], item[1], float(item[2])
            except (IndexError, TypeError, ValueError):
                continue
            if str(text).strip():
                lines.append(
                    {"text": str(text).strip(), "score": score, "box": _to_xyxy(box)}
                )
        return lines

    # ------------------------------------------------------------------
    @staticmethod
    def group_paragraphs(lines: list[dict], *, page: int) -> list[dict]:
        """按行聚合成段落块。"""
        if not lines:
            return []
        out: list[dict] = []
        cur: list[dict] = [lines[0]]

        def flush() -> None:
            if not cur:
                return
            text = "".join(x["text"] for x in cur) if _is_cjk(cur) else " ".join(x["text"] for x in cur)
            xs0 = [x["box"][0] for x in cur]
            ys0 = [x["box"][1] for x in cur]
            xs1 = [x["box"][2] for x in cur]
            ys1 = [x["box"][3] for x in cur]
            out.append(
                {
                    "type": BlockType.PARAGRAPH.value,
                    "text": text,
                    "page": page,
                    "bbox": [min(xs0), min(ys0), max(xs1), max(ys1)],
                    "meta": {
                        "ocr_score": round(sum(x["score"] for x in cur) / len(cur), 4),
                        "ocr_line_count": len(cur),
                        "bboxes": [x["box"] for x in cur],
                    },
                }
            )
            cur.clear()

        for prev, line in zip(lines, lines[1:]):
            prev_h = max(prev["box"][3] - prev["box"][1], 1.0)
            gap = line["box"][1] - prev["box"][3]
            same_para = (
                gap < prev_h * PARA_GAP_RATIO
                and not prev["text"].endswith(tuple(_SENTENCE_END))
                and not _looks_like_heading(prev["text"])
            )
            if same_para:
                cur.append(line)
            else:
                flush()
                cur.append(line)
        flush()
        return out


def _is_cjk(lines: list[dict]) -> bool:
    sample = "".join(x["text"] for x in lines)
    if not sample:
        return True
    cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    return cjk / max(len(sample), 1) > 0.3


def _looks_like_heading(text: str) -> bool:
    import re

    return bool(
        re.match(r"^\s*(?:第\s*[一二三四五六七八九十百零\d]+\s*[条章节部分]|\d+(?:\.\d+)+\s)", text)
    )


def _to_xyxy(box: Any) -> list[float]:
    """4 点坐标 → [x0, y0, x1, y1]。"""
    try:
        if box is None:
            return [0.0, 0.0, 0.0, 0.0]
        arr = box.tolist() if hasattr(box, "tolist") else box
        if len(arr) == 4 and all(isinstance(v, (int, float)) for v in arr):
            return [float(v) for v in arr]
        xs = [float(p[0]) for p in arr]
        ys = [float(p[1]) for p in arr]
        return [min(xs), min(ys), max(xs), max(ys)]
    except Exception:  # pragma: no cover
        return [0.0, 0.0, 0.0, 0.0]


class ImageOcrParser(BaseParser):
    """图片扫描件解析器。"""

    name = "ocr_parser"
    kind = FileKind.IMAGE

    def __init__(self) -> None:
        self._ocr = OcrMixin()

    def extract(self, path: Path) -> RawDoc:
        lines = self._ocr.run_ocr(str(path))
        return self._to_raw(lines, page_count=1, page=1, extra={"source": "image"})

    # ------------------------------------------------------------------
    def _to_raw(
        self, lines: list[dict], *, page_count: int, page: int = 1, extra: dict | None = None
    ) -> RawDoc:
        total_chars = sum(len(x["text"]) for x in lines)
        avg_score = (sum(x["score"] for x in lines) / len(lines)) if lines else 0.0

        if total_chars < MIN_TOTAL_CHARS or avg_score < MIN_AVG_SCORE:
            raise DocumentBlockedError(
                "扫描件识别到的文字过少或置信度过低，疑似严重模糊或空白",
                detail={
                    "block_reason": BlockReason.BLURRY_SCAN.value,
                    "total_chars": total_chars,
                    "avg_score": round(avg_score, 4),
                    "min_avg_score": MIN_AVG_SCORE,
                },
            )

        blocks = self._ocr.group_paragraphs(lines, page=page)
        for i, b in enumerate(blocks):
            b.setdefault("para_index", i)
        blocks = [{"para_index": i, **b} for i, b in enumerate(blocks)]

        warnings = []
        if avg_score < 0.75:
            warnings.append(f"OCR 平均置信度偏低（{avg_score:.2f}），建议人工复核原文")

        return RawDoc(
            blocks=blocks,
            page_count=page_count,
            warnings=warnings,
            extra={
                "ocr_engine": "rapidocr",
                "avg_score": round(avg_score, 4),
                "line_count": len(lines),
                **(extra or {}),
            },
        )


__all__ = ["ImageOcrParser", "OcrMixin", "MIN_AVG_SCORE", "MIN_TOTAL_CHARS"]
