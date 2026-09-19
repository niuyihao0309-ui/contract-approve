"""元数据与条款切分的结构化定义。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ClauseType, MetadataField
from app.schemas.document import SourceSpan


class MetadataItem(BaseModel):
    """一条核心元数据，必带原文定位。"""

    model_config = ConfigDict(extra="ignore")

    field_key: MetadataField = Field(..., description="字段名")
    field_value: str | None = Field(default=None, description="原始值（保留原文格式）")
    normalized_value: str | None = Field(default=None, description="归一化值，如金额去逗号、日期标准化")

    span: SourceSpan | None = Field(default=None, description="原文定位；提取失败时为 None")
    source: str = Field(default="regex", description="regex / llm")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @property
    def label(self) -> str:
        from app.models.enums import METADATA_FIELD_LABEL

        return METADATA_FIELD_LABEL.get(self.field_key.value, self.field_key.value)

    @property
    def is_missing(self) -> bool:
        return not (self.field_value or "").strip()


class MetadataResult(BaseModel):
    """一份文档的元数据抽取结果。"""

    model_config = ConfigDict(extra="ignore")

    items: list[MetadataItem] = Field(default_factory=list)

    def get(self, key: MetadataField | str) -> MetadataItem | None:
        k = key.value if isinstance(key, MetadataField) else key
        for it in self.items:
            if it.field_key.value == k:
                return it
        return None

    def value_of(self, key: MetadataField | str) -> str | None:
        it = self.get(key)
        return it.field_value if it else None

    @property
    def missing_fields(self) -> list[MetadataField]:
        """缺失（值为空）的字段列表。规则引擎「主体信息缺失 → 高风险」靠这个。"""
        return [it.field_key for it in self.items if it.is_missing]

    def to_map(self) -> dict[str, str]:
        return {
            it.field_key.value: it.field_value
            for it in self.items
            if it.field_value
        }


class Clause(BaseModel):
    """一个切分出来的条款。"""

    model_config = ConfigDict(extra="ignore")

    clause_type: ClauseType = Field(default=ClauseType.OTHER)
    title: str = Field(default="", description="条款标题，如「第六条 违约责任」")
    content: str = Field(default="", description="条款正文全量文本")

    # 覆盖的 block 区间 —— 切分结果必须能「指回原文」
    block_ids: list[str] = Field(default_factory=list)
    char_start: int = Field(default=0)
    char_end: int = Field(default=0)
    page_start: int = Field(default=1)
    page_end: int = Field(default=1)
    para_start: int = Field(default=0)

    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    method: str = Field(default="rule", description="rule / llm")

    @property
    def label(self) -> str:
        from app.models.enums import CLAUSE_TYPE_LABEL

        return CLAUSE_TYPE_LABEL.get(self.clause_type.value, self.clause_type.value)

    @property
    def head_block_id(self) -> str:
        return self.block_ids[0] if self.block_ids else ""

    @property
    def brief(self) -> str:
        t = self.content.replace("\n", " ").strip()
        return t[:80] + ("…" if len(t) > 80 else "")


class ClauseSegmentResult(BaseModel):
    """条款切分总结果。"""

    model_config = ConfigDict(extra="ignore")

    clauses: list[Clause] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def by_type(self, ctype: ClauseType) -> list[Clause]:
        return [c for c in self.clauses if c.clause_type == ctype]

    def has(self, ctype: ClauseType) -> bool:
        """必备条款是否存在，用于「必备条款缺失」判定。"""
        return any(c.clause_type == ctype for c in self.clauses)

    def joined(self, ctype: ClauseType, limit: int = 2000) -> str:
        parts = [c.content for c in self.by_type(ctype)]
        return "\n".join(parts)[:limit]


__all__ = ["MetadataItem", "MetadataResult", "Clause", "ClauseSegmentResult"]
