"""大模型客户端（OpenAI 兼容协议）。

设计要点
--------
1. **可插拔**：``APP_LLM_ENABLED=false`` 或没配 key 时，所有调用返回 ``None``，
   调用方必须自己走规则/模板兜底 —— 系统在无 LLM 环境下也能完整跑通。
2. **强制结构化**：默认开 ``response_format={"type": "json_object"}``，
   再用 Pydantic 校验。校验失败把错误信息回喂给模型重试一次（self-repair）。
3. **宽松解析**：第三方网关常不遵守 json_object，所以还准备了一层
   从 ```json 代码块里抠 JSON 的兜底。
4. 任何 LLM 故障都不应该拖垮整条流水线，最外层统一降级。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import logger
from app.llm.prompt_loader import load_prompt

T = TypeVar("T", bound=BaseModel)

_CODE_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LLMUsage(BaseModel):
    """一次调用的用量与耗时。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    model: str = ""


class LLMResult(BaseModel):
    """LLM 调用结果。"""

    text: str = ""
    parsed: dict[str, Any] | None = None
    usage: LLMUsage = LLMUsage()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出里尽力抠出一个 JSON 对象。"""
    if not text:
        return None
    s = text.strip()

    # 1) 直接就是 JSON
    for candidate in (s,):
        try:
            obj = json.loads(candidate)
            return obj if isinstance(obj, dict) else {"data": obj}
        except json.JSONDecodeError:
            pass

    # 2) 被 ```json 包着
    m = _CODE_FENCE.search(s)
    if m:
        try:
            obj = json.loads(m.group(1).strip())
            return obj if isinstance(obj, dict) else {"data": obj}
        except json.JSONDecodeError:
            pass

    # 3) 取第一个 { 到最后一个 } 之间的内容
    i, j = s.find("{"), s.rfind("}")
    if 0 <= i < j:
        try:
            obj = json.loads(s[i : j + 1])
            return obj if isinstance(obj, dict) else {"data": obj}
        except json.JSONDecodeError:
            pass

    return None


class LLMClient:
    """异步 LLM 客户端。每次实例化都很轻，可以随便建。"""

    def __init__(self) -> None:
        self._client: Any = None
        self.enabled = settings.llm_ready

    # ------------------------------------------------------------------
    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=settings.llm_api_key or "EMPTY",
                base_url=settings.llm_base_url,
                timeout=settings.llm_timeout,
                max_retries=settings.llm_max_retries,
            )
        return self._client

    # ------------------------------------------------------------------
    async def complete(
        self,
        *,
        prompt_name: str,
        variables: dict[str, Any],
        json_mode: bool = True,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        """渲染 prompt 并调用模型。LLM 未启用时返回带 error 的结果，不抛异常。"""
        if not self.enabled:
            return LLMResult(error="LLM 未启用（APP_LLM_ENABLED=false 或未配置 APP_LLM_API_KEY）")

        tpl = load_prompt(prompt_name)
        try:
            content = tpl.render_strict(**variables)
        except AppError as exc:
            return LLMResult(error=str(exc))

        return await self._chat(
            content,
            json_mode=json_mode,
            temperature=temperature,
            max_tokens=max_tokens,
            prompt_name=prompt_name,
        )

    # ------------------------------------------------------------------
    async def _chat(
        self,
        content: str,
        *,
        json_mode: bool,
        temperature: float | None,
        max_tokens: int | None,
        prompt_name: str,
        repair_hint: str | None = None,
    ) -> LLMResult:
        client = self._get_client()
        model = settings.llm_model
        messages: list[dict[str, str]] = [
            {"role": "system", "content": "你是严谨的中文法律文档助手，严格按要求格式输出。"},
            {"role": "user", "content": content},
        ]
        if repair_hint:
            messages.append(
                {
                    "role": "user",
                    "content": f"上一次的输出无法解析为合法 JSON。错误：{repair_hint}\n请只输出合法 JSON，不要任何其他文字。",
                }
            )

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": settings.llm_temperature if temperature is None else temperature,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if json_mode:
            # 不支持的网关会报错，下面会降级重试
            kwargs["response_format"] = {"type": "json_object"}

        t0 = time.perf_counter()
        try:
            resp = await client.chat.completions.create(**kwargs)
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            # response_format 不被支持 → 去掉重试
            if json_mode and ("response_format" in msg or "json_object" in msg.lower()):
                logger.warning(f"[LLM:{prompt_name}] 网关不支持 json_object，降级为纯文本模式")
                kwargs.pop("response_format", None)
                try:
                    resp = await client.chat.completions.create(**kwargs)
                except Exception as exc2:
                    return LLMResult(error=f"{type(exc2).__name__}: {exc2}")
            else:
                logger.error(f"[LLM:{prompt_name}] 调用失败：{msg}")
                return LLMResult(error=msg)

        latency = int((time.perf_counter() - t0) * 1000)
        text = (resp.choices[0].message.content or "").strip() if resp.choices else ""

        usage = LLMUsage(model=model, latency_ms=latency)
        if getattr(resp, "usage", None):
            usage.prompt_tokens = resp.usage.prompt_tokens or 0
            usage.completion_tokens = resp.usage.completion_tokens or 0
            usage.total_tokens = resp.usage.total_tokens or 0

        result = LLMResult(text=text, usage=usage)

        if json_mode:
            parsed = _extract_json(text)
            if parsed is None:
                # 自修复重试一次
                if repair_hint is None:
                    logger.warning(f"[LLM:{prompt_name}] 输出非 JSON，触发一次自修复重试")
                    retry = await self._chat(
                        content,
                        json_mode=json_mode,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        prompt_name=prompt_name,
                        repair_hint="输出中找不到合法 JSON 对象",
                    )
                    return retry
                result.error = f"无法解析模型输出的 JSON：{text[:200]}"
                return result
            result.parsed = parsed

        logger.info(
            f"[LLM:{prompt_name}] ok {latency}ms tokens={usage.total_tokens} "
            f"len={len(text)}"
        )
        return result

    # ------------------------------------------------------------------
    async def complete_typed(
        self,
        *,
        prompt_name: str,
        variables: dict[str, Any],
        schema: type[T],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> T | None:
        """调用并直接校验成 Pydantic 模型。失败返回 ``None``（调用方走兜底）。"""
        res = await self.complete(
            prompt_name=prompt_name,
            variables=variables,
            json_mode=True,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if not res.ok or res.parsed is None:
            if res.error:
                logger.warning(f"[LLM:{prompt_name}] 降级（{res.error}）")
            return None
        try:
            return schema.model_validate(res.parsed)
        except ValidationError as exc:
            logger.warning(
                f"[LLM:{prompt_name}] 结构校验失败，字段错误 "
                f"{[e['loc'] for e in exc.errors()][:5]}"
            )
            return None

    # ------------------------------------------------------------------
    async def health_check(self) -> dict[str, Any]:
        """连通性自检，给 /api/v1/llm/health 用。"""
        if not self.enabled:
            return {"ok": False, "reason": "未启用或未配置 API Key"}
        t0 = time.perf_counter()
        try:
            client = self._get_client()
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=settings.llm_model,
                    messages=[{"role": "user", "content": "回复两个字：可用"}],
                    temperature=0,
                    max_tokens=16,
                ),
                timeout=30,
            )
            return {
                "ok": True,
                "model": settings.llm_model,
                "base_url": settings.llm_base_url,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "reply": (resp.choices[0].message.content or "").strip()[:50],
            }
        except Exception as exc:
            return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    """进程级单例。"""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def reset_llm() -> None:
    """配置变更后重置（测试用）。"""
    global _client
    _client = None


__all__ = ["LLMClient", "LLMResult", "LLMUsage", "get_llm", "reset_llm"]
