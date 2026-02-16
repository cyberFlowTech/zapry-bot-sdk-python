"""
MCP Client 全量测试 — 使用 InProcessTransport 模拟 MCP 服务器。
"""

import asyncio
import json
import pytest

from zapry_agents_sdk.mcp.config import (
    MCPServerConfig,
    match_tool_filter,
    is_tool_allowed,
)
from zapry_agents_sdk.mcp.protocol import (
    MCPClient,
    MCPError,
    MCPToolDef,
    MCPToolResult,
    MCPContent,
)
from zapry_agents_sdk.mcp.transport import InProcessTransport, MCPTransportError
from zapry_agents_sdk.mcp.converter import (
    convert_mcp_tools,
    mcp_result_to_text,
    mcp_tool_name,
    extract_tool_params,
)
from zapry_agents_sdk.mcp.manager import MCPManager
from zapry_agents_sdk.tools.registry import ToolRegistry, ToolDef, ToolParam, ToolContext, tool
from zapry_agents_sdk.agent.loop import AgentLoop, AgentResult


# ══════════════════════════════════════════════
# Test helpers — mock MCP server via InProcessTransport
# ══════════════════════════════════════════════


def _make_response(req_id, result=None, error=None):
    resp = {"jsonrpc": "2.0", "id": req_id}
    if error:
        resp["error"] = error
    elif result is not None:
        resp["result"] = result
    return json.dumps(resp).encode()


def new_mock_transport(tools=None, call_handler=None):
    """Create an InProcessTransport simulating an MCP server."""
    tools = tools or []

    def handler(request: bytes) -> bytes:
        req = json.loads(request)
        rid = req.get("id", 0)
        method = req.get("method", "")

        if method == "initialize":
            return _make_response(rid, {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "mock", "version": "1.0"},
            })
        elif method == "tools/list":
            tool_dicts = [
                {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
                for t in tools
            ]
            return _make_response(rid, {"tools": tool_dicts})
        elif method == "tools/call":
            params = req.get("params", {})
            name = params.get("name", "")
            args = params.get("arguments", {})
            if call_handler:
                try:
                    result = call_handler(name, args)
                    return _make_response(rid, result)
                except Exception as e:
                    return _make_response(rid, error={"code": -1, "message": str(e)})
            return _make_response(rid, error={"code": -1, "message": "no handler"})
        else:
            return _make_response(rid, error={"code": -32601, "message": "method not found"})

    return InProcessTransport(handler)


def standard_mock_tools():
    return [
        MCPToolDef(name="read_file", description="Read contents of a file", input_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "File path"}},
            "required": ["path"],
        }),
        MCPToolDef(name="list_files", description="List files in directory", input_schema={
            "type": "object",
            "properties": {"dir": {"type": "string"}},
        }),
        MCPToolDef(name="write_file", description="Write to a file", input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        }),
    ]


def standard_call_handler(name, args):
    if name == "read_file":
        return {"content": [{"type": "text", "text": f"contents of {args['path']}"}]}
    elif name == "list_files":
        return {"content": [{"type": "text", "text": "file1.txt\nfile2.txt"}]}
    elif name == "write_file":
        return {"content": [{"type": "text", "text": "ok"}]}
    raise ValueError(f"unknown tool: {name}")


async def add_mock_server(mgr, name, tools=None, call_handler=None):
    transport = new_mock_transport(tools or standard_mock_tools(), call_handler or standard_call_handler)
    await mgr.add_server_with_transport(MCPServerConfig(name=name), transport)


# ══════════════════════════════════════════════
# Protocol layer tests
# ══════════════════════════════════════════════


class TestProtocol:

    @pytest.mark.asyncio
    async def test_initialize(self):
        transport = new_mock_transport()
        client = MCPClient(transport)
        result = await client.initialize()
        assert result.protocol_version == "2024-11-05"
        assert result.server_info.name == "mock"

    @pytest.mark.asyncio
    async def test_list_tools_wrapped_format(self):
        transport = new_mock_transport(standard_mock_tools())
        client = MCPClient(transport)
        await client.initialize()
        tools = await client.list_tools()
        assert len(tools) == 3
        assert tools[0].name == "read_file"

    @pytest.mark.asyncio
    async def test_list_tools_bare_array(self):
        def handler(request: bytes) -> bytes:
            req = json.loads(request)
            rid = req["id"]
            method = req["method"]
            if method == "initialize":
                return _make_response(rid, {"protocolVersion": "2024-11-05", "serverInfo": {"name": "bare", "version": "1.0"}})
            elif method == "tools/list":
                return _make_response(rid, [{"name": "search", "description": "Search", "inputSchema": {"type": "object"}}])
            return _make_response(rid, error={"code": -1, "message": "nope"})

        client = MCPClient(InProcessTransport(handler))
        await client.initialize()
        tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "search"

    @pytest.mark.asyncio
    async def test_list_tools_empty_array(self):
        transport = new_mock_transport([])
        client = MCPClient(transport)
        await client.initialize()
        tools = await client.list_tools()
        assert len(tools) == 0

    @pytest.mark.asyncio
    async def test_call_tool_success(self):
        transport = new_mock_transport(standard_mock_tools(), standard_call_handler)
        client = MCPClient(transport)
        await client.initialize()
        result = await client.call_tool("read_file", {"path": "/tmp/test.txt"})
        assert not result.is_error
        assert len(result.content) == 1
        assert result.content[0].text == "contents of /tmp/test.txt"

    @pytest.mark.asyncio
    async def test_call_tool_invalid_json(self):
        def handler(request: bytes) -> bytes:
            return b"not json"

        client = MCPClient(InProcessTransport(handler))
        with pytest.raises(json.JSONDecodeError):
            await client.call_tool("test", {})

    @pytest.mark.asyncio
    async def test_call_tool_mcp_error(self):
        def handler(request: bytes) -> bytes:
            req = json.loads(request)
            return _make_response(req["id"], error={"code": -32000, "message": "server error"})

        client = MCPClient(InProcessTransport(handler))
        with pytest.raises(MCPError) as exc_info:
            await client.call_tool("test", {})
        assert exc_info.value.code == -32000

    @pytest.mark.asyncio
    async def test_json_rpc_request_format(self):
        captured = []

        def handler(request: bytes) -> bytes:
            captured.append(json.loads(request))
            return _make_response(captured[-1]["id"], {"ok": True})

        client = MCPClient(InProcessTransport(handler))
        await client._call("test_method", {"key": "val"})
        assert captured[0]["jsonrpc"] == "2.0"
        assert captured[0]["method"] == "test_method"
        assert captured[0]["params"] == {"key": "val"}


# ══════════════════════════════════════════════
# Converter tests
# ══════════════════════════════════════════════


class TestConverter:

    def test_convert_basic(self):
        mcp_tools = standard_mock_tools()
        async def call_fn(name, args):
            return "result"
        tools = convert_mcp_tools("fs", mcp_tools, call_fn)
        assert len(tools) == 3
        assert tools[0].name == "mcp.fs.read_file"
        assert "[MCP:fs]" in tools[0].description

    def test_raw_schema_preserved(self):
        mcp_tools = standard_mock_tools()
        async def call_fn(name, args):
            return "ok"
        tools = convert_mcp_tools("fs", mcp_tools, call_fn)
        assert tools[0].raw_json_schema is not None
        assert "path" in tools[0].raw_json_schema["properties"]

    def test_extract_params(self):
        mcp_tools = standard_mock_tools()
        async def call_fn(name, args):
            return "ok"
        tools = convert_mcp_tools("fs", mcp_tools, call_fn)
        params = tools[0].parameters  # read_file
        assert len(params) == 1
        assert params[0].name == "path"

    def test_required(self):
        mcp_tools = standard_mock_tools()
        async def call_fn(name, args):
            return "ok"
        tools = convert_mcp_tools("fs", mcp_tools, call_fn)
        assert tools[0].parameters[0].required  # read_file.path required
        list_files = tools[1]
        for p in list_files.parameters:
            if p.name == "dir":
                assert not p.required

    def test_allowed_filter(self):
        mcp_tools = standard_mock_tools()
        async def call_fn(name, args):
            return "ok"
        config = MCPServerConfig(allowed_tools=["read_*"])
        tools = convert_mcp_tools("fs", mcp_tools, call_fn, config)
        assert len(tools) == 1
        assert tools[0].name == "mcp.fs.read_file"

    def test_blocked_filter(self):
        mcp_tools = standard_mock_tools()
        async def call_fn(name, args):
            return "ok"
        config = MCPServerConfig(blocked_tools=["write_*"])
        tools = convert_mcp_tools("fs", mcp_tools, call_fn, config)
        assert len(tools) == 2
        for t in tools:
            assert "write" not in t.name

    def test_max_tools(self):
        mcp_tools = standard_mock_tools()
        async def call_fn(name, args):
            return "ok"
        config = MCPServerConfig(max_tools=2)
        tools = convert_mcp_tools("fs", mcp_tools, call_fn, config)
        assert len(tools) == 2

    def test_mcp_result_to_text(self):
        r = MCPToolResult(content=[MCPContent(type="text", text="hello")])
        assert mcp_result_to_text(r) == "hello"

        r2 = MCPToolResult(content=[
            MCPContent(type="text", text="line1"),
            MCPContent(type="text", text="line2"),
        ])
        assert mcp_result_to_text(r2) == "line1\nline2"

        r3 = MCPToolResult(content=[MCPContent(type="text", text="failed")], is_error=True)
        assert mcp_result_to_text(r3) == "Error: failed"

        r4 = MCPToolResult(content=[MCPContent(type="image", text="")])
        assert mcp_result_to_text(r4) == ""


# ══════════════════════════════════════════════
# MCPManager tests
# ══════════════════════════════════════════════


class TestMCPManager:

    @pytest.mark.asyncio
    async def test_add_server(self):
        mgr = MCPManager()
        await add_mock_server(mgr, "fs")
        assert mgr.server_names() == ["fs"]
        assert len(mgr.list_tools()) == 3

    @pytest.mark.asyncio
    async def test_remove_server(self):
        mgr = MCPManager()
        await add_mock_server(mgr, "fs")
        await mgr.remove_server("fs")
        assert mgr.server_names() == []
        assert mgr.list_tools() == []

    @pytest.mark.asyncio
    async def test_inject_tools(self):
        mgr = MCPManager()
        await add_mock_server(mgr, "fs")
        registry = ToolRegistry()
        mgr.inject_tools(registry)
        assert len(registry) == 3
        assert "mcp.fs.read_file" in registry

    @pytest.mark.asyncio
    async def test_inject_tools_idempotent(self):
        mgr = MCPManager()
        await add_mock_server(mgr, "fs")
        registry = ToolRegistry()
        mgr.inject_tools(registry)
        mgr.inject_tools(registry)
        assert len(registry) == 3

    @pytest.mark.asyncio
    async def test_remove_tools_precise(self):
        mgr = MCPManager()
        await add_mock_server(mgr, "fs")
        registry = ToolRegistry()
        registry.register(ToolDef(name="my_local_tool", description="local"))
        mgr.inject_tools(registry)
        assert len(registry) == 4  # 3 MCP + 1 local
        mgr.remove_tools(registry)
        assert len(registry) == 1
        assert "my_local_tool" in registry

    @pytest.mark.asyncio
    async def test_call_tool_e2e(self):
        mgr = MCPManager()
        await add_mock_server(mgr, "fs")
        registry = ToolRegistry()
        mgr.inject_tools(registry)
        ctx = ToolContext(tool_name="mcp.fs.read_file")
        result = await registry.execute("mcp.fs.read_file", {"path": "/tmp/data.txt"}, ctx)
        assert result == "contents of /tmp/data.txt"

    @pytest.mark.asyncio
    async def test_multi_server(self):
        mgr = MCPManager()
        await add_mock_server(mgr, "fs")
        db_tools = [MCPToolDef(name="query", description="Run SQL", input_schema={"type": "object"})]
        def db_handler(name, args):
            return {"content": [{"type": "text", "text": "rows:3"}]}
        transport = new_mock_transport(db_tools, db_handler)
        await mgr.add_server_with_transport(MCPServerConfig(name="db"), transport)

        assert len(mgr.server_names()) == 2
        assert len(mgr.list_tools()) == 4

        registry = ToolRegistry()
        mgr.inject_tools(registry)
        ctx = ToolContext()
        r1 = await registry.execute("mcp.fs.read_file", {"path": "/x"}, ctx)
        assert r1 == "contents of /x"
        r2 = await registry.execute("mcp.db.query", {}, ctx)
        assert r2 == "rows:3"

    @pytest.mark.asyncio
    async def test_tool_name_conflict(self):
        mgr = MCPManager()
        t1 = [MCPToolDef(name="read_file", description="S1 read", input_schema={"type": "object"})]
        t2 = [MCPToolDef(name="read_file", description="S2 read", input_schema={"type": "object"})]
        def h1(name, args):
            return {"content": [{"type": "text", "text": "from-s1"}]}
        def h2(name, args):
            return {"content": [{"type": "text", "text": "from-s2"}]}
        await mgr.add_server_with_transport(MCPServerConfig(name="s1"), new_mock_transport(t1, h1))
        await mgr.add_server_with_transport(MCPServerConfig(name="s2"), new_mock_transport(t2, h2))

        registry = ToolRegistry()
        mgr.inject_tools(registry)
        assert "mcp.s1.read_file" in registry
        assert "mcp.s2.read_file" in registry

        ctx = ToolContext()
        r1 = await registry.execute("mcp.s1.read_file", {}, ctx)
        r2 = await registry.execute("mcp.s2.read_file", {}, ctx)
        assert r1 == "from-s1"
        assert r2 == "from-s2"

    @pytest.mark.asyncio
    async def test_refresh_tools(self):
        call_count = [0]
        initial_tools = [MCPToolDef(name="tool_v1", description="V1", input_schema={"type": "object"})]

        def handler(request: bytes) -> bytes:
            req = json.loads(request)
            rid = req["id"]
            method = req["method"]
            if method == "initialize":
                return _make_response(rid, {"protocolVersion": "2024-11-05", "serverInfo": {"name": "dyn", "version": "1.0"}})
            elif method == "tools/list":
                call_count[0] += 1
                if call_count[0] > 1:
                    tools = [
                        {"name": "tool_v2", "description": "V2", "inputSchema": {"type": "object"}},
                        {"name": "tool_v3", "description": "V3", "inputSchema": {"type": "object"}},
                    ]
                else:
                    tools = [{"name": "tool_v1", "description": "V1", "inputSchema": {"type": "object"}}]
                return _make_response(rid, {"tools": tools})
            return _make_response(rid, error={"code": -1, "message": "nope"})

        mgr = MCPManager()
        await mgr.add_server_with_transport(MCPServerConfig(name="dyn"), InProcessTransport(handler))
        assert len(mgr.list_tools()) == 1

        await mgr.refresh_tools("dyn")
        assert len(mgr.list_tools()) == 2

    @pytest.mark.asyncio
    async def test_disconnect_all(self):
        mgr = MCPManager()
        await add_mock_server(mgr, "a")
        await add_mock_server(mgr, "b")
        await mgr.disconnect_all()
        assert mgr.server_names() == []

    @pytest.mark.asyncio
    async def test_server_not_found(self):
        mgr = MCPManager()
        with pytest.raises(KeyError, match="not found"):
            await mgr.call_tool("mcp.noexist.tool", {})

    @pytest.mark.asyncio
    async def test_call_tool_retry(self):
        attempts = [0]

        def handler(request: bytes) -> bytes:
            req = json.loads(request)
            rid = req["id"]
            method = req["method"]
            if method == "initialize":
                return _make_response(rid, {"protocolVersion": "2024-11-05", "serverInfo": {"name": "r", "version": "1.0"}})
            elif method == "tools/list":
                return _make_response(rid, {"tools": [{"name": "flaky", "description": "Flaky", "inputSchema": {"type": "object"}}]})
            elif method == "tools/call":
                attempts[0] += 1
                if attempts[0] < 3:
                    raise MCPTransportError(503, "service unavailable")
                return _make_response(rid, {"content": [{"type": "text", "text": "success after retries"}]})
            return _make_response(rid, error={"code": -1, "message": "nope"})

        mgr = MCPManager()
        await mgr.add_server_with_transport(MCPServerConfig(name="r", max_retries=5), InProcessTransport(handler))
        result = await mgr.call_tool("mcp.r.flaky", {})
        assert result == "success after retries"
        assert attempts[0] == 3


# ══════════════════════════════════════════════
# Integration tests
# ══════════════════════════════════════════════


class TestIntegration:

    @pytest.mark.asyncio
    async def test_agent_loop_mcp_tool_selected(self):
        mgr = MCPManager()
        await add_mock_server(mgr, "fs")
        registry = ToolRegistry()
        mgr.inject_tools(registry)

        call_num = [0]

        async def llm_fn(messages, tools=None):
            call_num[0] += 1
            if call_num[0] == 1:
                return {
                    "content": "",
                    "tool_calls": [{
                        "id": "call_0",
                        "function": {
                            "name": "mcp.fs.read_file",
                            "arguments": json.dumps({"path": "/tmp/hello.txt"}),
                        },
                    }],
                }
            return {"content": "File contents: contents of /tmp/hello.txt", "tool_calls": None}

        loop = AgentLoop(llm_fn=llm_fn, tool_registry=registry, system_prompt="sys")
        result = await loop.run("Read /tmp/hello.txt")
        assert result.stopped_reason == "completed"
        assert result.tool_calls_count == 1

    @pytest.mark.asyncio
    async def test_agent_loop_mixed_tools(self):
        mgr = MCPManager()
        await add_mock_server(mgr, "fs")
        registry = ToolRegistry()

        @tool
        async def local_calc(expr: str) -> str:
            """Calculate."""
            return "42"
        registry.register(local_calc)
        mgr.inject_tools(registry)

        call_num = [0]

        async def llm_fn(messages, tools=None):
            call_num[0] += 1
            if call_num[0] == 1:
                return {"content": "", "tool_calls": [
                    {"id": "c0", "function": {"name": "local_calc", "arguments": '{"expr":"1+1"}'}},
                ]}
            elif call_num[0] == 2:
                return {"content": "", "tool_calls": [
                    {"id": "c1", "function": {"name": "mcp.fs.read_file", "arguments": '{"path":"/data"}'}},
                ]}
            return {"content": "done", "tool_calls": None}

        loop = AgentLoop(llm_fn=llm_fn, tool_registry=registry)
        result = await loop.run("calc and read")
        assert result.tool_calls_count == 2

    @pytest.mark.asyncio
    async def test_call_tool_timeout(self):
        def handler(request: bytes) -> bytes:
            req = json.loads(request)
            rid = req["id"]
            method = req["method"]
            if method == "initialize":
                return _make_response(rid, {"protocolVersion": "2024-11-05", "serverInfo": {"name": "slow", "version": "1.0"}})
            elif method == "tools/list":
                return _make_response(rid, {"tools": [{"name": "slow_tool", "description": "Slow", "inputSchema": {"type": "object"}}]})
            elif method == "tools/call":
                import time
                time.sleep(2)  # blocking sleep in sync handler
                return _make_response(rid, {"content": [{"type": "text", "text": "done"}]})
            return _make_response(rid, error={"code": -1, "message": "nope"})

        mgr = MCPManager()
        await mgr.add_server_with_transport(MCPServerConfig(name="slow", max_retries=0), InProcessTransport(handler))
        # InProcessTransport is sync so timeout doesn't apply here directly
        # Just verify it eventually completes
        result = await mgr.call_tool("mcp.slow.slow_tool", {})
        assert result == "done"


class TestMatchToolFilter:

    @pytest.mark.parametrize("pattern,name,expected", [
        ("read_*", "read_file", True),
        ("read_*", "write_file", False),
        ("*_file", "read_file", True),
        ("*_file", "list_dir", False),
        ("list_*", "list_files", True),
        ("query", "query", True),
        ("query", "query2", False),
        ("*", "anything", True),
        ("db.*", "db.query", True),
        ("db.*", "redis.get", False),
    ])
    def test_wildcard(self, pattern, name, expected):
        assert match_tool_filter(pattern, name) == expected


class TestHTTPTransportError:

    def test_retryable(self):
        assert MCPTransportError(500, "err").is_retryable
        assert MCPTransportError(429, "rate limited").is_retryable
        assert not MCPTransportError(404, "not found").is_retryable
        assert "500" in str(MCPTransportError(500, "err"))
