"""
测试 Tool Calling 框架和 OpenAI 适配器。
"""

import json
import pytest
from zapry_agents_sdk.tools.registry import (
    ToolRegistry,
    ToolDef,
    ToolParam,
    ToolContext,
    tool,
    _extract_tool_def,
)
from zapry_agents_sdk.tools.openai_adapter import (
    OpenAIToolAdapter,
    ToolCallResult,
)


# ══════════════════════════════════════════════
# @tool decorator tests
# ══════════════════════════════════════════════


class TestToolDecorator:
    """@tool 装饰器测试。"""

    def test_basic_decorator(self):
        @tool
        async def greet(name: str) -> str:
            """Say hello."""
            return f"Hello {name}"

        assert isinstance(greet, ToolDef)
        assert greet.name == "greet"
        assert greet.description == "Say hello."
        assert len(greet.parameters) == 1
        assert greet.parameters[0].name == "name"
        assert greet.parameters[0].type == "string"
        assert greet.parameters[0].required is True

    def test_decorator_with_args(self):
        @tool(name="custom_name", description="Custom desc")
        async def my_fn(x: int) -> int:
            return x

        assert my_fn.name == "custom_name"
        assert my_fn.description == "Custom desc"

    def test_optional_param(self):
        @tool
        async def search(query: str, limit: int = 10) -> str:
            return query

        assert len(search.parameters) == 2
        q = search.parameters[0]
        assert q.name == "query"
        assert q.required is True
        l = search.parameters[1]
        assert l.name == "limit"
        assert l.required is False
        assert l.default == 10

    def test_multiple_types(self):
        @tool
        def calc(a: int, b: float, flag: bool = False) -> str:
            return ""

        assert calc.parameters[0].type == "integer"
        assert calc.parameters[1].type == "number"
        assert calc.parameters[2].type == "boolean"
        assert calc.is_async is False

    def test_docstring_arg_extraction(self):
        @tool
        async def weather(city: str, unit: str = "celsius") -> str:
            """获取天气信息。

            Args:
                city: 城市名称
                unit: 温度单位
            """
            return ""

        assert weather.parameters[0].description == "城市名称"
        assert weather.parameters[1].description == "温度单位"

    def test_no_docstring(self):
        @tool
        async def bare(x: str) -> str:
            return x

        assert bare.description == ""

    def test_sync_function(self):
        @tool
        def sync_tool(x: str) -> str:
            return x

        assert sync_tool.is_async is False

    def test_tool_context_param_excluded(self):
        @tool
        async def with_ctx(ctx: ToolContext, name: str) -> str:
            return name

        assert len(with_ctx.parameters) == 1
        assert with_ctx.parameters[0].name == "name"


# ══════════════════════════════════════════════
# ToolDef schema tests
# ══════════════════════════════════════════════


class TestToolDefSchema:
    """ToolDef schema 导出测试。"""

    def test_to_json_schema(self):
        td = ToolDef(
            name="test",
            description="Test tool",
            parameters=[
                ToolParam(name="x", type="string", description="param x", required=True),
                ToolParam(name="y", type="integer", required=False, default=5),
            ],
        )
        schema = td.to_json_schema()
        assert schema["name"] == "test"
        assert schema["description"] == "Test tool"
        assert "x" in schema["parameters"]["properties"]
        assert schema["parameters"]["properties"]["x"]["type"] == "string"
        assert schema["parameters"]["required"] == ["x"]
        assert schema["parameters"]["properties"]["y"]["default"] == 5

    def test_to_openai_schema(self):
        td = ToolDef(name="t", description="d", parameters=[])
        schema = td.to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "t"


# ══════════════════════════════════════════════
# ToolRegistry tests
# ══════════════════════════════════════════════


class TestToolRegistry:
    """ToolRegistry 注册表测试。"""

    @pytest.fixture
    def registry(self):
        return ToolRegistry()

    def test_register_tool_def(self, registry):
        @tool
        async def hello(name: str) -> str:
            return f"Hi {name}"

        registry.register(hello)
        assert "hello" in registry
        assert len(registry) == 1

    def test_register_plain_function(self, registry):
        async def my_func(x: str) -> str:
            """Do something."""
            return x

        registry.register(my_func)
        assert "my_func" in registry

    def test_get(self, registry):
        @tool
        async def t(x: str) -> str:
            return x

        registry.register(t)
        assert registry.get("t") is t
        assert registry.get("nonexistent") is None

    def test_list_and_names(self, registry):
        @tool
        async def a() -> str:
            return ""

        @tool
        async def b() -> str:
            return ""

        registry.register(a)
        registry.register(b)
        assert set(registry.names()) == {"a", "b"}
        assert len(registry.list()) == 2

    def test_remove(self, registry):
        @tool
        async def r() -> str:
            return ""

        registry.register(r)
        assert "r" in registry
        registry.remove("r")
        assert "r" not in registry

    def test_to_json_schema(self, registry):
        @tool
        async def t(x: str) -> str:
            """Desc."""
            return x

        registry.register(t)
        schemas = registry.to_json_schema()
        assert len(schemas) == 1
        assert schemas[0]["name"] == "t"

    def test_to_openai_schema(self, registry):
        @tool
        async def t(x: str) -> str:
            """Desc."""
            return x

        registry.register(t)
        schemas = registry.to_openai_schema()
        assert len(schemas) == 1
        assert schemas[0]["type"] == "function"

    @pytest.mark.asyncio
    async def test_execute_async(self, registry):
        @tool
        async def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        registry.register(add)
        result = await registry.execute("add", {"a": 3, "b": 5})
        assert result == 8

    @pytest.mark.asyncio
    async def test_execute_sync(self, registry):
        @tool
        def multiply(a: int, b: int) -> int:
            """Multiply."""
            return a * b

        registry.register(multiply)
        result = await registry.execute("multiply", {"a": 3, "b": 4})
        assert result == 12

    @pytest.mark.asyncio
    async def test_execute_with_defaults(self, registry):
        @tool
        async def greet(name: str, greeting: str = "Hello") -> str:
            return f"{greeting} {name}"

        registry.register(greet)
        result = await registry.execute("greet", {"name": "World"})
        assert result == "Hello World"

    @pytest.mark.asyncio
    async def test_execute_missing_required(self, registry):
        @tool
        async def need_arg(x: str) -> str:
            return x

        registry.register(need_arg)
        with pytest.raises(TypeError, match="missing required argument"):
            await registry.execute("need_arg", {})

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self, registry):
        with pytest.raises(KeyError, match="Tool not found"):
            await registry.execute("nonexistent", {})

    @pytest.mark.asyncio
    async def test_execute_with_context(self, registry):
        @tool
        async def ctx_tool(ctx: ToolContext, msg: str) -> str:
            """Tool that uses context."""
            return f"{ctx.tool_name}: {msg}"

        registry.register(ctx_tool)
        result = await registry.execute("ctx_tool", {"msg": "hi"})
        assert result == "ctx_tool: hi"

    @pytest.mark.asyncio
    async def test_execute_with_custom_context(self, registry):
        @tool
        async def ctx_tool(ctx: ToolContext, msg: str) -> str:
            return ctx.extra.get("key", "none")

        registry.register(ctx_tool)
        custom_ctx = ToolContext(extra={"key": "value"})
        result = await registry.execute("ctx_tool", {"msg": ""}, ctx=custom_ctx)
        assert result == "value"


# ══════════════════════════════════════════════
# OpenAIToolAdapter tests
# ══════════════════════════════════════════════


class TestOpenAIToolAdapter:
    """OpenAI 适配器测试。"""

    @pytest.fixture
    def adapter(self):
        registry = ToolRegistry()

        @tool
        async def get_weather(city: str, unit: str = "celsius") -> str:
            """获取天气。"""
            return f"{city}: 25°C"

        @tool
        async def add(a: int, b: int) -> int:
            """加法。"""
            return a + b

        registry.register(get_weather)
        registry.register(add)
        return OpenAIToolAdapter(registry)

    def test_to_openai_tools(self, adapter):
        tools = adapter.to_openai_tools()
        assert len(tools) == 2
        names = {t["function"]["name"] for t in tools}
        assert names == {"get_weather", "add"}
        for t in tools:
            assert t["type"] == "function"
            assert "parameters" in t["function"]

    @pytest.mark.asyncio
    async def test_handle_tool_calls_dict(self, adapter):
        """使用 dict 格式的 tool_calls。"""
        tool_calls = [
            {
                "id": "call_1",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"city": "上海"}',
                },
            }
        ]
        results = await adapter.handle_tool_calls(tool_calls)
        assert len(results) == 1
        assert results[0].tool_call_id == "call_1"
        assert results[0].name == "get_weather"
        assert "上海" in results[0].content
        assert results[0].error is None

    @pytest.mark.asyncio
    async def test_handle_tool_calls_object(self, adapter):
        """使用 object 格式的 tool_calls（模拟 OpenAI 返回）。"""

        class FakeFunction:
            def __init__(self, name, arguments):
                self.name = name
                self.arguments = arguments

        class FakeToolCall:
            def __init__(self, id, function):
                self.id = id
                self.function = function

        tool_calls = [
            FakeToolCall("call_2", FakeFunction("add", '{"a": 3, "b": 7}')),
        ]
        results = await adapter.handle_tool_calls(tool_calls)
        assert len(results) == 1
        assert results[0].content == "10"

    @pytest.mark.asyncio
    async def test_handle_unknown_tool(self, adapter):
        tool_calls = [
            {"id": "c1", "function": {"name": "unknown", "arguments": "{}"}},
        ]
        results = await adapter.handle_tool_calls(tool_calls)
        assert len(results) == 1
        assert results[0].error is not None
        assert "not found" in results[0].error.lower()

    @pytest.mark.asyncio
    async def test_handle_multiple_calls(self, adapter):
        tool_calls = [
            {"id": "c1", "function": {"name": "get_weather", "arguments": '{"city": "北京"}'}},
            {"id": "c2", "function": {"name": "add", "arguments": '{"a": 1, "b": 2}'}},
        ]
        results = await adapter.handle_tool_calls(tool_calls)
        assert len(results) == 2
        assert "北京" in results[0].content
        assert results[1].content == "3"

    def test_results_to_messages(self, adapter):
        results = [
            ToolCallResult(tool_call_id="c1", name="t", content="ok"),
            ToolCallResult(tool_call_id="c2", name="t", error="fail"),
        ]
        msgs = adapter.results_to_messages(results)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "tool"
        assert msgs[0]["tool_call_id"] == "c1"
        assert msgs[0]["content"] == "ok"
        assert msgs[1]["content"] == "fail"

    def test_tool_call_result_to_message(self):
        r = ToolCallResult(tool_call_id="c1", name="t", content="data")
        m = r.to_message()
        assert m == {"role": "tool", "tool_call_id": "c1", "content": "data"}

    def test_tool_call_result_error_to_message(self):
        r = ToolCallResult(tool_call_id="c1", name="t", error="oops")
        m = r.to_message()
        assert m["content"] == "oops"
