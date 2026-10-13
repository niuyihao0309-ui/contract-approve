"""角色与权限矩阵 —— **服务端的那一份**（需求 2.4.2）。

前端 ``frontend/src/permissions.js`` 负责「呈现层收敛」（隐藏入口、403、
按钮置灰），本文件负责**真正的边界**：接口级鉴权。两份必须一致，
否则会出现「界面点不动、接口却能调通」或反过来的错位。

一致性靠回归测试钉住：``tests/test_permissions_sync.py`` 会把
``frontend/src/permissions.js`` 解析出来与本文件逐项比对，
任何一边改了忘了改另一边，测试就会红。

命名沿用「资源.动作」，与前端完全同名 —— 别在这里另起一套命名。
"""

from __future__ import annotations

# ======================================================================
# 角色
# ======================================================================
ROLES: dict[str, dict[str, str]] = {
    "lawyer": {
        "key": "lawyer",
        "name": "法务审查人",
        "duty": "对风险点拍板：核对原文证据、采纳或调整修改建议、补充法务意见并触发回写",
        "scope": "全部合同（不限部门）",
    },
    "operator": {
        "key": "operator",
        "name": "业务经办人",
        "duty": "发起与跟踪：提交合同审查申请、查看风险点与整改建议、下载审查报告",
        "scope": "本部门送审的合同",
    },
    "admin": {
        "key": "admin",
        "name": "系统管理员",
        "duty": "保管道：配置审批流对接参数、维护审查规则与标准条款库、监控任务并处理阻塞重试",
        "scope": "全部合同 + 系统配置",
    },
}

ROLE_KEYS: list[str] = ["lawyer", "operator", "admin"]

# ======================================================================
# 权限点
# ======================================================================
PERMS: dict[str, dict[str, str]] = {
    "task.view": {"group": "审查任务", "label": "查看任务列表与审查详情"},
    "task.submit": {"group": "审查任务", "label": "提交合同审查申请"},
    "task.upload": {"group": "审查任务", "label": "上传合同附件"},
    "task.monitor": {"group": "审查任务", "label": "监控任务状态（进度 / 阻塞原因）"},
    "task.retry": {"group": "审查任务", "label": "处理阻塞重试 / 重新解析"},
    "task.batch": {"group": "审查任务", "label": "批量操作（重试 / 执行 / 删除）"},
    "doc.view": {"group": "文档视窗", "label": "查看原文与高亮证据"},
    "doc.preview": {"group": "文档视窗", "label": "原件预览（PDF / 扫描件）"},
    "risk.view": {"group": "风险与建议", "label": "查看风险点与整改建议"},
    "risk.copy": {"group": "风险与建议", "label": "复制修改建议"},
    "risk.decide": {"group": "风险与建议", "label": "采纳 / 调整 / 驳回修改建议"},
    "comment.add": {"group": "协同回写", "label": "补充法务意见（批注）"},
    "writeback.trigger": {"group": "协同回写", "label": "触发审批回写"},
    "report.view": {"group": "协同回写", "label": "预览审查报告"},
    "report.download": {"group": "协同回写", "label": "下载审查报告（Markdown / PDF）"},
    "rule.view": {"group": "规则配置", "label": "查看审查规则与标准条款库"},
    "rule.edit": {"group": "规则配置", "label": "维护审查规则（启停 / 条件编辑）"},
    "template.edit": {"group": "规则配置", "label": "维护标准示范条款模板库"},
    "admin.approval": {"group": "系统配置", "label": "配置审批流对接参数与故障演练"},
    "engine.view": {"group": "系统配置", "label": "查看引擎状态与自检结果"},
    "user.manage": {"group": "系统配置", "label": "查看与维护登录账号"},
}

# ======================================================================
# 角色 → 权限（最小权限原则：需求里没写的就不给）
# ======================================================================
ROLE_PERMISSIONS: dict[str, list[str]] = {
    # 法务审查人：复核 + 拍板 + 回写。不参与建单，也不碰系统配置。
    "lawyer": [
        "task.view",
        "doc.view",
        "doc.preview",
        "risk.view",
        "risk.copy",
        "risk.decide",
        "comment.add",
        "writeback.trigger",
        "report.view",
        "report.download",
        "rule.view",
    ],
    # 业务经办人：提交 + 只读跟踪 + 下载。对风险点没有决定权，不能回写。
    "operator": [
        "task.view",
        "task.submit",
        "task.upload",
        "doc.view",
        "doc.preview",
        "risk.view",
        "risk.copy",
        "report.view",
        "report.download",
    ],
    # 系统管理员：管道与规则。不做业务决定，也不代替经办人建单。
    "admin": [
        "task.view",
        "task.monitor",
        "task.retry",
        "task.batch",
        "doc.view",
        "doc.preview",
        "risk.view",
        "engine.view",
        "rule.view",
        "rule.edit",
        "template.edit",
        "admin.approval",
        "user.manage",
    ],
}


# ======================================================================
# 辅助
# ======================================================================
def role_perms(role: str) -> list[str]:
    """某角色的权限点清单（未知角色返回空集合，不是全量 —— 失败要往紧里收）。"""
    return list(ROLE_PERMISSIONS.get(role or "", []))


def has_perm(role: str, perm: str) -> bool:
    return perm in ROLE_PERMISSIONS.get(role or "", [])


def perm_label(perm: str) -> str:
    return PERMS.get(perm, {}).get("label", perm)


def role_name(role: str) -> str:
    return ROLES.get(role, {}).get("name", role)


def perms_by_group() -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {}
    for key, meta in PERMS.items():
        out.setdefault(meta["group"], []).append({"key": key, "label": meta["label"]})
    return out


__all__ = [
    "ROLES",
    "ROLE_KEYS",
    "PERMS",
    "ROLE_PERMISSIONS",
    "role_perms",
    "has_perm",
    "perm_label",
    "role_name",
    "perms_by_group",
]
