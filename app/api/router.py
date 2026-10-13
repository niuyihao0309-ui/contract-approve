"""v1 路由汇总。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    approvals,
    auth,
    documents,
    health,
    reports,
    reviews,
    risks,
    rules,
    samples,
    tasks,
    writeback,
)

api_router = APIRouter()

# 认证与账号（登录页 / 身份 / 账号维护）
api_router.include_router(auth.router)

# 系统
api_router.include_router(health.router)

# 示例合同（上传页「一键试跑」用，全角色可读）
api_router.include_router(samples.router)

# 审查任务（大盘 / 工作台主入口）
api_router.include_router(tasks.router)

# 文档视窗（原文 / 条款 / 元数据）
api_router.include_router(documents.router)

# 风险项
api_router.include_router(risks.router)

# 审查引擎（跑流水线 / 自检 / LLM 冒烟）
api_router.include_router(reviews.router)

# 审批回写
api_router.include_router(writeback.router)

# 报告导出
api_router.include_router(reports.router)

# 规则与模板
api_router.include_router(rules.router)

# 审批系统对接（mock）
api_router.include_router(approvals.router)

__all__ = ["api_router"]
