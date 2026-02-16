"""
MCP Client — Connect any MCP server, auto-discover tools, inject into ToolRegistry.

Usage::

    from zapry_agents_sdk.mcp import MCPManager, MCPServerConfig

    mcp = MCPManager()
    await mcp.add_server(MCPServerConfig(name="fs", transport="stdio",
                                          command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]))
    mcp.inject_tools(registry)
"""

from zapry_agents_sdk.mcp.config import MCPServerConfig, MCPManagerConfig
from zapry_agents_sdk.mcp.manager import MCPManager
from zapry_agents_sdk.mcp.protocol import MCPClient, MCPError, MCPToolDef, MCPToolResult
from zapry_agents_sdk.mcp.transport import (
    HTTPTransport,
    InProcessTransport,
    StdioTransport,
)

__all__ = [
    "MCPManager",
    "MCPServerConfig",
    "MCPManagerConfig",
    "MCPClient",
    "MCPError",
    "MCPToolDef",
    "MCPToolResult",
    "HTTPTransport",
    "InProcessTransport",
    "StdioTransport",
]
