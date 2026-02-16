"""
Handoff 数据结构 — 运营级合同 + 统一 Message Schema + InputFilter。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional


# ──────────────────────────────────────────────
# Unified Message Schema
# ──────────────────────────────────────────────

@dataclass
class HandoffMessage:
    """统一的跨 Agent 消息格式。"""
    role: str              # "user" | "assistant" | "tool" | "system"
    content: str = ""
    name: Optional[str] = None
    attachments: List[Dict] = field(default_factory=list)
    redaction_tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "HandoffMessage":
        return cls(
            role=d.get("role", "user"),
            content=d.get("content", ""),
            name=d.get("name"),
        )


# ──────────────────────────────────────────────
# Handoff Error
# ──────────────────────────────────────────────

@dataclass
class HandoffError:
    """结构化错误。"""
    code: str       # NOT_FOUND | NOT_ALLOWED | SAFETY_BLOCK | TIMEOUT | LOOP_DETECTED | TOOL_ERROR | MODEL_ERROR | RATE_LIMITED
    message: str
    retryable: bool = False


# ──────────────────────────────────────────────
# Handoff Context
# ──────────────────────────────────────────────

@dataclass
class HandoffContext:
    """Handoff 传递的上下文。"""
    messages: List[HandoffMessage] = field(default_factory=list)
    memory_summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    token_budget: int = 4000
    redaction_report: List[str] = field(default_factory=list)
    attachments: List[Dict] = field(default_factory=list)
    locale: str = "zh-CN"


# ──────────────────────────────────────────────
# Handoff Request
# ──────────────────────────────────────────────

@dataclass
class HandoffRequest:
    """Handoff 请求合同。"""
    from_agent: str = ""
    to_agent: str = ""
    reason: str = ""
    requested_mode: str = "auto"  # "tool_based" | "coordinator" | "auto"

    request_id: str = ""
    trace_id: str = ""
    deadline_ms: int = 30000

    hop_count: int = 0
    visited_agents: List[str] = field(default_factory=list)

    caller_owner_id: str = ""
    caller_org_id: str = ""

    context: HandoffContext = field(default_factory=HandoffContext)

    # Original tool_call_id (for return contract)
    original_tool_call_id: str = ""

    def __post_init__(self):
        if not self.request_id:
            self.request_id = uuid.uuid4().hex


# ──────────────────────────────────────────────
# Handoff Result
# ──────────────────────────────────────────────

@dataclass
class HandoffResult:
    """Handoff 结果合同。"""
    output: str = ""
    agent_id: str = ""
    should_return: bool = True
    return_context: Optional[HandoffContext] = None

    status: str = "success"  # success | error | timeout | denied | loop_detected
    error: Optional[HandoffError] = None
    usage: Optional[Dict[str, Any]] = None
    duration_ms: float = 0
    request_id: str = ""
    cache_hit: bool = False

    def to_return_message(self, tool_call_id: str = "") -> Dict[str, Any]:
        """Generate the standardized return message for AgentLoop injection.

        Returns: {"role": "tool", "name": "handoff_result", "tool_call_id": ..., "content": ...}
        """
        import json
        content = json.dumps({
            "agent_id": self.agent_id,
            "status": self.status,
            "output": self.output,
            "usage": self.usage,
            "request_id": self.request_id,
            "cache_hit": self.cache_hit,
        }, ensure_ascii=False)
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": "handoff_result",
            "content": content,
        }


# ──────────────────────────────────────────────
# InputFilter
# ──────────────────────────────────────────────

# InputFilterFn: async def filter(ctx: HandoffContext) -> HandoffContext
InputFilterFn = Callable[[HandoffContext], Awaitable[HandoffContext]]


def last_n_messages(n: int) -> InputFilterFn:
    """Only keep the last N messages."""
    async def _filter(ctx: HandoffContext) -> HandoffContext:
        ctx.messages = ctx.messages[-n:]
        return ctx
    return _filter


def summary_only() -> InputFilterFn:
    """Only keep memory_summary, drop messages."""
    async def _filter(ctx: HandoffContext) -> HandoffContext:
        ctx.messages = []
        return ctx
    return _filter


def allow_all() -> InputFilterFn:
    """Pass everything through."""
    async def _filter(ctx: HandoffContext) -> HandoffContext:
        return ctx
    return _filter


def platform_redact(patterns: List[str]) -> InputFilterFn:
    """Platform-level forced redaction (developer cannot bypass)."""
    import re
    async def _filter(ctx: HandoffContext) -> HandoffContext:
        for msg in ctx.messages:
            for pattern in patterns:
                if re.search(pattern, msg.content, re.IGNORECASE):
                    ctx.redaction_report.append(f"Redacted pattern '{pattern}' from {msg.role} message")
                    msg.content = re.sub(pattern, "[REDACTED]", msg.content, flags=re.IGNORECASE)
                    msg.redaction_tags.append(pattern)
        return ctx
    return _filter
