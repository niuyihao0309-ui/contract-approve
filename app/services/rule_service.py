"""规则与标准条款库服务。

``app/rules/*.json`` 是**唯一事实来源**，DB 里存的是运行期副本：

- 启动时 :meth:`RuleService.sync_from_files` 把 JSON 同步入库（按业务编号 upsert，
  已存在则更新内容，保留法务在库里改过的 `enabled` 开关）；
- 引擎从 DB 读规则（这样前端改开关就能即时生效），DB 读不到再退回 JSON 文件。

这样既保证规则可版本化、可 review（JSON 进 git），又保证运行期可配置。
"""

from __future__ import annotations

import json
import uuid
from enum import Enum
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import logger
from app.db.base import session_scope
from app.engines.recommend.recommender import ClauseLibrary
from app.engines.review.rule_engine import RuleEngine
from app.models.enums import (
    CLAUSE_TYPE_LABEL,
    CONTRACT_TYPE_LABEL,
    RISK_LEVEL_LABEL,
    ClauseType,
    RiskLevel,
    RuleTrigger,
)
from app.models.tables import ClauseTemplate, ReviewRule
from app.schemas.rule import (
    ClauseTemplateLibrary,
    ClauseTemplateModel,
    ReviewRuleModel,
    ReviewRuleSet,
)


class RuleService:
    """规则的加载、同步与查询。"""

    # ------------------------------------------------------------------
    # 从文件加载
    # ------------------------------------------------------------------
    @staticmethod
    def load_ruleset_from_file() -> ReviewRuleSet:
        path = settings.rules_file
        if not path.exists():
            raise AppError(f"规则文件不存在：{path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AppError(f"规则文件 JSON 解析失败：{path} — {exc}") from exc
        return ReviewRuleSet.model_validate(raw)

    @staticmethod
    def load_library_from_file() -> ClauseTemplateLibrary:
        path = settings.templates_file
        if not path.exists():
            raise AppError(f"标准条款库文件不存在：{path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AppError(f"标准条款库 JSON 解析失败：{path} — {exc}") from exc
        return ClauseTemplateLibrary.model_validate(raw)

    # ------------------------------------------------------------------
    # 同步入库
    # ------------------------------------------------------------------
    @staticmethod
    async def sync_from_files() -> dict[str, int]:
        """把两个 JSON 同步进数据库。幂等。"""
        ruleset = RuleService.load_ruleset_from_file()
        library = RuleService.load_library_from_file()

        async with session_scope() as session:
            n_rules = await RuleService._sync_rules(session, ruleset)
            n_tpl = await RuleService._sync_templates(session, library)

        logger.info(f"[规则] 同步完成：{n_rules} 条规则、{n_tpl} 条示范条款")
        return {"rules": n_rules, "templates": n_tpl}

    @staticmethod
    async def _sync_rules(session: AsyncSession, ruleset: ReviewRuleSet) -> int:
        existing = {
            r.rule_id: r
            for r in (await session.execute(select(ReviewRule))).scalars().all()
        }
        n = 0
        for r in ruleset.rules:
            row = existing.get(r.rule_id)
            if row is None:
                row = ReviewRule(id=str(uuid.uuid4()), rule_id=r.rule_id)
                session.add(row)
            # 内容以文件为准；但 enabled 开关保留库里的值（法务可能在界面上关过）
            row.name = r.name
            row.category = r.category
            row.description = r.description
            row.contract_types = r.contract_types
            row.clause_types = r.clause_types
            row.risk_level = r.risk_level.value
            row.trigger = r.trigger.value
            row.patterns = r.patterns
            row.exclude_patterns = r.exclude_patterns
            row.checker = r.checker
            row.params = r.params
            row.reason = r.reason
            row.legal_basis = r.legal_basis
            row.suggestion_template = r.suggestion_template
            row.tags = r.tags
            row.priority = r.priority
            if row.created_at is None:
                row.enabled = r.enabled
            n += 1
        await session.flush()
        return n

    @staticmethod
    async def _sync_templates(session: AsyncSession, library: ClauseTemplateLibrary) -> int:
        existing = {
            t.template_id: t
            for t in (await session.execute(select(ClauseTemplate))).scalars().all()
        }
        n = 0
        for t in library.templates:
            row = existing.get(t.template_id)
            if row is None:
                row = ClauseTemplate(id=str(uuid.uuid4()), template_id=t.template_id)
                session.add(row)
                row.enabled = t.enabled
            row.title = t.title
            row.clause_type = t.clause_type.value
            row.contract_types = t.contract_types
            row.content = t.content
            row.tags = t.tags
            row.keywords = t.keywords
            row.scenario = t.scenario
            row.risk_level = t.risk_level.value
            row.legal_basis = t.legal_basis
            row.version = t.version
            n += 1
        await session.flush()
        return n

    # ------------------------------------------------------------------
    # 从 DB 加载（运行期）
    # ------------------------------------------------------------------
    @staticmethod
    async def load_ruleset() -> ReviewRuleSet:
        """优先从库读（能反映界面上的开关）；库空或失败就退回 JSON。"""
        try:
            async with session_scope() as session:
                rows = list((await session.execute(select(ReviewRule))).scalars().all())
            if rows:
                return ReviewRuleSet(
                    version="db",
                    description="来自数据库的运行期规则",
                    rules=[_rule_from_row(r) for r in rows],
                )
        except Exception as exc:
            logger.warning(f"[规则] 从数据库读取失败，回退到 JSON：{type(exc).__name__}: {exc}")
        return RuleService.load_ruleset_from_file()

    @staticmethod
    async def load_library() -> ClauseTemplateLibrary:
        try:
            async with session_scope() as session:
                rows = list(
                    (await session.execute(select(ClauseTemplate))).scalars().all()
                )
            if rows:
                return ClauseTemplateLibrary(
                    version="db",
                    description="来自数据库的运行期示范条款库",
                    templates=[_template_from_row(t) for t in rows],
                )
        except Exception as exc:
            logger.warning(f"[规则] 示范条款库读取失败，回退到 JSON：{type(exc).__name__}: {exc}")
        return RuleService.load_library_from_file()

    @staticmethod
    async def build_engine() -> RuleEngine:
        return RuleEngine(await RuleService.load_ruleset())

    @staticmethod
    async def build_library() -> ClauseLibrary:
        return ClauseLibrary(await RuleService.load_library())

    # ------------------------------------------------------------------
    # 规则管理（给规则配置页用）
    # ------------------------------------------------------------------
    @staticmethod
    async def list_rules(
        session: AsyncSession, *, enabled: bool | None = None, contract_type: str | None = None
    ) -> list[ReviewRule]:
        stmt = select(ReviewRule).order_by(ReviewRule.priority)
        if enabled is not None:
            stmt = stmt.where(ReviewRule.enabled.is_(enabled))
        rows = list((await session.execute(stmt)).scalars().all())
        if contract_type:
            rows = [r for r in rows if not r.contract_types or contract_type in r.contract_types]
        return rows

    @staticmethod
    async def set_rule_enabled(
        session: AsyncSession, rule_id: str, enabled: bool
    ) -> ReviewRule:
        row = await RuleService.get_rule(session, rule_id)
        row.enabled = enabled
        await session.flush()
        logger.info(f"[规则] {rule_id} → {'启用' if enabled else '停用'}")
        return row

    @staticmethod
    async def get_rule(session: AsyncSession, rule_id: str) -> ReviewRule:
        row = (
            await session.execute(select(ReviewRule).where(ReviewRule.rule_id == rule_id))
        ).scalar_one_or_none()
        if row is None:
            raise AppError(f"规则不存在：{rule_id}")
        return row

    #: 规则配置页允许编辑的字段（其余字段由 JSON 同步刷新，界面不改）
    RULE_EDITABLE_FIELDS: tuple[str, ...] = (
        "name", "category", "description", "risk_level", "trigger",
        "patterns", "exclude_patterns", "params", "contract_types", "clause_types",
        "reason", "legal_basis", "suggestion_template", "tags", "priority", "enabled",
    )

    @staticmethod
    async def update_rule(session: AsyncSession, rule_id: str, patch) -> ReviewRule:
        """条件编辑表单的落库入口。只写显式提交的字段（None = 不改）。

        注意 ``checker`` 不开放编辑：它指向 ``checkers.py`` 里的 Python 函数，
        改错了规则会在运行期直接报错，属于代码级改动而非配置。
        """
        row = await RuleService.get_rule(session, rule_id)
        data = patch.model_dump(exclude_unset=True, exclude_none=True)
        for field in RuleService.RULE_EDITABLE_FIELDS:
            if field in data:
                setattr(row, field, _as_value(data[field]))
        await session.flush()
        logger.info(f"[规则] {rule_id} 已更新字段：{sorted(data)}")
        return row

    # ------------------------------------------------------------------
    # 示范条款模板库维护
    # ------------------------------------------------------------------
    @staticmethod
    async def create_template(session: AsyncSession, payload) -> ClauseTemplate:
        from datetime import datetime, timezone

        tid = payload.template_id or f"T-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
        exists = (
            await session.execute(
                select(ClauseTemplate).where(ClauseTemplate.template_id == tid)
            )
        ).scalar_one_or_none()
        if exists is not None:
            raise AppError(f"模板编号已存在：{tid}")

        row = ClauseTemplate(id=str(uuid.uuid4()), template_id=tid)
        RuleService._assign_template(row, payload)
        session.add(row)
        await session.flush()
        logger.info(f"[模板] 新建 {tid}")
        return row

    @staticmethod
    async def update_template(
        session: AsyncSession, template_id: str, payload
    ) -> ClauseTemplate:
        row = (
            await session.execute(
                select(ClauseTemplate).where(ClauseTemplate.template_id == template_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise AppError(f"模板不存在：{template_id}")
        RuleService._assign_template(row, payload)
        await session.flush()
        logger.info(f"[模板] 更新 {template_id}")
        return row

    @staticmethod
    def _assign_template(row: ClauseTemplate, payload) -> None:
        data = payload.model_dump()
        data.pop("template_id", None)
        row.title = data["title"]
        row.clause_type = _as_value(data["clause_type"])
        row.contract_types = data["contract_types"]
        row.content = data["content"]
        row.tags = data["tags"]
        row.keywords = data["keywords"]
        row.scenario = data["scenario"]
        row.risk_level = _as_value(data["risk_level"])
        row.legal_basis = data["legal_basis"]
        row.version = data["version"]
        row.enabled = data["enabled"]

    # ------------------------------------------------------------------
    # 序列化（出参统一走这里，避免路由里重复拼 dict）
    # ------------------------------------------------------------------
    @staticmethod
    def rule_to_dict(r: ReviewRule) -> dict:
        return {
            "rule_id": r.rule_id,
            "name": r.name,
            "category": r.category,
            "description": r.description,
            "risk_level": r.risk_level,
            "risk_level_label": RISK_LEVEL_LABEL.get(r.risk_level),
            "trigger": r.trigger,
            "checker": r.checker,
            "params": r.params or {},
            "patterns": r.patterns or [],
            "exclude_patterns": r.exclude_patterns or [],
            "contract_types": r.contract_types or [],
            "contract_type_labels": [
                CONTRACT_TYPE_LABEL.get(t, t) for t in (r.contract_types or [])
            ],
            "clause_types": r.clause_types or [],
            "clause_type_labels": [
                CLAUSE_TYPE_LABEL.get(t, t) for t in (r.clause_types or [])
            ],
            "reason": r.reason,
            "legal_basis": r.legal_basis,
            "suggestion_template": r.suggestion_template,
            "tags": r.tags or [],
            "enabled": r.enabled,
            "priority": r.priority,
        }

    @staticmethod
    def template_to_dict(t: ClauseTemplate) -> dict:
        return {
            "template_id": t.template_id,
            "title": t.title,
            "clause_type": t.clause_type,
            "clause_type_label": CLAUSE_TYPE_LABEL.get(t.clause_type, t.clause_type),
            "contract_types": t.contract_types or [],
            "contract_type_labels": [
                CONTRACT_TYPE_LABEL.get(c, c) for c in (t.contract_types or [])
            ],
            "content": t.content,
            "tags": t.tags or [],
            "keywords": t.keywords or [],
            "scenario": t.scenario,
            "risk_level": t.risk_level,
            "risk_level_label": RISK_LEVEL_LABEL.get(t.risk_level),
            "legal_basis": t.legal_basis,
            "version": t.version,
            "enabled": t.enabled,
        }

    @staticmethod
    async def list_templates(
        session: AsyncSession, *, clause_type: str | None = None
    ) -> list[ClauseTemplate]:
        stmt = select(ClauseTemplate).order_by(ClauseTemplate.template_id)
        if clause_type:
            stmt = stmt.where(ClauseTemplate.clause_type == clause_type)
        return list((await session.execute(stmt)).scalars().all())


# ======================================================================
def _as_value(v):
    """枚举 → 字符串（DB 列存的是字符串）；其它类型原样返回。"""
    return v.value if isinstance(v, Enum) else v


def _rule_from_row(r: ReviewRule) -> ReviewRuleModel:
    return ReviewRuleModel(
        rule_id=r.rule_id,
        name=r.name,
        category=r.category,
        description=r.description,
        contract_types=list(r.contract_types or []),
        clause_types=[c for c in (r.clause_types or []) if c in {t.value for t in ClauseType}],
        risk_level=RiskLevel(r.risk_level),
        trigger=RuleTrigger(r.trigger),
        patterns=list(r.patterns or []),
        exclude_patterns=list(r.exclude_patterns or []),
        checker=r.checker,
        params=dict(r.params or {}),
        reason=r.reason,
        legal_basis=r.legal_basis,
        suggestion_template=r.suggestion_template,
        tags=list(r.tags or []),
        enabled=r.enabled,
        priority=r.priority,
    )


def _template_from_row(t: ClauseTemplate) -> ClauseTemplateModel:
    return ClauseTemplateModel(
        template_id=t.template_id,
        title=t.title,
        clause_type=ClauseType(t.clause_type),
        contract_types=list(t.contract_types or []),
        content=t.content,
        tags=list(t.tags or []),
        keywords=list(t.keywords or []),
        scenario=t.scenario,
        risk_level=RiskLevel(t.risk_level),
        legal_basis=t.legal_basis,
        version=t.version,
        enabled=t.enabled,
    )


__all__ = ["RuleService"]
