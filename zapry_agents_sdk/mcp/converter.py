"""MCP Client — Tool conversion (MCP → SDK ToolDef)."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional

from zapry_agents_sdk.mcp.config import MCPServerConfig, is_tool_allowed
from zapry_agents_sdk.mcp.protocol import MCPToolDef, MCPToolResult
from zapry_agents_sdk.tools.registry import ToolContext, ToolDef, ToolParam


def mcp_tool_name(server: str, tool: str) -> str:
    """Generate the injected SDK tool name: ``mcp.{server}.{tool}``."""
    return f"mcp.{server}.{tool}"


def mcp_result_to_text(result: MCPToolResult) -> str:
    """Normalize an MCPToolResult into a single text string."""
    parts = [c.text for c in result.content if c.type == "text" and c.text]
    text = "\n".join(parts)
    if result.is_error:
        text = f"Error: {text}"
    return text


def extract_tool_params(input_schema: Optional[Dict[str, Any]]) -> List[ToolParam]:
    """Extract top-level ToolParam from an MCP inputSchema for basic validation."""
    if not input_schema:
        return []
    props = input_schema.get("properties")
    if not isinstance(props, dict):
        return []

    required_set = set()
    req_raw = input_schema.get("required")
    if isinstance(req_raw, list):
        required_set = {r for r in req_raw if isinstance(r, str)}

    params: List[ToolParam] = []
    for name, prop_raw in props.items():
        if not isinstance(prop_raw, dict):
            continue
        params.append(
            ToolParam(
                name=name,
                type=prop_raw.get("type", "string"),
                description=prop_raw.get("description", ""),
                required=name in required_set,
            )
        )
    return params


def convert_mcp_tools(
    server_name: str,
    mcp_tools: List[MCPToolDef],
    call_fn: Callable[..., Awaitable[Any]],
    config: Optional[MCPServerConfig] = None,
) -> List[ToolDef]:
    """Convert MCP tool definitions to SDK :class:`ToolDef` instances.

    Design:
    - Wildcard filtering matches original MCP tool name.
    - ``raw_json_schema`` stores inputSchema as-is.
    - Handler closure is async.
    - ``max_tools`` truncation applied after filtering.
    """
    tools: List[ToolDef] = []

    for mt in mcp_tools:
        if config and not is_tool_allowed(mt.name, config):
            continue

        original_name = mt.name
        sdk_name = mcp_tool_name(server_name, original_name)

        async def _handler(
            ctx: ToolContext,
            _orig=original_name,
            **kwargs: Any,
        ) -> Any:
            return await call_fn(_orig, kwargs)

        tool_def = ToolDef(
            name=sdk_name,
            description=f"[MCP:{server_name}] {mt.description}",
            parameters=extract_tool_params(mt.input_schema),
            handler=_handler,
            is_async=True,
            raw_json_schema=mt.input_schema,
        )
        tools.append(tool_def)

        if config and config.max_tools > 0 and len(tools) >= config.max_tools:
            break

    return tools
