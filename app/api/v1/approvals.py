"""审批系统对接接口（本期为 mock）。

包含两条主线（大纲 2.4.7 / 阶段五）：

1. **推送模式**：``POST /approvals/push`` 模拟审批系统推事件，
   审查系统直接把单据落成一条任务（可选立刻跑流水线）。
2. **拉取模式**：``GET /approvals/poll`` 模拟定时轮询待办，
   把还没拉过的单据批量建成任务。

评论接口在 mock 内部，审查系统通过 :class:`WritebackService` 调用它。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Body, Query

from app.api.deps import SessionDep
from app.core.config import settings
from app.core.logging import logger
from app.integrations.approval_mock import get_approval_mock
from app.models.enums import TaskSource
from app.schemas.task import OpResult
from app.services.file_service import FileService
from app.services.task_service import TaskService

router = APIRouter(prefix="/approvals", tags=["审批集成(mock)"])


# ----------------------------------------------------------------------
# 待办
# ----------------------------------------------------------------------
@router.get("/tickets", summary="审批单列表（模拟审批系统侧）")
async def list_tickets(only_pending: bool = Query(default=False)) -> dict:
    mock = get_approval_mock()
    try:
        tickets = await mock.list_tickets(only_pending=only_pending)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "tickets": []}
    return {"ok": True, "total": len(tickets), "tickets": tickets, "stats": mock.stats()}


@router.get("/tickets/{ticket_id}", summary="审批单详情（含评论区）")
async def get_ticket(ticket_id: str) -> dict:
    mock = get_approval_mock()
    ticket = await mock.get_ticket(ticket_id)
    if ticket is None:
        return {"ok": False, "error": f"审批单不存在：{ticket_id}"}
    return {"ok": True, "ticket": ticket}


@router.post("/push", summary="模拟审批系统推送一条待审合同")
async def push(
    session: SessionDep,
    title: str = Body(..., embed=True),
    applicant: str = Body(default="张伟", embed=True),
    department: str = Body(default="采购部", embed=True),
    business_type: str = Body(default="purchase", embed=True),
    attachment_path: str | None = Body(
        default=None, embed=True, description="附件在本机上的路径；不传则只建单据不建任务"
    ),
    amount: float | None = Body(default=None, embed=True),
    run_now: bool = Body(default=False, embed=True, description="是否立刻跑流水线"),
) -> dict:
    """模拟「审批系统发起合同审批 → 推送事件给审查系统」。

    真实场景里附件是审查系统按 URL 去下载的；本地演示直接给文件路径。
    """
    mock = get_approval_mock()

    attach_name = None
    if attachment_path:
        p = Path(attachment_path)
        attach_name = p.name if p.exists() else None

    ticket = await mock.create_ticket(
        title=title,
        applicant=applicant,
        department=department,
        business_type=business_type,
        attachment_name=attach_name,
        attachment_path=attachment_path,
        amount=amount,
    )

    task_id = None
    if attachment_path:
        task = await TaskService.create(
            session,
            title=title,
            business_type=business_type,
            applicant=applicant,
            department=department,
            contract_no=None,
            amount=amount,
            source=TaskSource.APPROVAL_PUSH,
            approval_ticket_id=ticket["ticket_id"],
        )
        await FileService.attach_local_file(session, task, Path(attachment_path))
        await TaskService.add_comment(
            session,
            task,
            content=f"由审批系统推送建单（审批单号 {ticket['ticket_id']}，申请人 {applicant}）",
            author="系统",
            role="system",
            kind="action",
        )
        await mock.mark_pulled(ticket["ticket_id"])
        await session.commit()
        task_id = task.id

        if run_now:
            from app.services.pipeline import PipelineRunner

            await PipelineRunner.run(task_id)

    logger.info(f"[审批集成] 推送建单 {ticket['ticket_id']} → 任务 {task_id or '(未建)'}")
    return {
        "ok": True,
        "ticket": ticket,
        "task_id": task_id,
        "message": "已接收推送并建单" + ("，流水线已执行" if run_now and task_id else ""),
    }


@router.post("/poll", summary="模拟从审批系统拉取待办（轮询模式）")
async def poll(
    session: SessionDep,
    run_now: bool = Body(default=False, embed=True),
    limit: int = Body(default=10, embed=True),
) -> dict:
    """拉取还没被拉过的审批单，逐条建任务。"""
    mock = get_approval_mock()
    try:
        tickets = await mock.list_tickets(only_pending=True)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "created": []}

    created: list[dict] = []
    for t in tickets[:limit]:
        task = await TaskService.create(
            session,
            title=t["title"],
            business_type=t.get("business_type"),
            applicant=t.get("applicant"),
            department=t.get("department"),
            amount=t.get("amount"),
            source=TaskSource.APPROVAL_POLL,
            approval_ticket_id=t["ticket_id"],
        )
        attach = t.get("attachment_path")
        if attach and Path(attach).exists():
            await FileService.attach_local_file(session, task, Path(attach))
        await TaskService.add_comment(
            session,
            task,
            content=f"由待办轮询拉取建单（审批单号 {t['ticket_id']}）",
            author="系统",
            role="system",
            kind="action",
        )
        await mock.mark_pulled(t["ticket_id"])
        created.append({"ticket_id": t["ticket_id"], "task_id": task.id, "title": t["title"]})

    await session.commit()

    if run_now:
        from app.services.pipeline import PipelineRunner

        for c in created:
            await PipelineRunner.run(c["task_id"])

    logger.info(f"[审批集成] 轮询拉取 {len(created)} 张单据")
    return {
        "ok": True,
        "pulled": len(created),
        "created": created,
        "message": f"拉取到 {len(created)} 张待办并建单" + ("，流水线已执行" if run_now else ""),
    }


# ----------------------------------------------------------------------
# 评论（回写目标的另一侧视角）
# ----------------------------------------------------------------------
@router.get("/tickets/{ticket_id}/comments", summary="查看某张审批单的评论区")
async def ticket_comments(ticket_id: str) -> dict:
    ticket = await get_approval_mock().get_ticket(ticket_id)
    if ticket is None:
        return {"ok": False, "error": f"审批单不存在：{ticket_id}"}
    return {
        "ok": True,
        "ticket_id": ticket_id,
        "status": ticket.get("status"),
        "total": len(ticket.get("comments", [])),
        "comments": ticket.get("comments", []),
    }


# ----------------------------------------------------------------------
# mock 行为控制（演示异常链路用）
# ----------------------------------------------------------------------
@router.post("/mock/config", response_model=OpResult, summary="设置 mock 的故障/延迟（演示阻塞重试）")
async def mock_config(
    fail_rate: float = Body(default=0.0, ge=0.0, le=1.0, embed=True),
    latency: float = Body(default=0.0, ge=0.0, embed=True),
    timeout_at: float = Body(default=0.0, ge=0.0, embed=True),
) -> OpResult:
    """让 mock 接口变得不稳定，用来演示「接口超时 → 回写失败 → 重试成功」。"""
    mock = get_approval_mock()
    mock.fail_rate = fail_rate
    mock.latency = latency
    mock.timeout_at = timeout_at
    return OpResult(
        message=(
            f"mock 已配置：失败率 {fail_rate:.0%}、延迟 {latency}s"
            + (f"、超过 {timeout_at}s 直接超时" if timeout_at else "")
        ),
        data={"fail_rate": fail_rate, "latency": latency, "timeout_at": timeout_at},
    )


@router.post("/mock/reset", response_model=OpResult, summary="重置 mock 数据")
async def mock_reset(keep_tickets: bool = Body(default=False, embed=True)) -> OpResult:
    await get_approval_mock().reset(keep_tickets=keep_tickets)
    return OpResult(message="mock 审批系统已重置")


@router.get("/mock/stats", summary="mock 数据统计")
async def mock_stats() -> dict:
    return get_approval_mock().stats()


@router.get("/samples", summary="列出可用的示例合同（演示脚本用）")
async def list_samples() -> dict:
    samples_dir = settings.storage_dir.parent / "samples"
    if not samples_dir.exists():
        return {"total": 0, "dir": str(samples_dir), "files": []}
    files = sorted(p for p in samples_dir.iterdir() if p.is_file())
    return {
        "total": len(files),
        "dir": str(samples_dir),
        "files": [
            {"name": p.name, "size": p.stat().st_size, "ext": p.suffix, "path": str(p)}
            for p in files
        ],
    }


__all__ = ["router"]
