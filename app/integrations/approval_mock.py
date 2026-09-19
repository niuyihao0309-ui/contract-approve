"""审批系统 mock。

本期没有真实审批系统可对接（大纲阶段五明确说「做 mock 服务模拟推送/拉取与评论接口」）。
本模块提供：

1. 一类单据（审批单）的**待办列表**与**详情**；
2. 一条**评论接口** —— 审查系统把格式化意见写到这里，前端可以查看留痕；
3. 事件**推送**模拟：生成一张新待办，触发审查系统拉取。

数据落 JSON 文件（``storage/mock_approval.json``），这样重启服务后待办与评论还在，
演示时分多次讲也不会丢上下文。**刻意模拟真实系统的两点不友好行为**，
用来验证审查系统的韧性：

- ``POST /comment`` 有可配置的失败率（默认 0，可通过 ``fail_rate`` 打开）；
- 可注入延迟，用来演示「接口超时 → 任务阻塞 → 重试」。
"""

from __future__ import annotations

import asyncio
import json
import random
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logging import logger

_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApprovalSystemMock:
    """审批系统模拟实现。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (settings.storage_dir / "mock_approval.json")
        #: 模拟接口故障：0 = 从不失败，0.5 = 一半概率失败
        self.fail_rate: float = 0.0
        #: 模拟接口延迟（秒）
        self.latency: float = 0.0
        #: 模拟接口超时（秒）：超过就抛 TimeoutError，演示阻塞重试
        self.timeout_at: float = 0.0
        self._data: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    @property
    def data(self) -> dict[str, Any]:
        if self._data is None:
            self._data = self._load()
        return self._data

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                logger.info(f"[Mock审批] 载入 {len(raw.get('tickets', []))} 张单据：{self.path}")
                return raw
            except Exception as exc:  # pragma: no cover
                logger.warning(f"[Mock审批] 数据文件损坏，重建：{exc}")
        return {"tickets": [], "comments": [], "seq": 0}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    async def _simulate_network(self) -> None:
        """模拟真实接口的延迟与故障。"""
        if self.latency:
            delay = self.latency
            # timeout_at > 0 时，让它固定超时一次，方便演示阻塞链路
            if self.timeout_at and delay >= self.timeout_at:
                await asyncio.sleep(self.timeout_at)
                raise TimeoutError(
                    f"审批系统接口超时（>{self.timeout_at}s）"
                )
            await asyncio.sleep(delay)
        if self.fail_rate and random.random() < self.fail_rate:
            raise ConnectionError("审批系统返回 503 Service Unavailable（模拟故障）")

    # ------------------------------------------------------------------
    # 待办
    # ------------------------------------------------------------------
    async def create_ticket(
        self,
        *,
        title: str,
        applicant: str = "业务经办人",
        department: str = "采购部",
        business_type: str = "purchase",
        attachment_name: str | None = None,
        attachment_path: str | None = None,
        amount: float | None = None,
    ) -> dict:
        """新建一张审批单（模拟审批系统推送事件）。"""
        with _LOCK:
            d = self.data
            d["seq"] += 1
            ticket = {
                "ticket_id": f"SP-{datetime.now():%Y%m%d}-{d['seq']:04d}",
                "title": title,
                "applicant": applicant,
                "department": department,
                "business_type": business_type,
                "amount": amount,
                "attachment_name": attachment_name,
                "attachment_path": attachment_path,
                "status": "pending_review",
                "created_at": _now(),
                "pulled": False,
            }
            d["tickets"].append(ticket)
            self._save()
        logger.info(f"[Mock审批] 新建审批单 {ticket['ticket_id']}：{title}")
        return ticket

    async def list_tickets(self, *, only_pending: bool = True) -> list[dict]:
        """拉取待办列表（模拟审查系统轮询审批系统）。"""
        await self._simulate_network()
        with _LOCK:
            tickets = list(self.data["tickets"])
        if only_pending:
            tickets = [t for t in tickets if not t["pulled"]]
        for t in tickets:
            t["comment_count"] = sum(
                1 for c in self.data["comments"] if c["ticket_id"] == t["ticket_id"]
            )
        tickets.sort(key=lambda t: t["created_at"], reverse=True)
        return tickets

    async def get_ticket(self, ticket_id: str) -> dict | None:
        await self._simulate_network()
        with _LOCK:
            for t in self.data["tickets"]:
                if t["ticket_id"] == ticket_id:
                    out = dict(t)
                    out["comments"] = [
                        c for c in self.data["comments"] if c["ticket_id"] == ticket_id
                    ]
                    return out
        return None

    async def mark_pulled(self, ticket_id: str) -> None:
        with _LOCK:
            for t in self.data["tickets"]:
                if t["ticket_id"] == ticket_id:
                    t["pulled"] = True
                    t["pulled_at"] = _now()
            self._save()

    # ------------------------------------------------------------------
    # 评论接口 —— 回写目标
    # ------------------------------------------------------------------
    async def post_comment(self, ticket_id: str, content: str, *, author: str = "智能审查系统") -> dict:
        """把审查意见写进审批单评论区。这是回写服务唯一要调的接口。"""
        await self._simulate_network()

        with _LOCK:
            ticket = next(
                (t for t in self.data["tickets"] if t["ticket_id"] == ticket_id), None
            )
            if ticket is None:
                raise KeyError(f"审批单不存在：{ticket_id}")

            comment = {
                "comment_id": f"C-{uuid.uuid4().hex[:10]}",
                "ticket_id": ticket_id,
                "author": author,
                "content": content,
                "content_length": len(content),
                "created_at": _now(),
            }
            self.data["comments"].append(comment)
            ticket["status"] = "reviewed"
            ticket["reviewed_at"] = comment["created_at"]
            self._save()

        logger.info(
            f"[Mock审批] 单据 {ticket_id} 收到审查意见（{len(content)} 字，"
            f"comment_id={comment['comment_id']}）"
        )
        return comment

    # ------------------------------------------------------------------
    async def reset(self, *, keep_tickets: bool = False) -> None:
        with _LOCK:
            self._data = {
                "tickets": self.data["tickets"] if keep_tickets else [],
                "comments": [],
                "seq": self.data.get("seq", 0),
            }
            for t in self._data["tickets"]:
                t["pulled"] = False
                t["status"] = "pending_review"
            self._save()
        logger.info("[Mock审批] 已重置")

    def stats(self) -> dict:
        d = self.data
        return {
            "ticket_count": len(d["tickets"]),
            "pending_count": sum(1 for t in d["tickets"] if not t.get("pulled")),
            "comment_count": len(d["comments"]),
            "storage": str(self.path),
        }


_mock: ApprovalSystemMock | None = None


def get_approval_mock() -> ApprovalSystemMock:
    global _mock
    if _mock is None:
        _mock = ApprovalSystemMock()
    return _mock


__all__ = ["ApprovalSystemMock", "get_approval_mock"]
