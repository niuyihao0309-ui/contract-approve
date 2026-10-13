"""规则与标准条款库接口（规则配置页用）。

``app/rules/*.json`` 是唯一事实来源，DB 是运行期副本。因此：

- ``PATCH /rules/{id}`` 改的是**运行期副本**，立即对审查生效；
- ``POST /rules/sync`` 会以 JSON 为准把内容刷回去（``enabled`` 开关保留），
  界面上改过的字段会丢 —— 这一点在前端有明确提示。
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query

from app.api.deps import SessionDep, require_perm
from app.models.enums import CLAUSE_TYPE_LABEL, CONTRACT_TYPE_LABEL, RISK_LEVEL_LABEL
from app.schemas.rule import ClauseTemplateIn, RulePatchIn
from app.schemas.task import OpResult
from app.services.rule_service import RuleService

router = APIRouter(prefix="/rules", tags=["规则与模板"])


@router.get("", summary="规则列表（规则树）", dependencies=[Depends(require_perm("rule.view"))])
async def list_rules(
    session: SessionDep,
    contract_type: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
) -> dict:
    rows = await RuleService.list_rules(session, enabled=enabled, contract_type=contract_type)

    # 按 category 分组，前端直接渲染成树
    tree: dict[str, list[dict]] = {}
    for r in rows:
        tree.setdefault(r.category or "未分类", []).append(RuleService.rule_to_dict(r))
    return {
        "total": len(rows),
        "categories": list(tree),
        "tree": tree,
        "dictionaries": {
            "contract_type": CONTRACT_TYPE_LABEL,
            "clause_type": CLAUSE_TYPE_LABEL,
            "risk_level": RISK_LEVEL_LABEL,
        },
    }


@router.patch(
    "/{rule_id}",
    response_model=OpResult,
    summary="编辑规则（启用/停用 + 条件字段）",
    dependencies=[Depends(require_perm("rule.edit"))],
)
async def patch_rule(
    rule_id: str,
    session: SessionDep,
    body: dict | None = Body(default=None),
) -> OpResult:
    """条件编辑表单的保存入口。

    请求体是一张扁平的表单，两种用法都支持：
    - 只传 ``{"enabled": false}``（列表上的开关，向后兼容旧调用）；
    - 传完整表单 ``{"name": ..., "patterns": [...], "trigger": "regex", ...}``。

    未出现的字段一律不动（``None`` = 不改），所以前端可以按需只提交差异。
    """
    patch = RulePatchIn.model_validate(body or {})
    if not patch.model_dump(exclude_unset=True, exclude_none=True):
        return OpResult(ok=False, message="没有需要更新的字段")

    row = await RuleService.update_rule(session, rule_id, patch)
    await session.commit()
    return OpResult(
        message=f"规则「{row.name}」已保存",
        data=RuleService.rule_to_dict(row),
    )


@router.post(
    "/sync",
    response_model=OpResult,
    summary="从 JSON 文件重新同步规则与模板",
    dependencies=[Depends(require_perm("rule.edit"))],
)
async def sync() -> OpResult:
    stats = await RuleService.sync_from_files()
    return OpResult(
        message=f"同步完成：{stats['rules']} 条规则、{stats['templates']} 条示范条款",
        data=stats,
    )


@router.get("/templates", summary="标准示范条款库", dependencies=[Depends(require_perm("rule.view"))])
async def list_templates(
    session: SessionDep,
    clause_type: str | None = Query(default=None),
) -> dict:
    rows = await RuleService.list_templates(session, clause_type=clause_type)
    return {
        "total": len(rows),
        "clause_type_labels": CLAUSE_TYPE_LABEL,
        "contract_type_labels": CONTRACT_TYPE_LABEL,
        "risk_level_labels": RISK_LEVEL_LABEL,
        "items": [RuleService.template_to_dict(t) for t in rows],
    }


@router.post(
    "/templates",
    response_model=OpResult,
    summary="新建示范条款模板",
    dependencies=[Depends(require_perm("template.edit"))],
)
async def create_template(session: SessionDep, payload: ClauseTemplateIn) -> OpResult:
    row = await RuleService.create_template(session, payload)
    await session.commit()
    return OpResult(
        message=f"模板「{row.title}」已创建（编号 {row.template_id}）",
        data=RuleService.template_to_dict(row),
    )


@router.patch(
    "/templates/{template_id}",
    response_model=OpResult,
    summary="编辑示范条款模板",
    dependencies=[Depends(require_perm("template.edit"))],
)
async def update_template(
    template_id: str, session: SessionDep, payload: ClauseTemplateIn
) -> OpResult:
    row = await RuleService.update_template(session, template_id, payload)
    await session.commit()
    return OpResult(
        message=f"模板「{row.title}」已保存",
        data=RuleService.template_to_dict(row),
    )


@router.get(
    "/templates/search",
    summary="检索示范条款（调试推荐效果用）",
    dependencies=[Depends(require_perm("rule.view"))],
)
async def search_templates(
    query: str = Query(..., min_length=2),
    clause_type: str | None = Query(default=None),
    contract_type: str | None = Query(default=None),
    top_k: int = Query(default=3, ge=1, le=10),
) -> dict:
    library = await RuleService.build_library()
    hits = library.search(
        query, clause_type=clause_type, contract_type=contract_type, top_k=top_k
    )
    return {
        "query": query,
        "total": len(hits),
        "items": [
            {
                "template_id": t.template_id,
                "title": t.title,
                "clause_type": t.clause_type,
                "similarity": s,
                "content": t.content[:300],
            }
            for t, s in hits
        ],
    }


__all__ = ["router"]
