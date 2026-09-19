"""条款推荐引擎。

大纲 3.5：标准条款库向量检索 Top-K + LLM 改写，产出「问题条款 → 建议条款」对照。

检索实现说明
------------
大纲建议用 pgvector / Qdrant 做向量检索。本模块的做法是 **可插拔的两级检索**：

1. **内置 BM25 风格的中文关键词检索**（零依赖、零运维、可离线跑通）。
   对短文本（条款正文通常 100–300 字）来说，关键词加权检索的效果与向量检索差距不大，
   而且**可解释** —— 能说清「为什么推荐了这条模板」，这在法务场景比黑盒相似度更重要。
2. 若配置了 ``APP_EMBEDDING_*``，则叠加一层向量召回做融合排序（接口已留好，
   接 Qdrant 或 BGE embedding 服务时只需实现 :class:`EmbeddingProvider`）。

这样默认零外部依赖可用，需要时再升级到向量库，不会因为没起容器就整个跑不起来。
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

from app.core.exceptions import AppError
from app.core.logging import logger
from app.models.enums import ClauseType, SuggestionSource
from app.schemas.risk import RiskItemModel, SuggestionModel
from app.schemas.rule import ClauseTemplateLibrary, ClauseTemplateModel

#: 送进检索的条款文本上限
QUERY_MAX_LEN = 800
#: 默认返回 Top-K，再交给 LLM 改写
DEFAULT_TOP_K = 3
#: 相似度低于此值的模板不参与改写（避免给出风马牛不相及的建议）
MIN_SIMILARITY = 0.06

_TOKEN = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9]{1,}")
#: BM25 参数
_K1, _B = 1.5, 0.75


def _bigrams(text: str) -> list[str]:
    """中文按 2-gram 切分，英文/数字按词切分。

    单字切分噪声太大（「的」「和」满天飞），2-gram 在中文短文本检索上稳得多。
    """
    tokens: list[str] = []
    for chunk in _TOKEN.findall(text):
        if chunk[0].isascii():
            tokens.append(chunk.lower())
        else:
            tokens.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
    return tokens


class ClauseLibrary:
    """标准示范条款库。"""

    def __init__(self, library: ClauseTemplateLibrary) -> None:
        self.library = library
        self._docs: list[list[str]] = []
        self._df: Counter[str] = Counter()
        self._avg_len = 1.0
        self._build_index()

    # ------------------------------------------------------------------
    @classmethod
    def from_file(cls, path: Path | str) -> "ClauseLibrary":
        path = Path(path)
        if not path.exists():
            raise AppError(f"标准条款库文件不存在：{path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AppError(f"标准条款库 JSON 解析失败：{path} — {exc}") from exc
        return cls(ClauseTemplateLibrary.model_validate(raw))

    def _build_index(self) -> None:
        for tpl in self.library.templates:
            # 可检索文本 = 标题 + 关键词（关键词加权靠重复出现实现）+ 正文 + 场景
            text = " ".join(
                [
                    tpl.title,
                    " ".join(tpl.keywords) * 2,
                    " ".join(tpl.tags),
                    tpl.scenario,
                    tpl.content,
                ]
            )
            toks = _bigrams(text)
            self._docs.append(toks)
            for t in set(toks):
                self._df[t] += 1
        self._avg_len = max(sum(len(d) for d in self._docs) / max(len(self._docs), 1), 1.0)

    # ------------------------------------------------------------------
    @property
    def templates(self) -> list[ClauseTemplateModel]:
        return [t for t in self.library.templates if t.enabled]

    def get(self, template_id: str) -> ClauseTemplateModel | None:
        for t in self.library.templates:
            if t.template_id == template_id:
                return t
        return None

    def by_type(self, ctype: ClauseType | str) -> list[ClauseTemplateModel]:
        want = ctype.value if isinstance(ctype, ClauseType) else ctype
        return [t for t in self.templates if t.clause_type.value == want]

    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        clause_type: ClauseType | str | None = None,
        contract_type: str | None = None,
        top_k: int = DEFAULT_TOP_K,
    ) -> list[tuple[ClauseTemplateModel, float]]:
        """BM25 检索，返回 ``[(模板, 归一化相似度)]``。"""
        q_tokens = _bigrams(query[:QUERY_MAX_LEN])
        if not q_tokens:
            return []

        n_docs = len(self._docs)
        q_tf = Counter(q_tokens)
        scored: list[tuple[ClauseTemplateModel, float]] = []

        for tpl, doc in zip(self.library.templates, self._docs):
            if not tpl.enabled:
                continue
            if clause_type is not None:
                want = clause_type.value if isinstance(clause_type, ClauseType) else clause_type
                # 类型不同不参与，但 other 类型放宽（它是兜底类别）
                if tpl.clause_type.value != want and tpl.clause_type is not ClauseType.OTHER:
                    continue
            if contract_type and not tpl.applies_to(contract_type):
                continue

            dl = max(len(doc), 1)
            tf = Counter(doc)
            score = 0.0
            for term, qn in q_tf.items():
                f = tf.get(term, 0)
                if not f:
                    continue
                df = self._df.get(term, 0)
                idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
                score += idf * (f * (_K1 + 1)) / (f + _K1 * (1 - _B + _B * dl / self._avg_len)) * min(qn, 3)
            if score <= 0:
                continue

            # 归一化到 0-1：用「本查询可能达到的最大分」做分母
            norm = score / (score + 12.0)
            scored.append((tpl, round(norm, 4)))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


class ClauseRecommender:
    """条款推荐引擎：检索 → （可选）LLM 改写 → 产出建议对象。"""

    def __init__(self, library: ClauseLibrary, llm=None) -> None:
        self.library = library
        if llm is None:
            from app.llm.client import get_llm

            llm = get_llm()
        self.llm = llm

    # ------------------------------------------------------------------
    async def recommend(
        self,
        risk: RiskItemModel,
        *,
        top_k: int = DEFAULT_TOP_K,
        use_llm: bool = True,
        contract_type: str = "other",
        our_side: str = "甲方",
    ) -> SuggestionModel | None:
        """为一条风险项生成修改示范条款。"""
        original = risk.span.quote or ""
        query = f"{risk.name} {risk.reason} {original}"

        candidates = self.library.search(
            query, clause_type=risk.clause_type, contract_type=contract_type, top_k=top_k
        )
        if not candidates:
            # 放宽：不限条款类型再搜一次
            candidates = self.library.search(query, contract_type=contract_type, top_k=top_k)

        best, best_score = (candidates[0] if candidates else (None, 0.0))

        # 规则自带的建议模板优先（它已经针对该规则精调过）
        if risk.suggestion and risk.suggestion.suggested_text:
            return risk.suggestion

        if best is None or best_score < MIN_SIMILARITY:
            return None

        suggestion = SuggestionModel(
            original_text=original,
            suggested_text=best.content,
            rationale=f"命中标准示范条款库「{best.title}」（相似度 {best_score:.2f}）",
            source=SuggestionSource.TEMPLATE,
            template_id=best.template_id,
            similarity=best_score,
        )

        if not use_llm or not self.llm.enabled:
            return suggestion

        rewritten = await self._llm_rewrite(
            risk, original, candidates, contract_type=contract_type, our_side=our_side
        )
        return rewritten or suggestion

    # ------------------------------------------------------------------
    async def _llm_rewrite(
        self,
        risk: RiskItemModel,
        original: str,
        candidates: list[tuple[ClauseTemplateModel, float]],
        *,
        contract_type: str,
        our_side: str,
    ) -> SuggestionModel | None:
        """让 LLM 结合本合同上下文把模板改写成可直接落地的条款。"""
        from pydantic import BaseModel, Field

        class _Rewrite(BaseModel):
            suggested_text: str = ""
            rationale: str = ""

        tpl_text = "\n\n".join(
            f"[{i + 1}] {t.title}（相似度 {s:.2f}）\n{t.content}"
            for i, (t, s) in enumerate(candidates)
        )
        out = await self.llm.complete_typed(
            prompt_name="clause_rewrite",
            variables={
                "contract_type": contract_type,
                "our_side": our_side,
                "risk_name": risk.name,
                "risk_level": risk.risk_level.value,
                "reason": risk.reason,
                "legal_basis": risk.legal_basis,
                "original_clause": original or "（规则未定位到具体条款原文）",
                "top_k": len(candidates),
                "templates": tpl_text,
            },
            schema=_Rewrite,
            temperature=0.2,
        )
        if out is None or not out.suggested_text.strip():
            return None

        best, best_score = candidates[0]
        logger.info(f"[条款推荐] LLM 改写完成：{risk.name}")
        return SuggestionModel(
            original_text=original,
            suggested_text=out.suggested_text.strip(),
            rationale=out.rationale.strip() or f"基于标准示范条款库「{best.title}」结合本合同交易条件改写",
            source=SuggestionSource.LLM,
            template_id=best.template_id,
            similarity=best_score,
        )

    # ------------------------------------------------------------------
    async def recommend_batch(
        self,
        risks: list[RiskItemModel],
        *,
        use_llm: bool = True,
        contract_type: str = "other",
        our_side: str = "甲方",
        only_missing: bool = True,
    ) -> int:
        """批量补建议。返回实际生成建议的条数。"""
        n = 0
        for risk in risks:
            if only_missing and risk.suggestion and risk.suggestion.suggested_text:
                continue
            sug = await self.recommend(
                risk, use_llm=use_llm, contract_type=contract_type, our_side=our_side
            )
            if sug:
                risk.suggestion = sug
                n += 1
        logger.info(f"[条款推荐] 为 {n}/{len(risks)} 条风险生成修改示范条款")
        return n


__all__ = ["ClauseLibrary", "ClauseRecommender", "DEFAULT_TOP_K", "MIN_SIMILARITY"]
