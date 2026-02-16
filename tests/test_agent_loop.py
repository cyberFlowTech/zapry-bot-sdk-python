"""
AgentLoop 全量测试 — 模拟 LLM 响应来验证 ReAct 循环的所有行为。
"""

import json
import pytest

from zapry_agents_sdk.agent.loop import AgentLoop, AgentResult, AgentHooks, TurnRecord
from zapry_agents_sdk.tools.registry import ToolRegistry, tool


# ══════════════════════════════════════════════
# Fake LLM helpers
# ══════════════════════════════════════════════

def make_final_response(content: str):
    """Simulate an LLM response with only text (no tool calls)."""
    return {"content": content, "tool_calls": None}

def make_tool_call_response(calls: list, content: str = ""):
    """Simulate an LLM response that requests tool calls."""
    tool_calls = []
    for i, (name, args) in enumerate(calls):
        tool_calls.append({
            "id": f"call_{i}",
            "function": {
                "name": name,
                "arguments": json.dumps(args),
            },
        })
    return {"content": content, "tool_calls": tool_calls}


# ══════════════════════════════════════════════
# Test fixtures
# ══════════════════════════════════════════════

@pytest.fixture
def registry():
    r = ToolRegistry()

    @tool
    async def get_weather(city: str) -> str:
        """Get weather for a city."""
        return f"{city}: 25°C, 晴"

    @tool
    async def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    @tool
    async def search(query: str) -> str:
        """Search the web."""
        return f"Results for: {query}"

    r.register(get_weather)
    r.register(add)
    r.register(search)
    return r


# ══════════════════════════════════════════════
# Core behavior tests
# ══════════════════════════════════════════════

class TestAgentLoopBasic:

    @pytest.mark.asyncio
    async def test_direct_answer_no_tools(self, registry):
        """LLM 直接回答，不调用工具。"""
        async def llm_fn(messages, tools=None):
            return make_final_response("Hello! I'm here to help.")

        loop = AgentLoop(llm_fn=llm_fn, tool_registry=registry)
        result = await loop.run("Hi")

        assert result.final_output == "Hello! I'm here to help."
        assert result.total_turns == 1
        assert result.tool_calls_count == 0
        assert result.stopped_reason == "completed"
        assert len(result.turns) == 1
        assert result.turns[0].is_final is True

    @pytest.mark.asyncio
    async def test_single_tool_call(self, registry):
        """LLM 调用一个工具，然后给出最终回答。"""
        call_count = 0

        async def llm_fn(messages, tools=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_tool_call_response([("get_weather", {"city": "Shanghai"})])
            else:
                # After tool result, give final answer
                return make_final_response("上海现在 25°C，晴天。")

        loop = AgentLoop(llm_fn=llm_fn, tool_registry=registry)
        result = await loop.run("上海天气怎么样？")

        assert result.final_output == "上海现在 25°C，晴天。"
        assert result.total_turns == 2
        assert result.tool_calls_count == 1
        assert result.stopped_reason == "completed"
        assert result.turns[0].tool_calls[0].tool_name == "get_weather"
        assert "25°C" in result.turns[0].tool_calls[0].result

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_single_turn(self, registry):
        """LLM 在一轮中调用多个工具（并行调用）。"""
        call_count = 0

        async def llm_fn(messages, tools=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_tool_call_response([
                    ("get_weather", {"city": "Shanghai"}),
                    ("get_weather", {"city": "Beijing"}),
                ])
            else:
                return make_final_response("上海 25°C，北京 20°C。")

        loop = AgentLoop(llm_fn=llm_fn, tool_registry=registry)
        result = await loop.run("上海和北京天气")

        assert result.tool_calls_count == 2
        assert result.total_turns == 2
        assert len(result.turns[0].tool_calls) == 2

    @pytest.mark.asyncio
    async def test_multi_turn_tool_calls(self, registry):
        """LLM 多轮连续调用工具。"""
        call_count = 0

        async def llm_fn(messages, tools=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_tool_call_response([("search", {"query": "best restaurants"})])
            elif call_count == 2:
                return make_tool_call_response([("get_weather", {"city": "Shanghai"})])
            else:
                return make_final_response("Found restaurants, weather is 25°C.")

        loop = AgentLoop(llm_fn=llm_fn, tool_registry=registry)
        result = await loop.run("推荐餐厅并告诉我天气")

        assert result.total_turns == 3
        assert result.tool_calls_count == 2
        assert result.stopped_reason == "completed"


class TestAgentLoopMaxTurns:

    @pytest.mark.asyncio
    async def test_max_turns_reached(self, registry):
        """超过 max_turns 限制后停止。"""
        async def llm_fn(messages, tools=None):
            return make_tool_call_response([("search", {"query": "infinite"})])

        loop = AgentLoop(llm_fn=llm_fn, tool_registry=registry, max_turns=3)
        result = await loop.run("loop forever")

        assert result.stopped_reason == "max_turns"
        assert result.total_turns == 3

    @pytest.mark.asyncio
    async def test_max_turns_1(self, registry):
        """max_turns=1: 只允许一轮 LLM 调用。"""
        async def llm_fn(messages, tools=None):
            return make_tool_call_response([("search", {"query": "test"})])

        loop = AgentLoop(llm_fn=llm_fn, tool_registry=registry, max_turns=1)
        result = await loop.run("test")

        assert result.total_turns == 1
        assert result.stopped_reason == "max_turns"


class TestAgentLoopErrorHandling:

    @pytest.mark.asyncio
    async def test_tool_execution_error(self, registry):
        """工具执行报错，循环应继续（错误信息反馈给 LLM）。"""
        call_count = 0

        async def llm_fn(messages, tools=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_tool_call_response([("nonexistent_tool", {})])
            else:
                return make_final_response("Sorry, that tool is not available.")

        loop = AgentLoop(llm_fn=llm_fn, tool_registry=registry)
        result = await loop.run("test")

        assert result.stopped_reason == "completed"
        assert result.turns[0].tool_calls[0].error is not None
        assert "not found" in result.turns[0].tool_calls[0].error.lower()

    @pytest.mark.asyncio
    async def test_llm_exception(self, registry):
        """LLM 调用抛异常，循环应停止。"""
        async def llm_fn(messages, tools=None):
            raise RuntimeError("API connection failed")

        loop = AgentLoop(llm_fn=llm_fn, tool_registry=registry)
        result = await loop.run("test")

        assert result.stopped_reason == "error"
        assert "API connection failed" in result.final_output


class TestAgentLoopHooks:

    @pytest.mark.asyncio
    async def test_hooks_called(self, registry):
        """验证所有钩子被正确调用。"""
        events = []

        async def on_llm_start(turn, msgs):
            events.append(f"llm_start:{turn}")

        async def on_llm_end(turn, resp):
            events.append(f"llm_end:{turn}")

        async def on_tool_start(name, args):
            events.append(f"tool_start:{name}")

        async def on_tool_end(name, result, error):
            events.append(f"tool_end:{name}")

        async def on_turn_end(turn):
            events.append(f"turn_end:{turn.turn_number}")

        hooks = AgentHooks(
            on_llm_start=on_llm_start,
            on_llm_end=on_llm_end,
            on_tool_start=on_tool_start,
            on_tool_end=on_tool_end,
            on_turn_end=on_turn_end,
        )

        call_count = 0
        async def llm_fn(messages, tools=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_tool_call_response([("get_weather", {"city": "SH"})])
            return make_final_response("Done")

        loop = AgentLoop(llm_fn=llm_fn, tool_registry=registry, hooks=hooks)
        await loop.run("test")

        assert "llm_start:1" in events
        assert "llm_end:1" in events
        assert "tool_start:get_weather" in events
        assert "tool_end:get_weather" in events
        assert "turn_end:1" in events
        assert "turn_end:2" in events

    @pytest.mark.asyncio
    async def test_error_hook(self, registry):
        """验证 on_error 钩子。"""
        errors = []

        async def on_error(e):
            errors.append(str(e))

        hooks = AgentHooks(on_error=on_error)

        async def llm_fn(messages, tools=None):
            raise ValueError("boom")

        loop = AgentLoop(llm_fn=llm_fn, tool_registry=registry, hooks=hooks)
        await loop.run("test")

        assert len(errors) == 1
        assert "boom" in errors[0]


class TestAgentLoopMessages:

    @pytest.mark.asyncio
    async def test_system_prompt_in_messages(self, registry):
        """验证 system prompt 被正确注入。"""
        captured_messages = []

        async def llm_fn(messages, tools=None):
            captured_messages.extend(messages)
            return make_final_response("ok")

        loop = AgentLoop(
            llm_fn=llm_fn,
            tool_registry=registry,
            system_prompt="You are a helpful bot.",
        )
        await loop.run("hi")

        assert captured_messages[0]["role"] == "system"
        assert "helpful bot" in captured_messages[0]["content"]

    @pytest.mark.asyncio
    async def test_extra_context_in_messages(self, registry):
        """验证 extra_context 被注入。"""
        captured = []

        async def llm_fn(messages, tools=None):
            captured.extend(messages)
            return make_final_response("ok")

        loop = AgentLoop(llm_fn=llm_fn, tool_registry=registry, system_prompt="sys")
        await loop.run("hi", extra_context="User is 25 years old")

        system_msgs = [m for m in captured if m["role"] == "system"]
        assert any("25 years old" in m["content"] for m in system_msgs)

    @pytest.mark.asyncio
    async def test_conversation_history(self, registry):
        """验证对话历史被包含。"""
        captured = []

        async def llm_fn(messages, tools=None):
            captured.extend(messages)
            return make_final_response("ok")

        history = [
            {"role": "user", "content": "previous question"},
            {"role": "assistant", "content": "previous answer"},
        ]
        loop = AgentLoop(llm_fn=llm_fn, tool_registry=registry)
        await loop.run("new question", conversation_history=history)

        contents = [m["content"] for m in captured]
        assert "previous question" in contents
        assert "new question" in contents

    @pytest.mark.asyncio
    async def test_result_messages_complete(self, registry):
        """验证 result.messages 包含完整的对话历史（含工具调用）。"""
        call_count = 0

        async def llm_fn(messages, tools=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_tool_call_response([("add", {"a": 1, "b": 2})])
            return make_final_response("The answer is 3.")

        loop = AgentLoop(llm_fn=llm_fn, tool_registry=registry)
        result = await loop.run("1+2=?")

        roles = [m["role"] for m in result.messages]
        assert "user" in roles
        assert "assistant" in roles
        assert "tool" in roles


class TestAgentLoopEdgeCases:

    @pytest.mark.asyncio
    async def test_empty_registry(self):
        """空工具注册表也能正常工作。"""
        async def llm_fn(messages, tools=None):
            assert tools is None  # no tools passed
            return make_final_response("I have no tools.")

        loop = AgentLoop(llm_fn=llm_fn, tool_registry=ToolRegistry())
        result = await loop.run("test")

        assert result.final_output == "I have no tools."
        assert result.tool_calls_count == 0

    @pytest.mark.asyncio
    async def test_tool_returns_non_string(self, registry):
        """工具返回非字符串结果（应自动 JSON 序列化）。"""
        call_count = 0

        async def llm_fn(messages, tools=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_tool_call_response([("add", {"a": 3, "b": 4})])
            return make_final_response("7")

        loop = AgentLoop(llm_fn=llm_fn, tool_registry=registry)
        result = await loop.run("3+4")

        assert result.turns[0].tool_calls[0].result == "7"

    @pytest.mark.asyncio
    async def test_llm_response_as_object(self, registry):
        """LLM 返回 object（带属性访问）而非 dict。"""
        class FakeMessage:
            def __init__(self, content, tool_calls=None):
                self.content = content
                self.tool_calls = tool_calls

        async def llm_fn(messages, tools=None):
            return FakeMessage(content="Direct answer")

        loop = AgentLoop(llm_fn=llm_fn, tool_registry=registry)
        result = await loop.run("test")

        assert result.final_output == "Direct answer"
