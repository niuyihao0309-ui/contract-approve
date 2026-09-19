"""ORM 表定义。

按大纲「地基③」的清单落地，共 11 张表（在 10 张核心表基础上补了 ``doc_clause``，
用于承载条款切分结果 —— 验收标准第 2 条要求条款也要能定位回原文）。

    contract_task      审查任务主表（状态机载体）
    contract_file      附件表（hash 去重）
    doc_block          文档块（IR 的 blocks 落库）
    doc_clause         条款切分结果（绑定 block_id 区间）
    contract_metadata  核心元数据（带原文定位）
    risk_item          风险项（证据链：原文片段 + 位置 + 依据）
    clause_suggestion  修改示范条款
    review_comment     法务批注 / 协同记录
    writeback_log      回写日志（幂等键）
    review_rule        审查规则（可配置）
    clause_template    标准示范条款库

所有表统一使用 UTF-8 安全的 String/Text，金额用 Numeric(20,2)，
半结构化字段用 JSON（MySQL 原生 JSON / SQLite TEXT+JSON 序列化）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import (
    BlockType,
    ItemDecision,
    RiskLevel,
    RiskSource,
    ReviewConclusion,
    SuggestionSource,
    TaskSource,
    TaskStatus,
    WritebackStatus,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class TimestampMixin:
    """统一时间戳。"""

    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


# ======================================================================
# 1. 审查任务
# ======================================================================
class ContractTask(Base, TimestampMixin):
    """合同审查任务主表 —— 任务状态机的唯一载体。"""

    __tablename__ = "contract_task"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    # ---- 合同基本信息（大纲 2.4.8 报告头部要求）----
    contract_no: Mapped[str | None] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    department: Mapped[str | None] = mapped_column(String(128))       # 送审部门
    applicant: Mapped[str | None] = mapped_column(String(128))        # 申请人（业务经办人）
    business_type: Mapped[str | None] = mapped_column(String(32), index=True)  # purchase/sales/...
    counterparty: Mapped[str | None] = mapped_column(String(255))     # 相对方主体名称
    amount: Mapped[float | None] = mapped_column(Numeric(20, 2))
    currency: Mapped[str | None] = mapped_column(String(16), default="CNY")
    period: Mapped[str | None] = mapped_column(String(128))           # 履约周期

    # ---- 状态机 ----
    status: Mapped[str] = mapped_column(
        String(16), default=TaskStatus.PENDING.value, nullable=False, index=True
    )
    stage: Mapped[str | None] = mapped_column(String(32))            # 流水线细分阶段
    progress: Mapped[int] = mapped_column(Integer, default=0)        # 0-100
    block_reason: Mapped[str | None] = mapped_column(String(32))     # BlockReason
    block_detail: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    # ---- 审查结论 ----
    overall_risk: Mapped[str | None] = mapped_column(String(8), index=True)  # high/medium/low
    conclusion: Mapped[str | None] = mapped_column(String(16))       # pass/rectify/reject
    risk_summary: Mapped[str | None] = mapped_column(Text)           # 核心风险摘要
    focus_points: Mapped[list | None] = mapped_column(JSON)          # 审批关注要点 list[str]
    risk_counts: Mapped[dict | None] = mapped_column(JSON)           # {"high": 2, "medium": 1, ...}

    # ---- 回写状态（与任务状态机分离）----
    writeback_status: Mapped[str] = mapped_column(
        String(16), default=WritebackStatus.NOT_WRITTEN.value, nullable=False, index=True
    )
    writeback_attempts: Mapped[int] = mapped_column(Integer, default=0)

    # ---- 来源 ----
    source: Mapped[str] = mapped_column(String(16), default=TaskSource.UPLOAD.value)
    approval_ticket_id: Mapped[str | None] = mapped_column(String(64), index=True)

    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    # ---- 关系 ----
    files: Mapped[list["ContractFile"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", lazy="selectin"
    )
    risks: Mapped[list["RiskItem"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (Index("ix_task_status_risk", "status", "overall_risk"),)


# ======================================================================
# 2. 附件
# ======================================================================
class ContractFile(Base, TimestampMixin):
    """合同附件。``file_hash`` 用于跨任务去重，重复提交复用已解析结果。"""

    __tablename__ = "contract_file"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("contract_task.id", ondelete="CASCADE"), index=True
    )

    filename: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)   # sha256
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    ext: Mapped[str] = mapped_column(String(16))
    file_kind: Mapped[str] = mapped_column(String(16))               # docx/pdf/image
    storage_path: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str | None] = mapped_column(String(128))

    # 解析结果摘要
    parsed: Mapped[bool] = mapped_column(Boolean, default=False)
    parser: Mapped[str | None] = mapped_column(String(32))
    page_count: Mapped[int | None] = mapped_column(Integer)
    parse_meta: Mapped[dict | None] = mapped_column(JSON)            # DocIR.to_storage_meta()
    parse_error: Mapped[str | None] = mapped_column(Text)
    from_cache: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否命中指纹缓存

    task: Mapped[ContractTask] = relationship(back_populates="files")

    __table_args__ = (Index("ix_file_hash_parsed", "file_hash", "parsed"),)


# ======================================================================
# 3. 文档块（IR 的 blocks）
# ======================================================================
class DocBlock(Base):
    """IR 里的 block 落库。前端高亮的 DOM 映射靠这张表。"""

    __tablename__ = "doc_block"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("contract_task.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[str | None] = mapped_column(String(36), index=True)

    doc_id: Mapped[str] = mapped_column(String(36), index=True)
    block_id: Mapped[str] = mapped_column(String(32), index=True)    # b_0001
    type: Mapped[str] = mapped_column(String(16), default=BlockType.PARAGRAPH.value)

    text: Mapped[str] = mapped_column(Text, default="")
    page: Mapped[int] = mapped_column(Integer, default=1, index=True)
    para_index: Mapped[int] = mapped_column(Integer, default=0)
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)
    bbox: Mapped[list | None] = mapped_column(JSON)
    level: Mapped[int | None] = mapped_column(Integer)
    table_index: Mapped[int | None] = mapped_column(Integer)
    row_index: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("doc_id", "block_id", name="uq_doc_block"),
        Index("ix_block_task_order", "task_id", "char_start"),
    )


# ======================================================================
# 4. 条款切分
# ======================================================================
class DocClause(Base, TimestampMixin):
    """按标题层级 + 关键词切出的条款，绑定 block_id 区间以便回原文。"""

    __tablename__ = "doc_clause"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("contract_task.id", ondelete="CASCADE"), index=True
    )
    doc_id: Mapped[str] = mapped_column(String(36), index=True)

    clause_type: Mapped[str] = mapped_column(String(32), index=True)  # ClauseType
    title: Mapped[str] = mapped_column(String(255), default="")
    content: Mapped[str] = mapped_column(Text, default="")

    # 条款覆盖的 block 区间（含首尾）
    start_block_id: Mapped[str] = mapped_column(String(32))
    end_block_id: Mapped[str] = mapped_column(String(32))
    block_ids: Mapped[list | None] = mapped_column(JSON)
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)
    page_start: Mapped[int] = mapped_column(Integer, default=1)
    page_end: Mapped[int] = mapped_column(Integer, default=1)
    para_start: Mapped[int] = mapped_column(Integer, default=0)

    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    method: Mapped[str] = mapped_column(String(16), default="rule")   # rule / llm

    __table_args__ = (Index("ix_clause_task_type", "task_id", "clause_type"),)


# ======================================================================
# 5. 元数据
# ======================================================================
class ContractMetadata(Base, TimestampMixin):
    """核心元数据。每条都必须带原文定位。"""

    __tablename__ = "contract_metadata"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("contract_task.id", ondelete="CASCADE"), index=True
    )

    field_key: Mapped[str] = mapped_column(String(32), index=True)    # MetadataField
    field_value: Mapped[str | None] = mapped_column(String(512))
    normalized_value: Mapped[str | None] = mapped_column(String(512))  # 归一化（如金额去逗号）

    block_id: Mapped[str | None] = mapped_column(String(32))
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)
    para_index: Mapped[int | None] = mapped_column(Integer)
    quote: Mapped[str | None] = mapped_column(String(512))

    source: Mapped[str] = mapped_column(String(16), default="regex")  # regex / llm
    confidence: Mapped[float] = mapped_column(Float, default=1.0)

    __table_args__ = (Index("ix_meta_task_field", "task_id", "field_key"),)


# ======================================================================
# 6. 风险项（证据链）
# ======================================================================
class RiskItem(Base, TimestampMixin):
    """风险项。没有定位的风险项在前端点不动，等于无效 —— 所以定位字段非空。"""

    __tablename__ = "risk_item"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("contract_task.id", ondelete="CASCADE"), index=True
    )

    risk_no: Mapped[int] = mapped_column(Integer, default=1)          # 展示序号 R1/R2...
    name: Mapped[str] = mapped_column(String(255))
    risk_level: Mapped[str] = mapped_column(String(8), index=True)    # RiskLevel
    clause_type: Mapped[str] = mapped_column(String(32), index=True)  # ClauseType

    # ---- 来源 ----
    source: Mapped[str] = mapped_column(String(8), default=RiskSource.RULE.value)
    rule_id: Mapped[str | None] = mapped_column(String(64), index=True)

    # ---- 定位（证据链三要素之二）----
    block_id: Mapped[str] = mapped_column(String(32), index=True)
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)
    page: Mapped[int] = mapped_column(Integer, default=1)
    para_index: Mapped[int] = mapped_column(Integer, default=0)
    quote: Mapped[str] = mapped_column(Text, default="")              # 原文片段

    # ---- 判定说明（证据链三要素之三）----
    reason: Mapped[str] = mapped_column(Text, default="")             # 风险成因
    legal_basis: Mapped[str] = mapped_column(Text, default="")        # 法律依据
    tags: Mapped[list | None] = mapped_column(JSON)

    # ---- 法务处理 ----
    decision: Mapped[str] = mapped_column(String(16), default=ItemDecision.OPEN.value)
    lawyer_note: Mapped[str | None] = mapped_column(Text)

    probability: Mapped[float | None] = mapped_column(Float)          # 0-1，LLM 可给
    confidence: Mapped[float] = mapped_column(Float, default=1.0)

    task: Mapped[ContractTask] = relationship(back_populates="risks")
    suggestions: Mapped[list["ClauseSuggestion"]] = relationship(
        back_populates="risk", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (Index("ix_risk_task_level", "task_id", "risk_level"),)


# ======================================================================
# 7. 修改示范条款
# ======================================================================
class ClauseSuggestion(Base, TimestampMixin):
    """针对风险条款的修改示范文本。"""

    __tablename__ = "clause_suggestion"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("contract_task.id", ondelete="CASCADE"), index=True
    )
    risk_item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("risk_item.id", ondelete="CASCADE"), index=True
    )

    original_text: Mapped[str] = mapped_column(Text, default="")      # 问题条款原文
    suggested_text: Mapped[str] = mapped_column(Text, default="")     # 建议条款
    rationale: Mapped[str] = mapped_column(Text, default="")          # 改写理由

    source: Mapped[str] = mapped_column(String(16), default=SuggestionSource.TEMPLATE.value)
    template_id: Mapped[str | None] = mapped_column(String(64))
    similarity: Mapped[float | None] = mapped_column(Float)           # 模板检索相似度

    # 法务编辑痕迹
    edited_text: Mapped[str | None] = mapped_column(Text)
    edited_by: Mapped[str | None] = mapped_column(String(128))
    decision: Mapped[str] = mapped_column(String(16), default=ItemDecision.OPEN.value)

    @property
    def final_text(self) -> str:
        """法务改过就用改过的，否则用 AI 建议。"""
        return self.edited_text or self.suggested_text

    risk: Mapped[RiskItem] = relationship(back_populates="suggestions")


# ======================================================================
# 8. 法务批注 / 协同记录
# ======================================================================
class ReviewComment(Base, TimestampMixin):
    """法务人工批注与协同操作留痕。"""

    __tablename__ = "review_comment"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("contract_task.id", ondelete="CASCADE"), index=True
    )

    author: Mapped[str] = mapped_column(String(128), default="法务审查人")
    role: Mapped[str] = mapped_column(String(32), default="lawyer")   # lawyer/business/admin
    kind: Mapped[str] = mapped_column(String(16), default="comment")  # comment/decision/action
    content: Mapped[str] = mapped_column(Text, default="")

    # 可选：批注挂到某条风险上
    risk_item_id: Mapped[str | None] = mapped_column(String(36))
    block_id: Mapped[str | None] = mapped_column(String(32))
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)


# ======================================================================
# 9. 回写日志
# ======================================================================
class WritebackLog(Base, TimestampMixin):
    """回写日志。``idempotency_key`` 唯一约束保证不会重复写回同一版本的意见。"""

    __tablename__ = "writeback_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("contract_task.id", ondelete="CASCADE"), index=True
    )

    # 幂等键：task_id + 内容指纹。同一份意见重复提交只会命中已有记录
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    content_hash: Mapped[str] = mapped_column(String(64))

    status: Mapped[str] = mapped_column(
        String(16), default=WritebackStatus.NOT_WRITTEN.value, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, default=1)

    payload_md: Mapped[str] = mapped_column(Text, default="")         # 送出的 Markdown 意见
    request_body: Mapped[dict | None] = mapped_column(JSON)
    response_body: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)

    ticket_id: Mapped[str | None] = mapped_column(String(64))
    comment_id: Mapped[str | None] = mapped_column(String(64), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)


# ======================================================================
# 10. 审查规则
# ======================================================================
class ReviewRule(Base, TimestampMixin):
    """可配置审查规则。启动时从 review_rules.json 同步入库，改库即时生效。"""

    __tablename__ = "review_rule"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(32), default="general")
    description: Mapped[str] = mapped_column(Text, default="")

    contract_types: Mapped[list | None] = mapped_column(JSON)         # ["purchase","sales"]
    clause_types: Mapped[list | None] = mapped_column(JSON)           # 作用范围
    risk_level: Mapped[str] = mapped_column(String(8), default=RiskLevel.MEDIUM.value)

    trigger: Mapped[str] = mapped_column(String(16), default="keyword")  # RuleTrigger
    patterns: Mapped[list | None] = mapped_column(JSON)               # 关键词/正则列表
    exclude_patterns: Mapped[list | None] = mapped_column(JSON)       # 命中即排除（降噪）

    #: trigger=checker 时引用的判定器名（见 app/engines/review/checkers.py）
    checker: Mapped[str | None] = mapped_column(String(64))
    #: 判定器参数，如 {"max_daily_wan": 5}
    params: Mapped[dict | None] = mapped_column(JSON)

    reason: Mapped[str] = mapped_column(Text, default="")
    legal_basis: Mapped[str] = mapped_column(Text, default="")
    suggestion_template: Mapped[str | None] = mapped_column(Text)     # 兜底建议文本
    tags: Mapped[list | None] = mapped_column(JSON)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)       # 越小越先跑


# ======================================================================
# 11. 标准示范条款库
# ======================================================================
class ClauseTemplate(Base, TimestampMixin):
    """标准示范条款库。条款推荐引擎的检索源。"""

    __tablename__ = "clause_template"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    template_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    title: Mapped[str] = mapped_column(String(255))
    clause_type: Mapped[str] = mapped_column(String(32), index=True)   # ClauseType
    contract_types: Mapped[list | None] = mapped_column(JSON)
    content: Mapped[str] = mapped_column(Text, default="")             # 示范条款正文

    tags: Mapped[list | None] = mapped_column(JSON)
    keywords: Mapped[list | None] = mapped_column(JSON)                # 检索关键词
    scenario: Mapped[str] = mapped_column(String(255), default="")     # 适用场景
    risk_level: Mapped[str] = mapped_column(String(8), default=RiskLevel.MEDIUM.value)
    legal_basis: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String(16), default="1.0")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


__all__ = [
    "ContractTask",
    "ContractFile",
    "DocBlock",
    "DocClause",
    "ContractMetadata",
    "RiskItem",
    "ClauseSuggestion",
    "ReviewComment",
    "WritebackLog",
    "ReviewRule",
    "ClauseTemplate",
]
