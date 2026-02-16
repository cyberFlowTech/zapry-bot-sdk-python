"""
HandoffPolicy — 权限/隔离/循环防护/幂等缓存。
"""

from __future__ import annotations

import asyncio
import logging
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional

from zapry_agents_sdk.agent.card import AgentCardPublic
from zapry_agents_sdk.agent.handoff import HandoffError, HandoffRequest, HandoffResult

logger = logging.getLogger("zapry_agents_sdk.agent")


# ──────────────────────────────────────────────
# HandoffPolicy
# ──────────────────────────────────────────────

@dataclass
class HandoffPolicy:
    """权限检查 + 循环防护规则。"""

    max_hop_count: int = 3
    default_timeout_ms: int = 30000
    allow_cross_owner: bool = False

    def check_access(
        self, request: HandoffRequest, target: AgentCardPublic
    ) -> Optional[HandoffError]:
        """权限检查流水线。返回 None 表示通过。"""

        # 1. handoff_policy == "deny"
        if target.handoff_policy == "deny":
            return HandoffError(code="NOT_ALLOWED", message=f"Agent {target.agent_id} denies handoff")

        # 2. safety_level == "high" + tool_based → block
        if target.safety_level == "high" and request.requested_mode == "tool_based":
            return HandoffError(
                code="SAFETY_BLOCK",
                message=f"Agent {target.agent_id} (safety=high) requires coordinator mode",
            )

        # 3. coordinator_only check
        if target.handoff_policy == "coordinator_only" and request.requested_mode == "tool_based":
            return HandoffError(
                code="NOT_ALLOWED",
                message=f"Agent {target.agent_id} only accepts coordinator handoff",
            )

        # 4. visibility
        if target.visibility == "private":
            if request.caller_owner_id != target.owner_id:
                return HandoffError(code="NOT_ALLOWED", message="Private agent: owner mismatch")
        elif target.visibility == "org":
            if not target.org_id or request.caller_org_id != target.org_id:
                return HandoffError(code="NOT_ALLOWED", message="Org agent: org_id mismatch")

        # 5. allowed_caller_agents whitelist
        if target.allowed_caller_agents:
            if request.from_agent not in target.allowed_caller_agents:
                return HandoffError(code="NOT_ALLOWED", message="Caller agent not in whitelist")

        # 6. allowed_caller_owners whitelist
        if target.allowed_caller_owners:
            if request.caller_owner_id not in target.allowed_caller_owners:
                return HandoffError(code="NOT_ALLOWED", message="Caller owner not in whitelist")

        # 7. cross-owner check
        if not self.allow_cross_owner:
            if request.caller_owner_id and target.owner_id:
                if request.caller_owner_id != target.owner_id:
                    return HandoffError(code="NOT_ALLOWED", message="Cross-owner handoff disabled")

        return None

    def check_loop(self, request: HandoffRequest) -> Optional[HandoffError]:
        """循环防护（基于下一跳状态校验）。"""
        next_hop = request.hop_count + 1
        if next_hop > self.max_hop_count:
            return HandoffError(
                code="LOOP_DETECTED",
                message=f"Max hop count exceeded: {next_hop} > {self.max_hop_count}",
            )
        if request.to_agent in request.visited_agents:
            return HandoffError(
                code="LOOP_DETECTED",
                message=f"Agent {request.to_agent} already visited: {request.visited_agents}",
            )
        return None


# ──────────────────────────────────────────────
# IdempotencyCache (singleflight)
# ──────────────────────────────────────────────

class IdempotencyCache:
    """幂等缓存：同 request_id 至多一次执行（singleflight 语义）。"""

    def __init__(self, ttl_seconds: int = 86400) -> None:
        self._ttl = ttl_seconds
        self._cache: Dict[str, tuple] = {}  # request_id -> (result, timestamp)
        self._inflight: Dict[str, asyncio.Future] = {}  # singleflight
        self._lock = threading.Lock()

    async def get_or_execute(
        self,
        request_id: str,
        execute_fn: Callable[[], Awaitable[HandoffResult]],
    ) -> HandoffResult:
        """Singleflight: 同 request_id 并发只执行一次。"""
        if not request_id:
            return await execute_fn()

        # Check cache
        with self._lock:
            self._cleanup()
            if request_id in self._cache:
                result, _ = self._cache[request_id]
                # Return a copy with cache_hit set
                import copy
                cached = copy.copy(result)
                cached.cache_hit = True
                return cached

        # Execute (no singleflight lock for simplicity in v1; full singleflight in v2)
        result = await execute_fn()
        with self._lock:
            self._cache[request_id] = (result, time.time())
        return result

    def _cleanup(self) -> None:
        """Remove expired entries."""
        now = time.time()
        expired = [k for k, (_, ts) in self._cache.items() if now - ts > self._ttl]
        for k in expired:
            del self._cache[k]
