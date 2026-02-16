"""MCP Client — MCPManager: manages MCP server connections and tool injection."""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Dict, List, Optional

from zapry_agents_sdk.mcp.config import MCPManagerConfig, MCPServerConfig
from zapry_agents_sdk.mcp.converter import convert_mcp_tools, mcp_result_to_text, mcp_tool_name
from zapry_agents_sdk.mcp.protocol import MCPClient, MCPToolDef, MCPToolResult
from zapry_agents_sdk.mcp.transport import (
    HTTPTransport,
    MCPTransportError,
    StdioTransport,
)
from zapry_agents_sdk.tools.registry import ToolDef, ToolRegistry

logger = logging.getLogger("zapry_agents_sdk.mcp.manager")


class _ServerConn:
    """Internal: tracks a single MCP server connection."""

    def __init__(
        self,
        config: MCPServerConfig,
        client: MCPClient,
        mcp_tools: List[MCPToolDef],
        sdk_tools: List[ToolDef],
    ) -> None:
        self.config = config
        self.client = client
        self.mcp_tools = mcp_tools
        self.sdk_tools = sdk_tools


class MCPManager:
    """Manages multiple MCP server connections and injects their tools
    into a :class:`ToolRegistry` for seamless use with :class:`AgentLoop`.

    Usage::

        mcp = MCPManager()
        await mcp.add_server(MCPServerConfig(name="fs", transport="http", url="..."))
        mcp.inject_tools(registry)
        # ... AgentLoop uses MCP tools transparently ...
        await mcp.disconnect_all()
    """

    def __init__(self, config: Optional[MCPManagerConfig] = None) -> None:
        self._config = config or MCPManagerConfig()
        self._servers: Dict[str, _ServerConn] = {}
        self._tool_map: Dict[str, str] = {}  # sdk_name -> server_name
        self._injected_tools: List[str] = []

    # ── Server management ──

    async def add_server(self, config: MCPServerConfig) -> None:
        """Connect to an MCP server: create transport → start → initialize → list_tools → convert."""
        if config.timeout <= 0:
            config.timeout = 30
        if config.max_retries <= 0:
            config.max_retries = 3

        if config.transport == "http":
            transport = HTTPTransport(config.url, config.headers, config.timeout)
        elif config.transport == "stdio":
            transport = StdioTransport(config.command, config.args, config.env, config.timeout)
        else:
            raise ValueError(f"mcp: unsupported transport: {config.transport!r}")

        await self.add_server_with_transport(config, transport)

    async def add_server_with_transport(self, config: MCPServerConfig, transport: Any) -> None:
        """Connect using a custom transport (useful for testing with InProcessTransport)."""
        if config.timeout <= 0:
            config.timeout = 30
        if config.max_retries <= 0:
            config.max_retries = 3

        await transport.start()
        client = MCPClient(transport)

        try:
            await client.initialize()
        except Exception:
            await transport.close()
            raise

        try:
            mcp_tools = await client.list_tools()
        except Exception:
            await transport.close()
            raise

        async def call_fn(tool_name: str, args: Dict[str, Any]) -> Any:
            return await self._call_tool_direct(config.name, tool_name, args, config.max_retries)

        sdk_tools = convert_mcp_tools(config.name, mcp_tools, call_fn, config)

        conn = _ServerConn(config, client, mcp_tools, sdk_tools)
        self._servers[config.name] = conn

        for t in sdk_tools:
            self._tool_map[t.name] = config.name

        logger.info("Added server %r with %d tools", config.name, len(sdk_tools))

    async def remove_server(self, name: str) -> None:
        """Disconnect and remove a server and its tools."""
        conn = self._servers.get(name)
        if conn is None:
            raise KeyError(f"mcp: server {name!r} not found")

        for t in conn.sdk_tools:
            self._tool_map.pop(t.name, None)

        await conn.client.close()
        del self._servers[name]

    # ── Tool injection ──

    def inject_tools(self, registry: ToolRegistry) -> None:
        """Register all MCP tools into the registry (idempotent: removes old tools first)."""
        for name in self._injected_tools:
            registry.remove(name)
        self._injected_tools.clear()

        for conn in self._servers.values():
            for tool_def in conn.sdk_tools:
                registry.register(tool_def)
                self._injected_tools.append(tool_def.name)

    def remove_tools(self, registry: ToolRegistry) -> None:
        """Precisely remove only MCP-injected tools from the registry."""
        for name in self._injected_tools:
            registry.remove(name)
        self._injected_tools.clear()

    # ── Tool invocation ──

    async def call_tool(self, sdk_tool_name: str, args: Optional[Dict[str, Any]] = None) -> Any:
        """Route a call by SDK tool name to the correct server."""
        server_name = self._tool_map.get(sdk_tool_name)
        if server_name is None:
            raise KeyError(f"mcp: tool {sdk_tool_name!r} not found")

        conn = self._servers.get(server_name)
        if conn is None:
            raise KeyError(f"mcp: server {server_name!r} not found")

        prefix = f"mcp.{server_name}."
        original_name = sdk_tool_name
        if sdk_tool_name.startswith(prefix):
            original_name = sdk_tool_name[len(prefix):]

        return await self._call_tool_direct(server_name, original_name, args or {}, conn.config.max_retries)

    async def _call_tool_direct(
        self, server_name: str, tool_name: str, args: Dict[str, Any], max_retries: int
    ) -> Any:
        """Call a server's tool with retry logic for retryable errors."""
        conn = self._servers.get(server_name)
        if conn is None:
            raise KeyError(f"mcp: server {server_name!r} not found")

        last_err: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            if attempt > 0:
                backoff = (2 ** (attempt - 1)) * 0.1
                await asyncio.sleep(backoff)

            try:
                result = await conn.client.call_tool(tool_name, args)
                return mcp_result_to_text(result)
            except MCPTransportError as e:
                last_err = e
                if e.is_retryable:
                    continue
                raise
            except Exception as e:
                raise

        raise RuntimeError(
            f"mcp: call {server_name}.{tool_name} failed after {max_retries} retries: {last_err}"
        )

    # ── Refresh ──

    async def refresh_tools(self, *servers: str) -> None:
        """Re-discover tools for specified (or all) servers."""
        targets = list(servers) if servers else list(self._servers.keys())

        for name in targets:
            conn = self._servers.get(name)
            if conn is None:
                continue

            for t in conn.sdk_tools:
                self._tool_map.pop(t.name, None)

            mcp_tools = await conn.client.list_tools()

            async def call_fn(tool_name: str, args: Dict[str, Any]) -> Any:
                return await self._call_tool_direct(name, tool_name, args, conn.config.max_retries)

            sdk_tools = convert_mcp_tools(name, mcp_tools, call_fn, conn.config)

            conn.mcp_tools = mcp_tools
            conn.sdk_tools = sdk_tools

            for t in sdk_tools:
                self._tool_map[t.name] = name

    # ── Lifecycle ──

    async def disconnect_all(self) -> None:
        """Close all server connections and clear internal state."""
        errors = []
        for name, conn in self._servers.items():
            try:
                await conn.client.close()
            except Exception as e:
                errors.append(f"{name}: {e}")

        self._servers.clear()
        self._tool_map.clear()
        self._injected_tools.clear()

        if errors:
            raise RuntimeError(f"mcp: disconnect errors: {'; '.join(errors)}")

    # ── Query ──

    def list_tools(self, *servers: str) -> List[ToolDef]:
        """Return all (or server-specific) converted SDK tools."""
        result: List[ToolDef] = []
        if not servers:
            for conn in self._servers.values():
                result.extend(conn.sdk_tools)
        else:
            for name in servers:
                conn = self._servers.get(name)
                if conn:
                    result.extend(conn.sdk_tools)
        return result

    def server_names(self) -> List[str]:
        """Return the names of all connected servers."""
        return list(self._servers.keys())
