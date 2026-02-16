"""
AgentLoop — ReAct 自动推理循环。

核心流程:
    User Input → LLM → [tool_calls?] → Execute Tools → Feed Results → LLM → ... → Final Output

支持:
- 可配置的最大轮次 (max_turns)
- 事件钩子 (on_llm_call, on_tool_call, on_turn_end, on_error)
- 多 LLM provider (通过 llm_fn 注入)
- 与 ToolRegistry + MemorySession 集成
- 流式和非流式调用（通过 llm_fn 控制）

Usage::

    from zapry_bot_sdk.agent import AgentLoop
    from zapry_bot_sdk.tools import ToolRegistry, tool

    @tool
    async def get_weather(city: str) -> str:
        return f"{city}: 25°C, 晴"

    registry = ToolRegistry()
    registry.register(get_weather)

    async def my_llm(messages, tools=None):
        response = await openai_client.chat.completions.create(
            model="gpt-4o", messages=messages, tools=tools,
        )
        return response.choices[0].message

    loop = AgentLoop(
        llm_fn=my_llm,
        tool_registry=registry,
        system_prompt="You are a helpful assistant with tool access.",
    )

    result = await loop.run("What's the weather in Shanghai?")
    print(result.final_output)       # "The weather in Shanghai is 25°C, sunny."
    print(result.turns)              # Full turn-by-turn trace
    print(result.tool_calls_count)   # 1
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from zapry_bot_sdk.tools.registry import ToolContext, ToolRegistry

logger = logging.getLogger("zapry_bot_sdk.agent")


# ──────────────────────────────────────────────
# Types
# ──────────────────────────────────────────────

# LLM function signature:
#   async def llm_fn(messages: List[dict], tools: Optional[List[dict]]) -> LLMMessage
# Must return an object/dict with at least:
#   - content (str or None): text output
#   - tool_calls (list or None): list of tool calls
LLMFn = Callable[[List[Dict], Optional[List[Dict]]], Awaitable[Any]]


@dataclass
class ToolCallRecord:
    """Record of a single tool invocation within a turn."""
    tool_name: str
    arguments: Dict[str, Any]
    result: str
    error: Optional[str] = None
    call_id: str = ""


@dataclass
class TurnRecord:
    """Record of a single LLM turn (one call + any tool executions)."""
    turn_number: int
    llm_output: Optional[str] = None
    tool_calls: List[ToolCallRecord] = field(default_factory=list)
    is_final: bool = False


@dataclass
class AgentResult:
    """Final result of an AgentLoop run.

    Attributes:
        final_output: The LLM's final text response (when no more tool calls).
        turns: Complete turn-by-turn trace.
        tool_calls_count: Total number of tool calls across all turns.
        total_turns: Number of LLM invocations.
        stopped_reason: Why the loop stopped ("completed", "max_turns", "error").
        messages: The full message history (useful for continuing conversation).
    """
    final_output: str = ""
    turns: List[TurnRecord] = field(default_factory=list)
    tool_calls_count: int = 0
    total_turns: int = 0
    stopped_reason: str = "completed"
    messages: List[Dict] = field(default_factory=list)


@dataclass
class AgentHooks:
    """Optional event callbacks for observability.

    All hooks are async and optional. Set any of them to receive events.
    """
    on_llm_start: Optional[Callable[[int, List[Dict]], Awaitable[None]]] = None
    on_llm_end: Optional[Callable[[int, Any], Awaitable[None]]] = None
    on_tool_start: Optional[Callable[[str, Dict], Awaitable[None]]] = None
    on_tool_end: Optional[Callable[[str, str, Optional[str]], Awaitable[None]]] = None
    on_turn_end: Optional[Callable[[TurnRecord], Awaitable[None]]] = None
    on_error: Optional[Callable[[Exception], Awaitable[None]]] = None


# ──────────────────────────────────────────────
# AgentLoop
# ──────────────────────────────────────────────


class AgentLoop:
    """ReAct agent loop: LLM → tool calls → results → LLM → ... → final output.

    Parameters:
        llm_fn: Async function that calls the LLM.
            Signature: ``async def llm_fn(messages, tools=None) -> message``
            The returned message must have ``content`` and ``tool_calls`` attributes/keys.
        tool_registry: Registry of available tools.
        system_prompt: System prompt prepended to all conversations.
        max_turns: Maximum number of LLM invocations (default 10).
        hooks: Optional event callbacks for monitoring.
    """

    def __init__(
        self,
        llm_fn: LLMFn,
        tool_registry: ToolRegistry,
        system_prompt: str = "",
        max_turns: int = 10,
        hooks: Optional[AgentHooks] = None,
    ) -> None:
        self.llm_fn = llm_fn
        self.tool_registry = tool_registry
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.hooks = hooks or AgentHooks()

    async def run(
        self,
        user_input: str,
        conversation_history: Optional[List[Dict]] = None,
        extra_context: Optional[str] = None,
    ) -> AgentResult:
        """Execute the agent loop.

        Parameters:
            user_input: The user's message.
            conversation_history: Optional prior conversation messages.
            extra_context: Optional extra system context (e.g. memory prompt).

        Returns:
            AgentResult with final output, turn trace, and statistics.
        """
        # Build initial messages
        messages: List[Dict] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        if extra_context:
            messages.append({"role": "system", "content": extra_context})
        if conversation_history:
            messages.extend(conversation_history)
        messages.append({"role": "user", "content": user_input})

        # Get tools schema
        tools_schema = self.tool_registry.to_openai_schema() if len(self.tool_registry) > 0 else None

        result = AgentResult()
        turn_number = 0

        while turn_number < self.max_turns:
            turn_number += 1
            turn = TurnRecord(turn_number=turn_number)

            try:
                # --- LLM Call ---
                if self.hooks.on_llm_start:
                    await self.hooks.on_llm_start(turn_number, messages)

                llm_response = await self.llm_fn(messages, tools_schema)

                if self.hooks.on_llm_end:
                    await self.hooks.on_llm_end(turn_number, llm_response)

                # Extract content and tool_calls from response
                content = _get_attr(llm_response, "content")
                tool_calls = _get_attr(llm_response, "tool_calls")

                turn.llm_output = content

                # --- Check: Final output (no tool calls) ---
                if not tool_calls:
                    turn.is_final = True
                    result.final_output = content or ""
                    result.stopped_reason = "completed"
                    result.turns.append(turn)
                    if self.hooks.on_turn_end:
                        await self.hooks.on_turn_end(turn)
                    break

                # --- Execute tool calls ---
                # Append assistant message with tool_calls to history
                assistant_msg = {"role": "assistant", "content": content or ""}
                # Attach tool_calls in the format OpenAI expects
                raw_tool_calls = _serialize_tool_calls(tool_calls)
                if raw_tool_calls:
                    assistant_msg["tool_calls"] = raw_tool_calls
                messages.append(assistant_msg)

                for tc in tool_calls:
                    call_id = _get_attr(tc, "id") or ""
                    func = _get_attr(tc, "function") or tc
                    func_name = _get_attr(func, "name") or ""
                    func_args_raw = _get_attr(func, "arguments") or "{}"

                    # Parse arguments
                    try:
                        func_args = json.loads(func_args_raw) if isinstance(func_args_raw, str) else dict(func_args_raw)
                    except (json.JSONDecodeError, TypeError):
                        func_args = {}

                    if self.hooks.on_tool_start:
                        await self.hooks.on_tool_start(func_name, func_args)

                    # Execute tool
                    tool_record = ToolCallRecord(
                        tool_name=func_name,
                        arguments=func_args,
                        result="",
                        call_id=call_id,
                    )

                    try:
                        ctx = ToolContext(tool_name=func_name, call_id=call_id)
                        tool_result = await self.tool_registry.execute(func_name, func_args, ctx)
                        tool_result_str = tool_result if isinstance(tool_result, str) else json.dumps(tool_result, ensure_ascii=False)
                        tool_record.result = tool_result_str
                    except Exception as e:
                        tool_record.error = str(e)
                        tool_result_str = f"Error: {e}"
                        logger.warning("Tool %s failed: %s", func_name, e)

                    if self.hooks.on_tool_end:
                        await self.hooks.on_tool_end(func_name, tool_record.result, tool_record.error)

                    turn.tool_calls.append(tool_record)
                    result.tool_calls_count += 1

                    # Append tool result to messages
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": tool_result_str,
                    })

                result.turns.append(turn)
                if self.hooks.on_turn_end:
                    await self.hooks.on_turn_end(turn)

            except Exception as e:
                logger.error("AgentLoop error at turn %d: %s", turn_number, e)
                if self.hooks.on_error:
                    await self.hooks.on_error(e)
                result.stopped_reason = "error"
                result.final_output = f"Error: {e}"
                break

        else:
            # max_turns exceeded
            result.stopped_reason = "max_turns"
            # Use the last LLM content as output if available
            if result.turns and result.turns[-1].llm_output:
                result.final_output = result.turns[-1].llm_output

        result.total_turns = turn_number
        result.messages = messages
        return result


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────


def _get_attr(obj: Any, key: str, default: Any = None) -> Any:
    """Get attribute or dict key."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _serialize_tool_calls(tool_calls: Any) -> Optional[List[Dict]]:
    """Serialize tool_calls to a list of dicts for message history."""
    if not tool_calls:
        return None
    result = []
    for tc in tool_calls:
        call_id = _get_attr(tc, "id") or ""
        func = _get_attr(tc, "function") or tc
        func_name = _get_attr(func, "name") or ""
        func_args = _get_attr(func, "arguments") or "{}"
        result.append({
            "id": call_id,
            "type": "function",
            "function": {
                "name": func_name,
                "arguments": func_args if isinstance(func_args, str) else json.dumps(func_args),
            },
        })
    return result
