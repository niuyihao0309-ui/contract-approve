"""规则引擎。

流程
----
1. 载入 JSON 规则集 → 按合同类型过滤 → 按优先级排序。
2. 逐条规则执行：

   - ``trigger=checker``      → 交给 :mod:`app.engines.review.checkers` 里的判定器
   - ``trigger=keyword``      → 关键词在作用范围内的全文匹配
   - ``trigger=regex``        → 正则匹配
   - ``trigger=forbidden``    → 与 regex 同义，语义上表示「禁止出现的表述」
   - ``trigger=required``     → 必备条款存在性检查
   - ``trigger=threshold``    → 交给 checker（数值判断必须写 Python）

3. 每条命中都要产出**风险证据链三要素**：原文片段 + 位置 + 依据。
4. ``exclude_patterns`` 做降噪：命中点前后一个窗口内出现排除词就丢弃。
5. 同一规则最多产出 ``MAX_HITS_PER_RULE`` 条，避免一条规则刷屏。
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

from app.core.exceptions import AppError
from app.core.logging import logger
from app.engines.review.checkers import CHECKERS, CheckerHit, ReviewContext
from app.models.enums import (
    RISK_LEVEL_LABEL,
    ClauseType,
    RiskLevel,
    RiskSource,
    RuleTrigger,
)
from app.schemas.document import SourceSpan
from app.schemas.risk import RiskItemModel, SuggestionModel
from app.schemas.rule import ReviewRuleModel, ReviewRuleSet

#: 同一条规则最多产出几条风险（防止长合同里一条规则命中几十次）
MAX_HITS_PER_RULE = 2
#: 排除词检查窗口（命中点前后各取多少字符）
EXCLUDE_WINDOW = 120


class RuleEngine:
    """基于 JSON 规则的确定性审查引擎。"""

    def __init__(self, ruleset: ReviewRuleSet) -> None:
        self.ruleset = ruleset

    # ------------------------------------------------------------------
    @classmethod
    def from_file(cls, path: Path | str) -> "RuleEngine":
        path = Path(path)
        if not path.exists():
            raise AppError(f"规则文件不存在：{path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AppError(f"规则文件 JSON 解析失败：{path} — {exc}") from exc
        return cls(ReviewRuleSet.model_validate(raw))

    # ------------------------------------------------------------------
    def run(self, ctx: ReviewContext) -> list[RiskItemModel]:
        """执行全部适用规则，返回风险项（不含序号，由调用方统一编号）。"""
        rules = self.ruleset.active_rules(ctx.contract_type)
        results: list[RiskItemModel] = []

        for rule in rules:
            try:
                hits = self._eval_rule(rule, ctx)
            except Exception as exc:
                logger.warning(f"[规则引擎] 规则 {rule.rule_id} 执行异常，已跳过：{exc}")
                continue
            for hit in hits:
                item = self._to_risk_item(rule, hit, ctx)
                if item is not None:
                    results.append(item)

        logger.info(
            f"[规则引擎] 合同类型={ctx.contract_type} 适用 {len(rules)} 条规则 → "
            f"命中 {len(results)} 条风险"
            + (f"（{', '.join(sorted({r.rule_id for r in results if r.rule_id}))}）" if results else "")
        )
        return results

    # ------------------------------------------------------------------
    # 单条规则求值
    # ------------------------------------------------------------------
    def _eval_rule(self, rule: ReviewRuleModel, ctx: ReviewContext) -> list[CheckerHit]:
        trigger = rule.trigger

        if trigger in (
            RuleTrigger.CHECKER,
            RuleTrigger.REQUIRED,
            RuleTrigger.THRESHOLD,
        ):
            name = rule.checker or _DEFAULT_CHECKER_BY_TRIGGER.get(trigger.value)
            if name is None:
                logger.warning(f"[规则引擎] 规则 {rule.rule_id} 声明了 {trigger} 但没给 checker，跳过")
                return []
            fn = CHECKERS.get(name)
            if fn is None:
                logger.warning(
                    f"[规则引擎] 规则 {rule.rule_id} 引用了不存在的 checker「{name}」，"
                    f"可用：{sorted(CHECKERS)}"
                )
                return []
            # checker 需要规则的 params。
            # 注意 ReviewContext 是 dataclass，不是 pydantic BaseModel，
            # 所以用 dataclasses.replace 复制（它没有 model_copy）。
            ctx = replace(ctx, params=rule.params)
            return fn(ctx)[:MAX_HITS_PER_RULE]

        # --- 模式匹配类 ---
        patterns = rule.patterns
        if not patterns:
            return []

        hits: list[CheckerHit] = []
        for raw in patterns:
            try:
                pat = _compile(raw, keyword=trigger == RuleTrigger.KEYWORD)
            except re.error as exc:
                logger.warning(f"[规则引擎] 规则 {rule.rule_id} 的正则非法：{raw} — {exc}")
                continue
            for hit in self._search(ctx, rule, pat):
                if any(abs(hit.char_start - h.char_start) < 5 for h in hits):
                    continue
                hits.append(hit)
                if len(hits) >= MAX_HITS_PER_RULE:
                    return hits
        return hits

    def _search(
        self, ctx: ReviewContext, rule: ReviewRuleModel, pat: re.Pattern[str]
    ) -> list[CheckerHit]:
        """在规则声明的作用范围内搜索。"""
        from app.engines.review.checkers import _sentence_span  # 复用句级扩展

        # 作用范围：声明了 clause_types 就只在这些条款里找，否则全文
        spans = ctx.clause_spans(*rule.clause_types) if rule.clause_types else []
        if not spans:
            spans = [(0, len(ctx.ir.full_text))]

        exclude = [re.compile(p) for p in rule.exclude_patterns]
        out: list[CheckerHit] = []
        for s, e in spans:
            seg = ctx.ir.full_text[s:e]
            for m in pat.finditer(seg):
                abs_start, abs_end = s + m.start(), s + m.end()
                if self._excluded(ctx, abs_start, abs_end, exclude):
                    continue
                ss, se = _sentence_span(ctx.ir, abs_start, abs_end)
                out.append(CheckerHit(ss, se))
        return out

    @staticmethod
    def _excluded(
        ctx: ReviewContext, start: int, end: int, exclude: list[re.Pattern[str]]
    ) -> bool:
        """命中点附近的窗口里出现排除词 → 视为误报。"""
        if not exclude:
            return False
        lo = max(0, start - EXCLUDE_WINDOW)
        hi = min(len(ctx.ir.full_text), end + EXCLUDE_WINDOW)
        window = ctx.ir.full_text[lo:hi]
        return any(p.search(window) for p in exclude)

    # ------------------------------------------------------------------
    @staticmethod
    def _to_risk_item(
        rule: ReviewRuleModel, hit: CheckerHit, ctx: ReviewContext
    ) -> RiskItemModel | None:
        """把一次命中转成完整的风险项（补齐定位与依据）。"""
        span = _span_from_hit(ctx, hit)
        if span is None:
            logger.debug(f"[规则引擎] 规则 {rule.rule_id} 命中点无法定位，丢弃")
            return None

        reason = rule.reason or rule.description
        if hit.detail:
            reason = f"{reason}（具体情形：{hit.detail}）" if reason else hit.detail

        tags = list(rule.tags)
        if hit.document_level:
            tags.append("document_level")

        clause_type = (
            ClauseType(rule.clause_types[0]) if rule.clause_types
            and rule.clause_types[0] in {t.value for t in ClauseType}
            else ClauseType.OTHER
        )

        return RiskItemModel(
            name=rule.name,
            risk_level=rule.risk_level,
            clause_type=clause_type,
            source=RiskSource.RULE,
            rule_id=rule.rule_id,
            span=span,
            reason=reason,
            legal_basis=rule.legal_basis,
            tags=tags,
            confidence=1.0,
            suggestion=SuggestionModel(
                original_text=span.quote,
                suggested_text=rule.suggestion_template or "",
                rationale=f"依据规则「{rule.rule_id} {rule.name}」给出的标准示范条款",
                source="template",
            )
            if rule.suggestion_template
            else None,
        )


# ======================================================================
def _compile(pattern: str, *, keyword: bool) -> re.Pattern[str]:
    """关键词模式按字面量转义，正则模式原样编译。"""
    return re.compile(re.escape(pattern) if keyword else pattern)


def _span_from_hit(ctx: ReviewContext, hit: CheckerHit) -> SourceSpan | None:
    """由字符区间构造带 block_id 的证据引用。"""
    ir = ctx.ir
    start, end = hit.char_start, hit.char_end
    n = len(ir.full_text)
    if not (0 <= start < n):
        return None
    end = min(max(end, start + 1), n)

    blk = ir.locate(start, end)
    if blk is None:
        # 全文级命中：起点可能在连接符上，向后找最近的 block
        for b in ir.blocks:
            if b.char_start >= start:
                blk = b
                start, end = b.char_start, b.char_end
                break
    if blk is None:
        return None

    quote = ir.slice_text(start, end, 300)
    if hit.document_level:
        quote = f"（全文性风险）{hit.detail}" if hit.detail else "（全文性风险）"

    return SourceSpan(
        block_id=blk.block_id,
        char_start=start,
        char_end=end,
        page=blk.page,
        para_index=blk.para_index,
        quote=quote,
    )


#: trigger 到默认 checker 的映射
_DEFAULT_CHECKER_BY_TRIGGER: dict[str, str] = {
    RuleTrigger.REQUIRED.value: "required_clause_missing",
    RuleTrigger.THRESHOLD.value: "penalty_ratio_out_of_range",
}


def aggregate_level(items: list[RiskItemModel]) -> RiskLevel | None:
    """取最高等级（一票否决）。"""
    if not items:
        return None
    order = {RiskLevel.HIGH: 3, RiskLevel.MEDIUM: 2, RiskLevel.LOW: 1}
    return max((i.risk_level for i in items), key=lambda lv: order[lv])


__all__ = ["RuleEngine", "MAX_HITS_PER_RULE", "aggregate_level"]
