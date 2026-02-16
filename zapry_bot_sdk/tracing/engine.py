"""
Tracing Engine — 结构化 Span 追踪系统。

Span 类型:
- agent: 一次完整的 Agent 运行
- llm: 一次 LLM API 调用
- tool: 一次工具执行
- guardrail: 一次护栏检查
- custom: 自定义 Span

每个 Span 记录: 名称、类型、开始/结束时间、输入/输出、元数据、子 Span。
所有 Span 通过 trace_id 关联，形成树状结构。
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger("zapry_bot_sdk.tracing")


# ──────────────────────────────────────────────
# Span types
# ──────────────────────────────────────────────


class SpanKind(str, Enum):
    AGENT = "agent"
    LLM = "llm"
    TOOL = "tool"
    GUARDRAIL = "guardrail"
    CUSTOM = "custom"


# ──────────────────────────────────────────────
# Span
# ──────────────────────────────────────────────


@dataclass
class Span:
    """A single unit of work in a trace.

    Attributes:
        span_id: Unique ID for this span.
        trace_id: Shared across all spans in one trace.
        parent_id: Parent span ID (empty for root).
        name: Human-readable name.
        kind: Span type (agent/llm/tool/guardrail/custom).
        start_time: Unix timestamp (seconds).
        end_time: Unix timestamp (seconds), 0 if not ended.
        attributes: Key-value metadata.
        children: Child spans.
        status: "ok", "error", or "running".
        error: Error message if status is "error".
    """

    span_id: str = ""
    trace_id: str = ""
    parent_id: str = ""
    name: str = ""
    kind: SpanKind = SpanKind.CUSTOM
    start_time: float = 0.0
    end_time: float = 0.0
    attributes: Dict[str, Any] = field(default_factory=dict)
    children: List["Span"] = field(default_factory=list)
    status: str = "running"
    error: str = ""

    def __post_init__(self) -> None:
        if not self.span_id:
            self.span_id = _short_id()
        if not self.start_time:
            self.start_time = time.time()

    @property
    def duration_ms(self) -> float:
        """Duration in milliseconds."""
        if self.end_time > 0:
            return (self.end_time - self.start_time) * 1000
        return (time.time() - self.start_time) * 1000

    def end(self, status: str = "ok", error: str = "") -> None:
        """Mark the span as finished."""
        self.end_time = time.time()
        self.status = status
        if error:
            self.error = error

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def to_dict(self) -> Dict[str, Any]:
        """Export as a serializable dict."""
        d: Dict[str, Any] = {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "kind": self.kind.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": round(self.duration_ms, 2),
            "status": self.status,
            "attributes": self.attributes,
        }
        if self.error:
            d["error"] = self.error
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d


# ──────────────────────────────────────────────
# Exporters
# ──────────────────────────────────────────────


@runtime_checkable
class SpanExporter(Protocol):
    """Interface for exporting finished spans."""

    def export(self, span: Span) -> None: ...


class NullExporter:
    """Discards all spans (tracing disabled)."""

    def export(self, span: Span) -> None:
        pass


class ConsoleExporter:
    """Prints spans to the console logger."""

    def export(self, span: Span) -> None:
        logger.info(
            "[Trace] %s %s | %s | %.1fms | %s",
            span.kind.value.upper(),
            span.name,
            span.status,
            span.duration_ms,
            {k: v for k, v in span.attributes.items() if k != "messages"},
        )


class CallbackExporter:
    """Calls a user-provided function for each span.

    Usage::

        traces = []
        exporter = CallbackExporter(lambda span: traces.append(span.to_dict()))
    """

    def __init__(self, callback: Callable[[Span], None]) -> None:
        self._callback = callback

    def export(self, span: Span) -> None:
        self._callback(span)


# ──────────────────────────────────────────────
# Tracer
# ──────────────────────────────────────────────


class Tracer:
    """Creates and manages spans for structured tracing.

    Parameters:
        exporter: Where to send finished spans (default: NullExporter).
        enabled: Set False to disable tracing entirely.

    Usage::

        tracer = Tracer(exporter=ConsoleExporter())

        with tracer.agent_span("my_agent") as span:
            with tracer.llm_span("gpt-4o", tokens=100):
                ...
            with tracer.tool_span("weather", args={"city": "SH"}):
                ...
    """

    def __init__(
        self,
        exporter: Optional[SpanExporter] = None,
        enabled: bool = True,
    ) -> None:
        self._exporter = exporter or NullExporter()
        self._enabled = enabled
        self._current_trace_id: str = ""
        self._span_stack: List[Span] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def new_trace(self) -> str:
        """Start a new trace and return its ID."""
        self._current_trace_id = _uuid()
        self._span_stack.clear()
        return self._current_trace_id

    @contextmanager
    def span(self, name: str, kind: SpanKind = SpanKind.CUSTOM, **attributes):
        """Create a span (context manager).

        Automatically sets parent/child relationships and exports on exit.
        """
        if not self._enabled:
            yield Span(name=name, kind=kind)
            return

        if not self._current_trace_id:
            self.new_trace()

        parent_id = self._span_stack[-1].span_id if self._span_stack else ""

        s = Span(
            trace_id=self._current_trace_id,
            parent_id=parent_id,
            name=name,
            kind=kind,
            attributes=dict(attributes),
        )

        if self._span_stack:
            self._span_stack[-1].children.append(s)

        self._span_stack.append(s)
        try:
            yield s
        except Exception as e:
            s.end(status="error", error=str(e))
            self._span_stack.pop()
            self._export(s)
            raise
        else:
            s.end(status="ok")
        finally:
            if s in self._span_stack:
                self._span_stack.pop()
            self._export(s)

    @contextmanager
    def agent_span(self, name: str, **attributes):
        """Create an agent-level span."""
        with self.span(name, SpanKind.AGENT, **attributes) as s:
            yield s

    @contextmanager
    def llm_span(self, model: str = "", **attributes):
        """Create an LLM call span."""
        attrs = dict(attributes)
        if model:
            attrs["model"] = model
        with self.span(f"llm:{model}" if model else "llm", SpanKind.LLM, **attrs) as s:
            yield s

    @contextmanager
    def tool_span(self, tool_name: str, **attributes):
        """Create a tool execution span."""
        with self.span(f"tool:{tool_name}", SpanKind.TOOL, **attributes) as s:
            yield s

    @contextmanager
    def guardrail_span(self, guardrail_name: str, **attributes):
        """Create a guardrail check span."""
        with self.span(f"guardrail:{guardrail_name}", SpanKind.GUARDRAIL, **attributes) as s:
            yield s

    def _export(self, span: Span) -> None:
        """Export a span if it's a root span (no parent in stack)."""
        if not self._enabled:
            return
        # Only export root spans (they contain the full tree)
        if not span.parent_id:
            self._exporter.export(span)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _short_id() -> str:
    return uuid.uuid4().hex[:12]

def _uuid() -> str:
    return uuid.uuid4().hex
