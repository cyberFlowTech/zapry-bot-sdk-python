"""
Zapry Agents SDK — Python SDK for building AI Agents on the Zapry platform.

基于 python-telegram-bot，提供 Zapry 平台兼容层、
配置管理、Agent 框架（Tool Calling / Memory / Handoff）等开发工具。

Quick Start:
    from zapry_agents_sdk import ZapryAgent, AgentConfig

    config = AgentConfig.from_env()
    bot = ZapryAgent(config)

    @bot.command("start")
    async def start(update, context):
        await update.message.reply_text("Hello!")

    bot.run()
"""

__version__ = "0.6.0"

from zapry_agents_sdk.core.config import AgentConfig
from zapry_agents_sdk.core.agent import ZapryAgent
from zapry_agents_sdk.core.middleware import MiddlewareContext, MiddlewarePipeline
from zapry_agents_sdk.helpers.handler_registry import command, callback_query, message
from zapry_agents_sdk.proactive.scheduler import ProactiveScheduler, TriggerContext
from zapry_agents_sdk.proactive.feedback import (
    FeedbackDetector,
    FeedbackResult,
    build_preference_prompt,
)
from zapry_agents_sdk.tools.registry import ToolRegistry, ToolDef, ToolContext, tool
from zapry_agents_sdk.memory.session import MemorySession
from zapry_agents_sdk.memory.store import InMemoryStore
from zapry_agents_sdk.memory.store_sqlite import SQLiteMemoryStore
from zapry_agents_sdk.agent.loop import AgentLoop, AgentResult, AgentHooks
from zapry_agents_sdk.agent.card import AgentCardPublic, AgentRuntime
from zapry_agents_sdk.agent.registry import AgentRegistry
from zapry_agents_sdk.agent.handoff import HandoffRequest, HandoffResult
from zapry_agents_sdk.agent.engine import HandoffEngine
from zapry_agents_sdk.agent.orchestrator import AgentOrchestrator
from zapry_agents_sdk.agent.policy import HandoffPolicy
from zapry_agents_sdk.guardrails.engine import (
    GuardrailManager,
    GuardrailResult,
    GuardrailContext,
    InputGuardrailTriggered,
    OutputGuardrailTriggered,
    input_guardrail,
    output_guardrail,
)
from zapry_agents_sdk.tracing.engine import Tracer, Span, SpanKind, ConsoleExporter

__all__ = [
    "ZapryAgent",
    "AgentConfig",
    "MiddlewareContext",
    "MiddlewarePipeline",
    "command",
    "callback_query",
    "message",
    "ProactiveScheduler",
    "TriggerContext",
    "FeedbackDetector",
    "FeedbackResult",
    "build_preference_prompt",
    "ToolRegistry",
    "ToolDef",
    "ToolContext",
    "tool",
    "MemorySession",
    "InMemoryStore",
    "SQLiteMemoryStore",
    "AgentLoop",
    "AgentResult",
    "AgentHooks",
    "GuardrailManager",
    "GuardrailResult",
    "GuardrailContext",
    "InputGuardrailTriggered",
    "OutputGuardrailTriggered",
    "input_guardrail",
    "output_guardrail",
    "Tracer",
    "Span",
    "SpanKind",
    "ConsoleExporter",
    "AgentCardPublic",
    "AgentRuntime",
    "AgentRegistry",
    "HandoffRequest",
    "HandoffResult",
    "HandoffEngine",
    "AgentOrchestrator",
    "HandoffPolicy",
    "__version__",
]
