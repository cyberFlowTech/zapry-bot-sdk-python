"""
Memory 持久化框架全量测试。
"""

import json
import pytest

from zapry_bot_sdk.memory.types import Message, MemoryContext, DEFAULT_MEMORY_SCHEMA
from zapry_bot_sdk.memory.store import InMemoryStore
from zapry_bot_sdk.memory.store_sqlite import SQLiteMemoryStore
from zapry_bot_sdk.memory.working import WorkingMemory
from zapry_bot_sdk.memory.short_term import ShortTermMemory
from zapry_bot_sdk.memory.long_term import LongTermMemory
from zapry_bot_sdk.memory.buffer import ConversationBuffer
from zapry_bot_sdk.memory.extractor import LLMMemoryExtractor, _parse_json_response
from zapry_bot_sdk.memory.formatter import format_memory_for_prompt
from zapry_bot_sdk.memory.session import MemorySession


# ══════════════════════════════════════════════
# Message
# ══════════════════════════════════════════════

class TestMessage:
    def test_create(self):
        m = Message(role="user", content="hello")
        assert m.role == "user"
        assert m.content == "hello"
        assert m.timestamp != ""

    def test_to_dict(self):
        m = Message(role="assistant", content="hi", timestamp="2026-01-01")
        d = m.to_dict()
        assert d["role"] == "assistant"
        assert d["timestamp"] == "2026-01-01"

    def test_from_dict(self):
        m = Message.from_dict({"role": "user", "content": "test"})
        assert m.role == "user"


# ══════════════════════════════════════════════
# InMemoryStore
# ══════════════════════════════════════════════

class TestInMemoryStore:
    @pytest.fixture
    def store(self):
        return InMemoryStore()

    @pytest.mark.asyncio
    async def test_kv_get_set(self, store):
        await store.set("ns", "key1", "val1")
        assert await store.get("ns", "key1") == "val1"
        assert await store.get("ns", "missing") is None

    @pytest.mark.asyncio
    async def test_kv_delete(self, store):
        await store.set("ns", "k", "v")
        await store.delete("ns", "k")
        assert await store.get("ns", "k") is None

    @pytest.mark.asyncio
    async def test_list_append_get(self, store):
        await store.append("ns", "list1", "a")
        await store.append("ns", "list1", "b")
        result = await store.get_list("ns", "list1")
        assert result == ["a", "b"]

    @pytest.mark.asyncio
    async def test_list_limit(self, store):
        for i in range(10):
            await store.append("ns", "l", str(i))
        result = await store.get_list("ns", "l", limit=3)
        assert result == ["0", "1", "2"]

    @pytest.mark.asyncio
    async def test_list_trim(self, store):
        for i in range(10):
            await store.append("ns", "l", str(i))
        await store.trim_list("ns", "l", 3)
        result = await store.get_list("ns", "l")
        assert result == ["7", "8", "9"]

    @pytest.mark.asyncio
    async def test_list_clear(self, store):
        await store.append("ns", "l", "a")
        await store.clear_list("ns", "l")
        assert await store.list_length("ns", "l") == 0

    @pytest.mark.asyncio
    async def test_list_keys(self, store):
        await store.set("ns", "k1", "v")
        await store.append("ns", "l1", "v")
        keys = await store.list_keys("ns")
        assert "k1" in keys
        assert "l1" in keys


# ══════════════════════════════════════════════
# SQLiteMemoryStore
# ══════════════════════════════════════════════

class TestSQLiteMemoryStore:
    @pytest.fixture
    def store(self):
        s = SQLiteMemoryStore(":memory:")
        yield s
        s.close()

    @pytest.mark.asyncio
    async def test_kv_roundtrip(self, store):
        await store.set("ns", "k", "v")
        assert await store.get("ns", "k") == "v"

    @pytest.mark.asyncio
    async def test_kv_overwrite(self, store):
        await store.set("ns", "k", "v1")
        await store.set("ns", "k", "v2")
        assert await store.get("ns", "k") == "v2"

    @pytest.mark.asyncio
    async def test_kv_delete(self, store):
        await store.set("ns", "k", "v")
        await store.delete("ns", "k")
        assert await store.get("ns", "k") is None

    @pytest.mark.asyncio
    async def test_list_append_get(self, store):
        await store.append("ns", "l", "a")
        await store.append("ns", "l", "b")
        assert await store.get_list("ns", "l") == ["a", "b"]

    @pytest.mark.asyncio
    async def test_list_trim(self, store):
        for i in range(10):
            await store.append("ns", "l", str(i))
        await store.trim_list("ns", "l", 3)
        result = await store.get_list("ns", "l")
        assert len(result) == 3
        assert result == ["7", "8", "9"]

    @pytest.mark.asyncio
    async def test_list_clear(self, store):
        await store.append("ns", "l", "x")
        await store.clear_list("ns", "l")
        assert await store.list_length("ns", "l") == 0

    @pytest.mark.asyncio
    async def test_namespace_isolation(self, store):
        await store.set("agent1:user1", "k", "v1")
        await store.set("agent2:user1", "k", "v2")
        assert await store.get("agent1:user1", "k") == "v1"
        assert await store.get("agent2:user1", "k") == "v2"


# ══════════════════════════════════════════════
# WorkingMemory
# ══════════════════════════════════════════════

class TestWorkingMemory:
    def test_get_set(self):
        wm = WorkingMemory()
        wm.set("intent", "tarot")
        assert wm.get("intent") == "tarot"
        assert wm.get("missing", "default") == "default"

    def test_clear(self):
        wm = WorkingMemory()
        wm.set("a", 1)
        wm.clear()
        assert len(wm) == 0

    def test_to_dict(self):
        wm = WorkingMemory()
        wm.set("x", 1)
        assert wm.to_dict() == {"x": 1}


# ══════════════════════════════════════════════
# ShortTermMemory
# ══════════════════════════════════════════════

class TestShortTermMemory:
    @pytest.fixture
    def stm(self):
        return ShortTermMemory(InMemoryStore(), "test:user1", max_messages=5)

    @pytest.mark.asyncio
    async def test_add_and_get(self, stm):
        await stm.add_message("user", "hello")
        await stm.add_message("assistant", "hi")
        msgs = await stm.get_history()
        assert len(msgs) == 2
        assert msgs[0].role == "user"

    @pytest.mark.asyncio
    async def test_auto_trim(self, stm):
        for i in range(10):
            await stm.add_message("user", f"msg{i}")
        msgs = await stm.get_history()
        assert len(msgs) == 5
        assert msgs[0].content == "msg5"  # oldest remaining

    @pytest.mark.asyncio
    async def test_clear(self, stm):
        await stm.add_message("user", "x")
        await stm.clear()
        assert await stm.count() == 0

    @pytest.mark.asyncio
    async def test_get_history_dicts(self, stm):
        await stm.add_message("user", "hi")
        dicts = await stm.get_history_dicts()
        assert dicts[0]["role"] == "user"
        assert "timestamp" not in dicts[0]


# ══════════════════════════════════════════════
# LongTermMemory
# ══════════════════════════════════════════════

class TestLongTermMemory:
    @pytest.fixture
    def ltm(self):
        return LongTermMemory(InMemoryStore(), "test:user1", cache_ttl=0)

    @pytest.mark.asyncio
    async def test_get_default(self, ltm):
        data = await ltm.get()
        assert "basic_info" in data
        assert "meta" in data

    @pytest.mark.asyncio
    async def test_save_and_get(self, ltm):
        await ltm.save({"custom": "data", "meta": {}})
        data = await ltm.get()
        assert data["custom"] == "data"

    @pytest.mark.asyncio
    async def test_deep_merge_update(self, ltm):
        await ltm.save({"basic_info": {"age": 25}, "interests": ["coding"], "meta": {}})
        result = await ltm.update({"basic_info": {"location": "Shanghai"}, "interests": ["music"]})
        assert result["basic_info"]["age"] == 25
        assert result["basic_info"]["location"] == "Shanghai"
        assert "coding" in result["interests"]
        assert "music" in result["interests"]

    @pytest.mark.asyncio
    async def test_update_increments_count(self, ltm):
        await ltm.save({"meta": {"conversation_count": 5}})
        result = await ltm.update({"summary": "test"})
        assert result["meta"]["conversation_count"] == 6

    @pytest.mark.asyncio
    async def test_delete(self, ltm):
        await ltm.save({"custom": "data", "meta": {}})
        await ltm.delete()
        data = await ltm.get()
        assert "custom" not in data  # back to default

    @pytest.mark.asyncio
    async def test_cache(self):
        ltm = LongTermMemory(InMemoryStore(), "test:user1", cache_ttl=300)
        await ltm.save({"val": 1, "meta": {}})
        data1 = await ltm.get()
        assert data1["val"] == 1
        # Cache should be hit
        assert ltm._cache is not None


# ══════════════════════════════════════════════
# ConversationBuffer
# ══════════════════════════════════════════════

class TestConversationBuffer:
    @pytest.fixture
    def buf(self):
        return ConversationBuffer(InMemoryStore(), "test:user1", trigger_count=3)

    @pytest.mark.asyncio
    async def test_add_and_count(self, buf):
        await buf.add("user", "hello")
        await buf.add("assistant", "hi")
        assert await buf.count() == 2

    @pytest.mark.asyncio
    async def test_should_extract_by_count(self, buf):
        await buf.add("user", "a")
        await buf.add("user", "b")
        assert await buf.should_extract() is True  # first time, no meta → trigger
        await buf.get_and_clear()
        await buf.add("user", "c")
        assert await buf.should_extract() is False  # only 1, not enough
        await buf.add("user", "d")
        await buf.add("user", "e")
        assert await buf.should_extract() is True  # 3 >= trigger_count

    @pytest.mark.asyncio
    async def test_get_and_clear(self, buf):
        await buf.add("user", "hello")
        await buf.add("assistant", "hi")
        messages = await buf.get_and_clear()
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert await buf.count() == 0

    @pytest.mark.asyncio
    async def test_empty_should_not_extract(self, buf):
        assert await buf.should_extract() is False


# ══════════════════════════════════════════════
# MemoryExtractor
# ══════════════════════════════════════════════

class TestMemoryExtractor:
    def test_parse_json_response_clean(self):
        result = _parse_json_response('{"basic_info": {"age": 25}}')
        assert result["basic_info"]["age"] == 25

    def test_parse_json_response_code_block(self):
        result = _parse_json_response('```json\n{"key": "val"}\n```')
        assert result["key"] == "val"

    def test_parse_json_response_with_prefix(self):
        result = _parse_json_response('Here is the result: {"a": 1}')
        assert result["a"] == 1

    def test_parse_json_response_invalid(self):
        result = _parse_json_response("not json at all")
        assert result == {}

    @pytest.mark.asyncio
    async def test_llm_extractor(self):
        async def fake_llm(prompt):
            return '{"basic_info": {"age": 30}, "interests": ["hiking"]}'

        extractor = LLMMemoryExtractor(llm_fn=fake_llm)
        result = await extractor.extract(
            [{"role": "user", "content": "I'm 30 and love hiking"}],
            {},
        )
        assert result["basic_info"]["age"] == 30
        assert "hiking" in result["interests"]

    @pytest.mark.asyncio
    async def test_llm_extractor_empty_conversations(self):
        async def fake_llm(prompt):
            return "{}"

        extractor = LLMMemoryExtractor(llm_fn=fake_llm)
        result = await extractor.extract([], {})
        assert result == {}

    @pytest.mark.asyncio
    async def test_llm_extractor_error_handling(self):
        async def bad_llm(prompt):
            raise RuntimeError("LLM down")

        extractor = LLMMemoryExtractor(llm_fn=bad_llm)
        result = await extractor.extract(
            [{"role": "user", "content": "test"}], {}
        )
        assert result == {}


# ══════════════════════════════════════════════
# MemoryFormatter
# ══════════════════════════════════════════════

class TestMemoryFormatter:
    def test_format_with_data(self):
        memory = {
            "basic_info": {"age": 25, "location": "Shanghai"},
            "interests": ["coding", "music"],
            "summary": "A young developer",
            "meta": {"conversation_count": 10},
        }
        result = format_memory_for_prompt(memory)
        assert result is not None
        assert "25" in result
        assert "Shanghai" in result
        assert "coding" in result

    def test_format_empty(self):
        result = format_memory_for_prompt({})
        assert result is None

    def test_format_custom_template(self):
        memory = {"basic_info": {"age": 25}, "meta": {}}
        result = format_memory_for_prompt(memory, template="USER: {long_term_text}")
        assert result.startswith("USER:")

    def test_format_with_working(self):
        result = format_memory_for_prompt(
            {"meta": {}},
            working={"current_intent": "tarot"},
        )
        assert result is not None
        assert "tarot" in result


# ══════════════════════════════════════════════
# MemorySession (integration)
# ══════════════════════════════════════════════

class TestMemorySession:
    @pytest.fixture
    def session(self):
        return MemorySession("agent1", "user1", InMemoryStore())

    @pytest.mark.asyncio
    async def test_load(self, session):
        ctx = await session.load()
        assert isinstance(ctx, MemoryContext)
        assert ctx.short_term == []
        assert "basic_info" in ctx.long_term

    @pytest.mark.asyncio
    async def test_add_message(self, session):
        await session.add_message("user", "hello")
        await session.add_message("assistant", "hi")
        ctx = await session.load()
        assert len(ctx.short_term) == 2

    @pytest.mark.asyncio
    async def test_namespace_isolation(self):
        store = InMemoryStore()
        s1 = MemorySession("agent1", "user1", store)
        s2 = MemorySession("agent2", "user1", store)
        await s1.add_message("user", "from agent1")
        await s2.add_message("user", "from agent2")
        ctx1 = await s1.load()
        ctx2 = await s2.load()
        assert len(ctx1.short_term) == 1
        assert len(ctx2.short_term) == 1
        assert ctx1.short_term[0].content == "from agent1"

    @pytest.mark.asyncio
    async def test_format_for_prompt(self, session):
        await session.load()
        await session.update_long_term({
            "basic_info": {"age": 25},
            "interests": ["coding"],
        })
        prompt = session.format_for_prompt()
        assert prompt is not None
        assert "25" in prompt

    @pytest.mark.asyncio
    async def test_extract_if_needed_no_extractor(self, session):
        result = await session.extract_if_needed()
        assert result is None

    @pytest.mark.asyncio
    async def test_extract_if_needed_with_extractor(self):
        async def fake_llm(prompt):
            return '{"basic_info": {"age": 30}}'

        store = InMemoryStore()
        session = MemorySession(
            "agent1", "user1", store,
            extractor=LLMMemoryExtractor(llm_fn=fake_llm),
            trigger_count=2,
        )
        await session.add_message("user", "I'm 30")
        await session.add_message("assistant", "Got it")
        result = await session.extract_if_needed()
        assert result is not None
        assert result["basic_info"]["age"] == 30

        # Verify merged into long-term
        lt = await session.long_term.get()
        assert lt["basic_info"]["age"] == 30

    @pytest.mark.asyncio
    async def test_clear_history(self, session):
        await session.add_message("user", "x")
        await session.clear_history()
        ctx = await session.load()
        assert len(ctx.short_term) == 0

    @pytest.mark.asyncio
    async def test_clear_all(self, session):
        await session.add_message("user", "x")
        await session.update_long_term({"interests": ["test"]})
        session.working.set("k", "v")
        await session.clear_all()
        ctx = await session.load()
        assert len(ctx.short_term) == 0
        assert ctx.long_term.get("interests") == []  # back to default
        assert len(session.working) == 0

    @pytest.mark.asyncio
    async def test_working_memory(self, session):
        session.working.set("intent", "tarot")
        assert session.working.get("intent") == "tarot"

    @pytest.mark.asyncio
    async def test_update_long_term(self, session):
        result = await session.update_long_term({"basic_info": {"age": 22}})
        assert result["basic_info"]["age"] == 22
        assert result["meta"]["conversation_count"] == 1
