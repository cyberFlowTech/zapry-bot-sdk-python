"""
Tracing — 结构化追踪框架。

基于 Span 的层级追踪，记录 Agent 运行链路：
agent_span → llm_span → tool_span → guardrail_span

可导出到任何 OpenTelemetry 兼容后端（Jaeger, Grafana, Datadog）。

Quick Start::

    from zapry_agents_sdk.tracing import Tracer, SpanExporter

    tracer = Tracer(exporter=ConsoleExporter())

    with tracer.agent_span("my_agent") as span:
        with tracer.llm_span("gpt-4o", tokens=150):
            pass
        with tracer.tool_span("get_weather", args={"city": "SH"}):
            pass
"""

from zapry_agents_sdk.tracing.engine import (
    Tracer,
    Span,
    SpanKind,
    SpanExporter,
    ConsoleExporter,
    CallbackExporter,
    NullExporter,
)

__all__ = [
    "Tracer",
    "Span",
    "SpanKind",
    "SpanExporter",
    "ConsoleExporter",
    "CallbackExporter",
    "NullExporter",
]
