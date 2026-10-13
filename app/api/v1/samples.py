"""示例合同清单。

独立成一个文件而不是塞进 `approvals.py`：示例合同是**本地演示素材**，
跟「审批系统对接」是两回事。挂错地方的后果很具体 —— 上传页要用它，
而上传页是业务经办人（`task.submit`）的页面，经办人没有 `admin.approval`，
接口会 403，示例列表就空了。

权限取 `task.view`：所有已登录角色都有，因为「看一眼有哪些示例文件」
不构成任何越权。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import require_perm
from app.core.config import settings

router = APIRouter(prefix="/samples", tags=["示例合同"])


@router.get(
    "",
    summary="列出可用的示例合同",
    dependencies=[Depends(require_perm("task.view"))],
)
async def list_samples() -> dict:
    """扫描项目 `samples/` 目录。返回的是服务端路径，供「一键试跑」用。"""
    samples_dir = settings.storage_dir.parent / "samples"
    if not samples_dir.exists():
        return {"total": 0, "dir": str(samples_dir), "files": []}

    files = sorted(p for p in samples_dir.iterdir() if p.is_file())
    return {
        "total": len(files),
        "dir": str(samples_dir),
        "files": [
            {
                "name": p.name,
                "size": p.stat().st_size,
                "ext": p.suffix,
                "path": str(p),
            }
            for p in files
        ],
    }


__all__ = ["router"]
