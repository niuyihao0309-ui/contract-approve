"""端到端演示脚本（同时是验收自测）。

走完大纲 2.4.7 的三条主线：

    场景 A  软件采购合同（DOCX）：拉取 → 解析 → 审查 → 复核 → 回写 → 导报
    场景 B  大额销售合同（PDF） ：同上，验证 PDF 与 OCR 路径
    场景 C  异常链路：加密 / 空正文 / 严重模糊 → blocked → 重试成功
    场景 D  对照组：标准版合同不应产生高风险（验证不误报）
    场景 E  指纹去重：同一文件二次提交复用解析结果

用法::

    # 默认走 .env 里配置的数据库（Docker MySQL）
    .venv\\Scripts\\python.exe scripts/demo_e2e.py

    # 只想快速验逻辑、不想起 Docker：
    set APP_DB_URL=sqlite+aiosqlite:///./storage/demo.db
    .venv\\Scripts\\python.exe scripts/demo_e2e.py

    # 带上 LLM（需要 .env 里配好 APP_LLM_ENABLED / APP_LLM_API_KEY）
    .venv\\Scripts\\python.exe scripts/demo_e2e.py --llm

    # 只跑某个场景
    .venv\\Scripts\\python.exe scripts/demo_e2e.py --only A

    # 清空重建数据表后再跑（验收复现用，避免上一轮残留数据干扰）
    .venv\\Scripts\\python.exe scripts/demo_e2e.py --fresh
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import httpx  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402

SAMPLES = BASE / "samples"
LINE = "=" * 78
SUB = "─" * 78

PASS, FAIL, INFO = "  ✓", "  ✗", "  ·"

_results: list[tuple[str, bool, str]] = []


def head(title: str) -> None:
    print(f"\n{LINE}\n  {title}\n{LINE}")


def sub(title: str) -> None:
    print(f"\n{SUB}\n  {title}\n{SUB}")


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((name, ok, detail))
    print(f"{PASS if ok else FAIL} {name}" + (f"　→ {detail}" if detail else ""))
    return ok


def info(msg: str) -> None:
    print(f"{INFO} {msg}")


class Client:
    """包一层 httpx，直接打 ASGI app，不需要真的起 uvicorn。"""

    def __init__(self) -> None:
        from app.main import app

        self._transport = httpx.ASGITransport(app=app)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "Client":
        self._client = httpx.AsyncClient(
            transport=self._transport, base_url="http://test", timeout=300.0
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()

    async def get(self, url: str, **kw):
        return await self._client.get(url, **kw)

    async def post(self, url: str, **kw):
        return await self._client.post(url, **kw)

    async def patch(self, url: str, **kw):
        return await self._client.patch(url, **kw)


async def setup_db(*, fresh: bool = False) -> None:
    """建表 + 同步规则。启动 lifespan 不方便，这里手工做一遍。"""
    settings.ensure_dirs()
    from app.db.base import init_db
    from app.services.rule_service import RuleService

    await init_db(drop=fresh)
    stats = await RuleService.sync_from_files()
    info(f"数据库：{settings.db_url.split('@')[-1]}")
    if fresh:
        info("已清空重建全部数据表（--fresh）")
    info(f"规则同步：{stats['rules']} 条规则 / {stats['templates']} 条示范条款")


# ======================================================================
# 场景 A / B：正常全链路
# ======================================================================
async def scenario_full(
    api: Client, *, tag: str, filename: str, business_type: str, expect_risk: str | None
) -> str | None:
    path = SAMPLES / filename
    if not path.exists():
        check(f"{tag} 样例存在", False, f"缺少 {path}")
        return None

    sub(f"场景 {tag}　{filename}")

    # --- 1. 审批系统推送建单 + 拉取（走 /approvals/push）---
    r = await api.post(
        "/api/v1/approvals/push",
        json={
            "title": filename.rsplit(".", 1)[0],
            "applicant": "张伟",
            "department": "采购部" if business_type == "purchase" else "销售部",
            "business_type": business_type,
            "attachment_path": str(path),
            "run_now": True,
        },
    )
    if r.status_code != 200:
        check(f"{tag} 推送建单", False, f"HTTP {r.status_code}: {r.text[:200]}")
        return None
    pushed = r.json()
    ticket_id = pushed["ticket"]["ticket_id"]
    task_id = pushed["task_id"]
    check(f"{tag} 审批推送建单", True, f"审批单 {ticket_id} → 任务 {task_id[:8]}")

    # --- 2. 查看任务状态 ---
    r = await api.get(f"/api/v1/review/tasks/{task_id}/summary")
    s = r.json()
    print()
    info(f"状态　　：{s['status_label']}　阶段={s['stage']}　进度={s['progress']}%")
    if s["block_reason"]:
        info(f"阻塞原因：{s['block_reason']}")
        check(f"{tag} 流水线完成", False, f"被阻塞：{s['block_detail'][:120] if s['block_detail'] else ''}")
        return task_id

    info(f"总等级　：{s['overall_risk_label']}　结论={s['conclusion_label']}")
    info(f"风险分布：高 {s['risk_counts']['high']} / 中 {s['risk_counts']['medium']} / "
         f"低 {s['risk_counts']['low']}")
    check(f"{tag} 流水线走到 completed", s["status"] == "completed", s["status_label"])

    # --- 3. 文档解析结果 ---
    r = await api.get(f"/api/v1/tasks/{task_id}/document")
    doc = r.json()
    info(f"文档　　：{len(doc['blocks'])} 块 / {doc['page_count']} 页 / 全文 {len(doc['full_text'])} 字")
    check(f"{tag} 解析出文本块", len(doc["blocks"]) > 0, f"{len(doc['blocks'])} 块")

    r = await api.get(f"/api/v1/tasks/{task_id}/metadata")
    md = r.json()
    info(f"元数据　：命中 {md['hit']}/{md['total']}")
    for it in md["items"]:
        if it["value"]:
            loc = it["location_label"] or "无定位"
            print(f"      {it['field_label']:14s} = {str(it['value'])[:28]:30s} {loc}")
    check(f"{tag} 元数据带原文定位", all(
        it["block_id"] for it in md["items"] if it["value"]
    ), f"{md['hit']}/{md['total']}")

    r = await api.get(f"/api/v1/tasks/{task_id}/clauses")
    cl = r.json()
    info(f"条款切分：{cl['total']} 条")
    for c in cl["clauses"][:12]:
        print(f"      [{c['clause_type_label']:12s}] p{c['page_start']}-{c['page_end']} "
              f"conf={c['confidence']:.2f} {c['start_block_id']}..  {c['title'][:34]}")

    # --- 4. 风险清单 ---
    r = await api.get(f"/api/v1/tasks/{task_id}/risks")
    rk = r.json()
    sub(f"场景 {tag}　风险清单（{rk['total']} 条）")
    for it in rk["items"]:
        loc = it["location_label"]
        src = "规则" if it["source"] == "rule" else "LLM"
        print(f"  R{it['risk_no']} [{it['risk_level_label']}] {it['name']}")
        print(f"       来源={src}{'/' + it['rule_id'] if it['rule_id'] else ''}"
              f"　条款={it['clause_type_label']}　定位={loc} ({it['block_id']})")
        print(f"       依据：{(it['legal_basis'] or '—')[:70]}")
        if it["suggestion"]:
            txt = (it["suggestion"]["final_text"] or "").replace("\n", " ")
            print(f"       建议：{txt[:76]}…")

    # --- 5. 定位正确性校验（验收标准第 4 条的基础）---
    bad_loc = [i for i in rk["items"] if not i["block_id"] or i["char_end"] <= i["char_start"]]
    check(f"{tag} 每条风险都能定位回原文", not bad_loc,
          f"{len(rk['items']) - len(bad_loc)}/{len(rk['items'])} 条有效定位")

    if rk["items"]:
        first = rk["items"][0]
        quote_in_doc = doc["full_text"][first["char_start"]:first["char_end"]]
        ok = bool(quote_in_doc.strip())
        check(f"{tag} 风险定位区间能在全文切出原文", ok, repr(quote_in_doc[:40]))

    # --- 6. 预期风险命中（场景 A/B 的指定风险）---
    if expect_risk:
        hit = any(expect_risk in i["name"] or expect_risk in (i["reason"] or "")
                  for i in rk["items"])
        check(f"{tag} 命中预期风险「{expect_risk}」", hit)

    # --- 7. 法务复核：采纳第一条 + 补充批注 ---
    if rk["items"]:
        target = rk["items"][0]
        r = await api.post(
            f"/api/v1/tasks/{task_id}/risks/{target['id']}/decision",
            json={
                "decision": "adjusted",
                "by": "李静（法务）",
                "note": "已与业务确认，按建议条款修改后再签。",
                "edited_text": (target["suggestion"]["suggested_text"] if target["suggestion"]
                                else "（法务手写替代条款）"),
            },
        )
        check(f"{tag} 法务处理风险项", r.status_code == 200, f"R{target['risk_no']} → 调整后采纳")

        r = await api.post(
            f"/api/v1/tasks/{task_id}/comments",
            json={"content": "整体意见：高风险条款必须先改，其余可在签署前补充。",
                  "author": "李静（法务）", "role": "lawyer"},
        )
        check(f"{tag} 录入法务批注", r.status_code == 200)

    # --- 8. 回写审批系统 ---
    sub(f"场景 {tag}　回写与报告")
    r = await api.get(f"/api/v1/tasks/{task_id}/writeback/preview")
    preview = r.json()
    info(f"回写意见预览：{preview['chars']} 字")
    check(f"{tag} 生成回写意见文本", preview["chars"] > 100, f"{preview['chars']} 字")

    r = await api.post(
        f"/api/v1/tasks/{task_id}/writeback",
        json={"lawyer_notes": "法务要求：高风险条款整改后再签署。", "by": "李静（法务）"},
    )
    wb = r.json()
    check(f"{tag} 回写审批系统评论区", r.status_code == 200 and wb.get("comment_id"),
          f"comment_id={wb.get('comment_id')}")

    # --- 9. 幂等验证：把「同一份内容」原样重放一次，不应产生第二条评论 ---
    # 幂等键 = task_id + sha256(意见正文)，而正文里的生成时间戳不参与哈希。
    # 所以这里必须重放同一份 body；换了批注就是「另一版意见」，
    # 按设计本来就应该允许再写一次（且两次都留痕）。
    same_body = {"lawyer_notes": "法务要求：高风险条款整改后再签署。", "by": "李静（法务）"}
    r = await api.post(f"/api/v1/tasks/{task_id}/writeback", json=same_body)
    wb2 = r.json()
    check(f"{tag} 回写幂等（同一份内容重放被拦截）",
          bool(wb2.get("duplicated")), "duplicated=true" if wb2.get("duplicated") else "未拦截")
    check(f"{tag} 幂等重放未产生新 comment_id",
          wb2.get("comment_id") == wb.get("comment_id"),
          f"{wb.get('comment_id')} → {wb2.get('comment_id')}")

    # --- 10. 查看审批单评论区 ---
    r = await api.get(f"/api/v1/approvals/tickets/{ticket_id}/comments")
    cm = r.json()
    check(f"{tag} 审批单评论区可见审查意见", cm["total"] == 1,
          f"{cm['total']} 条评论（幂等生效则为 1）")

    # --- 11. 导出报告 ---
    r_md = await api.get(f"/api/v1/tasks/{task_id}/report/download", params={"fmt": "md"})
    ok_md = r_md.status_code == 200 and len(r_md.content) > 500
    check(f"{tag} 导出 Markdown 报告", ok_md, f"{len(r_md.content)} 字节")

    r_pdf = await api.get(f"/api/v1/tasks/{task_id}/report/download", params={"fmt": "pdf"})
    ok_pdf = r_pdf.status_code == 200 and r_pdf.content[:4] == b"%PDF"
    check(f"{tag} 导出 PDF 报告", ok_pdf, f"{len(r_pdf.content)} 字节")

    if ok_md:
        out = settings.report_dir / f"_demo_{tag}_report.md"
        out.write_bytes(r_md.content)
        info(f"报告样例已存：{out.relative_to(BASE)}")

    return task_id


# ======================================================================
# 场景 C：异常阻塞与重试
# ======================================================================
async def scenario_blocked(api: Client) -> None:
    sub("场景 C　异常链路：加密 / 空正文 / 严重模糊 → blocked")

    cases = [
        ("加密合同_模拟.docx", "encrypted", "文档已加密"),
        ("空正文合同.docx", "empty_body", "正文为空"),
        ("销售合同_严重模糊.png", "blurry_scan", "严重模糊"),
    ]

    blocked_task = None
    for filename, expect_reason, label in cases:
        path = SAMPLES / filename
        if not path.exists():
            check(f"C·{label} 样例存在", False, f"缺少 {path}")
            continue

        r = await api.post(
            "/api/v1/approvals/push",
            json={"title": f"[异常样例] {label}", "business_type": "purchase",
                  "attachment_path": str(path), "run_now": True},
        )
        task_id = r.json().get("task_id")
        if task_id is None:
            check(f"C·{label} 建单", False, r.text[:200])
            continue

        r = await api.get(f"/api/v1/review/tasks/{task_id}/summary")
        s = r.json()
        ok = s["status"] == "blocked" and s["block_reason"] == expect_reason
        check(f"C·{label} 正确进入 blocked（{expect_reason}）", ok,
              f"实际 status={s['status']} reason={s['block_reason']}")
        if s.get("block_detail"):
            info(f"    阻塞详情：{s['block_detail'][:100]}")
        if expect_reason == "encrypted":
            blocked_task = task_id

        # --- 重试校验（只对空正文那个演示「换文件后重试成功」）---
        if expect_reason == "empty_body":
            r = await api.post(
                f"/api/v1/tasks/{task_id}/retry",
                json={"by": "系统管理员", "file_path": str(SAMPLES / "软件采购合同_标准版.docx")},
            )
            if r.status_code == 200:
                check("C·重传文件后重试成功", r.json()["status"] == "completed",
                      f"状态={r.json()['status_label']} 风险={r.json()['overall_risk_label']}")
            else:
                check("C·重传文件后重试成功", False, r.text[:200])

    # --- 对已阻塞的任务做纯重试（不改文件）应再次 blocked ---
    if blocked_task:
        r = await api.post(f"/api/v1/tasks/{blocked_task}/retry",
                           json={"by": "系统管理员"})
        ok = r.status_code == 200 and r.json()["status"] == "blocked"
        check("C·加密文件原样重试仍 blocked（原因可复现）", ok,
              f"retry_count={r.json().get('retry_count')}" if r.status_code == 200
              else r.text[:150])


# ======================================================================
# 场景 D：对照组，验证不误报
# ======================================================================
async def scenario_control(api: Client) -> None:
    sub("场景 D　对照组：标准版合同不应出现高风险")
    for filename, bt in [("软件采购合同_标准版.docx", "purchase"),
                         ("大额销售合同_标准版.pdf", "sales")]:
        path = SAMPLES / filename
        if not path.exists():
            continue
        r = await api.post(
            "/api/v1/approvals/push",
            json={"title": f"[对照] {filename}", "business_type": bt,
                  "attachment_path": str(path), "run_now": True},
        )
        task_id = r.json()["task_id"]
        r = await api.get(f"/api/v1/review/tasks/{task_id}/summary")
        s = r.json()
        high = s["risk_counts"]["high"]
        check(f"D·{filename} 无高风险", high == 0,
              f"高 {high} / 中 {s['risk_counts']['medium']} / 低 {s['risk_counts']['low']}"
              f"　结论={s['conclusion_label']}")
        check(f"D·{filename} 结论不是「建议拒绝」", s["conclusion"] != "reject",
              s["conclusion_label"])


# ======================================================================
# 场景 E：指纹去重
# ======================================================================
async def scenario_dedup(api: Client, task_id: str | None) -> None:
    sub("场景 E　文件指纹去重（同一文件二次提交复用解析结果）")
    if not task_id:
        check("E·指纹去重", False, "没有可复用的前置任务")
        return

    path = SAMPLES / "软件采购合同_风险版.docx"
    r = await api.post(
        "/api/v1/approvals/push",
        json={"title": "[去重复验] 软件采购合同_风险版", "business_type": "purchase",
              "attachment_path": str(path), "run_now": True},
    )
    new_task = r.json()["task_id"]
    r = await api.get(f"/api/v1/tasks/{new_task}")
    detail = r.json()
    files = detail["files"]
    from_cache = bool(files and files[0].get("from_cache"))
    check("E·命中指纹缓存并复用解析结果", from_cache,
          f"from_cache={from_cache}, parser={files[0].get('parser') if files else '-'}")

    r = await api.get(f"/api/v1/review/tasks/{new_task}/summary")
    s = r.json()
    if s["status"] != "completed":
        info(f"    任务状态={s['status']} 阻塞原因={s['block_reason']} "
             f"详情={(s.get('block_detail') or '')[:160]}")
    check("E·复用后仍得出相同结论", s["status"] == "completed",
          f"{s['overall_risk_label']} / {s['conclusion_label']}")


# ======================================================================
# 引擎自检
# ======================================================================
async def engine_selfcheck(api: Client) -> None:
    sub("引擎自检")
    r = await api.get("/api/v1/review/health")
    h = r.json()
    info(f"规则　　：{h['rules']['total']} 条（启用 {h['rules']['enabled']}，"
         f"checker {len(h['rules']['checkers'])} 个）")
    info(f"示范条款：{h['clause_templates']['total']} 条")
    info(f"LLM　　 ：{'已启用 ' + str(h['llm']['model']) if h['llm']['enabled'] else '未启用（仅规则引擎）'}")
    info(f"OCR　　 ：{'可用' if h['ocr']['available'] else '不可用 — ' + h['ocr']['reason']}")
    info(f"我方身份：{h['our_side']['label']}（{h['our_side']['code']}）")

    r = await api.get("/api/v1/tasks/stats")
    st = r.json()
    info(f"任务统计：共 {st['total']} 条，阻塞 {st['blocked_count']} 条")
    info(f"　　　　　状态 {st['by_status']}")
    info(f"　　　　　风险 {st['by_risk']}")

    r = await api.get("/api/v1/rules")
    rules = r.json()
    check("规则树按分类组织", len(rules["categories"]) > 0, "、".join(rules["categories"]))

    r = await api.get("/api/v1/rules/templates/search",
                      params={"query": "违约金 赔偿 不设上限", "top_k": 3})
    hits = r.json()
    check("标准条款库检索可用", hits["total"] > 0,
          " / ".join(f"{i['title']}({i['similarity']:.2f})" for i in hits["items"]))


# ======================================================================
async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="启用 LLM（需在 .env 配好 Key）")
    ap.add_argument("--only", default=None, help="只跑指定场景，如 A / C / D / E")
    ap.add_argument("--fresh", action="store_true", help="先清空重建所有数据表，保证结果可复现")
    args = ap.parse_args()

    setup_logging()
    if args.llm:
        import os

        os.environ["APP_LLM_ENABLED"] = "true"
        settings.llm_enabled = True  # type: ignore[misc]
        from app.llm.client import reset_llm

        reset_llm()
        print("  ⚠ LLM 已启用（若 .env 未配 APP_LLM_API_KEY 会全部降级到规则引擎）")

    head("合同审批智能审查系统 · 端到端演示")
    print(f"  样例目录：{SAMPLES}")
    print(f"  LLM     ：{'启用' if settings.llm_ready else '未启用（仅规则引擎）'}")

    await setup_db(fresh=args.fresh)

    async with Client() as api:
        await engine_selfcheck(api)

        task_a = None
        only = args.only.upper() if args.only else None

        if only in (None, "A"):
            head("场景 A · 软件采购合同（DOCX）· 全流程")
            task_a = await scenario_full(
                api, tag="A", filename="软件采购合同_风险版.docx",
                business_type="purchase",
                expect_risk="知识产权",
            )

        if only in (None, "B"):
            head("场景 B · 大额销售合同（PDF）· 全流程")
            await scenario_full(
                api, tag="B", filename="大额销售合同_风险版.pdf",
                business_type="sales",
                expect_risk="违约责任",
            )

        if only in (None, "C"):
            head("场景 C · 异常链路与重试")
            await scenario_blocked(api)

        if only in (None, "D"):
            head("场景 D · 对照组（不误报）")
            await scenario_control(api)

        if only in (None, "E"):
            head("场景 E · 指纹去重")
            await scenario_dedup(api, task_a)

    # -------------------- 汇总 --------------------
    head("自测汇总")
    ok_n = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    for name, ok, detail in _results:
        print(f"  {'✓' if ok else '✗'} {name}" + (f"　→ {detail}" if detail and not ok else ""))
    print(f"\n  通过 {ok_n}/{total}")

    from app.db.base import dispose_engine

    await dispose_engine()

    if ok_n < total:
        print("\n  ⚠ 有未通过的检查项，详见上面标 ✗ 的行。")
        return 1
    print("\n  全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
