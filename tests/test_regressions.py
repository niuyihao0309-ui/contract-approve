"""回归测试：把上一轮端到端验收暴露出的 bug 钉住，防止回退。

对应修复：
1. ``RuleTrigger`` 枚举缺 ``CHECKER``，导致所有 checker 类规则被静默跳过。
2. ``ReviewContext`` 是 dataclass 却被当 pydantic 用（``model_copy`` 不存在）。
3. 零风险合集的摘要生成对 ``overall_risk=None`` 崩溃。
4. 回写幂等键被正文末尾的生成时间戳污染，导致永远无法命中。
5. IP 判定器把「对方原有知识产权归对方」误判为高风险。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.config import settings
from app.engines.review.aggregator import _fallback_summary, compute_aggregate, RiskAggregator
from app.engines.review.checkers import (
    ReviewContext,
    ip_unfavorable,
    unlimited_liability,
)
from app.engines.review.rule_engine import RuleEngine
from app.models.enums import ClauseType, RuleTrigger
from app.schemas.document import DocIR
from app.schemas.metadata import Clause, ClauseSegmentResult, MetadataResult
from app.services.report_service import build_writeback_markdown


# ======================================================================
# 夹具
# ======================================================================
def _ir(*texts: str) -> DocIR:
    return DocIR.build(doc_id="test-doc", raw_blocks=[{"text": t} for t in texts])


def _ctx_with_clause(ir: DocIR, ctype: ClauseType, block_idx: int) -> ReviewContext:
    """把第 ``block_idx`` 个 block 当成一条条款，构造审查上下文。"""
    blk = ir.blocks[block_idx]
    clause = Clause(
        clause_type=ctype,
        title=f"测试条款 {ctype.value}",
        content=blk.text,
        block_ids=[blk.block_id],
        char_start=blk.char_start,
        char_end=blk.char_end,
    )
    seg = ClauseSegmentResult(clauses=[clause])
    return ReviewContext(
        ir=ir,
        clauses=seg,
        metadata=MetadataResult(),
        contract_type="purchase",
        our_side="party_a",
    )


# ======================================================================
# 1 + 2：checker 类规则必须真正执行，而不是被静默跳过
# ======================================================================
def test_ruletrigger_has_checker():
    assert RuleTrigger.CHECKER.value == "checker"


def test_review_context_is_dataclass_not_pydantic():
    ctx = _ctx_with_clause(_ir("甲方采购软件一套。"), ClauseType.IP, 0)
    # 当年这里写成了 ctx.model_copy(...)，dataclass 根本没有这个方法
    assert not hasattr(ctx, "model_copy")
    c2 = replace(ctx, params={"our_side": "party_a"})
    assert c2.params == {"our_side": "party_a"}


def test_rule_engine_checker_trigger_fires():
    """规则引擎应能跑出 checker 类规则的风险（曾因 model_copy 全部被跳过）。"""
    ir = _ir(
        "第一条 标的：甲方向乙方采购软件一套。",
        "第三条 知识产权：本合同项下软件产品及全部技术资料、源代码、衍生开发成果的"
        "知识产权，均归乙方所有。",
    )
    ctx = _ctx_with_clause(ir, ClauseType.IP, 1)
    engine = RuleEngine.from_file(settings.rules_file)
    risks = engine.run(ctx)
    ids = {r.rule_id for r in risks}
    assert "R-IP-001" in ids, f"R-IP-001 未命中，实际命中 {ids}"


def test_unlimited_liability_hits():
    ir = _ir("第四条 违约责任：甲方应赔偿乙方全部损失，赔偿责任不设上限。")
    ctx = _ctx_with_clause(ir, ClauseType.BREACH, 0)
    hits = unlimited_liability(ctx)
    assert hits


# ======================================================================
# 3：零风险合集不崩溃
# ======================================================================
def test_zero_risk_aggregate_does_not_crash():
    agg = compute_aggregate([])
    assert agg.overall_risk is None
    assert agg.conclusion == "pass"
    summary, focus = _fallback_summary(agg, [])
    assert summary
    assert isinstance(focus, list)


@pytest.mark.asyncio
async def test_zero_risk_aggregator_public_path():
    agg = await RiskAggregator().aggregate([], use_llm=False)
    assert agg.overall_risk is None
    assert agg.conclusion == "pass"
    assert agg.summary


# ======================================================================
# 4：回写幂等依据不含生成时间戳
# ======================================================================
def test_writeback_digest_basis_is_stable():
    detail = {
        "title": "软件采购合同",
        "risk_counts": {"high": 0, "medium": 0, "low": 0},
        "overall_risk": None,
        "conclusion": "pass",
        "conclusion_label": "通过",
    }
    notes = "法务批注：按建议修改后再签。"
    basis = build_writeback_markdown(detail, [], lawyer_notes=notes, with_timestamp=False)
    md = build_writeback_markdown(detail, [], lawyer_notes=notes, with_timestamp=True)

    # 时间戳只出现在「给人看」的正文里，幂等依据里绝不能有
    assert "本意见由合同审批智能审查系统于" in md
    assert "本意见由合同审批智能审查系统于" not in basis
    # 幂等依据可复现
    assert basis == build_writeback_markdown(detail, [], lawyer_notes=notes, with_timestamp=False)


# ======================================================================
# 5：IP 判定器不误伤「对方原有知识产权归对方」
# ======================================================================
def test_ip_checker_ignores_preexisting_ip():
    # 标准写法：对方原有 IP 归对方、我方定制成果归我方 → 不应报风险
    ok_text = (
        "乙方软件产品原有的知识产权归乙方所有；甲方基于本合同需求定制的功能模块、"
        "以及实施过程中形成的衍生开发成果，其知识产权归甲方所有。"
    )
    assert ip_unfavorable(_ctx_with_clause(_ir(ok_text), ClauseType.IP, 0)) == []


def test_ip_checker_flags_transferred_ip():
    # 风险写法：定制成果无条件归对方 → 应报风险
    risky_text = "本合同项下软件产品及衍生开发成果的知识产权，均归乙方所有。"
    hits = ip_unfavorable(_ctx_with_clause(_ir(risky_text), ClauseType.IP, 0))
    assert hits
