"""规则与标准条款库的结构化定义。

``app/rules/*.json`` 是规则的唯一来源，启动时同步入库；
DB 只是运行期副本，方便做「规则开关」与前端展示。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import ClauseType, RiskLevel, RuleTrigger


class ReviewRuleModel(BaseModel):
    """一条审查规则。"""

    model_config = ConfigDict(extra="ignore")

    rule_id: str = Field(..., description="规则编号，如 R-BREACH-001")
    name: str
    category: str = "general"
    description: str = ""

    contract_types: list[str] = Field(
        default_factory=list, description="适用的合同类型；空表示全部适用"
    )
    clause_types: list[str] = Field(
        default_factory=list, description="作用范围（条款类型）；空表示全文搜索"
    )

    risk_level: RiskLevel = RiskLevel.MEDIUM
    trigger: RuleTrigger = RuleTrigger.KEYWORD

    #: trigger=keyword / regex / forbidden 时生效
    patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list, description="命中即排除，用于降噪")

    #: trigger=checker 时生效
    checker: str | None = None
    params: dict = Field(default_factory=dict)

    reason: str = ""
    legal_basis: str = ""
    suggestion_template: str | None = None
    tags: list[str] = Field(default_factory=list)

    enabled: bool = True
    priority: int = 100

    @field_validator("clause_types")
    @classmethod
    def _check_clause_types(cls, v: list[str]) -> list[str]:
        valid = {t.value for t in ClauseType}
        bad = [t for t in v if t not in valid]
        if bad:
            raise ValueError(f"未知的 clause_type：{bad}，可选 {sorted(valid)}")
        return v

    def applies_to(self, contract_type: str) -> bool:
        """规则是否适用于该合同类型。"""
        if not self.contract_types:
            return True
        return contract_type in self.contract_types


class ReviewRuleSet(BaseModel):
    """规则集（对应一个 JSON 文件）。"""

    model_config = ConfigDict(extra="ignore")

    version: str = "1.0"
    description: str = ""
    updated_at: str = ""
    rules: list[ReviewRuleModel] = Field(default_factory=list)

    def active_rules(self, contract_type: str) -> list[ReviewRuleModel]:
        """取出适用于该合同类型的启用规则，按优先级排序。"""
        return sorted(
            (r for r in self.rules if r.enabled and r.applies_to(contract_type)),
            key=lambda r: r.priority,
        )

    @property
    def rule_ids(self) -> list[str]:
        return [r.rule_id for r in self.rules]


class ClauseTemplateModel(BaseModel):
    """标准示范条款库中的一条。"""

    model_config = ConfigDict(extra="ignore")

    template_id: str
    title: str
    clause_type: ClauseType = ClauseType.OTHER
    contract_types: list[str] = Field(default_factory=list)
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    scenario: str = ""
    risk_level: RiskLevel = RiskLevel.MEDIUM
    legal_basis: str = ""
    version: str = "1.0"
    enabled: bool = True

    def applies_to(self, contract_type: str) -> bool:
        return not self.contract_types or contract_type in self.contract_types


class ClauseTemplateLibrary(BaseModel):
    """标准示范条款库。"""

    model_config = ConfigDict(extra="ignore")

    version: str = "1.0"
    description: str = ""
    templates: list[ClauseTemplateModel] = Field(default_factory=list)


__all__ = [
    "ReviewRuleModel",
    "ReviewRuleSet",
    "ClauseTemplateModel",
    "ClauseTemplateLibrary",
]
