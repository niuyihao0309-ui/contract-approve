"""解析引擎验证脚本。

跑一遍 DOCX / PDF / OCR 三条解析路径 + 完整性校验 + 元数据抽取 + 条款切分，
把结果打印出来，肉眼确认定位与切分是否正确。

用法::

    .venv\\Scripts\\python.exe scripts/check_parsing.py
    .venv\\Scripts\\python.exe scripts/check_parsing.py --llm    # 带上 LLM 通道
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from app.core.config import settings  # noqa: E402
from app.core.exceptions import DocumentBlockedError  # noqa: E402
from app.engines.parsing.base import get_parser  # noqa: E402
from app.engines.parsing.clause_segmenter import ClauseSegmenter  # noqa: E402
from app.engines.parsing.integrity import check_integrity  # noqa: E402
from app.engines.parsing.metadata_extractor import MetadataExtractor  # noqa: E402
from app.models.enums import CLAUSE_TYPE_LABEL  # noqa: E402

LINE = "─" * 78


def hr(title: str) -> None:
    print(f"\n{LINE}\n  {title}\n{LINE}")


async def check_one(path: Path, *, use_llm: bool) -> dict:
    hr(f"解析 {path.name}")
    try:
        parser = get_parser(path)
    except DocumentBlockedError as exc:
        reason = exc.detail.get("block_reason", "?")
        print(f"  ✗ 阻塞（{reason}）：{exc.message}")
        return {"file": path.name, "blocked": True, "reason": reason}

    try:
        ir = await parser.parse(path, doc_id=f"test-{path.stem}")
    except DocumentBlockedError as exc:
        reason = exc.detail.get("block_reason", "?")
        print(f"  ✗ 阻塞（{reason}）：{exc.message}")
        return {"file": path.name, "blocked": True, "reason": reason}

    print(f"  解析器   : {ir.parser}")
    print(f"  块数/页数: {len(ir.blocks)} / {ir.page_count}")
    print(f"  正文字数 : {ir.body_char_count}")
    print(f"  表格数   : {ir.extra.get('table_count', 0)}")
    if ir.warnings:
        for w in ir.warnings:
            print(f"  ⚠ {w}")

    print("\n  前 6 个块（block_id | 页 | 段 | char区间 | 类型 | 文本）:")
    for b in ir.blocks[:6]:
        print(
            f"    {b.block_id} | p{b.page} | #{b.para_index} | "
            f"[{b.char_start},{b.char_end}) | {b.type.value[:4]:4s} | {b.excerpt(38)}"
        )

    # --- 完整性 ---
    report = check_integrity(ir)
    hr(f"完整性校验：{path.name}")
    for k, v in report.metrics.items():
        print(f"    {k:26s} = {v}")
    if report.ok:
        print(f"    → 通过{'（' + report.detail + '）' if report.detail else ''}")
    else:
        print(f"    → 阻塞：{report.reason_label} —— {report.detail}")
        return {"file": path.name, "blocked": True, "reason": report.reason.value, "ir": ir}

    # --- 元数据 ---
    hr(f"元数据抽取：{path.name}")
    md = await MetadataExtractor().extract(ir, use_llm=use_llm)
    for item in md.items:
        mark = "✓" if item.field_value else "✗"
        span = (
            f"{item.span.block_id} [{item.span.char_start},{item.span.char_end}) "
            f"第{item.span.page}页"
            if item.span
            else "（无定位）"
        )
        print(
            f"  {mark} {item.label:16s} = {str(item.field_value)[:34]:36s} "
            f"{item.source:5s} {span}"
        )

    # --- 条款切分 ---
    hr(f"条款切分：{path.name}")
    seg = ClauseSegmenter().segment(ir)
    print(f"  共 {len(seg.clauses)} 条：")
    for c in seg.clauses[:16]:
        print(
            f"    [{c.label:12s}] p{c.page_start}-{c.page_end} "
            f"{c.block_ids[0]}..{c.block_ids[-1]} conf={c.confidence:.2f} "
            f"| {c.brief[:46]}"
        )
    if len(seg.clauses) > 16:
        print(f"    …… 还有 {len(seg.clauses) - 16} 条")
    for w in seg.warnings:
        print(f"  ⚠ {w}")

    present = {c.clause_type.value for c in seg.clauses}
    print("\n  条款覆盖情况：")
    for t, label in CLAUSE_TYPE_LABEL.items():
        print(f"    {'✓' if t in present else '·'} {label}")

    return {
        "file": path.name,
        "blocked": False,
        "blocks": len(ir.blocks),
        "chars": ir.body_char_count,
        "pages": ir.page_count,
        "metadata_hit": sum(1 for i in md.items if i.field_value),
        "metadata_total": len(md.items),
        "clauses": len(seg.clauses),
        "clause_types": sorted(present),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="启用 LLM 通道（需配置 API Key）")
    ap.add_argument("--only", default=None, help="只测匹配的文件名关键字")
    args = ap.parse_args()

    from app.core.logging import setup_logging

    setup_logging()

    files = [
        "软件采购合同_风险版.docx",
        "软件采购合同_标准版.docx",
        "大额销售合同_风险版.pdf",
        "大额销售合同_标准版.pdf",
        "销售合同_扫描件.png",
        "销售合同_严重模糊.png",
        "加密合同_模拟.docx",
        "空正文合同.docx",
    ]
    if args.only:
        files = [f for f in files if args.only in f]

    results = []
    for name in files:
        p = settings.storage_dir.parent / "samples" / name
        if not p.exists():
            print(f"跳过（不存在）：{name}")
            continue
        try:
            results.append(await check_one(p, use_llm=args.llm))
        except Exception as exc:
            import traceback

            print(f"  ✗ 异常：{type(exc).__name__}: {exc}")
            traceback.print_exc()
            results.append({"file": name, "error": str(exc)})

    hr("汇总")
    print(f"  {'文件':34s} {'结果':10s} 详情")
    for r in results:
        if r.get("blocked"):
            print(f"  {r['file']:34s} {'blocked':10s} {r.get('reason')}")
        elif r.get("error"):
            print(f"  {r['file']:34s} {'error':10s} {r['error'][:60]}")
        else:
            print(
                f"  {r['file']:34s} {'ok':10s} "
                f"{r['blocks']}块/{r['pages']}页/{r['chars']}字 | "
                f"元数据 {r['metadata_hit']}/{r['metadata_total']} | "
                f"条款 {r['clauses']}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
