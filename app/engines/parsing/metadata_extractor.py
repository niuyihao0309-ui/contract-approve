"""核心元数据抽取：正则 + LLM 双通道。

大纲 2.5 要求「正则兜底，LLM 补语义」。这里的实现顺序是反过来的 ——
**正则先跑**，因为正则匹配能直接拿到精确的字符偏移（``m.span()``），
定位天然准确；只有在正则抽不到的字段上才叫 LLM，并用它给的 ``evidence``
原文片段反查位置（:meth:`DocIR.locate_by_text`）。

这样既保证偏移精度，又让 LLM 调用量最小（只补缺字段），成本可控。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import logger
from app.engines.parsing.heading import looks_like_org
from app.llm.client import LLMClient, get_llm
from app.models.enums import MetadataField
from app.schemas.document import DocIR, SourceSpan
from app.schemas.metadata import MetadataItem, MetadataResult

# ======================================================================
# 正则表
# ======================================================================
_USCC = r"[0-9A-HJ-NPQRTUWXY]{18}"

#: 行内空白（**不含换行**）。金额类正则必须用它而不是 ``\s`` ——
#: ``\s`` 会吃掉换行，导致「第二条 合同金额与付款方式\n2.1 …」里的 "2.1"
#: 被当成金额抽出来。这是踩过的坑，别改回去。
_SP = r"[ \t\u3000]"

#: 甲乙方。允许名称里带全角括号（如「云枢软件技术（上海）有限公司」），
#: 也允许「（以下简称"甲方"）」这类插入语，收尾在 _clean_name 里统一清理。
_PARTY_A = re.compile(
    r"(?:甲方|买方|需方|委托方|发包方|采购方|出租方|甲方单位)"
    r"[^\n:：]{0,20}?[:：]\s*([^\n]{2,80})"
)
_PARTY_B = re.compile(
    r"(?:乙方|卖方|供方|受托方|承包方|供应方|承租方|乙方单位)"
    r"[^\n:：]{0,20}?[:：]\s*([^\n]{2,80})"
)
_USCC_RE = re.compile(
    rf"(?:统一社会信用代码|社会信用代码|纳税人识别号|信用代码)[^\n]{{0,12}}?[:：]?\s*({_USCC})"
)
_USCC_BARE = re.compile(rf"(?<![0-9A-Z])({_USCC})(?![0-9A-Z])")

_AMOUNT = re.compile(
    r"(?:合同(?:总)?金额|合同总价|总金额|合同价款|价款|总价款|总价|金额)"
    rf"[^\n]{{0,24}}?"
    rf"(?:为|是|:|：)?{_SP}*"
    rf"(?:人民币|RMB|CNY|￥|¥|\$)?{_SP}*"
    r"([0-9][0-9,，]*(?:\.[0-9]{1,2})?)"
    rf"{_SP}*(万元|万|元|圆|美元|USD)?"
)
_CURRENCY = re.compile(r"(人民币|美金|美元|欧元|港币|日元|USD|CNY|EUR|HKD|JPY|￥|¥|\$)")

_PERIOD_SPAN = re.compile(
    r"自\s*(\d{4}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?)\s*"
    r"(?:起|开始)?\s*(?:至|到|—|-)\s*"
    r"(\d{4}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?)"
)
_PERIOD = re.compile(
    r"(?:合同|服务|合作|租赁|履行)?(?:期限|有效期|履行期|服务期)"
    r"[^\n]{0,16}?[:：为是]?\s*([^\n，,。；;]{4,80})"
)
_PERIOD_DAYS = re.compile(r"(?:有效期|期限|服务期)[^\n]{0,10}?(\d{1,4})\s*(?:个)?(?:自然)?(?:日|天|月|年)")

_EFFECTIVE = re.compile(r"本(?:合同|协议|协议书)(?:书)?(?:自|经|于)[^\n]{0,80}?(?:生效|成立)")

_CONTRACT_NO = re.compile(
    r"(?:合同|协议|协议书)?\s*(?:编号|文号|No\.?|NO\.?|no\.?)"
    r"\s*[:：]?\s*([A-Za-z0-9\u4e00-\u9fff\-_/()（）\[\]]{3,40})"
)

_CN_UNIT = {"万": 10000.0, "万元": 10000.0, "元": 1.0, "圆": 1.0}

_CURRENCY_NORMALIZE = {
    "￥": "CNY", "¥": "CNY", "人民币": "CNY", "CNY": "CNY",
    "美元": "USD", "美金": "USD", "USD": "USD", "$": "USD",
    "欧元": "EUR", "EUR": "EUR",
    "港币": "HKD", "HKD": "HKD",
    "日元": "JPY", "JPY": "JPY",
}


@dataclass
class _Hit:
    """一次正则命中的结果。"""

    value: str
    start: int
    end: int
    normalized: str | None = None
    confidence: float = 0.9


# ======================================================================
# LLM 返回结构
# ======================================================================
class _LLMMetadataItem(BaseModel):
    field_key: str = Field(..., description="Eight known keys")
    value: str | None = None
    evidence: str | None = Field(default=None, description="必须逐字复制原文")
    confidence: float = 0.7


class MetadataLLMOut(BaseModel):
    items: list[_LLMMetadataItem] = Field(default_factory=list)


# ======================================================================
# 工具
# ======================================================================
def _clean_name(raw: str) -> str:
    """清掉主体名称里的引号、简称说明、尾部标点。"""
    s = raw.strip().strip("：: 　")
    s = re.sub(r"[（(]\s*(?:以下|下称|简称)[^）)]*[）)]", "", s)
    s = s.split("；")[0].split(";")[0]
    return s.strip("，,。；; 、")


def _pick_party(regex: re.Pattern[str], text: str) -> tuple[str, int, int] | None:
    """在多个候选里挑最像主体名称的那个。

    正文里「甲方应于……」「乙方有权……」这类句子有时也带冒号，会产生误命中。
    所以遍历全部候选，优先选**包含公司/集团/中心等组织形式特征词**的那个；
    都不像就退回第一个候选（可能是自然人，如劳动合同里的甲方）。
    """
    candidates: list[tuple[str, int, int]] = []
    for m in regex.finditer(text):
        # 只取该行剩余部分，避免跨行吃掉下一段
        raw = m.group(1)
        name = _clean_name(raw)
        if 2 <= len(name) <= 60:
            candidates.append((name, m.start(1), m.start(1) + len(raw)))

    for name, s, e in candidates:
        if looks_like_org(name):
            # 有组织形式特征词，且长度合理 → 直接采纳
            return name, s, s + len(name)
    return candidates[0] if candidates else None


def _norm_amount(num: str, unit: str | None) -> str | None:
    try:
        v = float(num.replace(",", "").replace("，", ""))
    except ValueError:
        return None
    v *= _CN_UNIT.get(unit or "", 1.0)
    return f"{v:.2f}"


def _to_span(ir: DocIR, start: int, end: int, fallback_text: str = "") -> SourceSpan | None:
    """由绝对偏移构造证据引用；越界时退回按文本搜索。"""
    n = len(ir.full_text)
    if 0 <= start < end <= n:
        blk = ir.locate(start, end)
        if blk is not None:
            return SourceSpan(
                block_id=blk.block_id,
                char_start=start,
                char_end=end,
                page=blk.page,
                para_index=blk.para_index,
                quote=ir.slice_text(start, end, 300),
            )
    if fallback_text:
        found = ir.locate_by_text(fallback_text)
        if found:
            blk, s, e = found
            return SourceSpan(
                block_id=blk.block_id, char_start=s, char_end=e,
                page=blk.page, para_index=blk.para_index,
                quote=ir.slice_text(s, e, 300),
            )
    return None


class MetadataExtractor:
    """元数据抽取器。"""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or get_llm()

    # ------------------------------------------------------------------
    async def extract(self, ir: DocIR, *, use_llm: bool = True) -> MetadataResult:
        """先正则，再按需 LLM。结果一定包含全部 8 个字段（抽不到的值为 None）。"""
        items: dict[str, MetadataItem] = {}

        for field, hit in self._regex_pass(ir):
            prev = items.get(field.value)
            if prev is not None and prev.confidence >= hit.confidence:
                continue
            span = _to_span(ir, hit.start, hit.end, hit.value)
            items[field.value] = MetadataItem(
                field_key=field,
                field_value=hit.value,
                normalized_value=hit.normalized,
                span=span,
                source="regex",
                confidence=hit.confidence if span else round(hit.confidence * 0.6, 3),
            )

        for field in MetadataField:
            items.setdefault(
                field.value,
                MetadataItem(field_key=field, field_value=None, source="regex", confidence=0.0),
            )

        missing = [MetadataField(k) for k, v in items.items() if not v.field_value]
        if missing and use_llm and self.llm.enabled:
            await self._llm_pass(ir, items, missing)

        result = MetadataResult(items=list(items.values()))
        logger.info(
            f"[元数据] 命中 {sum(1 for i in result.items if i.field_value)}"
            f"/{len(result.items)} 项"
            + (f"，仍缺失 {[m.value for m in result.missing_fields]}" if result.missing_fields else "")
        )
        return result

    # ------------------------------------------------------------------
    # 正则通道
    # ------------------------------------------------------------------
    def _regex_pass(self, ir: DocIR) -> Iterable[tuple[MetadataField, _Hit]]:
        text = ir.full_text

        if hit := _pick_party(_PARTY_A, text):
            name, s, e = hit
            yield MetadataField.PARTY_A, _Hit(name, s, e, confidence=0.9)

        if hit := _pick_party(_PARTY_B, text):
            name, s, e = hit
            yield MetadataField.PARTY_B, _Hit(name, s, e, confidence=0.9)

        m = _USCC_RE.search(text) or _USCC_BARE.search(text)
        if m:
            yield MetadataField.USCC, _Hit(
                m.group(1), m.start(1), m.end(1), normalized=m.group(1), confidence=0.98
            )

        if m := _AMOUNT.search(text):
            amount = _norm_amount(m.group(1), m.group(2))
            if amount:
                yield MetadataField.AMOUNT, _Hit(
                    m.group(1) + (m.group(2) or ""),
                    m.start(1),
                    m.end(1),
                    normalized=amount,
                    confidence=0.9,
                )

        if m := _CURRENCY.search(text):
            raw = m.group(1)
            yield MetadataField.CURRENCY, _Hit(
                raw, m.start(1), m.end(1),
                normalized=_CURRENCY_NORMALIZE.get(raw, raw.upper()),
                confidence=0.85,
            )

        hit: _Hit | None = None
        if m := _PERIOD_SPAN.search(text):
            hit = _Hit(f"{m.group(1)} 至 {m.group(2)}", m.start(), m.end(), confidence=0.92)
        elif m := _PERIOD.search(text):
            v = _clean_name(m.group(1))
            if len(v) >= 4:
                hit = _Hit(v, m.start(1), m.end(1), confidence=0.78)
        elif m := _PERIOD_DAYS.search(text):
            hit = _Hit(m.group(0), m.start(), m.end(), confidence=0.7)
        if hit:
            yield MetadataField.PERIOD, hit

        if m := _EFFECTIVE.search(text):
            yield MetadataField.EFFECTIVE_CONDITION, _Hit(
                m.group(0), m.start(), m.end(), confidence=0.85
            )

        if m := _CONTRACT_NO.search(text):
            v = m.group(1).strip()
            if 2 < len(v) <= 40:
                yield MetadataField.CONTRACT_NO, _Hit(v, m.start(1), m.end(1), confidence=0.8)

    # ------------------------------------------------------------------
    # LLM 通道
    # ------------------------------------------------------------------
    async def _llm_pass(
        self,
        ir: DocIR,
        items: dict[str, MetadataItem],
        missing: list[MetadataField],
    ) -> None:
        logger.info(f"[元数据] 正则未命中 {[m.value for m in missing]}，调用 LLM 补抽")

        budget = settings.llm_chunk_chars * 3
        doc_text = ir.full_text[:budget]
        if len(ir.full_text) > budget:
            doc_text += "\n……（后文略，仅用于补充抬头与金额信息）"

        out = await self.llm.complete_typed(
            prompt_name="metadata_extract",
            variables={"contract_text": doc_text},
            schema=MetadataLLMOut,
            temperature=0.0,
        )
        if out is None:
            return

        for it in out.items:
            key = (it.field_key or "").strip()
            if key not in items or items[key].field_value:
                continue
            if key not in {f.value for f in MetadataField}:
                continue
            if not (it.value or "").strip():
                continue

            span = _to_span(ir, -1, -1, fallback_text=it.evidence or "") if it.evidence else None
            items[key] = MetadataItem(
                field_key=MetadataField(key),
                field_value=it.value.strip(),
                span=span,
                source="llm",
                confidence=float(min(max(it.confidence, 0.1), 1.0)),
            )
            if span is None:
                logger.warning(
                    f"[元数据] LLM 给的 evidence 在原文中定位失败（{key}）："
                    f"{(it.evidence or '')[:40]}…"
                )


__all__ = ["MetadataExtractor", "MetadataLLMOut"]
