"""风险聚合：总风险等级、审查结论、摘要与关注要点。

大纲 3.4 明确要求：**总风险等级「高 > 中 > 低，一票否决」的规则要写进文档，
不要靠 LLM 拍脑袋。** 所以等级与结论完全由这里的确定性逻辑算出；
LLM 只负责把结论「翻译」成一段人能读的话（摘要与关注要点），
而且 LLM 挂了就退回模板化文本，不阻塞流程。
"""

from __future__ import annotations

from app.core.logging import logger
from app.llm.client import LLMClient, get_llm
from app.models.enums import (
    CLAUSE_TYPE_LABEL,
    CONCLUSION_LABEL,
    RISK_LEVEL_LABEL,
    RiskLevel,
    ReviewConclusion,
)
from app.schemas.risk import RiskAggregate, RiskItemModel

#: 「一票否决」级规则：命中一条就建议拒绝，不需要攒够两条
VETO_RULES: set[str] = {
    "R-BREACH-001",   # 赔偿无上限，责任敞口不受控
    "R-IP-001",       # 核心知识产权无条件归对方
    "R-PARTY-001",    # 主体信息缺失，无法核验资质
}

#: 判定为「建议拒绝」需要的高风险条数
REJECT_HIGH_THRESHOLD = 2


def compute_aggregate(risks: list[RiskItemModel]) -> RiskAggregate:
    """纯计算：由风险清单得出总等级与结论。不调用任何外部服务。"""
    counts = {"high": 0, "medium": 0, "low": 0}
    for r in risks:
        counts[r.risk_level.value] = counts.get(r.risk_level.value, 0) + 1

    agg = RiskAggregate(
        counts=counts,
        total=len(risks),
        focus_points=[],
    )

    if not risks:
        agg.overall_risk = None
        agg.conclusion = ReviewConclusion.PASS.value
        agg.conclusion_desc = CONCLUSION_LABEL[agg.conclusion]
        agg.summary = "未发现需关注的合规与商业风险，合同条款结构完整、权责基本对等，可按现有文本提交审批。"
        agg.focus_points = ["按审批流正常推进签署流程"]
        return agg

    # ---- 一票否决：高 > 中 > 低 ----
    if counts["high"] > 0:
        agg.overall_risk = RiskLevel.HIGH
        veto_hit = [r for r in risks if r.rule_id and r.rule_id in VETO_RULES]
        if counts["high"] >= REJECT_HIGH_THRESHOLD or veto_hit:
            agg.conclusion = ReviewConclusion.REJECT.value
        else:
            agg.conclusion = ReviewConclusion.RECTIFY.value
    elif counts["medium"] > 0:
        agg.overall_risk = RiskLevel.MEDIUM
        agg.conclusion = ReviewConclusion.RECTIFY.value
    else:
        agg.overall_risk = RiskLevel.LOW
        agg.conclusion = ReviewConclusion.PASS.value

    agg.conclusion_desc = CONCLUSION_LABEL[agg.conclusion]
    logger.info(
        f"[风险聚合] 总等级={RISK_LEVEL_LABEL[agg.overall_risk.value]} "
        f"结论={agg.conclusion_desc}（高{counts['high']}/中{counts['medium']}/低{counts['low']}）"
    )
    return agg


def _fallback_summary(agg: RiskAggregate, risks: list[RiskItemModel]) -> tuple[str, list[str]]:
    """LLM 不可用时的模板化摘要与关注要点。"""
    # 零风险时 compute_aggregate 已经把摘要写好了，而且 overall_risk 是 None，
    # 下面几处 RISK_LEVEL_LABEL[agg.overall_risk.value] 会直接抛 AttributeError。
    # 这里提前返回，别去碰 None。
    if not risks or agg.overall_risk is None:
        return agg.summary, list(agg.focus_points)

    level_label = RISK_LEVEL_LABEL[agg.overall_risk.value]
    # StrEnum 成员是单例，但这里用 == 更稳：万一 risk_level 是从字符串反序列化出来的
    # 也不会因为对象身份不同而漏判。
    highs = [r for r in risks if r.risk_level == RiskLevel.HIGH]
    names = "、".join(r.name for r in highs[:3]) or "无"

    if agg.conclusion == ReviewConclusion.REJECT.value:
        summary = (
            f"本合同综合风险等级为{level_label}，"
            f"共识别 {agg.total} 项风险，其中高风险 {agg.counts['high']} 项。"
            f"主要问题集中在：{names}。"
            f"上述条款使我方承担的责任敞口不受控或核心权利丧失，建议退回业务部门与对方重新谈判后再行提交。"
        )
    elif agg.conclusion == ReviewConclusion.RECTIFY.value:
        summary = (
            f"本合同综合风险等级为{level_label}，"
            f"共识别 {agg.total} 项风险（高 {agg.counts['high']} / 中 {agg.counts['medium']} / 低 {agg.counts['low']}）。"
            f"主要问题：{names}。建议按审查意见逐条整改后再行签署。"
        )
    else:
        summary = (
            f"本合同综合风险等级为{level_label}，"
            f"仅识别到 {agg.total} 项轻微提示性问题，不影响签署。"
        )

    focus: list[str] = []
    for r in risks[:6]:
        label = CLAUSE_TYPE_LABEL.get(r.clause_type.value, "")
        focus.append(
            f"【{RISK_LEVEL_LABEL[r.risk_level.value]}·{label}】{r.name}"
            + (f"：{r.span.brief()}" if r.span else "")
        )
    return summary, focus


class RiskAggregator:
    """聚合器：算出等级与结论，再（可选）让 LLM 把结论写成一段人能读的话。"""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or get_llm()

    async def aggregate(
        self,
        risks: list[RiskItemModel],
        *,
        use_llm: bool = True,
        title: str = "",
        contract_type: str = "other",
        counterparty: str = "",
        amount: str = "",
        currency: str = "CNY",
        our_side: str = "甲方",
        lawyer_notes: str = "（暂无）",
    ) -> RiskAggregate:
        agg = compute_aggregate(risks)          # ← 等级与结论：确定性计算
        fb_summary, fb_focus = _fallback_summary(agg, risks)

        if not use_llm or not self.llm.enabled:
            agg.summary, agg.focus_points = fb_summary, fb_focus
            return agg

        out = await self._llm_summarize(
            risks,
            agg,
            title=title,
            contract_type=contract_type,
            counterparty=counterparty,
            amount=amount,
            currency=currency,
            our_side=our_side,
            lawyer_notes=lawyer_notes,
        )
        if out is None:
            agg.summary, agg.focus_points = fb_summary, fb_focus
        else:
            summary, focus = out
            agg.summary = summary or fb_summary
            agg.focus_points = focus or fb_focus
        return agg

    # ------------------------------------------------------------------
    async def _llm_summarize(
        self,
        risks: list[RiskItemModel],
        agg: RiskAggregate,
        **kw,
    ) -> tuple[str, list[str]] | None:
        from pydantic import BaseModel, Field

        class _Summary(BaseModel):
            summary: str = ""
            focus_points: list[str] = Field(default_factory=list)

        risk_list = "\n".join(
            f"{i}. [{RISK_LEVEL_LABEL[r.risk_level.value]}] {r.name}"
            f"（{CLAUSE_TYPE_LABEL.get(r.clause_type.value, '')}）\n"
            f"   原文位置：{r.span.brief()}\n"
            f"   成因：{r.reason[:180]}\n"
            f"   依据：{r.legal_basis[:120]}"
            for i, r in enumerate(risks, 1)
        )

        out = await self.llm.complete_typed(
            prompt_name="review_summary",
            variables={
                "title": kw.get("title", ""),
                "contract_type": kw.get("contract_type", ""),
                "counterparty": kw.get("counterparty", ""),
                "amount": kw.get("amount", ""),
                "currency": kw.get("currency", ""),
                "our_side": kw.get("our_side", "甲方"),
                "risk_list": risk_list or "（无）",
                "lawyer_notes": kw.get("lawyer_notes", "（暂无）"),
            },
            schema=_Summary,
            temperature=0.2,
        )
        if out is None:
            return None

        # 摘要里强制带上算出来的结论，不允许 LLM 改动定性
        prefix = (
            f"【综合结论：{RISK_LEVEL_LABEL[agg.overall_risk.value] if agg.overall_risk else '无风险'} · "
            f"{agg.conclusion_desc}】"
        )
        return prefix + (out.summary or ""), (out.focus_points or [])[:6]


__all__ = ["RiskAggregator", "compute_aggregate", "VETO_RULES", "REJECT_HIGH_THRESHOLD"]
