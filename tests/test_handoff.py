"""
Multi-Agent Handoff 全量测试。
"""

import json
import pytest

from zapry_bot_sdk.agent.card import AgentCardPublic, AgentRuntime
from zapry_bot_sdk.agent.registry import AgentRegistry
from zapry_bot_sdk.agent.handoff import (
    HandoffMessage, HandoffError, HandoffContext, HandoffRequest, HandoffResult,
    last_n_messages, summary_only, allow_all, platform_redact,
)
from zapry_bot_sdk.agent.policy import HandoffPolicy, IdempotencyCache
from zapry_bot_sdk.agent.engine import HandoffEngine
from zapry_bot_sdk.agent.orchestrator import AgentOrchestrator, CoordinatorDecision
from zapry_bot_sdk.tools.registry import ToolRegistry


# ══════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════

def make_card(agent_id, owner_id="dev1", **kwargs):
    return AgentCardPublic(agent_id=agent_id, name=agent_id, owner_id=owner_id, **kwargs)

def make_runtime(agent_id, owner_id="dev1", response="Hello", **card_kwargs):
    card = make_card(agent_id, owner_id, **card_kwargs)
    async def llm_fn(messages, tools=None):
        return {"content": response, "tool_calls": None}
    return AgentRuntime(card=card, llm_fn=llm_fn, system_prompt="test")

def make_engine(runtimes, policy=None, platform_filter=None):
    reg = AgentRegistry()
    for rt in runtimes:
        reg.register(rt)
    return HandoffEngine(reg, policy=policy or HandoffPolicy(), platform_filter=platform_filter), reg


# ══════════════════════════════════════════════
# AgentCardPublic
# ══════════════════════════════════════════════

class TestAgentCard:
    def test_to_dict(self):
        card = make_card("a1", description="desc", skills=["tarot"])
        d = card.to_dict()
        assert d["agent_id"] == "a1"
        assert "allowed_caller_agents" not in d

    def test_to_dict_admin(self):
        card = make_card("a1", allowed_caller_agents=["b1"])
        d = card.to_dict_admin()
        assert "allowed_caller_agents" in d
        assert d["allowed_caller_agents"] == ["b1"]


# ══════════════════════════════════════════════
# AgentRegistry
# ══════════════════════════════════════════════

class TestAgentRegistry:
    def test_register_get(self):
        reg = AgentRegistry()
        rt = make_runtime("a1")
        reg.register(rt)
        assert reg.get("a1") is rt
        assert reg.get("missing") is None

    def test_find_by_skill_visibility(self):
        reg = AgentRegistry()
        reg.register(make_runtime("pub", skills=["tarot"], visibility="public"))
        reg.register(make_runtime("priv", owner_id="dev2", skills=["tarot"], visibility="private"))

        found = reg.find_by_skill("tarot", caller_owner_id="dev1")
        ids = [r.agent_id for r in found]
        assert "pub" in ids
        assert "priv" not in ids  # different owner, private

    def test_find_by_skill_org(self):
        reg = AgentRegistry()
        reg.register(make_runtime("org_agent", org_id="org1", skills=["x"], visibility="org"))
        found = reg.find_by_skill("x", caller_org_id="org1")
        assert len(found) == 1
        found2 = reg.find_by_skill("x", caller_org_id="org2")
        assert len(found2) == 0

    def test_can_handoff(self):
        reg = AgentRegistry()
        reg.register(make_runtime("target", visibility="public"))
        assert reg.can_handoff("caller", "target") is True
        assert reg.can_handoff("caller", "missing") is False

    def test_to_handoff_tools(self):
        reg = AgentRegistry()
        reg.register(make_runtime("tarot", visibility="public", description="Tarot expert", skills=["tarot"]))
        reg.register(make_runtime("psych", visibility="public", description="Psychologist", skills=["psych"]))
        tools = reg.to_handoff_tools(caller_agent_id="receptionist")
        names = [t.name for t in tools]
        assert "transfer_to_tarot" in names
        assert "transfer_to_psych" in names

    def test_to_handoff_tools_excludes_deny(self):
        reg = AgentRegistry()
        reg.register(make_runtime("blocked", visibility="public", handoff_policy="deny"))
        tools = reg.to_handoff_tools()
        assert len(tools) == 0

    def test_to_handoff_tools_no_self_handoff(self):
        reg = AgentRegistry()
        reg.register(make_runtime("self_agent", visibility="public"))
        tools = reg.to_handoff_tools(caller_agent_id="self_agent")
        assert len(tools) == 0


# ══════════════════════════════════════════════
# HandoffPolicy
# ══════════════════════════════════════════════

class TestHandoffPolicy:
    def test_check_access_deny(self):
        policy = HandoffPolicy()
        card = make_card("a1", handoff_policy="deny")
        err = policy.check_access(HandoffRequest(to_agent="a1"), card)
        assert err is not None
        assert err.code == "NOT_ALLOWED"

    def test_check_access_safety_block(self):
        policy = HandoffPolicy()
        card = make_card("a1", safety_level="high")
        req = HandoffRequest(to_agent="a1", requested_mode="tool_based")
        err = policy.check_access(req, card)
        assert err is not None
        assert err.code == "SAFETY_BLOCK"

    def test_check_access_coordinator_only(self):
        policy = HandoffPolicy()
        card = make_card("a1", handoff_policy="coordinator_only")
        req = HandoffRequest(to_agent="a1", requested_mode="tool_based")
        err = policy.check_access(req, card)
        assert err is not None

    def test_check_access_private_same_owner(self):
        policy = HandoffPolicy()
        card = make_card("a1", visibility="private")
        req = HandoffRequest(to_agent="a1", caller_owner_id="dev1")
        err = policy.check_access(req, card)
        assert err is None

    def test_check_access_private_diff_owner(self):
        policy = HandoffPolicy()
        card = make_card("a1", visibility="private")
        req = HandoffRequest(to_agent="a1", caller_owner_id="dev2")
        err = policy.check_access(req, card)
        assert err is not None

    def test_check_access_caller_agent_whitelist(self):
        policy = HandoffPolicy()
        card = make_card("a1", visibility="public", allowed_caller_agents=["allowed_agent"])
        req = HandoffRequest(from_agent="blocked_agent", to_agent="a1")
        err = policy.check_access(req, card)
        assert err is not None
        req2 = HandoffRequest(from_agent="allowed_agent", to_agent="a1")
        err2 = policy.check_access(req2, card)
        assert err2 is None

    def test_check_access_cross_owner_blocked(self):
        policy = HandoffPolicy(allow_cross_owner=False)
        card = make_card("a1", visibility="public", owner_id="dev2")
        req = HandoffRequest(to_agent="a1", caller_owner_id="dev1")
        err = policy.check_access(req, card)
        assert err is not None

    def test_check_access_cross_owner_allowed(self):
        policy = HandoffPolicy(allow_cross_owner=True)
        card = make_card("a1", visibility="public", owner_id="dev2")
        req = HandoffRequest(to_agent="a1", caller_owner_id="dev1")
        err = policy.check_access(req, card)
        assert err is None

    def test_check_loop_ok(self):
        policy = HandoffPolicy(max_hop_count=3)
        req = HandoffRequest(to_agent="b", hop_count=1, visited_agents=["a"])
        err = policy.check_loop(req)
        assert err is None

    def test_check_loop_max_hops(self):
        policy = HandoffPolicy(max_hop_count=2)
        req = HandoffRequest(to_agent="c", hop_count=2, visited_agents=["a", "b"])
        err = policy.check_loop(req)
        assert err is not None
        assert err.code == "LOOP_DETECTED"

    def test_check_loop_revisit(self):
        policy = HandoffPolicy()
        req = HandoffRequest(to_agent="a", hop_count=1, visited_agents=["a", "b"])
        err = policy.check_loop(req)
        assert err is not None
        assert err.code == "LOOP_DETECTED"


# ══════════════════════════════════════════════
# HandoffEngine
# ══════════════════════════════════════════════

class TestHandoffEngine:
    @pytest.mark.asyncio
    async def test_basic_handoff(self):
        engine, _ = make_engine([make_runtime("target", visibility="public", response="I'm the target")])
        req = HandoffRequest(from_agent="caller", to_agent="target", reason="test", caller_owner_id="dev1")
        result = await engine.handoff(req)
        assert result.status == "success"
        assert "target" in result.output.lower() or result.output != ""

    @pytest.mark.asyncio
    async def test_not_found(self):
        engine, _ = make_engine([])
        req = HandoffRequest(to_agent="missing")
        result = await engine.handoff(req)
        assert result.error.code == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_permission_denied(self):
        engine, _ = make_engine([make_runtime("priv", visibility="private", owner_id="dev2")])
        req = HandoffRequest(to_agent="priv", caller_owner_id="dev1")
        result = await engine.handoff(req)
        assert result.error is not None
        assert result.error.code == "NOT_ALLOWED"

    @pytest.mark.asyncio
    async def test_loop_detected(self):
        engine, _ = make_engine(
            [make_runtime("a", visibility="public")],
            policy=HandoffPolicy(max_hop_count=2),
        )
        req = HandoffRequest(to_agent="a", hop_count=2, visited_agents=["x", "y"], caller_owner_id="dev1")
        result = await engine.handoff(req)
        assert result.error.code == "LOOP_DETECTED"

    @pytest.mark.asyncio
    async def test_timeout(self):
        async def slow_llm(messages, tools=None):
            import asyncio
            await asyncio.sleep(10)
            return {"content": "slow", "tool_calls": None}

        card = make_card("slow", visibility="public")
        rt = AgentRuntime(card=card, llm_fn=slow_llm, system_prompt="test")
        engine, _ = make_engine([rt])
        req = HandoffRequest(to_agent="slow", deadline_ms=100, caller_owner_id="dev1")
        result = await engine.handoff(req)
        assert result.error.code == "TIMEOUT"

    @pytest.mark.asyncio
    async def test_platform_filter_runs_first(self):
        """Platform filter should redact before target filter."""
        filter_order = []

        async def pf(ctx):
            filter_order.append("platform")
            return ctx

        card = make_card("a", visibility="public")
        async def tf(ctx):
            filter_order.append("target")
            return ctx
        rt = AgentRuntime(card=card, llm_fn=make_runtime("a").llm_fn, input_filter=tf, system_prompt="t")
        engine, _ = make_engine([rt], platform_filter=pf)
        req = HandoffRequest(to_agent="a", caller_owner_id="dev1")
        await engine.handoff(req)
        assert filter_order == ["platform", "target"]


# ══════════════════════════════════════════════
# InputFilter
# ══════════════════════════════════════════════

class TestInputFilter:
    @pytest.mark.asyncio
    async def test_last_n(self):
        ctx = HandoffContext(messages=[
            HandoffMessage(role="user", content="1"),
            HandoffMessage(role="assistant", content="2"),
            HandoffMessage(role="user", content="3"),
        ])
        f = last_n_messages(2)
        result = await f(ctx)
        assert len(result.messages) == 2

    @pytest.mark.asyncio
    async def test_summary_only(self):
        ctx = HandoffContext(messages=[HandoffMessage(role="user", content="x")], memory_summary="summary")
        f = summary_only()
        result = await f(ctx)
        assert len(result.messages) == 0
        assert result.memory_summary == "summary"

    @pytest.mark.asyncio
    async def test_platform_redact(self):
        ctx = HandoffContext(messages=[HandoffMessage(role="user", content="My phone is 13812345678")])
        f = platform_redact([r"\d{11}"])
        result = await f(ctx)
        assert "[REDACTED]" in result.messages[0].content
        assert len(result.redaction_report) > 0


# ══════════════════════════════════════════════
# HandoffResult return contract
# ══════════════════════════════════════════════

class TestReturnContract:
    def test_to_return_message(self):
        result = HandoffResult(output="Hello", agent_id="tarot", status="success", request_id="req1")
        msg = result.to_return_message(tool_call_id="tc1")
        assert msg["role"] == "tool"
        assert msg["name"] == "handoff_result"
        assert msg["tool_call_id"] == "tc1"
        content = json.loads(msg["content"])
        assert content["agent_id"] == "tarot"
        assert content["status"] == "success"


# ══════════════════════════════════════════════
# CoordinatorDecision
# ══════════════════════════════════════════════

class TestCoordinatorDecision:
    def test_from_json(self):
        d = CoordinatorDecision.from_json('{"selected_agents": ["tarot"], "reason": "user wants tarot", "confidence": 0.9}')
        assert d.selected_agents == ["tarot"]
        assert d.reason == "user wants tarot"
        assert d.confidence == 0.9

    def test_from_json_code_block(self):
        d = CoordinatorDecision.from_json('```json\n{"selected_agents": ["a"]}\n```')
        assert d.selected_agents == ["a"]

    def test_from_json_invalid(self):
        d = CoordinatorDecision.from_json("not json")
        assert d.selected_agents == []

    def test_from_json_with_prefix(self):
        d = CoordinatorDecision.from_json('Here is my decision: {"selected_agents": ["b"]}')
        assert d.selected_agents == ["b"]


# ══════════════════════════════════════════════
# AgentOrchestrator — tool_based mode
# ══════════════════════════════════════════════

class TestOrchestratorToolBased:
    @pytest.mark.asyncio
    async def test_basic_flow(self):
        """Entry agent directly answers without handoff."""
        reg = AgentRegistry()
        entry = make_runtime("entry", visibility="public", response="Direct answer")
        reg.register(entry)
        engine = HandoffEngine(reg, HandoffPolicy(allow_cross_owner=True))
        orch = AgentOrchestrator(reg, engine, mode="tool_based", entry_agent_id="entry")
        result = await orch.run("hello", owner_id="dev1")
        assert "Direct answer" in result.output or result.status == "completed"

    @pytest.mark.asyncio
    async def test_entry_not_found(self):
        reg = AgentRegistry()
        engine = HandoffEngine(reg)
        orch = AgentOrchestrator(reg, engine, mode="tool_based", entry_agent_id="missing")
        result = await orch.run("hello")
        assert result.status == "error"


# ══════════════════════════════════════════════
# AgentOrchestrator — coordinator mode
# ══════════════════════════════════════════════

class TestOrchestratorCoordinator:
    @pytest.mark.asyncio
    async def test_coordinator_routes_to_agent(self):
        reg = AgentRegistry()
        reg.register(make_runtime("tarot", visibility="public", response="Your card is...", skills=["tarot"], description="Tarot"))

        async def coord_llm(messages, tools=None):
            return {"content": json.dumps({"selected_agents": ["tarot"], "reason": "user wants tarot"})}

        engine = HandoffEngine(reg, HandoffPolicy(allow_cross_owner=True))
        orch = AgentOrchestrator(reg, engine, mode="coordinator", coordinator_llm_fn=coord_llm)
        result = await orch.run("帮我看看塔罗", owner_id="dev1")
        assert result.status == "success"
        assert result.output != ""

    @pytest.mark.asyncio
    async def test_coordinator_fallback(self):
        reg = AgentRegistry()

        async def coord_llm(messages, tools=None):
            return {"content": json.dumps({"selected_agents": [], "fallback_response": "I cannot help"})}

        engine = HandoffEngine(reg)
        orch = AgentOrchestrator(reg, engine, mode="coordinator", coordinator_llm_fn=coord_llm)
        result = await orch.run("random")
        assert "cannot help" in result.output.lower()

    @pytest.mark.asyncio
    async def test_coordinator_no_llm(self):
        reg = AgentRegistry()
        engine = HandoffEngine(reg)
        orch = AgentOrchestrator(reg, engine, mode="coordinator")
        result = await orch.run("test")
        assert result.status == "error"


# ══════════════════════════════════════════════
# IdempotencyCache
# ══════════════════════════════════════════════

class TestIdempotencyCache:
    @pytest.mark.asyncio
    async def test_cache_hit(self):
        cache = IdempotencyCache(ttl_seconds=60)
        call_count = 0

        async def execute():
            nonlocal call_count
            call_count += 1
            return HandoffResult(output="result1", request_id="r1")

        r1 = await cache.get_or_execute("r1", execute)
        r2 = await cache.get_or_execute("r1", execute)
        assert call_count == 1
        assert r2.cache_hit is True

    @pytest.mark.asyncio
    async def test_no_request_id(self):
        cache = IdempotencyCache()
        count = 0

        async def execute():
            nonlocal count
            count += 1
            return HandoffResult(output="ok")

        await cache.get_or_execute("", execute)
        await cache.get_or_execute("", execute)
        assert count == 2  # no caching without request_id
