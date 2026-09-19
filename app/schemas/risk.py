"""风险与建议的结构化定义。

大纲 3.2 要求：LLM 输出必须用 Pydantic schema 强制约束，字段包含
``risk_level / reason / legal_basis / block_id / char_range / suggestion``。
本模块的 :class:`LLMRiskDraft` 就是那个契约。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import ClauseType, RiskLevel, RiskSource, SuggestionSource
from app.schemas.document import SourceSpan


class LLMRiskDraft(BaseModel):
    """**LLM 必须返回的单条风险结构。**

    注意：这里刻意不要求 LLM 直接给 ``block_id``，因为它看不到 block 编号；
    改为让它回 ``quote``（原文片段）+ ``page``，由 :class:`RiskItemModel` 在本侧
    用 :meth:`DocIR.locate_by_text` 反查补全定位。
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str = Field(..., description="风险项名称，如「知识产权归属供应商」")
    risk_level: RiskLevel = Field(..., description="风险等级 high/medium/low")
    clause_type: ClauseType = Field(
        default=ClauseType.OTHER, description="涉及条款类型"
    )

    quote: str = Field(
        ...,
        min_length=2,
        description="触发该风险的合同原文片段，必须**逐字复制**原文，不得改写",
    )
    page: int | None = Field(default=None, description="该片段所在页码，未知填 null")

    reason: str = Field(..., description="风险成因分析")
    legal_basis: str = Field(
        default="", description="法律合规依据，如「《民法典》第 585 条」"
    )

    suggestion: str = Field(
        default="", description="推荐的修改示范条款正文（可直接替换原文的成品文本）"
    )
    suggestion_rationale: str = Field(default="", description="为什么这么改")

    probability: float | None = Field(
        default=None, ge=0.0, le=1.0, description="发生概率估计 0-1，可选"
    )

    @field_validator("quote")
    @classmethod
    def _strip_quotes(cls, v: str) -> str:
        """LLM 常把引号一起吐出来，去掉首尾成对引号。"""
        s = v.strip()
        for pair in ('""', "''", "「」", "“”", "『』"):
            if len(s) >= 2 and s[0] == pair[0] and s[-1] == pair[1]:
                s = s[1:-1].strip()
        return s


class LLMReviewPayload(BaseModel):
    """LLM 一次审查返回的整体结构。"""

    model_config = ConfigDict(extra="ignore")

    risks: list[LLMRiskDraft] = Field(default_factory=list)
    summary: str = Field(default="", description="本次审查的整体判断，1-3 句")
    notes: str = Field(default="", description="模型对不确定处的说明")


class SuggestionModel(BaseModel):
    """修改示范条款（落库前的内存模型）。"""

    model_config = ConfigDict(extra="ignore")

    original_text: str = Field(default="")
    suggested_text: str = Field(default="")
    rationale: str = Field(default="")
    source: SuggestionSource = SuggestionSource.TEMPLATE
    template_id: str | None = None
    similarity: float | None = Field(default=None, ge=0.0, le=1.0)


class RiskItemModel(BaseModel):
    """落库前的一条完整风险项（含证据链与建议）。"""

    model_config = ConfigDict(extra="ignore")

    risk_no: int = Field(default=0)
    name: str
    risk_level: RiskLevel
    clause_type: ClauseType = ClauseType.OTHER

    source: RiskSource = RiskSource.RULE
    rule_id: str | None = None

    # 证据链：原文片段 + 位置 + 依据
    span: SourceSpan
    reason: str = ""
    legal_basis: str = ""
    tags: list[str] = Field(default_factory=list)

    probability: float | None = None
    confidence: float = 1.0

    suggestion: SuggestionModel | None = None

    @property
    def level_weight(self) -> int:
        from app.models.enums import RISK_WEIGHT

        return RISK_WEIGHT.get(self.risk_level.value, 0)

    def card(self) -> dict:
        """给工作台风险卡片用的扁平结构。"""
        return {
            "risk_no": self.risk_no,
            "name": self.name,
            "risk_level": self.risk_level.value,
            "clause_type": self.clause_type.value,
            "source": self.source.value,
            "rule_id": self.rule_id,
            "block_id": self.span.block_id,
            "char_start": self.span.char_start,
            "char_end": self.span.char_end,
            "page": self.span.page,
            "para_index": self.span.para_index,
            "quote": self.span.quote,
            "reason": self.reason,
            "legal_basis": self.legal_basis,
            "tags": self.tags,
            "suggestion": self.suggestion.model_dump(mode="json") if self.suggestion else None,
        }


class RiskAggregate(BaseModel):
    """总风险等级与结论（大纲 3.4：高>中>低，一票否决，规则写死不靠 LLM 拍脑袋）。"""

    model_config = ConfigDict(extra="ignore")

    overall_risk: RiskLevel | None = None
    conclusion: str = ""              # ReviewConclusion 值
    conclusion_desc: str = ""         # 中文结论
    summary: str = ""
    focus_points: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=lambda: {"high": 0, "medium": 0, "low": 0})
    total: int = 0

    @classmethod
    def empty(cls) -> "RiskAggregate":
        return cls(overall_risk=None, conclusion="", summary="未发现风险项")


__all__ = [
    "LLMRiskDraft",
    "LLMReviewPayload",
    "SuggestionModel",
    "RiskItemModel",
    "RiskAggregate",
]
