"""
Tool Calling 框架 — LLM-agnostic 的工具注册、schema 管理与调用分发。

提供 ``@tool`` 装饰器自动从 type hints 生成 JSON schema，
以及 ``ToolRegistry`` 统一管理和执行工具。

Quick Start::

    from zapry_agents_sdk.tools import tool, ToolRegistry

    @tool
    async def get_weather(city: str, unit: str = "celsius") -> str:
        \"\"\"获取指定城市的当前天气。\"\"\"
        return f"{city}: 25°C"

    registry = ToolRegistry()
    registry.register(get_weather)

    # 导出给 LLM
    schema = registry.to_json_schema()

    # 执行
    result = await registry.execute("get_weather", {"city": "上海"})
"""

from zapry_agents_sdk.tools.registry import (
    ToolRegistry,
    ToolDef,
    ToolContext,
    tool,
)

__all__ = [
    "ToolRegistry",
    "ToolDef",
    "ToolContext",
    "tool",
]
