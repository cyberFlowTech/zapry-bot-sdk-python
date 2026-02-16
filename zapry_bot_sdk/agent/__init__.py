"""
Agent Loop — 自动推理循环（ReAct 模式）。

让 LLM 自主决策调用工具、获取结果、再决策，直到产出最终回答。

Quick Start::

    from zapry_bot_sdk.agent import AgentLoop

    loop = AgentLoop(
        llm_fn=my_openai_call,
        tool_registry=registry,
        system_prompt="You are a helpful assistant.",
    )

    result = await loop.run("What's the weather in Shanghai?")
    print(result.final_output)
"""

from zapry_bot_sdk.agent.loop import (
    AgentLoop,
    AgentResult,
    TurnRecord,
    AgentHooks,
)

__all__ = [
    "AgentLoop",
    "AgentResult",
    "TurnRecord",
    "AgentHooks",
]
