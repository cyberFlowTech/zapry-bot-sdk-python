"""MCP Client — JSON-RPC 2.0 protocol layer."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("zapry_agents_sdk.mcp.protocol")


# ──────────────────────────────────────────────
# MCP protocol types
# ──────────────────────────────────────────────


@dataclass
class MCPToolDef:
    """Tool definition returned by MCP ``tools/list``."""

    name: str = ""
    description: str = ""
    input_schema: Optional[Dict[str, Any]] = None


@dataclass
class MCPContent:
    """A single content block in an MCP tool result."""

    type: str = "text"
    text: str = ""


@dataclass
class MCPToolResult:
    """Result of MCP ``tools/call``."""

    content: List[MCPContent] = field(default_factory=list)
    is_error: bool = False


@dataclass
class MCPServerInfo:
    """MCP server identity."""

    name: str = ""
    version: str = ""


@dataclass
class MCPInitResult:
    """Response from MCP ``initialize``."""

    protocol_version: str = ""
    server_info: MCPServerInfo = field(default_factory=MCPServerInfo)


# ──────────────────────────────────────────────
# MCPError
# ──────────────────────────────────────────────


class MCPError(Exception):
    """Unified protocol-level error carrying JSON-RPC error code."""

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"mcp error {code}: {message}")


# ──────────────────────────────────────────────
# MCPClient
# ──────────────────────────────────────────────


class MCPClient:
    """Wraps a transport and provides typed MCP protocol methods."""

    def __init__(self, transport: Any) -> None:
        self._transport = transport
        self._next_id = 0

    async def _call(self, method: str, params: Any = None) -> Any:
        """Internal unified JSON-RPC call."""
        self._next_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
        }
        if params is not None:
            request["params"] = params

        payload = json.dumps(request).encode("utf-8")
        resp_bytes = await self._transport.call(payload)

        resp = json.loads(resp_bytes)

        if "error" in resp and resp["error"] is not None:
            err = resp["error"]
            raise MCPError(err.get("code", -1), err.get("message", "unknown error"))

        return resp.get("result")

    async def initialize(self) -> MCPInitResult:
        """Perform the MCP handshake."""
        params = {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "zapry-agents-sdk-python", "version": "1.0.0"},
        }
        raw = await self._call("initialize", params)
        if not raw:
            return MCPInitResult()

        si = raw.get("serverInfo", {})
        return MCPInitResult(
            protocol_version=raw.get("protocolVersion", ""),
            server_info=MCPServerInfo(
                name=si.get("name", ""),
                version=si.get("version", ""),
            ),
        )

    async def list_tools(self) -> List[MCPToolDef]:
        """Discover available tools.

        Handles both ``{tools: [...]}`` (standard) and bare ``[...]`` formats.
        """
        raw = await self._call("tools/list")
        if raw is None:
            return []

        tools_list: Optional[list] = None

        if isinstance(raw, dict):
            tools_list = raw.get("tools")
        elif isinstance(raw, list):
            tools_list = raw

        if tools_list is None:
            return []

        result: List[MCPToolDef] = []
        for t in tools_list:
            result.append(
                MCPToolDef(
                    name=t.get("name", ""),
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema"),
                )
            )
        return result

    async def call_tool(
        self, name: str, args: Optional[Dict[str, Any]] = None
    ) -> MCPToolResult:
        """Invoke a tool on the MCP server."""
        params = {"name": name, "arguments": args or {}}
        raw = await self._call("tools/call", params)
        if not raw:
            return MCPToolResult()

        content_list = []
        for c in raw.get("content", []):
            content_list.append(
                MCPContent(type=c.get("type", "text"), text=c.get("text", ""))
            )
        return MCPToolResult(
            content=content_list,
            is_error=raw.get("isError", False),
        )

    async def close(self) -> None:
        """Close the underlying transport."""
        await self._transport.close()
