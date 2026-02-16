"""
Guardrails Engine — Input/Output 安全护栏 + Tripwire 异常机制。

架构:
- Input Guardrails: LLM 调用前拦截（prompt injection、PII、长度等）
- Output Guardrails: 返回用户前拦截（内容审核、格式验证、敏感信息）
- Tripwire: 检测到违规时抛出异常，中断 Agent Loop
- 两种执行模式: parallel (低延迟) / blocking (高安全)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("zapry_agents_sdk.guardrails")


# ──────────────────────────────────────────────
# Exceptions (Tripwire)
# ──────────────────────────────────────────────


class InputGuardrailTriggered(Exception):
    """Raised when an input guardrail tripwire is triggered."""

    def __init__(self, guardrail_name: str, reason: str = "") -> None:
        self.guardrail_name = guardrail_name
        self.reason = reason
        super().__init__(f"Input guardrail triggered: {guardrail_name} — {reason}")


class OutputGuardrailTriggered(Exception):
    """Raised when an output guardrail tripwire is triggered."""

    def __init__(self, guardrail_name: str, reason: str = "") -> None:
        self.guardrail_name = guardrail_name
        self.reason = reason
        super().__init__(f"Output guardrail triggered: {guardrail_name} — {reason}")


# ──────────────────────────────────────────────
# Data types
# ──────────────────────────────────────────────


@dataclass
class GuardrailContext:
    """Context passed to guardrail functions.

    Attributes:
        text: The text to check (user input or agent output).
        messages: Full message history (if available).
        extra: Arbitrary metadata.
    """

    text: str = ""
    messages: List[Dict] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GuardrailResult:
    """Result of a single guardrail check.

    Attributes:
        passed: True if the check passed (content is safe).
        reason: Explanation when check fails.
        guardrail_name: Name of the guardrail that produced this result.
        metadata: Additional data from the check.
    """

    passed: bool = True
    reason: str = ""
    guardrail_name: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


# Guardrail function signature
GuardrailFn = Callable[[GuardrailContext], Awaitable[GuardrailResult]]


# ──────────────────────────────────────────────
# Guardrail descriptor (internal)
# ──────────────────────────────────────────────


@dataclass
class _GuardrailDef:
    name: str
    fn: GuardrailFn
    kind: str  # "input" or "output"


# ──────────────────────────────────────────────
# Decorators
# ──────────────────────────────────────────────


def input_guardrail(
    fn: Optional[GuardrailFn] = None,
    *,
    name: Optional[str] = None,
) -> Any:
    """Decorator to mark a function as an input guardrail.

    Usage::

        @input_guardrail
        async def no_injection(ctx):
            if "ignore" in ctx.text.lower():
                return GuardrailResult(passed=False, reason="Injection")
            return GuardrailResult(passed=True)

        @input_guardrail(name="custom_name")
        async def check(ctx): ...
    """

    def decorator(func: GuardrailFn) -> _GuardrailDef:
        gname = name or func.__name__
        return _GuardrailDef(name=gname, fn=func, kind="input")

    if fn is not None:
        return decorator(fn)
    return decorator


def output_guardrail(
    fn: Optional[GuardrailFn] = None,
    *,
    name: Optional[str] = None,
) -> Any:
    """Decorator to mark a function as an output guardrail.

    Usage::

        @output_guardrail
        async def no_pii(ctx):
            if detect_pii(ctx.text):
                return GuardrailResult(passed=False, reason="PII detected")
            return GuardrailResult(passed=True)
    """

    def decorator(func: GuardrailFn) -> _GuardrailDef:
        gname = name or func.__name__
        return _GuardrailDef(name=gname, fn=func, kind="output")

    if fn is not None:
        return decorator(fn)
    return decorator


# ──────────────────────────────────────────────
# GuardrailManager
# ──────────────────────────────────────────────


class GuardrailManager:
    """Manages input and output guardrails with tripwire support.

    Parameters:
        parallel: If True (default), run guardrails in parallel for lower latency.
            If False, run sequentially and stop at first failure.

    Usage::

        manager = GuardrailManager()
        manager.add_input(my_input_guard)
        manager.add_output(my_output_guard)

        # Check input (raises InputGuardrailTriggered on failure)
        await manager.check_input(text="user message")

        # Check output (raises OutputGuardrailTriggered on failure)
        await manager.check_output(text="agent response")

        # Or check without raising (returns GuardrailResult)
        result = await manager.check_input_safe(text="test")
        if not result.passed:
            print(result.reason)
    """

    def __init__(self, parallel: bool = True) -> None:
        self._input_guards: List[_GuardrailDef] = []
        self._output_guards: List[_GuardrailDef] = []
        self._parallel = parallel

    # ─── Registration ───

    def add_input(self, guard: Any) -> None:
        """Add an input guardrail (decorated function or _GuardrailDef)."""
        gdef = self._resolve(guard, "input")
        self._input_guards.append(gdef)
        logger.debug("Input guardrail added: %s", gdef.name)

    def add_output(self, guard: Any) -> None:
        """Add an output guardrail (decorated function or _GuardrailDef)."""
        gdef = self._resolve(guard, "output")
        self._output_guards.append(gdef)
        logger.debug("Output guardrail added: %s", gdef.name)

    @property
    def input_count(self) -> int:
        return len(self._input_guards)

    @property
    def output_count(self) -> int:
        return len(self._output_guards)

    # ─── Check (with tripwire) ───

    async def check_input(
        self,
        text: str,
        messages: Optional[List[Dict]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> GuardrailResult:
        """Check input against all input guardrails.

        Raises InputGuardrailTriggered if any guardrail fails.
        Returns the first failure or a passed result.
        """
        result = await self._run_guards(
            self._input_guards,
            GuardrailContext(text=text, messages=messages or [], extra=extra or {}),
        )
        if not result.passed:
            raise InputGuardrailTriggered(result.guardrail_name, result.reason)
        return result

    async def check_output(
        self,
        text: str,
        messages: Optional[List[Dict]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> GuardrailResult:
        """Check output against all output guardrails.

        Raises OutputGuardrailTriggered if any guardrail fails.
        """
        result = await self._run_guards(
            self._output_guards,
            GuardrailContext(text=text, messages=messages or [], extra=extra or {}),
        )
        if not result.passed:
            raise OutputGuardrailTriggered(result.guardrail_name, result.reason)
        return result

    # ─── Check (safe, no exception) ───

    async def check_input_safe(
        self,
        text: str,
        messages: Optional[List[Dict]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> GuardrailResult:
        """Check input without raising exceptions."""
        return await self._run_guards(
            self._input_guards,
            GuardrailContext(text=text, messages=messages or [], extra=extra or {}),
        )

    async def check_output_safe(
        self,
        text: str,
        messages: Optional[List[Dict]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> GuardrailResult:
        """Check output without raising exceptions."""
        return await self._run_guards(
            self._output_guards,
            GuardrailContext(text=text, messages=messages or [], extra=extra or {}),
        )

    # ─── Internal ───

    async def _run_guards(
        self,
        guards: List[_GuardrailDef],
        ctx: GuardrailContext,
    ) -> GuardrailResult:
        if not guards:
            return GuardrailResult(passed=True)

        if self._parallel:
            return await self._run_parallel(guards, ctx)
        else:
            return await self._run_sequential(guards, ctx)

    async def _run_parallel(
        self,
        guards: List[_GuardrailDef],
        ctx: GuardrailContext,
    ) -> GuardrailResult:
        """Run all guardrails in parallel, return first failure."""
        tasks = [self._execute_one(g, ctx) for g in guards]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, Exception):
                return GuardrailResult(
                    passed=False,
                    reason=f"Guardrail error: {r}",
                    guardrail_name="unknown",
                )
            if not r.passed:
                return r

        return GuardrailResult(passed=True)

    async def _run_sequential(
        self,
        guards: List[_GuardrailDef],
        ctx: GuardrailContext,
    ) -> GuardrailResult:
        """Run guardrails sequentially, stop at first failure."""
        for g in guards:
            try:
                result = await self._execute_one(g, ctx)
                if not result.passed:
                    return result
            except Exception as e:
                return GuardrailResult(
                    passed=False,
                    reason=f"Guardrail error: {e}",
                    guardrail_name=g.name,
                )
        return GuardrailResult(passed=True)

    async def _execute_one(
        self,
        guard: _GuardrailDef,
        ctx: GuardrailContext,
    ) -> GuardrailResult:
        """Execute a single guardrail and set its name on the result."""
        result = await guard.fn(ctx)
        result.guardrail_name = guard.name
        return result

    def _resolve(self, guard: Any, default_kind: str) -> _GuardrailDef:
        """Resolve a guardrail to a _GuardrailDef."""
        if isinstance(guard, _GuardrailDef):
            return guard
        if callable(guard):
            return _GuardrailDef(
                name=getattr(guard, "__name__", "anonymous"),
                fn=guard,
                kind=default_kind,
            )
        raise TypeError(f"Expected guardrail function or decorator, got {type(guard)}")
