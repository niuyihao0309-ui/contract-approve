"""端到端流水线编排。

对应大纲 2.4.7 的第一条主线：

    下载/校验附件 → 解析与 OCR → 元数据提取 → 条款切分
        → 规则引擎 → LLM 语义研判 → 条款推荐 → 汇总等级 → 落库 → completed

设计要点
--------
1. **单一状态出口**：所有状态变更都通过 :class:`TaskService`，本模块不直接写 ``task.status``。
2. **可续跑**：每完成一个阶段就落库。重复执行时已完成的阶段直接跳过
   （IR 从 ``doc_block`` 重建，元数据/条款有记录就不重算），方便中途失败后重试。
3. **异常归一**：
   - :class:`DocumentBlockedError` → 置 ``blocked`` 并带 ``block_reason``（验收标准第 7 条）
   - 其它异常 → 同样置 ``blocked``，原因是 ``parse_failed``，并把堆栈摘要写进 ``block_detail``
4. **LLM 全链路可降级**：任何 LLM 环节失败都只降级，不中断流水线。
"""

from __future__ import annotations

import time
import traceback
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import DocumentBlockedError, FileParseError
from app.core.logging import logger
from app.db.base import session_scope
from app.engines.parsing.base import get_parser
from app.engines.parsing.clause_segmenter import ClauseSegmenter
from app.engines.parsing.integrity import check_integrity, estimate_scan_quality
from app.engines.parsing.metadata_extractor import MetadataExtractor
from app.engines.recommend.recommender import ClauseRecommender
from app.engines.review.aggregator import RiskAggregator
from app.engines.review.checkers import ReviewContext
from app.engines.review.llm_reviewer import LLMReviewer
from app.engines.review.rule_engine import RuleEngine
from app.models.enums import (
    CLAUSE_TYPE_LABEL,
    RISK_LEVEL_LABEL,
    BlockReason,
    MetadataField,
    ReviewStage,
    TaskStatus,
)
from app.schemas.document import DocIR
from app.schemas.metadata import ClauseSegmentResult, MetadataResult
from app.services.file_service import FileService
from app.services.ir_loader import IRLoader
from app.services.persistence import PersistenceService
from app.services.rule_service import RuleService
from app.services.task_service import TaskService


class PipelineRunner:
    """流水线执行器。"""

    # ------------------------------------------------------------------
    @staticmethod
    async def run(task_id: str, *, force_reparse: bool = False) -> dict:
        """跑完一条任务的完整链路。返回执行摘要。"""
        t0 = time.perf_counter()
        summary: dict = {"task_id": task_id, "ok": True, "skipped": [], "steps": []}

        try:
            async with session_scope() as session:
                summary = await PipelineRunner._run_inner(
                    session, task_id, force_reparse=force_reparse
                )
        except DocumentBlockedError as exc:
            summary = await PipelineRunner._block(
                task_id, exc.detail.get("block_reason") or BlockReason.PARSE_FAILED.value,
                exc.message,
            )
        except Exception as exc:  # 兜底：任何未预期异常都要落到 blocked，不能留下半死不活的任务
            detail = f"{type(exc).__name__}: {exc}"
            logger.exception(f"[流水线] 任务 {task_id[:8]} 执行异常")
            summary = await PipelineRunner._block(
                task_id,
                BlockReason.PARSE_FAILED.value,
                f"{detail}\n{traceback.format_exc()[-1200:]}",
            )

        summary["duration_ms"] = int((time.perf_counter() - t0) * 1000)
        logger.info(
            f"[流水线] 任务 {task_id[:8]} 结束："
            + (
                f"总等级={summary.get('overall_risk')} 结论={summary.get('conclusion')} "
                f"风险 {summary.get('risk_count', 0)} 条"
                if summary.get("ok")
                else f"阻塞（{summary.get('block_reason')}）"
            )
            + f"，耗时 {summary['duration_ms']}ms"
        )
        return summary

    # ------------------------------------------------------------------
    @staticmethod
    async def _run_inner(
        session: AsyncSession, task_id: str, *, force_reparse: bool
    ) -> dict:
        task = await TaskService.get(session, task_id)
        summary: dict = {"task_id": task_id, "ok": True, "steps": []}
        done = summary["steps"]

        # ---------- 1. 进入解析中 ----------
        if task.status not in (TaskStatus.PARSING.value, TaskStatus.REVIEWING.value):
            await TaskService.start_parsing(session, task)
        await session.commit()

        # ---------- 2. 附件与解析 ----------
        ir = None if force_reparse else await IRLoader.load_ir(session, task_id)
        if ir is not None:
            done.append("parse:reused")
            logger.info(f"[流水线] 任务 {task_id[:8]} 复用已落库的文档结构（{len(ir.blocks)} 块）")
        else:
            await TaskService.set_stage(session, task, ReviewStage.DOWNLOADED)
            await session.commit()
            ir = await PipelineRunner._parse(session, task)
            done.append("parse:done")

        # ---------- 3. 元数据 ----------
        metadata = await IRLoader.load_metadata(session, task_id)
        if metadata is None:
            await TaskService.set_stage(session, task, ReviewStage.METADATA_DONE)
            await session.commit()
            metadata = await MetadataExtractor().extract(ir)
            await PersistenceService.save_metadata(session, task_id, metadata)
            done.append("metadata:done")
        else:
            done.append("metadata:reused")
        await PipelineRunner._sync_task_from_metadata(session, task, metadata)

        # ---------- 4. 条款切分 ----------
        clauses = await IRLoader.load_clauses(session, task_id)
        if clauses is None:
            await TaskService.set_stage(session, task, ReviewStage.CLAUSES_SEGMENTED)
            await session.commit()
            clauses = ClauseSegmenter().segment(ir)
            await PersistenceService.save_clauses(session, task_id, ir.doc_id, clauses)
            done.append("clauses:done")
        else:
            done.append("clauses:reused")
        for w in clauses.warnings:
            logger.warning(f"[流水线] {w}")

        # ---------- 5. 进入审查中 ----------
        if task.status != TaskStatus.REVIEWING.value:
            await TaskService.start_reviewing(session, task)
        await session.commit()

        ctx = ReviewContext(
            ir=ir,
            clauses=clauses,
            metadata=metadata,
            contract_type=task.business_type or "other",
            our_side=settings.our_side,
        )

        # ---------- 6. 规则引擎 ----------
        await TaskService.set_stage(session, task, ReviewStage.RULES_APPLIED)
        await session.commit()
        engine = await RuleService.build_engine()
        rule_risks = engine.run(ctx)
        done.append(f"rules:{len(rule_risks)}")

        # ---------- 7. LLM 语义研判 ----------
        await TaskService.set_stage(session, task, ReviewStage.LLM_REVIEWED)
        await session.commit()
        known = [f"{r.name}（{CLAUSE_TYPE_LABEL.get(r.clause_type.value, '')}）：{r.reason[:80]}"
                 for r in rule_risks]
        llm_risks = await LLMReviewer().review(
            ir,
            clauses=clauses,
            metadata=metadata,
            contract_type=task.business_type or "other",
            our_side=settings.our_side_label,
            known_issues=known,
        )
        done.append(f"llm:{len(llm_risks)}")

        risks = _sort_risks(rule_risks + llm_risks)

        # ---------- 8. 条款推荐 ----------
        await TaskService.set_stage(session, task, ReviewStage.SUGGESTED)
        await session.commit()
        library = await RuleService.build_library()
        recommender = ClauseRecommender(library)
        n_sug = await recommender.recommend_batch(
            risks,
            contract_type=task.business_type or "other",
            our_side=settings.our_side_label,
        )
        done.append(f"suggestions:{n_sug}")

        # ---------- 9. 汇总 ----------
        await TaskService.set_stage(session, task, ReviewStage.AGGREGATED)
        await session.commit()
        agg = await RiskAggregator().aggregate(
            risks,
            title=task.title,
            contract_type=task.business_type or "other",
            counterparty=task.counterparty or "",
            amount=str(task.amount or ""),
            currency=task.currency or "CNY",
            our_side=settings.our_side_label,
        )

        # ---------- 10. 落库 ----------
        await PersistenceService.save_risks(session, task_id, risks)
        await PersistenceService.apply_aggregate(session, task_id, agg)

        # ---------- 11. 完成 ----------
        await TaskService.complete(session, task, summary=agg.summary)
        await session.commit()

        summary.update(
            ok=True,
            overall_risk=agg.overall_risk.value if agg.overall_risk else None,
            overall_risk_label=(
                RISK_LEVEL_LABEL[agg.overall_risk.value] if agg.overall_risk else "无风险"
            ),
            conclusion=agg.conclusion,
            conclusion_desc=agg.conclusion_desc,
            risk_count=len(risks),
            risk_counts=agg.counts,
            metadata_hit=sum(1 for i in metadata.items if i.field_value),
            clause_count=len(clauses.clauses),
            block_count=len(ir.blocks),
            page_count=ir.page_count,
        )
        return summary

    # ------------------------------------------------------------------
    # 解析阶段
    # ------------------------------------------------------------------
    @staticmethod
    async def _parse(session: AsyncSession, task) -> DocIR:
        """取附件 → 指纹去重 → 解析 → 完整性校验 → 落库。"""
        file = await FileService.primary_file(session, task.id)
        if file is None:
            raise DocumentBlockedError(
                "任务没有附件，无法开始审查",
                detail={"block_reason": BlockReason.PARSE_FAILED.value},
            )

        # --- 指纹去重：同一文件解析过就直接复用 ---
        cached = await FileService.find_cache(session, file.file_hash, exclude_task_id=task.id)
        if cached is not None:
            await FileService.reuse_parse(session, task, cached)
            await FileService.mark_parsed(
                session,
                file,
                parser=cached.parser or "cache",
                page_count=cached.page_count or 1,
                meta={"from_cache": True, "source_task": cached.task_id},
                from_cache=True,
            )
            await TaskService.set_stage(session, task, ReviewStage.PARSED)
            await TaskService.add_comment(
                session,
                task,
                author="系统",
                role="system",
                kind="action",
                content=f"命中文件指纹缓存（源任务 {cached.task_id[:8]}），已复用解析结果",
            )
            await session.commit()
            ir = await IRLoader.load_ir(session, task.id)
            if ir is None:  # pragma: no cover - 缓存复制失败时不应静默
                raise FileParseError("指纹缓存复用失败：数据库中没有解析产物")
            return ir

        # --- 真正解析 ---
        path = Path(file.storage_path)
        try:
            parser = get_parser(path)
            ir = await parser.parse(path, doc_id=f"{task.id}", filename=file.filename)
        except DocumentBlockedError as exc:
            await FileService.mark_failed(session, file, exc.message)
            raise
        except FileParseError as exc:
            await FileService.mark_failed(session, file, exc.message)
            raise DocumentBlockedError(
                exc.message, detail={"block_reason": BlockReason.PARSE_FAILED.value}
            ) from exc

        # --- 完整性体检（验收标准第 7 条的兜底）---
        report = check_integrity(ir)
        if not report.ok:
            await FileService.mark_failed(session, file, report.detail)
            raise DocumentBlockedError(report.detail, detail=report.to_detail_dict())

        if report.detail:
            ir.warnings.append(report.detail)
        ir.extra.update(estimate_scan_quality(ir))
        ir.extra["integrity"] = report.metrics

        # --- 落库 ---
        await PersistenceService.save_document(session, task.id, ir, file_id=file.id)
        await FileService.mark_parsed(
            session,
            file,
            parser=parser.name,
            page_count=ir.page_count,
            meta=ir.to_storage_meta(),
        )
        await TaskService.set_stage(session, task, ReviewStage.PARSED)
        await session.commit()

        if ir.warnings:
            await TaskService.add_comment(
                session,
                task,
                author="系统",
                role="system",
                kind="action",
                content="解析告警：" + "；".join(ir.warnings[:3]),
            )
            await session.commit()
        return ir

    # ------------------------------------------------------------------
    @staticmethod
    async def _sync_task_from_metadata(
        session: AsyncSession, task, metadata: MetadataResult
    ) -> None:
        """把抽取到的元数据回填到任务主表，让大盘列表能看到金额/相对方等信息。"""
        changed = False

        val = metadata.value_of(MetadataField.CONTRACT_NO)
        if val and not task.contract_no:
            task.contract_no = val[:128]
            changed = True

        val = metadata.value_of(MetadataField.PARTY_B)
        if val and not task.counterparty:
            task.counterparty = val[:255]
            changed = True

        item = metadata.get(MetadataField.AMOUNT)
        if item and item.normalized_value and task.amount is None:
            try:
                task.amount = float(item.normalized_value)
                changed = True
            except (TypeError, ValueError):
                pass

        val = metadata.value_of(MetadataField.CURRENCY)
        if val:
            norm = metadata.get(MetadataField.CURRENCY)
            task.currency = (norm.normalized_value if norm else val) or "CNY"
            changed = True

        val = metadata.value_of(MetadataField.PERIOD)
        if val and not task.period:
            task.period = val[:128]
            changed = True

        if changed:
            await session.flush()

    # ------------------------------------------------------------------
    @staticmethod
    async def _block(task_id: str, reason: str, detail: str) -> dict:
        """把任务置为 blocked。单独开一个会话，因为原会话可能已经因异常不可用。"""
        try:
            async with session_scope() as session:
                task = await TaskService.get(session, task_id)
                await TaskService.block(session, task, reason, detail)
        except Exception as exc:  # pragma: no cover
            logger.error(f"[流水线] 置阻塞状态失败：{type(exc).__name__}: {exc}")
        return {
            "task_id": task_id,
            "ok": False,
            "block_reason": reason,
            "block_detail": detail,
        }


def _sort_risks(risks: list) -> list:
    """排序：等级高的在前；同级按「规则先于 LLM」；再按文档位置。"""
    from app.models.enums import RISK_WEIGHT, RiskSource

    return sorted(
        risks,
        key=lambda r: (
            -RISK_WEIGHT.get(r.risk_level.value, 0),
            0 if r.source is RiskSource.RULE else 1,
            r.span.char_start,
        ),
    )


__all__ = ["PipelineRunner"]
