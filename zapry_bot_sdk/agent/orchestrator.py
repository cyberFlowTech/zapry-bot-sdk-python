"""
AgentOrchestrator — 双模式编排器（tool_based + coordinator）。

两种模式共用 HandoffEngine 统一执行。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from zapry_bot_sdk.agent.card import AgentCardPublic, AgentRuntime
from zapry_bot_sdk.agent.engine import HandoffEngine
from zapry_bot_sdk.agent.handoff import (
    HandoffContext,
    HandoffMessage,
    HandoffRequest,
    HandoffResult,
)
from zapry_bot_sdk.agent.policy import HandoffPolicy, IdempotencyCache
from zapry_bot_sdk.agent.registry import AgentRegistry

logger = logging.getLogger("zapry_bot_sdk.agent")


# ──────────────────────────────────────────────
# CoordinatorDecision
# ──────────────────────────────────────────────

@dataclass
class CoordinatorDecision:
    """Coordinator 必须输出的结构化决策合同。"""
    selected_agents: List[str] = field(default_factory=list)
    execution_mode: str = "sequential"  # "sequential" | "parallel"
    agent_inputs: Dict[str, str] = field(default_factory=dict)
    expected_output: str = ""
    fallback_agent: Optional[str] = None
    fallback_response: str = "I'm sorry, I couldn't process your request."
    reason: str = ""
    confidence: float = 1.0
    constraints: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, text: str) -> "CoordinatorDecision":
        """Parse from LLM JSON output."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]

        try:
            d = json.loads(text)
            return cls(
                selected_agents=d.get("selected_agents", []),
                execution_mode=d.get("execution_mode", "sequential"),
                agent_inputs=d.get("agent_inputs", {}),
                expected_output=d.get("expected_output", ""),
                fallback_agent=d.get("fallback_agent"),
                fallback_response=d.get("fallback_response", ""),
                reason=d.get("reason", ""),
                confidence=d.get("confidence", 1.0),
                constraints=d.get("constraints", {}),
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            return cls()  # empty = no agents selected


# ──────────────────────────────────────────────
# AgentOrchestrator
# ──────────────────────────────────────────────

COORDINATOR_SYSTEM_PROMPT_SUFFIX = """

You MUST respond with a JSON object in the following format (no other text):
{
    "selected_agents": ["agent_id_1"],
    "execution_mode": "sequential",
    "agent_inputs": {"agent_id_1": "specific input for this agent"},
    "expected_output": "what you expect the agent to produce",
    "fallback_agent": null,
    "fallback_response": "response if all agents fail",
    "reason": "why you chose these agents",
    "confidence": 0.9
}

Available agents:
"""


class AgentOrchestrator:
    """双模式 Agent 编排器。

    Parameters:
        registry: Agent 注册表。
        engine: Handoff 执行引擎。
        mode: "tool_based" | "coordinator"。
        entry_agent_id: 入口 Agent ID（tool_based 模式时使用）。
        coordinator_llm_fn: Coordinator 的 LLM 函数（coordinator 模式时使用）。
        coordinator_prompt: Coordinator 的 system prompt 前缀。

    Usage (tool_based)::

        orch = AgentOrchestrator(registry, engine, mode="tool_based", entry_agent_id="receptionist")
        result = await orch.run("帮我看看塔罗", user_id="u1")

    Usage (coordinator)::

        orch = AgentOrchestrator(registry, engine, mode="coordinator", coordinator_llm_fn=my_llm)
        result = await orch.run("最近压力大", user_id="u1")
    """

    def __init__(
        self,
        registry: AgentRegistry,
        engine: HandoffEngine,
        mode: str = "tool_based",
        entry_agent_id: str = "",
        coordinator_llm_fn: Optional[Any] = None,
        coordinator_prompt: str = "You are an intelligent dispatcher that routes user requests to the best specialist agent.",
    ) -> None:
        self.registry = registry
        self.engine = engine
        self.mode = mode
        self.entry_agent_id = entry_agent_id
        self.coordinator_llm_fn = coordinator_llm_fn
        self.coordinator_prompt = coordinator_prompt

    async def run(
        self,
        user_input: str,
        user_id: str = "",
        owner_id: str = "",
        org_id: str = "",
        memory_summary: str = "",
    ) -> HandoffResult:
        """Execute the orchestration."""
        if self.mode == "coordinator":
            return await self._run_coordinator(user_input, user_id, owner_id, org_id, memory_summary)
        else:
            return await self._run_tool_based(user_input, user_id, owner_id, org_id, memory_summary)

    async def _run_tool_based(
        self, user_input: str, user_id: str, owner_id: str, org_id: str, memory_summary: str,
    ) -> HandoffResult:
        """Tool-based: entry agent's AgentLoop with handoff tools injected."""
        entry = self.registry.get(self.entry_agent_id)
        if not entry:
            return HandoffResult(
                status="error",
                error=HandoffError(code="NOT_FOUND", message=f"Entry agent not found: {self.entry_agent_id}"),
            )

        from zapry_bot_sdk.agent.loop import AgentLoop
        from zapry_bot_sdk.tools.registry import ToolRegistry

        # Merge entry agent's tools + handoff tools
        merged_registry = ToolRegistry()
        if entry.tool_registry:
            for t in entry.tool_registry.list():
                merged_registry.register(t)

        handoff_tools = self.registry.to_handoff_tools(
            caller_agent_id=self.entry_agent_id,
            caller_owner_id=owner_id,
            caller_org_id=org_id,
        )
        for t in handoff_tools:
            # Wrap as handoff-aware tool handler
            agent_id = t.name.replace("transfer_to_", "")
            t.handler = self._make_handoff_handler(agent_id, owner_id, org_id, user_input, memory_summary)
            t.is_async = True
            merged_registry.register(t)

        loop = AgentLoop(
            llm_fn=entry.llm_fn,
            tool_registry=merged_registry,
            system_prompt=entry.system_prompt,
            max_turns=entry.max_turns,
            guardrails=entry.guardrails,
            tracer=entry.tracer,
        )

        result = await loop.run(user_input, extra_context=memory_summary or None)

        return HandoffResult(
            output=result.final_output,
            agent_id=self.entry_agent_id,
            status="completed" if result.stopped_reason == "completed" else result.stopped_reason,
            request_id="",
        )

    def _make_handoff_handler(
        self, target_agent_id: str, owner_id: str, org_id: str, user_input: str, memory_summary: str,
    ):
        """Create an async handler for a transfer_to_xxx tool."""
        engine = self.engine

        async def handler(reason: str = "") -> str:
            req = HandoffRequest(
                from_agent=self.entry_agent_id,
                to_agent=target_agent_id,
                reason=reason or user_input,
                requested_mode="tool_based",
                caller_owner_id=owner_id,
                caller_org_id=org_id,
                context=HandoffContext(
                    messages=[HandoffMessage(role="user", content=user_input)],
                    memory_summary=memory_summary,
                ),
            )
            result = await engine.handoff(req)
            if result.error:
                return f"Handoff failed: {result.error.message}"
            return result.output

        return handler

    async def _run_coordinator(
        self, user_input: str, user_id: str, owner_id: str, org_id: str, memory_summary: str,
    ) -> HandoffResult:
        """Coordinator: LLM decides which agents to call with structured output."""
        if not self.coordinator_llm_fn:
            return HandoffResult(status="error", error=HandoffError(code="MODEL_ERROR", message="No coordinator LLM"))

        # Build agent catalog for coordinator
        agents = self.registry.list_all()
        catalog = "\n".join(
            f"- {a.card.agent_id}: {a.card.name} — {a.card.description} (skills: {', '.join(a.card.skills)})"
            for a in agents
        )

        system_prompt = self.coordinator_prompt + COORDINATOR_SYSTEM_PROMPT_SUFFIX + catalog

        # Call coordinator LLM
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]

        try:
            resp = await self.coordinator_llm_fn(messages, None)
            content = resp.get("content", "") if isinstance(resp, dict) else getattr(resp, "content", "")
        except Exception as e:
            return HandoffResult(status="error", error=HandoffError(code="MODEL_ERROR", message=str(e)))

        # Parse decision
        decision = CoordinatorDecision.from_json(content)
        if not decision.selected_agents:
            return HandoffResult(
                output=decision.fallback_response or content,
                status="completed",
            )

        # Execute selected agents
        results = []
        for agent_id in decision.selected_agents:
            agent_input = decision.agent_inputs.get(agent_id, user_input)
            req = HandoffRequest(
                from_agent="coordinator",
                to_agent=agent_id,
                reason=decision.reason,
                requested_mode="coordinator",
                caller_owner_id=owner_id,
                caller_org_id=org_id,
                context=HandoffContext(
                    messages=[HandoffMessage(role="user", content=agent_input)],
                    memory_summary=memory_summary,
                ),
            )
            result = await self.engine.handoff(req)
            results.append(result)

            # Sequential: stop on first success
            if decision.execution_mode == "sequential" and result.status == "success":
                break

        # Find best result
        for r in results:
            if r.status == "success":
                return r

        # All failed: use fallback
        if decision.fallback_response:
            return HandoffResult(output=decision.fallback_response, status="completed")

        return results[-1] if results else HandoffResult(status="error")


# Import for type reference
from zapry_bot_sdk.agent.handoff import HandoffError as _HE
HandoffError = _HE
