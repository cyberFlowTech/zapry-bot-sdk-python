"""Natural Conversation 全量测试。"""

import asyncio
import json
import pytest
from datetime import datetime, timedelta, timezone

from zapry_agents_sdk.memory.session import MemorySession
from zapry_agents_sdk.memory.store import InMemoryStore
from zapry_agents_sdk.agent.loop import AgentLoop, AgentResult
from zapry_agents_sdk.tools.registry import ToolRegistry

from zapry_agents_sdk.natural.prompt_fragments import PromptFragments
from zapry_agents_sdk.natural.conversation_state import ConversationState, ConversationStateTracker
from zapry_agents_sdk.natural.emotional_tone import EmotionalTone, EmotionalToneDetector
from zapry_agents_sdk.natural.response_style import StyleConfig, ResponseStyleController, NATURAL_ENDINGS
from zapry_agents_sdk.natural.conversation_opener import OpenerConfig, OpenerGenerator
from zapry_agents_sdk.natural.context_compressor import CompressorConfig, ContextCompressor
from zapry_agents_sdk.natural.natural_conversation import (
    NaturalConversationConfig,
    DefaultNaturalConversationConfig,
    NaturalConversation,
    NaturalAgentLoop,
)


def _new_session():
    store = InMemoryStore()
    return MemorySession(agent_id="test", user_id="u1", store=store)


# ══════════════════════════════════════════════
# ConversationStateTracker tests
# ══════════════════════════════════════════════


class TestConversationState:

    @pytest.mark.asyncio
    async def test_first_conversation(self):
        tracker = ConversationStateTracker("UTC")
        session = _new_session()
        now = datetime(2025, 6, 15, 10, 0, tzinfo=timezone.utc)
        state = await tracker.track(session, "hello", now)
        assert state.is_first_conversation
        assert state.days_since_last == -1
        assert state.turn_index == 1

    @pytest.mark.asyncio
    async def test_days_since_last(self):
        tracker = ConversationStateTracker("UTC")
        session = _new_session()
        now = datetime(2025, 6, 15, 10, 0, tzinfo=timezone.utc)
        await tracker.touch_session(session, now - timedelta(days=3))
        state = await tracker.track(session, "hello", now)
        assert state.days_since_last == 3
        assert not state.is_first_conversation

    @pytest.mark.asyncio
    async def test_is_followup(self):
        tracker = ConversationStateTracker("UTC")
        session = _new_session()
        now = datetime(2025, 6, 15, 10, 0, tzinfo=timezone.utc)
        s1 = await tracker.track(session, "hello", now)
        assert not s1.is_followup
        s2 = await tracker.track(session, "more", now + timedelta(seconds=30))
        assert s2.is_followup
        s3 = await tracker.track(session, "new topic", now + timedelta(seconds=120))
        assert not s3.is_followup

    @pytest.mark.asyncio
    async def test_turn_index(self):
        tracker = ConversationStateTracker("UTC")
        session = _new_session()
        now = datetime(2025, 6, 15, 10, 0, tzinfo=timezone.utc)
        for i in range(1, 6):
            state = await tracker.track(session, "msg", now + timedelta(seconds=i))
            assert state.turn_index == i

    @pytest.mark.asyncio
    async def test_time_of_day(self):
        tracker = ConversationStateTracker("UTC")
        cases = [(7, "morning"), (14, "afternoon"), (20, "evening"), (2, "late_night")]
        for hour, expected in cases:
            session = _new_session()
            now = datetime(2025, 6, 15, hour, 0, tzinfo=timezone.utc)
            state = await tracker.track(session, "test", now)
            assert state.time_of_day == expected, f"hour={hour}"

    @pytest.mark.asyncio
    async def test_to_kv_namespace(self):
        tracker = ConversationStateTracker("UTC")
        session = _new_session()
        state = await tracker.track(session, "test", datetime.now(timezone.utc))
        kv = state.to_kv()
        for key in kv:
            assert key.startswith("sdk."), f"key {key} missing sdk. prefix"

    @pytest.mark.asyncio
    async def test_user_msg_length(self):
        tracker = ConversationStateTracker("UTC")
        session = _new_session()
        now = datetime(2025, 6, 15, 10, 0, tzinfo=timezone.utc)
        s1 = await tracker.track(session, "hi", now)
        assert s1.user_msg_length == "short"
        session2 = _new_session()
        s2 = await tracker.track(session2, "x" * 50, now)
        assert s2.user_msg_length == "medium"
        session3 = _new_session()
        s3 = await tracker.track(session3, "x" * 200, now)
        assert s3.user_msg_length == "long"


# ══════════════════════════════════════════════
# EmotionalToneDetector tests
# ══════════════════════════════════════════════


class TestEmotionalTone:

    def test_anxious_chinese(self):
        d = EmotionalToneDetector()
        tone = d.detect("快点给我看看结果")
        assert tone.tone == "anxious"
        assert tone.confidence >= 0.3

    def test_angry_strong_word(self):
        d = EmotionalToneDetector()
        tone = d.detect("什么破东西能不能正常点")
        assert tone.tone == "angry"
        assert tone.confidence >= 0.5

    def test_happy_low_weight(self):
        d = EmotionalToneDetector()
        tone = d.detect("哈哈")
        assert tone.tone == "happy"

    def test_happy_multi_hit(self):
        d = EmotionalToneDetector()
        tone = d.detect("太好了哈哈真棒")
        assert tone.tone == "happy"
        assert tone.confidence >= 0.6

    def test_english(self):
        d = EmotionalToneDetector()
        tone = d.detect("I need this ASAP please hurry")
        assert tone.tone == "anxious"

    def test_followup_boost(self):
        d = EmotionalToneDetector()

        class FakeState:
            is_followup = True
            user_msg_length = "short"

        tone = d.detect("急", FakeState())
        assert tone.scores["anxious"] >= 0.5

    def test_neutral_no_output(self):
        d = EmotionalToneDetector()
        tone = d.detect("今天天气怎么样")
        assert tone.format_for_prompt() == ""


# ══════════════════════════════════════════════
# ResponseStyleController tests
# ══════════════════════════════════════════════


class TestResponseStyle:

    def test_too_long_natural_ending(self):
        ctrl = ResponseStyleController(StyleConfig(max_length=30, min_preserve=10))
        long_text = "第一句话到这里结束。第二句话继续说下去。第三句话还在延伸。第四句话也很长呢。"
        result, changed, violations = ctrl.post_process(long_text)
        assert changed
        found_natural = any(result.endswith(e) for e in NATURAL_ENDINGS)
        assert found_natural, f"expected natural ending, got: {result}"
        assert any("truncated" in v for v in violations)

    def test_min_preserve_no_truncate(self):
        ctrl = ResponseStyleController(StyleConfig(max_length=30, min_preserve=40))
        short_text = "这是一段三十多个字的测试文本不应该被截断。"
        _, changed, _ = ctrl.post_process(short_text)
        assert not changed

    def test_forbidden_phrase_removed(self):
        ctrl = ResponseStyleController()
        result, changed, violations = ctrl.post_process("你好！作为一个AI，我来帮你。实际内容。")
        assert changed
        assert "作为一个AI" not in result
        assert len(violations) > 0

    def test_end_question_fixed(self):
        ctrl = ResponseStyleController(StyleConfig(end_style="no_question"))
        result, changed, _ = ctrl.post_process("这样可以吗？")
        assert changed
        assert result.strip().endswith("。")

    def test_normal_no_change(self):
        ctrl = ResponseStyleController(StyleConfig(max_length=500))
        result, changed, violations = ctrl.post_process("这是正常的回复。")
        assert not changed
        assert len(violations) == 0

    def test_warnings_recorded(self):
        ctrl = ResponseStyleController(StyleConfig(max_length=20, min_preserve=10))
        _, _, violations = ctrl.post_process("作为一个AI，这段话很长很长很长很长很长很长很长很长很长。")
        assert len(violations) > 0

    def test_build_style_prompt(self):
        ctrl = ResponseStyleController(StyleConfig(preferred_length=150, end_style="no_question"))
        prompt = ctrl.build_style_prompt()
        assert "150" in prompt


# ══════════════════════════════════════════════
# ConversationOpener tests
# ══════════════════════════════════════════════


class TestOpener:

    def test_first_meeting(self):
        g = OpenerGenerator()
        state = ConversationState(is_first_conversation=True, days_since_last=-1)
        s = g.generate(state, 0)
        assert s.situation == "first_meeting"

    def test_long_absence(self):
        g = OpenerGenerator()
        state = ConversationState(is_first_conversation=False, days_since_last=7, total_sessions=5)
        s = g.generate(state, 0)
        assert s.situation == "long_absence"

    def test_followup(self):
        g = OpenerGenerator()
        state = ConversationState(is_first_conversation=False, is_followup=True, days_since_last=0)
        s = g.generate(state, 0)
        assert s.situation == "followup"

    def test_late_night(self):
        g = OpenerGenerator()
        state = ConversationState(is_first_conversation=False, time_of_day="late_night", days_since_last=0)
        s = g.generate(state, 0)
        assert s.situation == "late_night"

    def test_frequency_limit(self):
        g = OpenerGenerator(OpenerConfig(max_mentions_per_session=1))
        state = ConversationState(is_first_conversation=True, days_since_last=-1)
        s1 = g.generate(state, 0)
        assert s1.situation == "first_meeting"
        s2 = g.generate(state, 1)
        assert s2.situation == "normal"

    def test_normal(self):
        g = OpenerGenerator()
        state = ConversationState(is_first_conversation=False, days_since_last=0, time_of_day="afternoon")
        s = g.generate(state, 0)
        assert s.situation == "normal"


# ══════════════════════════════════════════════
# ContextCompressor tests
# ══════════════════════════════════════════════


def _make_history(n):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"Message {i} content"} for i in range(n)]


class _FakeWorking:
    def __init__(self):
        self._data = {}

    def get(self, key):
        return self._data.get(key)

    def set(self, key, value):
        self._data[key] = value


class TestCompressor:

    @pytest.mark.asyncio
    async def test_below_threshold(self):
        called = False

        async def fn(msgs):
            nonlocal called
            called = True
            return "summary"

        comp = ContextCompressor(fn, CompressorConfig(token_threshold=99999))
        result = await comp.compress(_make_history(5), _FakeWorking())
        assert not called
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_above_threshold_summarized(self):
        async def fn(msgs):
            return "This is the summary."

        comp = ContextCompressor(fn, CompressorConfig(window_size=2, token_threshold=1))
        result = await comp.compress(_make_history(10), _FakeWorking())
        assert len(result) == 3  # 1 summary + 2 recent
        assert result[0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_summary_has_tag(self):
        async def fn(msgs):
            return "Summary."

        comp = ContextCompressor(fn, CompressorConfig(window_size=2, token_threshold=1, summary_version="v1"))
        result = await comp.compress(_make_history(10), _FakeWorking())
        assert result[0]["content"].startswith("[sdk.summary:v1]")

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        call_count = 0

        async def fn(msgs):
            nonlocal call_count
            call_count += 1
            return "cached"

        wm = _FakeWorking()
        comp = ContextCompressor(fn, CompressorConfig(window_size=2, token_threshold=1))
        await comp.compress(_make_history(10), wm)
        await comp.compress(_make_history(10), wm)
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_version_change_invalidates(self):
        call_count = 0

        async def fn(msgs):
            nonlocal call_count
            call_count += 1
            return "summary"

        wm = _FakeWorking()
        c1 = ContextCompressor(fn, CompressorConfig(window_size=2, token_threshold=1, summary_version="v1"))
        await c1.compress(_make_history(10), wm)
        c2 = ContextCompressor(fn, CompressorConfig(window_size=2, token_threshold=1, summary_version="v2"))
        await c2.compress(_make_history(10), wm)
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_custom_estimator(self):
        async def fn(msgs):
            return "summary"

        comp = ContextCompressor(fn, CompressorConfig(
            window_size=2, token_threshold=5000,
            estimate_tokens_fn=lambda h: 99999,
        ))
        result = await comp.compress(_make_history(5), _FakeWorking())
        assert len(result) == 3


# ══════════════════════════════════════════════
# NaturalConversation integration tests
# ══════════════════════════════════════════════


class TestNaturalConversation:

    @pytest.mark.asyncio
    async def test_enhance_default_config(self):
        nc = NaturalConversation(DefaultNaturalConversationConfig())
        session = _new_session()
        now = datetime(2025, 6, 15, 10, 0, tzinfo=timezone.utc)
        fragments, history = await nc.enhance(session, "你好呀", None, now)
        assert fragments.text() != ""
        assert len(fragments.kv) > 0
        assert len(fragments.warnings) > 0

    def test_post_process(self):
        nc = NaturalConversation(DefaultNaturalConversationConfig())
        result, changed = nc.post_process("这是回复。希望对你有帮助？")
        assert changed
        assert "希望对你有帮助" not in result

    @pytest.mark.asyncio
    async def test_wrap_loop(self):
        nc = NaturalConversation(DefaultNaturalConversationConfig())

        async def llm_fn(messages, tools=None):
            return {"content": "Hello!", "tool_calls": None}

        loop = AgentLoop(llm_fn=llm_fn, tool_registry=ToolRegistry())
        natural_loop = nc.wrap_loop(loop)
        session = _new_session()
        result = await natural_loop.run(session, "hi")
        assert result.stopped_reason == "completed"
        assert result.final_output != ""
        assert natural_loop.last_fragments is not None

    def test_default_config(self):
        config = DefaultNaturalConversationConfig()
        assert config.state_tracking
        assert config.emotion_detection
        assert config.style_post_process
        assert not config.opener_generation
        assert not config.context_compress
        assert not config.style_retry
