"""规则与标准条款库接口（规则配置页用）。"""

from __future__ import annotations

from fastapi import APIRouter, Body, Query

from app.api.deps import SessionDep
from app.models.enums import CLAUSE_TYPE_LABEL, CONTRACT_TYPE_LABEL, RISK_LEVEL_LABEL
from app.schemas.task import OpResult
from app.services.rule_service import RuleService

router = APIRouter(prefix="/rules", tags=["规则与模板"])


@router.get("", summary="规则列表（规则树）")
async def list_rules(
    session: SessionDep,
    contract_type: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
) -> dict:
    rows = await RuleService.list_rules(session, enabled=enabled, contract_type=contract_type)

    # 按 category 分组，前端直接渲染成树
    tree: dict[str, list[dict]] = {}
    for r in rows:
        tree.setdefault(r.category or "未分类", []).append(
            {
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
        )
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


@router.patch("/{rule_id}", response_model=OpResult, summary="启用 / 停用规则")
async def toggle_rule(
    rule_id: str,
    session: SessionDep,
    enabled: bool = Body(..., embed=True),
) -> OpResult:
    row = await RuleService.set_rule_enabled(session, rule_id, enabled)
    await session.commit()
    return OpResult(
        message=f"规则「{row.name}」已{'启用' if enabled else '停用'}",
        data={"rule_id": rule_id, "enabled": enabled},
    )


@router.post("/sync", response_model=OpResult, summary="从 JSON 文件重新同步规则与模板")
async def sync() -> OpResult:
    stats = await RuleService.sync_from_files()
    return OpResult(
        message=f"同步完成：{stats['rules']} 条规则、{stats['templates']} 条示范条款",
        data=stats,
    )


@router.get("/templates", summary="标准示范条款库")
async def list_templates(
    session: SessionDep,
    clause_type: str | None = Query(default=None),
) -> dict:
    rows = await RuleService.list_templates(session, clause_type=clause_type)
    return {
        "total": len(rows),
        "clause_type_labels": CLAUSE_TYPE_LABEL,
        "items": [
            {
                "template_id": t.template_id,
                "title": t.title,
                "clause_type": t.clause_type,
                "clause_type_label": CLAUSE_TYPE_LABEL.get(t.clause_type, t.clause_type),
                "contract_types": t.contract_types or [],
                "content": t.content,
                "tags": t.tags or [],
                "keywords": t.keywords or [],
                "scenario": t.scenario,
                "risk_level": t.risk_level,
                "legal_basis": t.legal_basis,
                "version": t.version,
                "enabled": t.enabled,
            }
            for t in rows
        ],
    }


@router.get("/templates/search", summary="检索示范条款（调试推荐效果用）")
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
