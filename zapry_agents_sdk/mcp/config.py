"""MCP Client — Configuration types."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class MCPServerConfig:
    """Connection configuration for a single MCP server.

    Attributes:
        name: Unique server identifier (e.g. ``"filesystem"``).
        transport: ``"stdio"`` or ``"http"``.
        command: Executable path (stdio).
        args: Command arguments (stdio).
        env: Extra environment variables (stdio).
        url: HTTP endpoint (http).
        headers: Custom HTTP headers (http).
        timeout: Timeout in seconds (default 30).
        max_retries: Retries for retryable errors (default 3).
        allowed_tools: Whitelist filter on **original MCP tool names** (wildcards via ``fnmatch``).
        blocked_tools: Blacklist filter (wildcards via ``fnmatch``).
        max_tools: Maximum tools to inject (0 = no limit).
    """

    name: str = ""
    transport: str = "stdio"

    # Stdio
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)

    # HTTP
    url: str = ""
    headers: Dict[str, str] = field(default_factory=dict)

    # General
    timeout: int = 30
    max_retries: int = 3

    # Tool filtering (matches original MCP tool name, NOT injected sdk name)
    allowed_tools: List[str] = field(default_factory=list)
    blocked_tools: List[str] = field(default_factory=list)
    max_tools: int = 0


@dataclass
class MCPManagerConfig:
    """Manager-level configuration."""

    tool_prefix: str = "mcp.{server}.{tool}"
    trace_args: bool = False


def match_tool_filter(pattern: str, tool_name: str) -> bool:
    """Check if *tool_name* matches a wildcard *pattern* (via ``fnmatch``)."""
    return fnmatch.fnmatch(tool_name, pattern)


def is_tool_allowed(name: str, config: MCPServerConfig) -> bool:
    """Check whether an original MCP tool name passes the filter.

    Blocked takes precedence over allowed.
    """
    for p in config.blocked_tools:
        if match_tool_filter(p, name):
            return False
    if not config.allowed_tools:
        return True
    for p in config.allowed_tools:
        if match_tool_filter(p, name):
            return True
    return False
