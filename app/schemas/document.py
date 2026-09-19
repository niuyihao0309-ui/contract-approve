"""文档中间表示（IR）。

大纲「地基①」：DOCX / PDF / OCR 三种来源解析后必须归一成同一种结构，
前端高亮、后端定位全靠它。

核心约定
--------
1. ``block_id`` 是全文唯一的「地址」。风险卡片、元数据、批注一律只引用
   ``block_id + char_start/char_end``，**绝不存裸文本**。
2. ``char_start/char_end`` 是相对于 :attr:`DocIR.full_text` 的**全局**偏移，
   而不是相对 block 内部的偏移。这样跨 block 的区间也能用一个 (start, end) 表示，
   前端只要有一份 full_text 就能直接切出来做高亮。
3. ``full_text`` 由各 block 文本用 ``"\\n"`` 连接而成，连接符只占 1 个字符，
   可稳定复现。
"""

from __future__ import annotations

from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.models.enums import BlockType

#: full_text 中两个 block 之间的连接符
BLOCK_SEPARATOR = "\n"


class Block(BaseModel):
    """一个不可再分的文本块（段落 / 表格行 / 标题 / 页眉页脚）。"""

    model_config = ConfigDict(extra="ignore")

    #: 由 :meth:`DocIR.build` 统一分配，解析器不需要（也不应该）自己填
    block_id: str = Field(default="", description="全文唯一定位地址，如 b_0001")
    type: BlockType = Field(default=BlockType.PARAGRAPH, description="块类型")
    text: str = Field(default="", description="块的纯文本内容")

    # ---- 定位信息（三个维度全部要记录，大纲 2.4.4 硬性要求）----
    page: int = Field(default=1, ge=1, description="页码，从 1 开始")
    para_index: int = Field(default=0, ge=0, description="段落索引，从 0 开始")
    char_start: int = Field(default=0, ge=0, description="在 full_text 中的起始字符位置")
    char_end: int = Field(default=0, ge=0, description="在 full_text 中的结束字符位置（不含）")

    # ---- 渲染辅助（前端高亮叠加层要用）----
    bbox: list[float] | None = Field(
        default=None,
        description="页面坐标系 [x0, y0, x1, y1]，PDF/OCR 有，DOCX 通常为 None",
    )
    level: int | None = Field(default=None, ge=1, le=9, description="标题层级，仅 title 有效")

    # ---- 表格专用（表格必须按行拍平成独立 block，否则条款切不干净）----
    table_index: int | None = Field(default=None, description="该行属于第几张表，从 0 开始")
    row_index: int | None = Field(default=None, description="行号，从 0 开始")
    cells: list[str] | None = Field(default=None, description="该行各单元格原文")

    # ---- 解析器附加信息（OCR 置信度等），不参与定位 ----
    meta: dict[str, Any] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def char_len(self) -> int:
        return max(self.char_end - self.char_start, 0)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()

    def excerpt(self, limit: int = 60) -> str:
        """截断展示，日志与报告用。"""
        t = self.text.replace("\n", " ").strip()
        return t if len(t) <= limit else t[:limit] + "…"


class DocIR(BaseModel):
    """一份文档的统一中间表示。"""

    model_config = ConfigDict(extra="ignore")

    doc_id: str = Field(..., description="文档 UUID")
    source_file: str = Field(default="", description="原始文件名")
    file_kind: str = Field(default="", description="docx / pdf / image")
    parser: str = Field(default="", description="实际使用的解析器名")
    page_count: int = Field(default=1, ge=1)
    blocks: list[Block] = Field(default_factory=list)
    full_text: str = Field(default="", description="所有 block 文本按序拼接后的全文")

    # 解析过程留下的告警（不致命，但要在工作台提示）
    warnings: list[str] = Field(default_factory=list)
    # 解析器附加信息（如 OCR 平均置信度、PDF 是否走 OCR 分支）
    extra: dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------
    @classmethod
    def build(
        cls,
        *,
        doc_id: str,
        source_file: str = "",
        file_kind: str = "",
        parser: str = "",
        raw_blocks: Iterable[dict[str, Any] | Block],
        page_count: int = 1,
    ) -> "DocIR":
        """由「裸 block 列表」构建 IR：自动编号、自动算 char 偏移、自动拼 full_text。

        这是所有解析器唯一的出口。解析器只管吐出 ``{"type","text","page",
        "para_index","bbox",...}``，位置计算统一在这里做，保证三种格式完全一致。
        """
        blocks: list[Block] = []
        cursor = 0
        for idx, raw in enumerate(raw_blocks):
            b = raw if isinstance(raw, Block) else Block(**raw)
            b.block_id = f"b_{idx + 1:04d}"
            b.char_start = cursor
            cursor += len(b.text)
            b.char_end = cursor
            cursor += len(BLOCK_SEPARATOR)
            blocks.append(b)

        full_text = BLOCK_SEPARATOR.join(b.text for b in blocks)
        # 用真实拼接结果回填，防止 separator 长度假设出错
        assert all(b.char_end <= len(full_text) for b in blocks)
        return cls(
            doc_id=doc_id,
            source_file=source_file,
            file_kind=file_kind,
            parser=parser,
            page_count=page_count,
            blocks=blocks,
            full_text=full_text,
        )

    @model_validator(mode="after")
    def _check_offsets(self) -> "DocIR":
        """校验 char 偏移没有越界 —— 越界就意味着前端一定高亮错位。"""
        if not self.blocks:
            return self
        n = len(self.full_text)
        for b in self.blocks:
            if b.char_end > n:
                raise ValueError(f"block {b.block_id} 的 char_end={b.char_end} 超出全文长度 {n}")
            if b.char_end < b.char_start:
                raise ValueError(f"block {b.block_id} 的 char 区间倒置")
        return self

    # ------------------------------------------------------------------
    # 查询与定位
    # ------------------------------------------------------------------
    def get_block(self, block_id: str) -> Block | None:
        for b in self.blocks:
            if b.block_id == block_id:
                return b
        return None

    def slice_text(self, char_start: int, char_end: int, max_len: int | None = None) -> str:
        """按全局字符区间取原文。"""
        text = self.full_text[char_start:char_end]
        if max_len and len(text) > max_len:
            return text[:max_len] + "…"
        return text

    def locate(self, char_start: int, char_end: int) -> Block | None:
        """给定字符区间，反查它落在哪个 block 里。

        用于「风险项只拿到一段文字、需要补全 block_id」的场景。
        """
        for b in self.blocks:
            if b.char_start <= char_start < b.char_end:
                return b
        return None

    def locate_by_text(self, needle: str, start_at: int = 0) -> tuple[Block, int, int] | None:
        """按文本内容反查位置，返回 ``(block, char_start, char_end)``。

        LLM 有时会返回自己复述过的原文片段，用这个把偏移还原回来。
        """
        if not needle.strip():
            return None
        idx = self.full_text.find(needle, start_at)
        if idx < 0:
            # 退化策略：去掉空白后再找（应对 PDF 抽出的空格差异）
            compact = "".join(needle.split())
            if not compact:
                return None
            for b in self.blocks:
                if compact in "".join(b.text.split()):
                    return b, b.char_start, b.char_end
            return None
        blk = self.locate(idx, idx + len(needle))
        if blk is None:
            # 区间跨越多个 block 时，归属到起点所在 block
            for b in self.blocks:
                if b.char_start <= idx:
                    blk = b
        return (blk, idx, idx + len(needle)) if blk else None

    def text_of_blocks(self, block_ids: Iterable[str], joiner: str = "\n") -> str:
        """按顺序取出若干 block 的原文（用于条款内容拼装）。"""
        wanted = list(block_ids)
        idx = {b.block_id: b for b in self.blocks}
        return joiner.join(idx[i].text for i in wanted if i in idx)

    def page_of(self, block_id: str) -> int:
        b = self.get_block(block_id)
        return b.page if b else 1

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------
    @property
    def body_text(self) -> str:
        """正文文本（去掉页眉页脚）。完整性校验用这个。"""
        return "\n".join(
            b.text
            for b in self.blocks
            if b.type not in (BlockType.HEADER, BlockType.FOOTER)
        )

    @property
    def body_char_count(self) -> int:
        return len(self.body_text.strip())

    @property
    def non_empty_blocks(self) -> list[Block]:
        return [b for b in self.blocks if not b.is_empty]

    def to_storage_meta(self) -> dict[str, Any]:
        """落到 file 表里的摘要信息。"""
        return {
            "block_count": len(self.blocks),
            "non_empty_block_count": len(self.non_empty_blocks),
            "body_char_count": self.body_char_count,
            "page_count": self.page_count,
            "parser": self.parser,
            "warnings": self.warnings,
            "extra": self.extra,
        }


class SourceSpan(BaseModel):
    """「原文证据」的通用引用对象。

    风险项、元数据、条款切分结果、批注全都用它指向原文，
    保证任何结构化结果都能一键跳回原文高亮。
    """

    model_config = ConfigDict(extra="ignore")

    block_id: str = Field(..., description="主定位块的 block_id")
    char_start: int = Field(default=0, ge=0)
    char_end: int = Field(default=0, ge=0)
    page: int = Field(default=1, ge=1)
    para_index: int = Field(default=0, ge=0)
    quote: str = Field(default="", description="原文片段快照，仅作展示与人工核对，不用于定位")

    @classmethod
    def from_span(
        cls,
        ir: DocIR,
        block_id: str,
        char_start: int | None = None,
        char_end: int | None = None,
        quote_limit: int = 300,
    ) -> "SourceSpan":
        """从 IR 构造证据引用；不传区间时默认取整个 block。"""
        blk = ir.get_block(block_id)
        if blk is None:
            raise ValueError(f"block_id={block_id} 不存在于文档 {ir.doc_id}")
        cs = blk.char_start if char_start is None else char_start
        ce = blk.char_end if char_end is None else char_end
        return cls(
            block_id=block_id,
            char_start=cs,
            char_end=ce,
            page=blk.page,
            para_index=blk.para_index,
            quote=ir.slice_text(cs, ce, quote_limit),
        )

    def brief(self) -> str:
        """一句话描述定位，报告里用：``第3页 第12段``。"""
        return f"第{self.page}页 第{self.para_index + 1}段"


#: LLM 返回的定位信息可能只有页码/段落，统一用这个转换
LocatorHint = Literal["block_id", "char_range", "text_search"]


__all__ = ["Block", "DocIR", "SourceSpan", "BLOCK_SEPARATOR", "LocatorHint"]
