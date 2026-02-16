"""
Agent 框架 — AgentLoop + Multi-Agent Handoff。
"""

from zapry_bot_sdk.agent.loop import AgentLoop, AgentResult, TurnRecord, AgentHooks
from zapry_bot_sdk.agent.card import AgentCardPublic, AgentRuntime
from zapry_bot_sdk.agent.registry import AgentRegistry
from zapry_bot_sdk.agent.handoff import (
    HandoffMessage,
    HandoffError,
    HandoffContext,
    HandoffRequest,
    HandoffResult,
    InputFilterFn,
    last_n_messages,
    summary_only,
    allow_all,
    platform_redact,
)
from zapry_bot_sdk.agent.policy import HandoffPolicy, IdempotencyCache
from zapry_bot_sdk.agent.engine import HandoffEngine
from zapry_bot_sdk.agent.orchestrator import AgentOrchestrator, CoordinatorDecision

__all__ = [
    "AgentLoop",
    "AgentResult",
    "TurnRecord",
    "AgentHooks",
    "AgentCardPublic",
    "AgentRuntime",
    "AgentRegistry",
    "HandoffMessage",
    "HandoffError",
    "HandoffContext",
    "HandoffRequest",
    "HandoffResult",
    "InputFilterFn",
    "last_n_messages",
    "summary_only",
    "allow_all",
    "platform_redact",
    "HandoffPolicy",
    "IdempotencyCache",
    "HandoffEngine",
    "AgentOrchestrator",
    "CoordinatorDecision",
]
