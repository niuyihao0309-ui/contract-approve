"""标题识别（DOCX / PDF / 条款切分三处共用）。

之前的版本把 ``1.1 甲方向乙方采购……`` 这种**条内小项**也当标题，
导致一条「第六条 违约责任」被切成一堆碎片。核心修复是加了两道闸：

1. **只有「第X条 / 第X章 / 一、」级别才算条款标题**，``1.1`` 这种是条内项。
2. 数字编号项只有当剩余文本**足够短、且不含句末标点**时才升级为标题 ——
   因为真正的标题是「1.1 合同标的」，而不是「1.2 交付内容包括：……。」。
"""

from __future__ import annotations

import re

#: 「第X条 / 第X章 / 第X节 / 第X部分」—— 最标准的条款标题
_CLAUSE = re.compile(
    r"^\s*第\s*([一二三四五六七八九十百零〇\d]+)\s*"
    r"(条|章|节|部分|编|项)\s*[:：、.．]?\s*(.*)$"
)
#: 「一、」「（一）」「(一)」—— 中文序号标题
_ORDINAL = re.compile(r"^\s*(?:[（(]\s*)?([一二三四五六七八九十]{1,3})\s*[)）]?\s*[、.．]\s*(.*)$")
#: 「1」「1.1」「1.1.1」「1、」—— 数字编号
_NUMERIC = re.compile(r"^\s*(\d+(?:\.\d+)*)\s*[、.．)）]?\s+(.*)$")

#: 标题里不该出现的标点（出现即判定为正文句）
_NOT_TITLE_PUNCT = "。，；！？,;!?"
#: 数字编号升级为标题时，剩余文本的最大长度
_NUMERIC_TITLE_MAX_REST = 30
#: 任何标题的整行最大长度
_TITLE_MAX_LEN = 60

#: 章 / 条 / 节 对应的层级
_CLAUSE_LEVEL = {"编": 1, "章": 1, "部分": 1, "节": 2, "条": 2, "项": 3}


def detect_heading(text: str, *, style_level: int | None = None) -> int | None:
    """判断一段文本是否是标题，返回层级（1 最高）或 ``None``。

    :param style_level: Word 样式表给出的层级，优先级最高（作者标了就是标了）
    """
    if style_level:
        return style_level

    t = (text or "").strip()
    if not t or len(t) > _TITLE_MAX_LEN:
        return None

    # 表格行永远不是标题。表格被拍平成 "单元格 | 单元格 | ……" 的形式，
    # 首列常是「1」「2」这种序号，不加这条闸就会被误判成编号标题。
    if "|" in t or "｜" in t:
        return None

    if m := _CLAUSE.match(t):
        unit = m.group(2)
        rest = (m.group(3) or "").strip()
        # 「第六条 违约责任」是标题；「第八条所述内容……。」是正文引用
        if not rest or not _has_sentence_punct(rest):
            return _CLAUSE_LEVEL.get(unit, 2)
        return None

    if m := _ORDINAL.match(t):
        rest = (m.group(2) or "").strip()
        if rest and len(rest) <= 24 and not _has_sentence_punct(rest):
            return 3
        return None

    if m := _NUMERIC.match(t):
        rest = (m.group(2) or "").strip()
        if len(rest) <= _NUMERIC_TITLE_MAX_REST and not _has_sentence_punct(rest):
            depth = m.group(1).count(".") + 1
            return min(3 + depth, 9)
        return None

    return None


def _has_sentence_punct(text: str) -> bool:
    return any(ch in text for ch in _NOT_TITLE_PUNCT)


def clean_heading(text: str) -> str:
    """把编号与标题正文接成规范的标题文本（保留编号，「第六条」是要显示的）。"""
    t = (text or "").strip()
    for pattern in (_CLAUSE, _ORDINAL, _NUMERIC):
        m = pattern.match(t)
        if not m:
            continue
        groups = m.groups()
        rest = (groups[-1] or "").strip()
        cut = m.end() - len(groups[-1]) if groups[-1] else m.end()
        head = t[:cut].strip().rstrip(":：、.．)）").strip()
        return f"{head} {rest}".strip() if rest else head
    return t[:50]


#: 公司/组织名称特征词，用于「主体名称」正则的候选打分
ORG_MARKERS = (
    "公司", "集团", "企业", "中心", "工厂", "厂", "研究院", "学院", "大学",
    "事务所", "银行", "医院", "合作社", "合伙", "商行", "商店", "酒店",
    "有限公司", "股份", "工作室", "协会", "学会", "机关", "事务",
)


def looks_like_org(name: str) -> bool:
    """判断一个字符串像不像主体名称。"""
    n = (name or "").strip()
    if not (2 <= len(n) <= 60):
        return False
    if any(bad in n for bad in ("应", "须", "不得", "有权", "负责", "按照")):
        return False
    return any(mark in n for mark in ORG_MARKERS)


__all__ = ["detect_heading", "clean_heading", "looks_like_org", "ORG_MARKERS"]
