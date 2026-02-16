"""
OpenAIToolAdapter — 将 ToolRegistry 对接 OpenAI function calling API。

轻量适配层：自动将注册的 tools 转为 OpenAI ``tools`` 参数格式，
并将 OpenAI 返回的 ``tool_calls`` 分发到对应的 tool handler。

Usage::

    from zapry_agents_sdk.tools import ToolRegistry, tool
    from zapry_agents_sdk.tools.openai_adapter import OpenAIToolAdapter

    registry = ToolRegistry()

    @tool
    async def get_weather(city: str) -> str:
        return f"{city}: 25°C"

    registry.register(get_weather)
    adapter = OpenAIToolAdapter(registry)

    # 1. 获取 OpenAI tools 参数
    tools_param = adapter.to_openai_tools()

    # 2. 调用 OpenAI
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        tools=tools_param,
    )

    # 3. 处理 tool_calls
    if response.choices[0].message.tool_calls:
        results = await adapter.handle_tool_calls(
            response.choices[0].message.tool_calls
        )
        # results: [{"tool_call_id": "...", "role": "tool", "content": "..."}]
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from zapry_agents_sdk.tools.registry import ToolContext, ToolRegistry

logger = logging.getLogger("zapry_agents_sdk.tools")


@dataclass
class ToolCallResult:
    """Result of a single tool call execution.

    Attributes:
        tool_call_id: The ID from the OpenAI tool_call.
        name: Tool name.
        content: Serialized result (string).
        error: Error message if execution failed.
    """

    tool_call_id: str
    name: str
    content: str = ""
    error: Optional[str] = None

    def to_message(self) -> Dict[str, str]:
        """Convert to an OpenAI-compatible tool result message.

        Returns::

            {"role": "tool", "tool_call_id": "...", "content": "..."}
        """
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": self.error if self.error else self.content,
        }


class OpenAIToolAdapter:
    """Adapter between ToolRegistry and OpenAI function calling API.

    Parameters:
        registry: The tool registry to use.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def to_openai_tools(self) -> List[Dict[str, Any]]:
        """Export tools in OpenAI ``tools`` parameter format.

        Returns a list of dicts ready for
        ``openai.chat.completions.create(tools=...)``::

            [{"type": "function", "function": {"name": ..., ...}}, ...]
        """
        return self._registry.to_openai_schema()

    async def handle_tool_calls(
        self,
        tool_calls: Any,
        extra: Optional[Dict[str, Any]] = None,
    ) -> List[ToolCallResult]:
        """Execute tool calls returned by OpenAI and collect results.

        Parameters:
            tool_calls: The ``message.tool_calls`` list from an OpenAI
                response.  Each item should have ``.id``, ``.function.name``,
                and ``.function.arguments`` attributes (or dict equivalents).
            extra: Optional extra data to pass into ToolContext.

        Returns:
            List of ToolCallResult, one per tool call.
        """
        results: List[ToolCallResult] = []

        for tc in tool_calls:
            # Support both object attributes and dict access
            call_id = _get(tc, "id", "")
            func = _get(tc, "function", tc)
            func_name = _get(func, "name", "")
            func_args_raw = _get(func, "arguments", "{}")

            # Parse arguments
            try:
                if isinstance(func_args_raw, str):
                    func_args = json.loads(func_args_raw)
                else:
                    func_args = dict(func_args_raw)
            except (json.JSONDecodeError, TypeError):
                func_args = {}

            ctx = ToolContext(
                tool_name=func_name,
                call_id=call_id,
                extra=dict(extra or {}),
            )

            try:
                result = await self._registry.execute(func_name, func_args, ctx=ctx)
                content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
                results.append(
                    ToolCallResult(
                        tool_call_id=call_id,
                        name=func_name,
                        content=content,
                    )
                )
            except Exception as e:
                logger.error("Tool call failed: %s(%s) -> %s", func_name, func_args, e)
                results.append(
                    ToolCallResult(
                        tool_call_id=call_id,
                        name=func_name,
                        error=str(e),
                    )
                )

        return results

    def results_to_messages(self, results: List[ToolCallResult]) -> List[Dict[str, str]]:
        """Convert a list of ToolCallResult to OpenAI tool messages.

        Useful for appending to the messages list before the next API call.
        """
        return [r.to_message() for r in results]


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Get attribute or dict key, with fallback."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
