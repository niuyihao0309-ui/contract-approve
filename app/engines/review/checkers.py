"""规则引擎的判定器（checker）集合。

设计取舍
--------
大纲要求「把判定规则写成可配置 DSL / JSON」。但纯 JSON 表达不了「违约金日利率是否
超过万分之五」这类需要**数值解析**的判断，硬塞正则会又脆又难维护。

所以采取分工：

- **JSON 负责**规则项、触发方式、风险等级、法律依据、建议模板、适用合同/条款类型、开关、优先级；
- **Python 负责**需要计算与语义判断的部分（本模块），每个 checker 是一个纯函数，
  用 ``checker`` 字段在 JSON 里引用。

这样规则依然可配可开关可分级，而判定逻辑可单测、可复用、可解释。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.enums import ClauseType, MetadataField
from app.schemas.document import DocIR
from app.schemas.metadata import Clause, ClauseSegmentResult, MetadataResult


@dataclass
class CheckerHit:
    """一次判定命中。位置用 IR 的全局字符偏移表示。"""

    char_start: int
    char_end: int
    detail: str = ""
    document_level: bool = False


@dataclass
class ReviewContext:
    """判定上下文：checker 需要的一切都在这里。"""

    ir: DocIR
    clauses: ClauseSegmentResult
    metadata: MetadataResult
    contract_type: str = "other"
    our_side: str = "party_a"
    params: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # 常用工具
    # ------------------------------------------------------------------
    def clauses_of(self, *types: ClauseType | str) -> list[Clause]:
        want = {t.value if isinstance(t, ClauseType) else t for t in types}
        return [c for c in self.clauses.clauses if c.clause_type.value in want]

    def clause_text(self, *types: ClauseType | str) -> str:
        return "\n".join(c.content for c in self.clauses_of(*types))

    def clause_spans(self, *types: ClauseType | str) -> list[tuple[int, int]]:
        return [(c.char_start, c.char_end) for c in self.clauses_of(*types)]

    def has_clause(self, ctype: ClauseType | str) -> bool:
        return self.clauses.has(ctype if isinstance(ctype, ClauseType) else ClauseType(ctype))

    #: 本方与对方的指称（用于管辖、知识产权等「偏向谁」的判断）
    @property
    def self_terms(self) -> list[str]:
        return ["甲方", "买方", "需方", "采购方", "委托方", "发包方"] if self.our_side == "party_a" \
            else ["乙方", "卖方", "供方", "供应方", "受托方", "承包方"]

    @property
    def other_terms(self) -> list[str]:
        return ["乙方", "卖方", "供方", "供应方", "受托方", "承包方"] if self.our_side == "party_a" \
            else ["甲方", "买方", "需方", "采购方", "委托方", "发包方"]

    def anchor_document(self) -> tuple[int, int]:
        """全文级风险的锚点：整篇全文，让前端点击时至少能滚到文档开头。"""
        return 0, len(self.ir.full_text)

    def anchor_first_block(self) -> tuple[int, int]:
        blocks = self.ir.non_empty_blocks
        if not blocks:
            return 0, len(self.ir.full_text)
        b = blocks[0]
        return b.char_start, b.char_end


# ======================================================================
# 通用匹配工具
# ======================================================================
def _find_in_spans(ir: DocIR, spans: list[tuple[int, int]], pattern: re.Pattern[str]) -> list[tuple[int, int, str]]:
    """在给定字符区间集合内搜索正则，返回 ``(start, end, matched_text)``。

    直接对 ``full_text`` 做切片搜索，所以拿到的偏移天然是全局偏移 ——
    这正是「风险项必须能指回原文」的实现基础。
    """
    hits: list[tuple[int, int, str]] = []
    for s, e in spans:
        if e <= s:
            continue
        seg = ir.full_text[s:e]
        for m in pattern.finditer(seg):
            hits.append((s + m.start(), s + m.end(), m.group(0)))
    return hits


def _sentence_span(ir: DocIR, start: int, end: int, *, max_len: int = 220) -> tuple[int, int]:
    """把命中点扩展成「所在句」，作为风险证据的原文片段。

    先向左找句末标点，再向右找句末标点；找不到就退回固定窗口。
    这样证据读起来是完整一句话，而不是半截词。
    """
    text = ir.full_text
    n = len(text)
    left = start
    while left > 0 and left > start - max_len:
        if text[left - 1] in "。！？；\n":
            break
        left -= 1
    right = end
    while right < n and right < end + max_len:
        if text[right - 1] in "。！？；\n":
            break
        right += 1
    if right <= left:
        return start, end
    return left, right


def _hit_in_clauses(
    ctx: ReviewContext, types: list[str], pattern: re.Pattern[str], *, max_hits: int = 2
) -> list[CheckerHit]:
    """在指定类型的条款里搜索，返回句级证据。"""
    spans = ctx.clause_spans(*types) if types else [(0, len(ctx.ir.full_text))]
    if not spans:
        return []
    out: list[CheckerHit] = []
    for s, e, _ in _find_in_spans(ctx.ir, spans, pattern):
        ss, se = _sentence_span(ctx.ir, s, e)
        if any(abs(ss - o.char_start) < 5 for o in out):
            continue
        out.append(CheckerHit(ss, se))
        if len(out) >= max_hits:
            break
    return out


# ======================================================================
# Checker 1：主体信息缺失
# ======================================================================
def party_info_missing(ctx: ReviewContext) -> list[CheckerHit]:
    """签约主体名称或统一社会信用代码缺失 → 高风险。"""
    missing: list[str] = []
    if ctx.metadata.get(MetadataField.PARTY_A) is None or not ctx.metadata.value_of(MetadataField.PARTY_A):
        missing.append("甲方名称")
    if ctx.metadata.get(MetadataField.PARTY_B) is None or not ctx.metadata.value_of(MetadataField.PARTY_B):
        missing.append("乙方名称")
    if not ctx.metadata.value_of(MetadataField.USCC):
        missing.append("统一社会信用代码")
    if not missing:
        return []

    # 锚点：优先锚到已有的主体信息那一段，没有就锚到全文开头
    anchor = ctx.anchor_first_block()
    for key in (MetadataField.PARTY_A, MetadataField.PARTY_B, MetadataField.USCC):
        item = ctx.metadata.get(key)
        if item and item.span:
            anchor = (item.span.char_start, item.span.char_end)
            break

    return [
        CheckerHit(
            *anchor,
            detail=f"缺少：{'、'.join(missing)}",
            document_level=True,
        )
    ]


# ======================================================================
# Checker 2：违约责任不对等 / 赔偿无上限
# ======================================================================
#: 表示「责任敞口不受控」的表述
_UNLIMITED = re.compile(
    r"(?:不设|无)(?:赔偿)?上限|上不封顶|不封顶|"
    r"(?:全部|一切|所有|任何)(?:的)?(?:损失|责任|费用)|"
    r"包括但不限于[^。；\n]{0,60}(?:损失|费用)|"
    r"不限于[^。；\n]{0,40}(?:损失|费用)"
)


def unlimited_liability(ctx: ReviewContext) -> list[CheckerHit]:
    """一方赔偿责任不设上限 → 高风险。"""
    return _hit_in_clauses(ctx, [ClauseType.BREACH.value], _UNLIMITED)


# ======================================================================
# Checker 3：付款未设验收前置
# ======================================================================
_PAY_TRIGGER = re.compile(r"(?:到货|交货|签收|发货|合同生效|签订|交付)之日起[^。；\n]{0,30}(?:支付|付款)")
_PAY_FULL = re.compile(r"(?:100%\s*的?(?:合同)?(?:总)?(?:金额|价款|货款)|全部(?:合同)?(?:款项|价款|货款)|全额(?:支付|付款)|一次性(?:支付|付清))")
_ACCEPT_WORD = re.compile(r"验收")


def payment_without_acceptance(ctx: ReviewContext) -> list[CheckerHit]:
    """约定到货即付全款、且未把验收作为付款前置 → 高风险。"""
    pay_clauses = ctx.clauses_of(ClauseType.PAYMENT)
    if not pay_clauses:
        return []

    # 验收条件是否在付款条款里有体现（或全文有独立的验收条款）
    has_acceptance_clause = ctx.has_clause(ClauseType.ACCEPTANCE)

    out: list[CheckerHit] = []
    for c in pay_clauses:
        text = c.content
        if _ACCEPT_WORD.search(text):
            continue                     # 付款条款自己说了验收，视为有前置条件
        has_full = _PAY_FULL.search(text)
        has_trigger = _PAY_TRIGGER.search(text)
        if has_full and has_trigger:
            m = has_full
            ss, se = _sentence_span(ctx.ir, c.char_start + m.start(), c.char_start + m.end())
            out.append(
                CheckerHit(
                    ss,
                    se,
                    detail=(
                        "付款义务在交付/生效后即触发并覆盖全部价款，"
                        + ("且全文未设置验收条款" if not has_acceptance_clause else "付款未以验收合格为前提")
                    ),
                )
            )
        if len(out) >= 1:
            break
    return out


# ======================================================================
# Checker 4：管辖地约定不利于我方
# ======================================================================
def jurisdiction_unfavorable(ctx: ReviewContext) -> list[CheckerHit]:
    """管辖约定在对方所在地 / 管辖条款缺失 → 高风险。"""
    clauses = ctx.clauses_of(ClauseType.JURISDICTION)
    if not clauses:
        anchor = ctx.anchor_first_block()
        return [
            CheckerHit(
                *anchor,
                detail="全文未约定争议解决方式与管辖法院，只能适用法定管辖，结果不可控",
                document_level=True,
            )
        ]

    # 对方所在地的表述
    other_loc = re.compile(
        r"(?:"
        + "|".join(re.escape(t) for t in ctx.other_terms)
        + r")(?:公司)?(?:所在|住所|注册)地"
    )
    out: list[CheckerHit] = []
    for c in clauses:
        for m in other_loc.finditer(c.content):
            # 只有当条款里没有同时约定我方所在地时才判定为不利
            if not re.search(
                r"(?:" + "|".join(re.escape(t) for t in ctx.self_terms) + r")(?:公司)?(?:所在|住所|注册)地",
                c.content,
            ):
                ss, se = _sentence_span(ctx.ir, c.char_start + m.start(), c.char_start + m.end())
                out.append(
                    CheckerHit(
                        ss,
                        se,
                        detail=f"管辖约定在{ctx.other_terms[0]}所在地，我方涉诉需异地应诉，诉讼成本与举证难度显著上升",
                    )
                )
                break
        if out:
            break
    return out


# ======================================================================
# Checker 5：知识产权归属不利于我方
# ======================================================================
def _split_sentences(text: str) -> list[tuple[int, str]]:
    """把一段文本按句末标点切开，返回 ``(相对偏移, 句子)``。

    为什么需要它：一个段落里常同时写着「对的约定」和「有问题的约定」，例如
    「乙方原有知识产权归乙方所有；甲方定制成果归甲方所有」。
    必须在**句子**粒度上判断，整段一起判会把正常约定也一起误伤。
    """
    out: list[tuple[int, str]] = []
    start = 0
    for i, ch in enumerate(text):
        if ch in "。；！？\n":
            seg = text[start : i + 1].strip()
            if seg:
                out.append((start, seg))
            start = i + 1
    tail = text[start:].strip()
    if tail:
        out.append((start, tail))
    return out


#: 「对方原有的 / 背景性的」知识产权 —— 归属对方是正常商业安排，不算风险
_IP_PREEXISTING = re.compile(
    r"原有|既有|原本|原来|固有|既存|背景(?:知识)?产权|自有|已有|先前|既往"
)


def ip_unfavorable(ctx: ReviewContext) -> list[CheckerHit]:
    """定制成果或数据权益无条件归对方 → 高风险。"""
    clauses = ctx.clauses_of(ClauseType.IP)
    if not clauses:
        return []

    other = "|".join(re.escape(t) for t in ctx.other_terms)
    self_ = "|".join(re.escape(t) for t in ctx.self_terms)
    patterns = [
        # 全部知识产权归对方
        re.compile(rf"(?:知识产权|著作权|专利权|成果|版权)[^。；\n]{{0,20}}(?:均)?归[^。；\n]{{0,6}}(?:{other})所有"),
        # 源代码 / 源程序归对方
        re.compile(rf"(?:源代码|源程序)[^。；\n]{{0,20}}归(?:{other})"),
        # 对方可自行处置我方数据
        re.compile(rf"(?:{other})(?:有权|可以)[^。；\n]{{0,20}}(?:脱敏|使用|分析|处置)(?:[^。；\n]{{0,10}})?(?:数据|业务数据)"),
        # 我方仅获使用权 / 非独占授权
        re.compile(rf"(?:{self_})(?:仅|只|仅只)?(?:享有|获得|取得)[^。；\n]{{0,30}}(?:使用权|非独占|不可转让)"),
    ]

    out: list[CheckerHit] = []
    for c in clauses:
        for off, sent in _split_sentences(c.content):
            # 「对方原有的知识产权归对方」是标准写法，跳过
            if _IP_PREEXISTING.search(sent):
                continue
            m = next((p.search(sent) for p in patterns if p.search(sent)), None)
            if m is None:
                continue
            ss, se = _sentence_span(
                ctx.ir, c.char_start + off + m.start(), c.char_start + off + m.end()
            )
            if any(abs(ss - o.char_start) < 5 for o in out):
                continue
            out.append(
                CheckerHit(
                    ss,
                    se,
                    detail="项目定制成果的知识产权归对方所有，我方出资但无法取得成果权利",
                )
            )
            break
        if len(out) >= 2:
            break
    return out


# ======================================================================
# Checker 6：保密义务缺期限
# ======================================================================
_TERM_WORDS = re.compile(
    r"(?:保密)?期限|有效期|持续(?:至|到)?|(?:[一二三四五六七八九十两\d]+)\s*年(?:内|后)|"
    r"合同(?:终止|结束|解除)后\s*[一二三四五六七八九十两\d]+\s*年"
)


def confidentiality_no_term(ctx: ReviewContext) -> list[CheckerHit]:
    """有保密条款但没有保密期限 → 中风险。"""
    out: list[CheckerHit] = []
    for c in ctx.clauses_of(ClauseType.CONFIDENTIALITY):
        if _TERM_WORDS.search(c.content):
            continue
        head = c.block_ids[0] if c.block_ids else ""
        blk = ctx.ir.get_block(head)
        ss = blk.char_start if blk else c.char_start
        se = blk.char_end if blk else c.char_end
        out.append(CheckerHit(ss, se, detail="约定了保密义务但未明确保密期限，合同终止后义务是否存续存在争议"))
    return out[:1]


# ======================================================================
# Checker 7：不可抗力未约定通知时效
# ======================================================================
_NOTICE_DEADLINE = re.compile(r"(?:[一二三四五六七八九十两\d]+)\s*(?:个)?(?:工作日|自然日|日|天|小时)内")


def force_majeure_no_deadline(ctx: ReviewContext) -> list[CheckerHit]:
    """不可抗力条款缺失，或缺通知时效 → 中风险。"""
    clauses = ctx.clauses_of(ClauseType.FORCE_MAJEURE)
    if not clauses:
        anchor = ctx.anchor_first_block()
        return [
            CheckerHit(
                *anchor,
                detail="全文未约定不可抗力条款，我方作为主张免责一方将缺乏程序依据",
                document_level=True,
            )
        ]
    out: list[CheckerHit] = []
    for c in clauses:
        if _NOTICE_DEADLINE.search(c.content):
            continue
        blk = ctx.ir.get_block(c.block_ids[0]) if c.block_ids else None
        out.append(
            CheckerHit(
                blk.char_start if blk else c.char_start,
                blk.char_end if blk else c.char_end,
                detail="不可抗力条款未约定通知时效与证明提交期限，事后极易就「是否及时通知」产生争议",
            )
        )
    return out[:1]


# ======================================================================
# Checker 8：违约金比例超出合理范围
# ======================================================================
#: 「每日 / 每逾期一日 按 …… 万分之X」
_DAILY_RATE = re.compile(r"(?:每日|每天|每逾期一日|每日按)[^。；\n]{0,30}?万分之([零一二三四五六七八九十两\d]+)")
#: 「按 …… 万分之X …… 计算」的裸露写法
_BARE_RATE = re.compile(r"万分之([零一二三四五六七八九十两\d]+)")
_CAP_PERCENT = re.compile(r"(?:不超过|上限(?:为)?|以)[^。；\n]{0,20}?([\d.]+)\s*%")

_CN_DIGIT = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _to_number(raw: str) -> float | None:
    try:
        return float(raw)
    except ValueError:
        pass
    if raw in _CN_DIGIT:
        return float(_CN_DIGIT[raw])
    # 「十五」这类
    if len(raw) == 2 and raw[0] == "十" and raw[1] in _CN_DIGIT:
        return 10.0 + _CN_DIGIT[raw[1]]
    if len(raw) == 2 and raw[1] == "十" and raw[0] in _CN_DIGIT:
        return _CN_DIGIT[raw[0]] * 10.0
    return None


def penalty_ratio_out_of_range(ctx: ReviewContext) -> list[CheckerHit]:
    """违约金日利率 > 万分之五（约定参数），或累计上限 > 30% → 中风险。"""
    max_daily = float(ctx.params.get("max_daily_wan", 5))
    max_cap = float(ctx.params.get("max_cap_percent", 30))

    out: list[CheckerHit] = []
    for c in ctx.clauses_of(ClauseType.BREACH, ClauseType.OTHER):
        text = c.content

        m = _DAILY_RATE.search(text) or _BARE_RATE.search(text)
        if m:
            v = _to_number(m.group(1))
            if v is not None and v > max_daily:
                ss, se = _sentence_span(ctx.ir, c.char_start + m.start(), c.char_start + m.end())
                out.append(
                    CheckerHit(
                        ss, se,
                        detail=f"违约金日利率为万分之{m.group(1)}，高于常见的万分之{max_daily:.0f}，存在被认定过高的风险",
                    )
                )

        for cm in _CAP_PERCENT.finditer(text):
            try:
                cap = float(cm.group(1))
            except ValueError:
                continue
            if cap > max_cap:
                ss, se = _sentence_span(ctx.ir, c.char_start + cm.start(), c.char_start + cm.end())
                if any(abs(ss - o.char_start) < 5 for o in out):
                    continue
                out.append(
                    CheckerHit(
                        ss, se,
                        detail=f"违约金累计上限为 {cap:.0f}%，高于常见的 {max_cap:.0f}% 参考线",
                    )
                )
        if len(out) >= 2:
            break
    return out


# ======================================================================
# Checker 9：数据安全条款缺失
# ======================================================================
_DATA_MENTION = re.compile(r"数据|个人信息|隐私|系统(?:接入|对接)|账号|用户信息")


def data_clause_missing(ctx: ReviewContext) -> list[CheckerHit]:
    """合同涉及数据但无数据安全条款 → 中风险。"""
    if ctx.has_clause(ClauseType.DATA_SECURITY):
        return []
    mention = _DATA_MENTION.search(ctx.ir.full_text)
    if not mention:
        return []
    ss, se = _sentence_span(ctx.ir, mention.start(), mention.end())
    return [
        CheckerHit(
            ss, se,
            detail="合同履约涉及业务数据/个人信息，但未约定数据安全责任、数据使用边界与泄露处置机制",
        )
    ]


# ======================================================================
# Checker 10：必备条款缺失
# ======================================================================
#: 必备条款：(类型, 中文名, 缺失时的等级由规则的 risk_level 决定)
REQUIRED_CLAUSES: list[tuple[ClauseType, str]] = [
    (ClauseType.SUBJECT_MATTER, "标的物条款"),
    (ClauseType.PAYMENT, "付款进度条款"),
    (ClauseType.ACCEPTANCE, "验收标准条款"),
    (ClauseType.BREACH, "违约责任条款"),
]


def required_clause_missing(ctx: ReviewContext) -> list[CheckerHit]:
    """法定的核心条款没能切分出来 → 中风险。"""
    missing = [label for ctype, label in REQUIRED_CLAUSES if not ctx.has_clause(ctype)]
    if not missing:
        return []
    # 锚到已有的第一个核心条款上，便于前端跳转
    anchor = ctx.anchor_first_block()
    for ctype, _ in REQUIRED_CLAUSES:
        cs = ctx.clauses_of(ctype)
        if cs:
            anchor = (cs[0].char_start, cs[0].char_end)
            break
    return [
        CheckerHit(
            *anchor,
            detail="未从合同中切分出以下核心条款：" + "、".join(missing),
            document_level=True,
        )
    ]


# ======================================================================
# 注册表
# ======================================================================
CHECKERS: dict[str, callable] = {
    "party_info_missing": party_info_missing,
    "unlimited_liability": unlimited_liability,
    "payment_without_acceptance": payment_without_acceptance,
    "jurisdiction_unfavorable": jurisdiction_unfavorable,
    "ip_unfavorable": ip_unfavorable,
    "confidentiality_no_term": confidentiality_no_term,
    "force_majeure_no_deadline": force_majeure_no_deadline,
    "penalty_ratio_out_of_range": penalty_ratio_out_of_range,
    "data_clause_missing": data_clause_missing,
    "required_clause_missing": required_clause_missing,
}


__all__ = ["CheckerHit", "ReviewContext", "CHECKERS", "REQUIRED_CLAUSES"]
