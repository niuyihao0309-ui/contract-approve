"""文档完整性校验。

大纲 2.4 要求：遇到「文档加密、扫描件严重模糊、正文为空」时任务流转为 ``blocked``
并记录原因，支持人工重传或一键重试。加密与模糊在解析器里就拦掉了，
这里做**解析之后的兜底体检**，防止解析器「成功但产出垃圾」的情况。

校验项
------
1. 正文为空 / 过短（``empty_body``）
2. 乱码率过高：同一字符反复出现、或非中文非 ASCII 的符号占比过高（``blurry_scan``）
3. 有效块比例：非空块占比过低，说明大部分内容没解析出来
"""

from __future__ import annotations

import re
from collections import Counter

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import logger
from app.models.enums import BLOCK_REASON_LABEL, BlockReason
from app.schemas.document import DocIR

#: 允许出现在合同里的常见符号
_OK_PUNCT = set("，。、；：？！“”‘’（）《》〈〉【】—…·-—–_/\\|@#$%^&*()[]{}+=<>~`'\".,;:!?，。　 \n\t\r%０１２３４５６７８９")

_MEANINGFUL = re.compile(r"[\u4e00-\u9fff0-9A-Za-z]")


class IntegrityReport(BaseModel):
    """体检报告。"""

    ok: bool = True
    reason: BlockReason | None = None
    detail: str = ""
    metrics: dict = Field(default_factory=dict)

    @property
    def reason_label(self) -> str:
        return BLOCK_REASON_LABEL.get(self.reason.value, "") if self.reason else ""

    def to_detail_dict(self) -> dict:
        return {
            "block_reason": self.reason.value if self.reason else None,
            "detail": self.detail,
            "metrics": self.metrics,
        }


def _meaningful_ratio(text: str) -> float:
    """有意义字符（中文/字母/数字）占比。"""
    if not text:
        return 0.0
    hit = len(_MEANINGFUL.findall(text))
    return hit / len(text)


def _repeat_ratio(text: str) -> float:
    """最高频字符的占比。水印/乱码文档这个值会异常高。"""
    if not text:
        return 0.0
    counter = Counter(text)
    return counter.most_common(1)[0][1] / len(text)


def check_integrity(ir: DocIR) -> IntegrityReport:
    """对 IR 做体检。"""
    body = ir.body_text.strip()
    body_len = len(body)
    total_blocks = len(ir.blocks)
    non_empty = len(ir.non_empty_blocks)

    meaningful = _meaningful_ratio(body)
    repeat = _repeat_ratio(body[:5000])
    avg_chars = body_len / max(non_empty, 1)
    pages = max(ir.page_count, 1)

    metrics = {
        "body_char_count": body_len,
        "block_count": total_blocks,
        "non_empty_block_count": non_empty,
        "page_count": pages,
        "chars_per_page": round(body_len / pages, 1),
        "meaningful_ratio": round(meaningful, 4),
        "top_char_ratio": round(repeat, 4),
        "avg_chars_per_block": round(avg_chars, 2),
    }

    # --- 1. 正文为空 / 过短 ---
    if body_len < settings.min_body_chars:
        return IntegrityReport(
            ok=False,
            reason=BlockReason.EMPTY_BODY,
            detail=(
                f"正文仅 {body_len} 字，低于下限 {settings.min_body_chars} 字，"
                f"没有可审查的内容（共 {total_blocks} 个文本块）"
            ),
            metrics=metrics,
        )

    # --- 2. 有效内容比例过低 ---
    if meaningful < 0.35:
        return IntegrityReport(
            ok=False,
            reason=BlockReason.BLURRY_SCAN,
            detail=(
                f"正文中有意义字符占比仅 {meaningful:.0%}，"
                f"疑似 OCR 识别失败或文档内容以图形为主"
            ),
            metrics=metrics,
        )

    # --- 3. 乱码 / 水印 ---
    if repeat > settings.gibberish_ratio and body_len < 5000:
        return IntegrityReport(
            ok=False,
            reason=BlockReason.BLURRY_SCAN,
            detail=(
                f"单一字符占比高达 {repeat:.0%}，疑似乱码或水印覆盖正文"
            ),
            metrics=metrics,
        )

    # --- 4. 解析覆盖率（只告警不阻塞）---
    detail = ""
    if non_empty / max(total_blocks, 1) < 0.3:
        detail = f"仅 {non_empty}/{total_blocks} 个文本块有内容，解析覆盖率偏低"

    logger.debug(
        f"[完整性] doc={ir.doc_id[:8]} 正文 {body_len} 字 / {pages} 页 / "
        f"有意义占比 {meaningful:.2f} / 最高频字符 {repeat:.2f} → ok"
    )
    return IntegrityReport(ok=True, detail=detail, metrics=metrics)


def estimate_scan_quality(ir: DocIR) -> dict:
    """从 IR 里汇总 OCR 质量信息，给前端做「低置信度」提示。"""
    scores: list[float] = []
    for b in ir.blocks:
        s = (b.meta or {}).get("ocr_score")
        if isinstance(s, (int, float)):
            scores.append(float(s))
    if not scores:
        return {}
    return {
        "ocr_block_count": len(scores),
        "ocr_avg_score": round(sum(scores) / len(scores), 4),
        "ocr_low_score_blocks": sum(1 for s in scores if s < 0.7),
    }


__all__ = ["IntegrityReport", "check_integrity", "estimate_scan_quality"]
