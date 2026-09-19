"""全局枚举与状态机定义。

大纲「地基③」的硬性要求：**状态字段严格枚举，禁止代码里裸写字符串；
状态流转集中在 service 里，不要在多个地方改状态。**

本模块是唯一的枚举来源，任何地方要用状态都必须 import 这里。
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """可直接参与 JSON 序列化 / 字符串比较的枚举基类。"""

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志展示
        return self.value


# ======================================================================
# 任务状态机
# ======================================================================
class TaskStatus(StrEnum):
    """合同审查任务状态。

    正常链路::

        pending -> parsing -> reviewing -> completed

    旁支::

        parsing/reviewing --(加密/严重模糊/正文为空/接口异常)--> blocked
        blocked --(人工重传或一键重试)--> pending
    """

    PENDING = "pending"          # 待处理
    PARSING = "parsing"          # 解析中
    REVIEWING = "reviewing"      # 审查中
    BLOCKED = "blocked"          # 解析受阻
    COMPLETED = "completed"      # 审查完成


#: 任务状态合法流转表。键 = 当前状态，值 = 允许进入的下一状态集合。
TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.PARSING, TaskStatus.BLOCKED},
    TaskStatus.PARSING: {TaskStatus.REVIEWING, TaskStatus.BLOCKED, TaskStatus.PENDING},
    TaskStatus.REVIEWING: {TaskStatus.COMPLETED, TaskStatus.BLOCKED},
    TaskStatus.BLOCKED: {TaskStatus.PENDING, TaskStatus.PARSING},
    # 已完成允许回退到待处理，用于「重新审查」
    TaskStatus.COMPLETED: {TaskStatus.PENDING, TaskStatus.REVIEWING},
}

#: 任务状态中文名，用于前端展示与报告输出
TASK_STATUS_LABEL: dict[str, str] = {
    TaskStatus.PENDING.value: "待处理",
    TaskStatus.PARSING.value: "解析中",
    TaskStatus.REVIEWING.value: "审查中",
    TaskStatus.BLOCKED.value: "解析受阻",
    TaskStatus.COMPLETED.value: "审查完成",
}

#: 可触发「重新审查」的状态
RETRYABLE_TASK_STATUS: set[TaskStatus] = {TaskStatus.BLOCKED, TaskStatus.COMPLETED}


# ======================================================================
# 回写状态机（与任务状态机分离，别混在一起）
# ======================================================================
class WritebackStatus(StrEnum):
    """审查意见回写审批系统的状态。

    ::

        not_written -> writing -> success
                              \\-> failed --(重试)--> writing
    """

    NOT_WRITTEN = "not_written"
    WRITING = "writing"
    SUCCESS = "success"
    FAILED = "failed"


WRITEBACK_TRANSITIONS: dict[WritebackStatus, set[WritebackStatus]] = {
    WritebackStatus.NOT_WRITTEN: {WritebackStatus.WRITING},
    WritebackStatus.WRITING: {WritebackStatus.SUCCESS, WritebackStatus.FAILED},
    WritebackStatus.SUCCESS: {WritebackStatus.WRITING},   # 允许重新回写（内容更新）
    WritebackStatus.FAILED: {WritebackStatus.WRITING},
}

WRITEBACK_STATUS_LABEL: dict[str, str] = {
    WritebackStatus.NOT_WRITTEN.value: "未回写",
    WritebackStatus.WRITING.value: "回写中",
    WritebackStatus.SUCCESS.value: "回写成功",
    WritebackStatus.FAILED.value: "回写失败",
}


# ======================================================================
# 风险等级
# ======================================================================
class RiskLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


#: 等级排序权重，用于「高 > 中 > 低，一票否决」的总等级计算
RISK_WEIGHT: dict[str, int] = {
    RiskLevel.HIGH.value: 3,
    RiskLevel.MEDIUM.value: 2,
    RiskLevel.LOW.value: 1,
}

RISK_LEVEL_LABEL: dict[str, str] = {
    RiskLevel.HIGH.value: "高风险",
    RiskLevel.MEDIUM.value: "中风险",
    RiskLevel.LOW.value: "低风险",
}


class ReviewConclusion(StrEnum):
    """综合审查结论。"""

    PASS = "pass"          # 通过
    RECTIFY = "rectify"    # 整改
    REJECT = "reject"      # 建议拒绝


CONCLUSION_LABEL: dict[str, str] = {
    ReviewConclusion.PASS.value: "通过",
    ReviewConclusion.RECTIFY.value: "整改",
    ReviewConclusion.REJECT.value: "建议拒绝",
}


# ======================================================================
# 文档与业务维度
# ======================================================================
class FileKind(StrEnum):
    """源文件类型，决定走哪个解析器。"""

    DOCX = "docx"
    PDF = "pdf"
    IMAGE = "image"


PARSER_BY_KIND: dict[FileKind, str] = {
    FileKind.DOCX: "docx_parser",
    FileKind.PDF: "pdf_parser",
    FileKind.IMAGE: "ocr_parser",
}

EXT_TO_KIND: dict[str, FileKind] = {
    ".docx": FileKind.DOCX,
    ".doc": FileKind.DOCX,        # .doc 需先另存为 .docx，见解析器说明
    ".pdf": FileKind.PDF,
    ".png": FileKind.IMAGE,
    ".jpg": FileKind.IMAGE,
    ".jpeg": FileKind.IMAGE,
    ".bmp": FileKind.IMAGE,
    ".tiff": FileKind.IMAGE,
}


class BlockType(StrEnum):
    """IR 中的块类型。"""

    TITLE = "title"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    HEADER = "header"
    FOOTER = "footer"


class ClauseType(StrEnum):
    """法定与业务条款类型（大纲 2.4.4 要求的切分目标）。"""

    SUBJECT_MATTER = "subject_matter"        # 标的物
    PAYMENT = "payment"                      # 付款进度
    ACCEPTANCE = "acceptance"                # 验收标准
    BREACH = "breach"                        # 违约责任
    CONFIDENTIALITY = "confidentiality"      # 保密义务
    DATA_SECURITY = "data_security"          # 数据安全
    JURISDICTION = "jurisdiction"            # 争议解决管辖
    IP = "ip"                                # 知识产权
    FORCE_MAJEURE = "force_majeure"          # 不可抗力
    TERM = "term"                            # 履行期限
    OTHER = "other"


CLAUSE_TYPE_LABEL: dict[str, str] = {
    ClauseType.SUBJECT_MATTER.value: "标的物条款",
    ClauseType.PAYMENT.value: "付款进度条款",
    ClauseType.ACCEPTANCE.value: "验收标准条款",
    ClauseType.BREACH.value: "违约责任条款",
    ClauseType.CONFIDENTIALITY.value: "保密义务条款",
    ClauseType.DATA_SECURITY.value: "数据安全条款",
    ClauseType.JURISDICTION.value: "争议解决管辖条款",
    ClauseType.IP.value: "知识产权条款",
    ClauseType.FORCE_MAJEURE.value: "不可抗力条款",
    ClauseType.TERM.value: "履行期限条款",
    ClauseType.OTHER.value: "其他条款",
}


class ContractType(StrEnum):
    """合同业务类型（规则按类型分组）。"""

    PURCHASE = "purchase"      # 采购
    SALES = "sales"            # 销售
    SERVICE = "service"        # 服务
    LABOR = "labor"            # 劳动
    OTHER = "other"


CONTRACT_TYPE_LABEL: dict[str, str] = {
    ContractType.PURCHASE.value: "采购合同",
    ContractType.SALES.value: "销售合同",
    ContractType.SERVICE.value: "服务合同",
    ContractType.LABOR.value: "劳动合同",
    ContractType.OTHER.value: "其他合同",
}


class MetadataField(StrEnum):
    """必须提取的核心元数据字段（大纲 2.4.4）。"""

    PARTY_A = "party_a"                      # 甲方 / 签约主体名称
    PARTY_B = "party_b"                      # 乙方 / 相对方主体名称
    USCC = "uscc"                            # 统一社会信用代码
    AMOUNT = "amount"                        # 合同金额
    CURRENCY = "currency"                    # 币种
    PERIOD = "period"                        # 履行期限
    EFFECTIVE_CONDITION = "effective_condition"  # 生效条件
    CONTRACT_NO = "contract_no"              # 合同编号


METADATA_FIELD_LABEL: dict[str, str] = {
    MetadataField.PARTY_A.value: "签约主体（甲方）",
    MetadataField.PARTY_B.value: "相对方主体（乙方）",
    MetadataField.USCC.value: "统一社会信用代码",
    MetadataField.AMOUNT.value: "合同金额",
    MetadataField.CURRENCY.value: "币种",
    MetadataField.PERIOD.value: "履行期限",
    MetadataField.EFFECTIVE_CONDITION.value: "生效条件",
    MetadataField.CONTRACT_NO.value: "合同编号",
}


class RiskSource(StrEnum):
    """风险项来源：规则引擎 or LLM 语义研判。"""

    RULE = "rule"
    LLM = "llm"


class SuggestionSource(StrEnum):
    """修改建议来源。"""

    TEMPLATE = "template"    # 标准条款库直接命中
    LLM = "llm"              # 大模型改写生成


class ItemDecision(StrEnum):
    """法务对风险项 / 建议的处理状态。"""

    OPEN = "open"            # 待处理
    ACCEPTED = "accepted"    # 已采纳
    ADJUSTED = "adjusted"    # 已调整
    REJECTED = "rejected"    # 已驳回


class TaskSource(StrEnum):
    """任务来源。"""

    UPLOAD = "upload"                      # 本地上传
    APPROVAL_PUSH = "approval_push"        # 审批系统事件推送
    APPROVAL_POLL = "approval_poll"        # 审批系统待办拉取


class RuleTrigger(StrEnum):
    """规则触发方式。"""

    KEYWORD = "keyword"          # 命中关键词
    REGEX = "regex"              # 命中正则
    CHECKER = "checker"          # 交给 Python 判定器（checkers.py 里的函数）
    REQUIRED = "required"        # 必备条款缺失（找不到才算命中）
    FORBIDDEN = "forbidden"      # 禁止性表述
    THRESHOLD = "threshold"      # 数值阈值（如违约金比例）


class BlockReason(StrEnum):
    """任务进入 blocked 的原因（验收标准第 7 条）。"""

    ENCRYPTED = "encrypted"              # 文档加密 / 有密码
    EMPTY_BODY = "empty_body"            # 正文为空
    BLURRY_SCAN = "blurry_scan"          # 扫描件严重模糊
    UNSUPPORTED_FORMAT = "unsupported_format"
    PARSE_FAILED = "parse_failed"        # 解析异常
    OCR_UNAVAILABLE = "ocr_unavailable"  # OCR 引擎不可用
    API_TIMEOUT = "api_timeout"          # 审批接口超时


BLOCK_REASON_LABEL: dict[str, str] = {
    BlockReason.ENCRYPTED.value: "文档已加密，无法解析",
    BlockReason.EMPTY_BODY.value: "正文为空，无可审查内容",
    BlockReason.BLURRY_SCAN.value: "扫描件严重模糊，文本识别率过低",
    BlockReason.UNSUPPORTED_FORMAT.value: "不支持的文件格式",
    BlockReason.PARSE_FAILED.value: "文档解析异常",
    BlockReason.OCR_UNAVAILABLE.value: "OCR 引擎不可用，请人工重传可复制文本的文档",
    BlockReason.API_TIMEOUT.value: "审批系统接口超时",
}


class ReviewStage(StrEnum):
    """流水线阶段，用于进度展示。"""

    RECEIVED = "received"          # 已接收
    DOWNLOADED = "downloaded"      # 附件已下载
    PARSED = "parsed"              # 解析完成
    METADATA_DONE = "metadata_done"
    CLAUSES_SEGMENTED = "clauses_segmented"
    RULES_APPLIED = "rules_applied"
    LLM_REVIEWED = "llm_reviewed"
    SUGGESTED = "suggested"
    AGGREGATED = "aggregated"
    REPORTED = "reported"


__all__ = [
    "StrEnum",
    "TaskStatus",
    "TASK_TRANSITIONS",
    "TASK_STATUS_LABEL",
    "RETRYABLE_TASK_STATUS",
    "WritebackStatus",
    "WRITEBACK_TRANSITIONS",
    "WRITEBACK_STATUS_LABEL",
    "RiskLevel",
    "RISK_WEIGHT",
    "RISK_LEVEL_LABEL",
    "ReviewConclusion",
    "CONCLUSION_LABEL",
    "FileKind",
    "PARSER_BY_KIND",
    "EXT_TO_KIND",
    "BlockType",
    "ClauseType",
    "CLAUSE_TYPE_LABEL",
    "ContractType",
    "CONTRACT_TYPE_LABEL",
    "MetadataField",
    "METADATA_FIELD_LABEL",
    "RiskSource",
    "SuggestionSource",
    "ItemDecision",
    "TaskSource",
    "RuleTrigger",
    "BlockReason",
    "BLOCK_REASON_LABEL",
    "ReviewStage",
]
