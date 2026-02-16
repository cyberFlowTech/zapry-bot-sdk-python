"""
测试主动触发调度器和反馈检测框架。
"""

import asyncio
import pytest
import pytest_asyncio

from zapry_agents_sdk.proactive.scheduler import (
    ProactiveScheduler,
    TriggerContext,
    InMemoryUserStore,
)
from zapry_agents_sdk.proactive.feedback import (
    FeedbackDetector,
    FeedbackResult,
    build_preference_prompt,
    DEFAULT_FEEDBACK_PATTERNS,
    DEFAULT_PREFERENCE_PROMPTS,
)


# ══════════════════════════════════════════════
# ProactiveScheduler 测试
# ══════════════════════════════════════════════


class TestInMemoryUserStore:
    """InMemoryUserStore 基础功能测试。"""

    @pytest.fixture
    def store(self):
        return InMemoryUserStore()

    @pytest.mark.asyncio
    async def test_enable_disable(self, store):
        await store.enable("u1", "daily")
        assert await store.is_enabled("u1", "daily") is True
        await store.disable("u1", "daily")
        assert await store.is_enabled("u1", "daily") is False

    @pytest.mark.asyncio
    async def test_get_enabled_users(self, store):
        await store.enable("u1", "daily")
        await store.enable("u2", "daily")
        await store.enable("u3", "birthday")
        users = await store.get_enabled_users("daily")
        assert set(users) == {"u1", "u2"}

    @pytest.mark.asyncio
    async def test_already_sent_today(self, store):
        from datetime import datetime
        assert await store.already_sent_today("u1", "daily") is False
        await store.record_sent("u1", "daily", datetime.now())
        assert await store.already_sent_today("u1", "daily") is True

    @pytest.mark.asyncio
    async def test_not_enabled_by_default(self, store):
        assert await store.is_enabled("unknown_user", "daily") is False
        assert await store.get_enabled_users("nonexistent") == []


class TestProactiveScheduler:
    """ProactiveScheduler 核心功能测试。"""

    @pytest.mark.asyncio
    async def test_start_stop(self):
        scheduler = ProactiveScheduler(interval=1)
        await scheduler.start()
        assert scheduler._running is True
        await scheduler.stop()
        assert scheduler._running is False

    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        scheduler = ProactiveScheduler(interval=1)
        await scheduler.start()
        task1 = scheduler._task
        await scheduler.start()  # 重复启动不应创建新 task
        assert scheduler._task is task1
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_trigger_decorator(self):
        scheduler = ProactiveScheduler(interval=1)

        @scheduler.trigger("test_trigger")
        async def check(ctx):
            return ["u1"]

        @check.message
        async def msg(ctx, user_id):
            return f"Hello {user_id}"

        assert "test_trigger" in scheduler._triggers
        handle = scheduler._triggers["test_trigger"]
        # 装饰器返回的 check 是 TriggerHandle 本身
        assert handle is check
        assert handle.message_fn is msg

    @pytest.mark.asyncio
    async def test_add_trigger_programmatic(self):
        scheduler = ProactiveScheduler(interval=1)

        async def check(ctx):
            return ["u1"]

        async def msg(ctx, uid):
            return "Hi"

        scheduler.add_trigger("test", check, msg)
        assert "test" in scheduler._triggers

    @pytest.mark.asyncio
    async def test_remove_trigger(self):
        scheduler = ProactiveScheduler(interval=1)

        async def check(ctx):
            return []

        async def msg(ctx, uid):
            return ""

        scheduler.add_trigger("to_remove", check, msg)
        assert "to_remove" in scheduler._triggers
        scheduler.remove_trigger("to_remove")
        assert "to_remove" not in scheduler._triggers

    @pytest.mark.asyncio
    async def test_enable_disable_user(self):
        scheduler = ProactiveScheduler(interval=1)

        async def check(ctx):
            return []

        async def msg(ctx, uid):
            return ""

        scheduler.add_trigger("t1", check, msg)
        scheduler.add_trigger("t2", check, msg)

        await scheduler.enable_user("u1")
        assert await scheduler.is_user_enabled("u1") is True
        assert await scheduler.is_user_enabled("u1", "t1") is True
        assert await scheduler.is_user_enabled("u1", "t2") is True

        await scheduler.disable_user("u1", triggers=["t1"])
        assert await scheduler.is_user_enabled("u1", "t1") is False
        assert await scheduler.is_user_enabled("u1", "t2") is True

        await scheduler.disable_user("u1")
        assert await scheduler.is_user_enabled("u1") is False

    @pytest.mark.asyncio
    async def test_trigger_execution_sends_message(self):
        """验证触发器执行完整流程：check → message → send。"""
        sent_messages = []

        async def send_fn(user_id, text):
            sent_messages.append((user_id, text))

        scheduler = ProactiveScheduler(interval=1, send_fn=send_fn)

        @scheduler.trigger("greet")
        async def check(ctx):
            return ["u1", "u2"]

        @check.message
        async def msg(ctx, uid):
            return f"Hello {uid}!"

        # 手动执行一次触发器
        ctx = TriggerContext(scheduler=scheduler, state=scheduler.state)
        handle = scheduler._triggers["greet"]
        await scheduler._run_trigger(ctx, "greet", handle)

        assert len(sent_messages) == 2
        assert ("u1", "Hello u1!") in sent_messages
        assert ("u2", "Hello u2!") in sent_messages

    @pytest.mark.asyncio
    async def test_trigger_dedup_today(self):
        """验证同一天不重复发送。"""
        sent_messages = []

        async def send_fn(user_id, text):
            sent_messages.append((user_id, text))

        scheduler = ProactiveScheduler(interval=1, send_fn=send_fn)

        @scheduler.trigger("daily")
        async def check(ctx):
            return ["u1"]

        @check.message
        async def msg(ctx, uid):
            return "Hi"

        ctx = TriggerContext(scheduler=scheduler, state=scheduler.state)
        handle = scheduler._triggers["daily"]

        await scheduler._run_trigger(ctx, "daily", handle)
        assert len(sent_messages) == 1

        # 再次执行，不应重复发送
        await scheduler._run_trigger(ctx, "daily", handle)
        assert len(sent_messages) == 1  # 仍然是 1

    @pytest.mark.asyncio
    async def test_trigger_message_fn_returns_none_skips(self):
        """message_fn 返回 None 时应跳过发送。"""
        sent_messages = []

        async def send_fn(user_id, text):
            sent_messages.append((user_id, text))

        scheduler = ProactiveScheduler(interval=1, send_fn=send_fn)

        @scheduler.trigger("skip")
        async def check(ctx):
            return ["u1"]

        @check.message
        async def msg(ctx, uid):
            return None  # 跳过

        ctx = TriggerContext(scheduler=scheduler, state=scheduler.state)
        await scheduler._run_trigger(ctx, "skip", scheduler._triggers["skip"])
        assert len(sent_messages) == 0

    @pytest.mark.asyncio
    async def test_state_shared_across_triggers(self):
        """验证 ctx.state 在触发器间共享。"""
        scheduler = ProactiveScheduler(interval=1)

        @scheduler.trigger("writer")
        async def write_check(ctx):
            ctx.state["counter"] = ctx.state.get("counter", 0) + 1
            return []

        @write_check.message
        async def write_msg(ctx, uid):
            return ""

        ctx = TriggerContext(scheduler=scheduler, state=scheduler.state)
        await scheduler._run_trigger(ctx, "writer", scheduler._triggers["writer"])
        await scheduler._run_trigger(ctx, "writer", scheduler._triggers["writer"])
        assert scheduler.state["counter"] == 2

    @pytest.mark.asyncio
    async def test_no_send_fn_logs_warning(self):
        """没有 send_fn 时应该不崩溃。"""
        scheduler = ProactiveScheduler(interval=1, send_fn=None)

        @scheduler.trigger("test")
        async def check(ctx):
            return ["u1"]

        @check.message
        async def msg(ctx, uid):
            return "Hello"

        ctx = TriggerContext(scheduler=scheduler, state=scheduler.state)
        # 不应抛异常
        await scheduler._run_trigger(ctx, "test", scheduler._triggers["test"])


# ══════════════════════════════════════════════
# FeedbackDetector 测试
# ══════════════════════════════════════════════


class TestFeedbackDetector:
    """FeedbackDetector 反馈检测测试。"""

    @pytest.fixture
    def detector(self):
        return FeedbackDetector()

    def test_detect_concise(self, detector):
        result = detector.detect("太长了")
        assert result.matched is True
        assert result.changes["style"] == "concise"
        assert result.triggers["style"] == "太长了"

    def test_detect_detailed(self, detector):
        result = detector.detect("详细说说")
        assert result.matched is True
        assert result.changes["style"] == "detailed"

    def test_detect_casual_tone(self, detector):
        result = detector.detect("说人话")
        assert result.matched is True
        assert result.changes["tone"] == "casual"

    def test_detect_formal_tone(self, detector):
        result = detector.detect("专业一些")
        assert result.matched is True
        assert result.changes["tone"] == "formal"

    def test_no_match(self, detector):
        result = detector.detect("今天天气真好")
        assert result.matched is False
        assert result.changes == {}

    def test_long_message_skipped(self, detector):
        result = detector.detect("这是一条非常非常非常非常非常非常非常长的消息，应该不会被当作反馈信号来处理的")
        assert result.matched is False

    def test_empty_message(self, detector):
        result = detector.detect("")
        assert result.matched is False

    def test_whitespace_message(self, detector):
        result = detector.detect("   ")
        assert result.matched is False

    def test_dedup_same_value(self, detector):
        """已有相同偏好时不重复返回变更。"""
        result = detector.detect("太长了", {"style": "concise"})
        assert result.matched is False

    def test_detect_different_value(self, detector):
        """偏好变化时才返回。"""
        result = detector.detect("详细说说", {"style": "concise"})
        assert result.matched is True
        assert result.changes["style"] == "detailed"

    def test_multiple_signals(self, detector):
        """一条消息可能同时命中多个维度。"""
        result = detector.detect("简短点说人话")
        assert result.matched is True
        assert result.changes.get("style") == "concise"
        assert result.changes.get("tone") == "casual"

    def test_custom_patterns(self):
        detector = FeedbackDetector(patterns={
            "language": {
                "english": ["speak english", "in english"],
                "chinese": ["说中文", "用中文"],
            },
        })
        result = detector.detect("speak english")
        assert result.matched is True
        assert result.changes["language"] == "english"

    def test_add_pattern(self, detector):
        detector.add_pattern("mood", "positive", ["开心", "好心情"])
        result = detector.detect("开心")
        assert result.matched is True
        assert result.changes["mood"] == "positive"

    def test_set_patterns_replaces(self, detector):
        detector.set_patterns({"custom": {"val": ["触发词"]}})
        # 原有关键词不再生效
        result = detector.detect("太长了")
        assert result.matched is False
        # 新关键词生效
        result = detector.detect("触发词")
        assert result.matched is True

    def test_custom_max_length(self):
        detector = FeedbackDetector(max_length=10)
        result = detector.detect("这是十个字以上的消息太长了")
        assert result.matched is False

    @pytest.mark.asyncio
    async def test_detect_and_adapt(self, detector):
        prefs = {"style": "balanced"}
        result = await detector.detect_and_adapt("u1", "太长了", prefs)
        assert result.matched is True
        assert prefs["style"] == "concise"
        assert "updated_at" in prefs

    @pytest.mark.asyncio
    async def test_detect_and_adapt_no_match(self, detector):
        prefs = {"style": "balanced"}
        result = await detector.detect_and_adapt("u1", "今天天气真好", prefs)
        assert result.matched is False
        assert prefs["style"] == "balanced"
        assert "updated_at" not in prefs

    @pytest.mark.asyncio
    async def test_on_change_callback(self):
        changes_log = []

        async def on_change(user_id, changes):
            changes_log.append((user_id, changes))

        detector = FeedbackDetector(on_change=on_change)
        prefs = {}
        await detector.detect_and_adapt("u1", "太长了", prefs)
        assert len(changes_log) == 1
        assert changes_log[0] == ("u1", {"style": "concise"})


# ══════════════════════════════════════════════
# build_preference_prompt 测试
# ══════════════════════════════════════════════


class TestBuildPreferencePrompt:
    """偏好注入 prompt 构建测试。"""

    def test_concise_style(self):
        prompt = build_preference_prompt({"style": "concise"})
        assert prompt is not None
        assert "简洁" in prompt
        assert "100 字" in prompt

    def test_detailed_style(self):
        prompt = build_preference_prompt({"style": "detailed"})
        assert prompt is not None
        assert "详细" in prompt

    def test_casual_tone(self):
        prompt = build_preference_prompt({"tone": "casual"})
        assert prompt is not None
        assert "轻松" in prompt or "口语" in prompt

    def test_formal_tone(self):
        prompt = build_preference_prompt({"tone": "formal"})
        assert prompt is not None
        assert "专业" in prompt or "正式" in prompt

    def test_multiple_preferences(self):
        prompt = build_preference_prompt({"style": "concise", "tone": "casual"})
        assert prompt is not None
        assert "简洁" in prompt
        assert "轻松" in prompt or "口语" in prompt

    def test_no_matching_preference(self):
        prompt = build_preference_prompt({"style": "balanced"})
        assert prompt is None

    def test_empty_preferences(self):
        prompt = build_preference_prompt({})
        assert prompt is None

    def test_skip_updated_at(self):
        prompt = build_preference_prompt({"updated_at": "2025-01-01T00:00:00"})
        assert prompt is None

    def test_custom_prompt_map(self):
        custom_map = {
            "mood": {
                "happy": "用户心情好，可以活泼一点。",
            },
        }
        prompt = build_preference_prompt({"mood": "happy"}, prompt_map=custom_map)
        assert prompt is not None
        assert "活泼" in prompt

    def test_custom_header(self):
        prompt = build_preference_prompt(
            {"style": "concise"},
            header="Style Preferences:",
        )
        assert prompt.startswith("Style Preferences:")

    def test_default_header(self):
        prompt = build_preference_prompt({"style": "concise"})
        assert prompt.startswith("回复风格偏好：")
