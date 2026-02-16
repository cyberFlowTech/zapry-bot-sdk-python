"""
AgentCard — Agent 身份证（Public + Runtime 分层）。

AgentCardPublic: 可序列化，可上报到 Zapry 平台 discover。
AgentRuntime: 本地绑定 llm_fn/tool_registry 等运行时指针。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional


@dataclass
class AgentCardPublic:
    """可序列化的 Agent 元数据（可注册到 Zapry 平台）。"""

    agent_id: str
    name: str
    description: str = ""
    skills: List[str] = field(default_factory=list)
    owner_id: str = ""
    org_id: str = ""

    # --- 治理/权限 ---
    visibility: str = "private"  # "private" | "org" | "public"
    allowed_caller_agents: List[str] = field(default_factory=list)
    allowed_caller_owners: List[str] = field(default_factory=list)
    required_scopes: List[str] = field(default_factory=list)
    safety_level: str = "medium"  # "low" | "medium" | "high"
    handoff_policy: str = "auto"  # "auto" | "coordinator_only" | "deny"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "description": self.description,
            "skills": self.skills,
            "owner_id": self.owner_id,
            "org_id": self.org_id,
            "visibility": self.visibility,
            "safety_level": self.safety_level,
            "handoff_policy": self.handoff_policy,
        }

    def to_dict_admin(self) -> Dict[str, Any]:
        """Full dict including caller rules (for agents:admin scope)."""
        d = self.to_dict()
        d["allowed_caller_agents"] = self.allowed_caller_agents
        d["allowed_caller_owners"] = self.allowed_caller_owners
        d["required_scopes"] = self.required_scopes
        return d


@dataclass
class AgentRuntime:
    """本地运行时绑定（不可序列化，不上报到平台）。"""

    card: AgentCardPublic
    llm_fn: Optional[Any] = None
    tool_registry: Optional[Any] = None
    system_prompt: str = ""
    max_turns: int = 10
    input_filter: Optional[Any] = None
    guardrails: Optional[Any] = None
    tracer: Optional[Any] = None

    @property
    def agent_id(self) -> str:
        return self.card.agent_id

    @property
    def owner_id(self) -> str:
        return self.card.owner_id

    @property
    def org_id(self) -> str:
        return self.card.org_id
