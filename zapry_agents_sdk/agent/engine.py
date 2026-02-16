"""
HandoffEngine — 统一执行引擎。

不论 tool-based 还是 coordinator 模式，最终都通过 engine.handoff() 执行。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from zapry_agents_sdk.agent.card import AgentRuntime
from zapry_agents_sdk.agent.handoff import (
    HandoffContext,
    HandoffError,
    HandoffMessage,
    HandoffRequest,
    HandoffResult,
    InputFilterFn,
)
from zapry_agents_sdk.agent.policy import HandoffPolicy, IdempotencyCache
from zapry_agents_sdk.agent.registry import AgentRegistry

logger = logging.getLogger("zapry_agents_sdk.agent")


class HandoffEngine:
    """统一的 Handoff 执行引擎。

    Parameters:
        registry: Agent 注册表。
        policy: 权限/循环防护规则。
        tracer: 可选的 Tracer（自动生成 handoff_span）。
        idempotency_cache: 可选的幂等缓存。
        platform_filter: 平台级强制过滤（开发者不可绕过）。
    """

    def __init__(
        self,
        registry: AgentRegistry,
        policy: Optional[HandoffPolicy] = None,
        tracer: Optional[Any] = None,
        idempotency_cache: Optional[IdempotencyCache] = None,
        platform_filter: Optional[InputFilterFn] = None,
    ) -> None:
        self.registry = registry
        self.policy = policy or HandoffPolicy()
        self.tracer = tracer
        self.idempotency_cache = idempotency_cache
        self.platform_filter = platform_filter

    async def handoff(self, request: HandoffRequest) -> HandoffResult:
        """统一执行流程（12 步）。"""
        start = time.time()

        # 1. 幂等检查
        if self.idempotency_cache and request.request_id:
            return await self.idempotency_cache.get_or_execute(
                request.request_id,
                lambda: self._execute(request, start),
            )

        return await self._execute(request, start)

    async def _execute(self, request: HandoffRequest, start: float) -> HandoffResult:
        """Core execution pipeline."""
        try:
            # 2. 查找目标 Agent
            target = self.registry.get(request.to_agent)
            if not target:
                return self._error_result(request, "NOT_FOUND", f"Agent not found: {request.to_agent}", start)

            # 3. 权限检查
            access_err = self.policy.check_access(request, target.card)
            if access_err:
                return self._error_result(request, access_err.code, access_err.message, start)

            # 4. 循环检测
            loop_err = self.policy.check_loop(request)
            if loop_err:
                return self._error_result(request, loop_err.code, loop_err.message, start)

            # 5. 上下文过滤（固定顺序: platform → target → token_budget）
            ctx = request.context
            if self.platform_filter:
                ctx = await self.platform_filter(ctx)
            if target.input_filter:
                ctx = await target.input_filter(ctx)

            # 6. 超时控制 + 执行目标 Agent
            timeout_s = request.deadline_ms / 1000.0
            try:
                agent_result = await asyncio.wait_for(
                    self._run_agent(target, ctx, request),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                return self._error_result(request, "TIMEOUT", f"Handoff timed out after {request.deadline_ms}ms", start)

            # 7. 构造结果
            result = HandoffResult(
                output=agent_result.get("output", ""),
                agent_id=request.to_agent,
                should_return=True,
                status="success",
                usage=agent_result.get("usage"),
                duration_ms=(time.time() - start) * 1000,
                request_id=request.request_id,
            )

            # 8. Tracing
            if self.tracer:
                try:
                    from zapry_agents_sdk.tracing.engine import SpanKind
                    with self.tracer.span(
                        f"handoff:{request.from_agent}->{request.to_agent}",
                        SpanKind.CUSTOM,
                        from_agent=request.from_agent,
                        to_agent=request.to_agent,
                        hop_count=request.hop_count,
                        status=result.status,
                    ):
                        pass
                except Exception:
                    pass

            return result

        except Exception as e:
            logger.error("HandoffEngine error: %s", e)
            return self._error_result(request, "TOOL_ERROR", str(e), start)

    async def _run_agent(
        self,
        target: AgentRuntime,
        ctx: HandoffContext,
        request: HandoffRequest,
    ) -> Dict[str, Any]:
        """Run the target agent's AgentLoop."""
        from zapry_agents_sdk.agent.loop import AgentLoop
        from zapry_agents_sdk.tools.registry import ToolRegistry

        # Build conversation from HandoffContext
        messages_for_history = [m.to_dict() for m in ctx.messages]

        # Build the user query from the last user message or reason
        user_input = request.reason
        for msg in reversed(ctx.messages):
            if msg.role == "user":
                user_input = msg.content
                break

        loop = AgentLoop(
            llm_fn=target.llm_fn,
            tool_registry=target.tool_registry or ToolRegistry(),
            system_prompt=target.system_prompt,
            max_turns=target.max_turns,
            guardrails=target.guardrails,
            tracer=target.tracer,
        )

        result = await loop.run(
            user_input=user_input,
            conversation_history=messages_for_history[:-1] if messages_for_history else None,
            extra_context=ctx.memory_summary if ctx.memory_summary else None,
        )

        return {
            "output": result.final_output,
            "usage": {"total_turns": result.total_turns, "tool_calls": result.tool_calls_count},
        }

    def _error_result(
        self,
        request: HandoffRequest,
        code: str,
        message: str,
        start: float,
    ) -> HandoffResult:
        return HandoffResult(
            agent_id=request.to_agent,
            status="error" if code not in ("LOOP_DETECTED", "TIMEOUT") else code.lower(),
            error=HandoffError(code=code, message=message, retryable=code in ("TIMEOUT", "MODEL_ERROR")),
            duration_ms=(time.time() - start) * 1000,
            request_id=request.request_id,
        )
