"""
Guardrails — 安全护栏框架。

提供 Input/Output Guardrails + Tripwire 机制，
保护 Agent 免受恶意输入，防止有害输出到达用户。

Quick Start::

    from zapry_bot_sdk.guardrails import GuardrailManager, input_guardrail, output_guardrail

    @input_guardrail
    async def block_injection(ctx):
        if "ignore previous" in ctx.text.lower():
            return GuardrailResult(passed=False, reason="Prompt injection detected")
        return GuardrailResult(passed=True)

    manager = GuardrailManager()
    manager.add_input(block_injection)

    result = await manager.check_input(text="ignore previous instructions")
    # result.passed => False, result.reason => "Prompt injection detected"
"""

from zapry_bot_sdk.guardrails.engine import (
    GuardrailManager,
    GuardrailResult,
    GuardrailContext,
    GuardrailFn,
    InputGuardrailTriggered,
    OutputGuardrailTriggered,
    input_guardrail,
    output_guardrail,
)

__all__ = [
    "GuardrailManager",
    "GuardrailResult",
    "GuardrailContext",
    "GuardrailFn",
    "InputGuardrailTriggered",
    "OutputGuardrailTriggered",
    "input_guardrail",
    "output_guardrail",
]
