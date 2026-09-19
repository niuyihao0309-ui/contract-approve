"""审查报告生成与导出。

两种输出
--------
1. **审查报告**（大纲 2.4.8）：合同基本信息 + 综合审查结论 + 风险清单明细 + 回写意见
2. **回写评论文本**：格式化的 Markdown，含风险总评 + 重点关注事项 + 法务批注

导出
----
先出 Markdown（人可读、可 diff、可再编辑），再由 Markdown 出 PDF。
PDF 用 reportlab 的 platypus 排版，中文字体按「Windows 系统字体 → reportlab 内置
CID 字体」的顺序探测，所以在没有系统字体的环境里也能出中文 PDF。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.config import settings
from app.core.logging import logger
from app.models.enums import (
    CLAUSE_TYPE_LABEL,
    CONCLUSION_LABEL,
    CONTRACT_TYPE_LABEL,
    RISK_LEVEL_LABEL,
    RISK_LEVEL_LABEL as _RLL,
)

# ======================================================================
# Markdown 生成
# ======================================================================
_RISK_ORDER = {"high": 0, "medium": 1, "low": 2}


def _fmt_amount(amount: Any, currency: str | None) -> str:
    if amount in (None, ""):
        return "—"
    try:
        v = float(amount)
    except (TypeError, ValueError):
        return str(amount)
    return f"{currency or 'CNY'} {v:,.2f}"


def _dt(value: Any) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        return value
    try:
        return value.strftime("%Y-%m-%d %H:%M")
    except Exception:  # pragma: no cover
        return str(value)


def build_report_markdown(detail: dict, risks: list[dict]) -> str:
    """生成完整审查报告（Markdown）。``detail`` 用 TaskQueryService.detail 的结构。"""
    meta = {m["field_key"]: m for m in detail.get("metadata", [])}

    def mv(key: str, default: str = "—") -> str:
        it = meta.get(key)
        return (it.get("value") or default) if it else default

    overall = detail.get("overall_risk")
    counts = detail.get("risk_counts") or {"high": 0, "medium": 0, "low": 0}

    lines: list[str] = []
    lines.append(f"# 合同审查报告 · {detail.get('title', '未命名合同')}")
    lines.append("")
    lines.append(f"> 报告生成时间：{datetime.now():%Y-%m-%d %H:%M}　|　"
                 f"报告编号：RPT-{str(detail.get('id', ''))[:8].upper()}")
    lines.append("")

    # ---------- 一、合同基本信息 ----------
    lines.append("## 一、合同基本信息")
    lines.append("")
    lines.append("| 项目 | 内容 |")
    lines.append("| --- | --- |")
    rows = [
        ("合同标题", detail.get("title") or "—"),
        ("合同编号", detail.get("contract_no") or mv("contract_no")),
        ("送审部门", detail.get("department") or "—"),
        ("申请人", detail.get("applicant") or "—"),
        ("业务类型", CONTRACT_TYPE_LABEL.get(detail.get("business_type") or "", "—")),
        ("我方（甲方）", mv("party_a")),
        ("相对方（乙方）", detail.get("counterparty") or mv("party_b")),
        ("统一社会信用代码", mv("uscc")),
        ("合同金额", _fmt_amount(detail.get("amount"), detail.get("currency"))),
        ("履行期限", detail.get("period") or mv("period")),
        ("生效条件", mv("effective_condition")),
        ("审查完成时间", _dt(detail.get("completed_at"))),
    ]
    lines += [f"| {k} | {v} |" for k, v in rows]
    lines.append("")

    # ---------- 二、综合审查结论 ----------
    lines.append("## 二、综合审查结论")
    lines.append("")
    lines.append(f"**总风险等级：{RISK_LEVEL_LABEL.get(overall or '', '无风险')}**　"
                 f"　**审查结论：{detail.get('conclusion_label') or CONCLUSION_LABEL.get(detail.get('conclusion') or '', '通过')}**")
    lines.append("")
    lines.append(f"风险分布：高风险 {counts.get('high', 0)} 项 · "
                 f"中风险 {counts.get('medium', 0)} 项 · 低风险 {counts.get('low', 0)} 项")
    lines.append("")
    if detail.get("risk_summary"):
        lines.append("**核心风险摘要**")
        lines.append("")
        lines.append(detail["risk_summary"])
        lines.append("")
    if detail.get("focus_points"):
        lines.append("**审批关注要点**")
        lines.append("")
        for i, p in enumerate(detail["focus_points"], 1):
            lines.append(f"{i}. {p}")
        lines.append("")

    # ---------- 三、风险清单明细 ----------
    lines.append("## 三、风险清单明细")
    lines.append("")
    if not risks:
        lines.append("未发现风险项。")
        lines.append("")
    else:
        ordered = sorted(risks, key=lambda r: (_RISK_ORDER.get(r["risk_level"], 9), r["risk_no"]))
        for r in ordered:
            lines.append(
                f"### 风险 {r['risk_no']}　{r['name']}"
                f"　`{RISK_LEVEL_LABEL.get(r['risk_level'], r['risk_level'])}`"
            )
            lines.append("")
            lines.append(f"- **风险等级**：{RISK_LEVEL_LABEL.get(r['risk_level'], r['risk_level'])}"
                         f"（来源：{'规则引擎' if r.get('source') == 'rule' else '大模型语义研判'}"
                         + (f" · {r['rule_id']}" if r.get("rule_id") else "") + "）")
            lines.append(f"- **涉及条款**：{CLAUSE_TYPE_LABEL.get(r.get('clause_type') or '', '—')}")
            lines.append(f"- **原文定位**：{r.get('location_label') or '—'}"
                         f"（{r.get('block_id')} · 字符 {r.get('char_start')}-{r.get('char_end')}）")
            quote = (r.get("quote") or "").strip().replace("\n", " ")
            if len(quote) > 200:
                quote = quote[:200] + "…"
            lines.append(f"- **原文片段**：")
            lines.append("")
            lines.append(f"  > {quote or '（全文性风险，无具体片段）'}")
            lines.append("")
            lines.append(f"- **风险成因**：{(r.get('reason') or '—').strip()}")
            lines.append(f"- **法律依据**：{(r.get('legal_basis') or '—').strip()}")
            sug = r.get("suggestion") or {}
            if sug:
                lines.append("")
                lines.append("**推荐修改示范条款**")
                lines.append("")
                lines.append("```")
                lines.append((sug.get("final_text") or sug.get("suggested_text") or "").strip())
                lines.append("```")
                if sug.get("rationale"):
                    lines.append("")
                    lines.append(f"*改写说明：{sug['rationale']}*")
            if r.get("lawyer_note"):
                lines.append("")
                lines.append(f"**法务意见**：{r['lawyer_note']}")
            lines.append("")

    # ---------- 四、法务批注记录 ----------
    comments = [c for c in detail.get("comments", []) if c.get("kind") in ("comment", "decision")]
    if comments:
        lines.append("## 四、法务批注与协同记录")
        lines.append("")
        for c in comments:
            lines.append(f"- **{_dt(c.get('created_at'))}　{c.get('author')}**：{c.get('content')}")
        lines.append("")

    # ---------- 五、附件与解析信息 ----------
    lines.append("## 五、附件说明")
    lines.append("")
    for f in detail.get("files", []):
        lines.append(
            f"- `{f['filename']}`（{f['file_kind']}，{f['file_size']} 字节，"
            f"sha256:{f['hash_short']}…）→ 解析器 {f.get('parser') or '—'}，"
            f"{f.get('page_count') or '—'} 页"
            + ("　（命中指纹缓存，复用历史解析结果）" if f.get("from_cache") else "")
        )
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"*本报告由合同审批智能审查系统自动生成，"
                 f"共 {len(risks)} 项风险，风险等级与结论由确定性规则计算得出，"
                 f"修改示范条款与摘要可能包含大模型生成内容，须经法务人工复核后方可对外使用。*")
    return "\n".join(lines)


def build_writeback_markdown(
    detail: dict, risks: list[dict], *, lawyer_notes: str = "", with_timestamp: bool = True
) -> str:
    """生成写回审批系统评论区的格式化意见文本。

    :param with_timestamp: 是否在末尾附「生成时间」。展示/回写时要用（True）；
        **算幂等键时必须传 False** —— 时间戳每次都不同，带上它会让同一份意见
        永远算不出同一个哈希，幂等就彻底失效了。
    """
    counts = detail.get("risk_counts") or {"high": 0, "medium": 0, "low": 0}
    overall = detail.get("overall_risk")
    lines: list[str] = []

    lines.append(f"### 【智能审查意见】{detail.get('title', '')}")
    lines.append("")
    lines.append(
        f"**综合结论**：总风险等级 **{RISK_LEVEL_LABEL.get(overall or '', '无风险')}**，"
        f"审查意见 **{detail.get('conclusion_label') or CONCLUSION_LABEL.get(detail.get('conclusion') or '', '通过')}**　"
        f"（高 {counts.get('high', 0)} / 中 {counts.get('medium', 0)} / 低 {counts.get('low', 0)}）"
    )
    lines.append("")
    if detail.get("risk_summary"):
        lines.append("**风险总评**")
        lines.append("")
        lines.append(detail["risk_summary"].strip())
        lines.append("")

    if detail.get("focus_points"):
        lines.append("**需重点关注事项**")
        lines.append("")
        for i, p in enumerate(detail["focus_points"], 1):
            lines.append(f"{i}. {p}")
        lines.append("")

    highs = [r for r in risks if r["risk_level"] == "high"]
    others = [r for r in risks if r["risk_level"] != "high"]
    if highs or others:
        lines.append("**风险清单**")
        lines.append("")
        lines.append("| 序号 | 风险项 | 等级 | 涉及条款 | 原文定位 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for r in sorted(risks, key=lambda x: (_RISK_ORDER.get(x["risk_level"], 9), x["risk_no"])):
            lines.append(
                f"| {r['risk_no']} | {r['name']} | "
                f"{RISK_LEVEL_LABEL.get(r['risk_level'], r['risk_level'])} | "
                f"{CLAUSE_TYPE_LABEL.get(r.get('clause_type') or '', '—')} | "
                f"{r.get('location_label') or '—'} |"
            )
        lines.append("")

    if lawyer_notes.strip():
        lines.append("**法务批注**")
        lines.append("")
        lines.append(lawyer_notes.strip())
        lines.append("")

    if with_timestamp:
        lines.append(f"*本意见由合同审批智能审查系统于 {datetime.now():%Y-%m-%d %H:%M} 生成，"
                     f"修改示范条款详见系统审查工作台。*")
    return "\n".join(lines)


# ======================================================================
# Markdown → PDF
# ======================================================================
_FONT_REGULAR = "STSong-Light"
_FONT_BOLD = "STSong-Light"
_fonts_registered = False

#: Windows 常见中文字体，按优先级探测
_FONT_CANDIDATES = [
    ("regular", r"C:\Windows\Fonts\simsun.ttc", 0),
    ("regular", r"C:\Windows\Fonts\msyh.ttc", 0),
    ("regular", r"C:\Windows\Fonts\simkai.ttf", None),
    ("bold", r"C:\Windows\Fonts\simhei.ttf", None),
    ("bold", r"C:\Windows\Fonts\msyhbd.ttc", 0),
]


def _register_fonts() -> tuple[str, str]:
    """探测并注册中文字体，返回 ``(常规字体名, 粗体字体名)``。"""
    global _fonts_registered, _FONT_REGULAR, _FONT_BOLD
    if _fonts_registered:
        return _FONT_REGULAR, _FONT_BOLD

    try:
        from reportlab.pdfbase.ttfonts import TTFont

        got: dict[str, str] = {}
        for kind, path_str, index in _FONT_CANDIDATES:
            if kind in got:
                continue
            path = Path(path_str)
            if not path.exists():
                continue
            try:
                name = f"{kind.title()}CJK"
                if index is None:
                    pdfmetrics.registerFont(TTFont(name, str(path)))
                else:
                    pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=index))
                got[kind] = name
            except Exception as exc:  # pragma: no cover - 字体文件千奇百怪
                logger.debug(f"[报告] 字体 {path.name} 注册失败：{exc}")

        # reportlab 内置 CID 字体兜底（任何环境都有）
        if "regular" not in got or "bold" not in got:
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

        _FONT_REGULAR = got.get("regular", "STSong-Light")
        _FONT_BOLD = got.get("bold", _FONT_REGULAR)
        logger.info(f"[报告] PDF 字体：常规={_FONT_REGULAR} 粗体={_FONT_BOLD}")
    except Exception as exc:  # pragma: no cover
        logger.warning(f"[报告] 字体注册异常，使用内置字体：{exc}")
        _FONT_REGULAR = _FONT_BOLD = "STSong-Light"
    finally:
        _fonts_registered = True

    return _FONT_REGULAR, _FONT_BOLD


def _styles(regular: str, bold: str):
    base = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontName=bold,
                             fontSize=18, leading=26, alignment=TA_CENTER,
                             spaceAfter=6, textColor=colors.HexColor("#1a1a1a")),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName=bold,
                             fontSize=13.5, leading=20, spaceBefore=12, spaceAfter=6,
                             textColor=colors.HexColor("#1f4e79")),
        "h3": ParagraphStyle("h3", parent=base["Heading3"], fontName=bold,
                             fontSize=11.5, leading=17, spaceBefore=10, spaceAfter=4,
                             textColor=colors.HexColor("#2c2c2c")),
        "body": ParagraphStyle("body", parent=base["BodyText"], fontName=regular,
                               fontSize=10, leading=16, alignment=TA_LEFT, spaceAfter=4),
        "quote": ParagraphStyle("quote", parent=base["BodyText"], fontName=regular,
                                fontSize=9.5, leading=15, leftIndent=12, rightIndent=6,
                                textColor=colors.HexColor("#444444"),
                                backColor=colors.HexColor("#f5f5f5"),
                                borderPadding=5, spaceAfter=6),
        "code": ParagraphStyle("code", parent=base["BodyText"], fontName=regular,
                               fontSize=9, leading=14.5, leftIndent=10, rightIndent=6,
                               backColor=colors.HexColor("#f0f4f8"), borderPadding=6,
                               textColor=colors.HexColor("#12304a"), spaceAfter=6),
        "meta": ParagraphStyle("meta", parent=base["BodyText"], fontName=regular,
                               fontSize=8.5, leading=13, textColor=colors.HexColor("#777777")),
        "cell": ParagraphStyle("cell", parent=base["BodyText"], fontName=regular,
                               fontSize=9, leading=13.5, spaceAfter=0),
        "cellh": ParagraphStyle("cellh", parent=base["BodyText"], fontName=bold,
                                fontSize=9, leading=13.5, spaceAfter=0),
    }


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    """行内 Markdown → reportlab 支持的标签。"""
    t = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    t = _BOLD_RE.sub(r"<b>\1</b>", t)
    t = _CODE_RE.sub(r'<font face="Courier">\1</font>', t)
    return t


def markdown_to_pdf(md: str, out_path: Path, *, title: str = "合同审查报告") -> Path:
    """把 Markdown 渲染成 PDF。支持标题、段落、列表、引用、表格、代码块。"""
    regular, bold = _register_fonts()
    st = _styles(regular, bold)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        title=title,
        author="合同审批智能审查系统",
    )

    flow: list = []
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        s = line.strip()

        # --- 代码块 ---
        if s.startswith("```"):
            i += 1
            buf: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            text = "<br/>".join(_inline(b) for b in buf) or "&nbsp;"
            flow.append(Paragraph(text, st["code"]))
            continue

        # --- 表格 ---
        if s.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            header = [c.strip() for c in s.strip("|").split("|")]
            i += 2
            body: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                body.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            data = [[Paragraph(_inline(c), st["cellh"]) for c in header]]
            for row in body:
                row = (row + [""] * len(header))[: len(header)]
                data.append([Paragraph(_inline(c), st["cell"]) for c in row])
            avail = A4[0] - 36 * mm
            widths = [avail / len(header)] * len(header)
            tbl = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
            tbl.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef5")),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b8c4d0")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 5),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 3),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ]
                )
            )
            flow.append(KeepTogether(tbl) if len(body) <= 12 else tbl)
            flow.append(Spacer(1, 5))
            continue

        # --- 标题 ---
        if s.startswith("# "):
            flow.append(Paragraph(_inline(s[2:]), st["h1"]))
            flow.append(HRFlowable(width="100%", thickness=0.8,
                                   color=colors.HexColor("#1f4e79"), spaceAfter=8))
        elif s.startswith("## "):
            flow.append(Paragraph(_inline(s[3:]), st["h2"]))
        elif s.startswith("### "):
            flow.append(Paragraph(_inline(s[4:]), st["h3"]))
        # --- 引用 ---
        elif s.startswith(">"):
            flow.append(Paragraph(_inline(s.lstrip("> ").strip() or "&nbsp;"), st["quote"]))
        # --- 无序列表 ---
        elif s.startswith(("- ", "* ")):
            flow.append(Paragraph("•　" + _inline(s[2:]), st["body"]))
        # --- 有序列表 ---
        elif re.match(r"^\d+\.\s", s):
            flow.append(Paragraph(_inline(s), st["body"]))
        # --- 分隔线 ---
        elif s.startswith("---"):
            flow.append(Spacer(1, 4))
            flow.append(HRFlowable(width="100%", thickness=0.4,
                                   color=colors.HexColor("#cccccc"), spaceAfter=6))
        # --- 空行 ---
        elif not s:
            flow.append(Spacer(1, 3))
        # --- 斜体小字（报告尾注）---
        elif s.startswith("*") and s.endswith("*") and len(s) > 2:
            flow.append(Paragraph(_inline(s.strip("*")), st["meta"]))
        else:
            flow.append(Paragraph(_inline(s), st["body"]))
        i += 1

    doc.build(flow)
    logger.info(f"[报告] PDF 已生成：{out_path}（{out_path.stat().st_size} bytes）")
    return out_path


def export_report(detail: dict, risks: list[dict], *, fmt: str = "md") -> Path:
    """导出报告，返回文件路径。``fmt`` 支持 ``md`` / ``pdf``。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_title = re.sub(r'[\\/:*?"<>|\s]+', "_", detail.get("title") or "合同")[:40]
    base = f"{safe_title}_审查报告_{stamp}"
    md = build_report_markdown(detail, risks)

    settings.ensure_dirs()
    md_path = settings.report_dir / f"{base}.md"
    md_path.write_text(md, encoding="utf-8")

    if fmt == "md":
        return md_path

    pdf_path = settings.report_dir / f"{base}.pdf"
    markdown_to_pdf(md, pdf_path, title=f"{detail.get('title') or '合同'} 审查报告")
    return pdf_path


__all__ = [
    "build_report_markdown",
    "build_writeback_markdown",
    "markdown_to_pdf",
    "export_report",
]
