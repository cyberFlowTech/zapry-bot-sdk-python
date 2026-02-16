"""
AgentRegistry — 带权限检查的 Agent 注册表。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from zapry_bot_sdk.agent.card import AgentCardPublic, AgentRuntime
from zapry_bot_sdk.tools.registry import ToolDef, ToolParam

logger = logging.getLogger("zapry_bot_sdk.agent")


class AgentRegistry:
    """Central registry for Agents with visibility/permission-aware discovery."""

    def __init__(self) -> None:
        self._agents: Dict[str, AgentRuntime] = {}

    def register(self, runtime: AgentRuntime) -> None:
        """Register an Agent."""
        self._agents[runtime.agent_id] = runtime
        logger.debug("Agent registered: %s", runtime.agent_id)

    def get(self, agent_id: str) -> Optional[AgentRuntime]:
        return self._agents.get(agent_id)

    def list_all(self) -> List[AgentRuntime]:
        return list(self._agents.values())

    def remove(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._agents

    # ─── Permission-aware discovery ───

    def find_by_skill(
        self,
        skill: str,
        caller_agent_id: str = "",
        caller_owner_id: str = "",
        caller_org_id: str = "",
    ) -> List[AgentRuntime]:
        """Find agents by skill, filtered by caller's permissions."""
        results = []
        for rt in self._agents.values():
            card = rt.card
            if skill not in card.skills:
                continue
            if not self._is_visible(card, caller_agent_id, caller_owner_id, caller_org_id):
                continue
            results.append(rt)
        return results

    def can_handoff(
        self,
        from_agent: str,
        to_agent: str,
        caller_owner_id: str = "",
        caller_org_id: str = "",
    ) -> bool:
        """Check if a handoff from one agent to another is permitted."""
        target = self._agents.get(to_agent)
        if not target:
            return False
        card = target.card
        if card.handoff_policy == "deny":
            return False
        return self._is_visible(card, from_agent, caller_owner_id, caller_org_id)

    def to_handoff_tools(
        self,
        caller_agent_id: str = "",
        caller_owner_id: str = "",
        caller_org_id: str = "",
    ) -> List[ToolDef]:
        """Generate transfer_to_xxx ToolDefs for all permitted agents.

        These are injected into an Agent's ToolRegistry so the LLM can
        decide to handoff via tool calling.
        """
        tools = []
        for rt in self._agents.values():
            card = rt.card
            if card.handoff_policy == "deny":
                continue
            if card.agent_id == caller_agent_id:
                continue  # no self-handoff
            if not self._is_visible(card, caller_agent_id, caller_owner_id, caller_org_id):
                continue

            tool_def = ToolDef(
                name=f"transfer_to_{card.agent_id}",
                description=f"Transfer conversation to {card.name}: {card.description}",
                parameters=[
                    ToolParam(
                        name="reason",
                        type="string",
                        description="Why you are transferring to this agent",
                        required=True,
                    ),
                ],
            )
            tools.append(tool_def)
        return tools

    # ─── Internal ───

    def _is_visible(
        self,
        card: AgentCardPublic,
        caller_agent_id: str,
        caller_owner_id: str,
        caller_org_id: str,
    ) -> bool:
        """Check if card is visible to the caller."""
        if card.visibility == "public":
            return True
        if card.visibility == "org":
            return bool(card.org_id and caller_org_id and card.org_id == caller_org_id)
        # private: same owner only
        return bool(caller_owner_id and card.owner_id == caller_owner_id)
