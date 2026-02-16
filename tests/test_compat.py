"""
测试 Zapry 兼容层的数据规范化逻辑。
"""

import pytest

from zapry_agents_sdk.utils.telegram_compat import (
    _normalize_user_data,
    _normalize_chat_data,
    _fix_message_data,
    ZapryCompat,
)


class TestNormalizeUserData:
    """User 数据规范化测试。"""

    def test_normal_user(self):
        data = {"id": 12345, "first_name": "Alice", "is_bot": False}
        result = _normalize_user_data(data)
        assert result["id"] == 12345
        assert result["first_name"] == "Alice"
        assert result["is_bot"] is False

    def test_string_id(self):
        data = {"id": "12345", "first_name": "Alice", "is_bot": False}
        result = _normalize_user_data(data)
        assert result["id"] == 12345

    def test_nested_user(self):
        data = {"user": {"id": 123, "first_name": "Bob", "is_bot": False}, "token": "xxx"}
        result = _normalize_user_data(data)
        assert result["id"] == 123
        assert result["first_name"] == "Bob"
        assert "token" not in result

    def test_missing_first_name(self):
        data = {"id": 123, "is_bot": False, "username": "alice"}
        result = _normalize_user_data(data)
        assert result["first_name"] == "alice"

    def test_missing_is_bot(self):
        data = {"id": 123, "first_name": "Alice"}
        result = _normalize_user_data(data)
        assert result["is_bot"] is False

    def test_field_aliases(self):
        data = {"bot_id": 999, "name": "BotName", "is_bot": True}
        result = _normalize_user_data(data)
        assert result["id"] == 999
        assert result["first_name"] == "BotName"

    def test_removes_extra_fields(self):
        data = {"id": 1, "first_name": "A", "is_bot": False, "token": "secret", "extra": "junk"}
        result = _normalize_user_data(data)
        assert "token" not in result
        assert "extra" not in result


class TestNormalizeChatData:
    """Chat 数据规范化测试。"""

    def test_normal_chat(self):
        data = {"id": 123, "type": "private"}
        result = _normalize_chat_data(data)
        assert result["id"] == 123
        assert result["type"] == "private"

    def test_string_id(self):
        data = {"id": "123", "type": "private"}
        result = _normalize_chat_data(data)
        assert result["id"] == 123

    def test_group_prefix(self):
        data = {"id": "g_456", "type": "private"}
        result = _normalize_chat_data(data)
        assert result["id"] == 456
        assert result["type"] == "group"

    def test_missing_type(self):
        data = {"id": 123}
        result = _normalize_chat_data(data)
        assert result["type"] == "private"


class TestFixMessageData:
    """Message 数据修复测试。"""

    def test_add_missing_entities(self):
        msg = {"text": "/start", "chat": {"id": 123, "type": "private"}}
        result = _fix_message_data(msg)
        assert "entities" in result
        assert result["entities"][0]["type"] == "bot_command"
        assert result["entities"][0]["length"] == 6

    def test_add_entities_with_args(self):
        msg = {"text": "/tarot my question", "chat": {"id": 123, "type": "private"}}
        result = _fix_message_data(msg)
        assert result["entities"][0]["length"] == 6  # "/tarot"

    def test_no_entities_for_normal_text(self):
        msg = {"text": "hello", "chat": {"id": 123, "type": "private"}}
        result = _fix_message_data(msg)
        assert "entities" not in result

    def test_fix_group_chat_id(self):
        msg = {"text": "hi", "chat": {"id": "g_789", "type": "private"}}
        result = _fix_message_data(msg)
        assert result["chat"]["id"] == 789
        assert result["chat"]["type"] == "group"


class TestZapryCompat:
    """ZapryCompat 工具类测试。"""

    def test_telegram_mode(self):
        compat = ZapryCompat(is_zapry=False)
        assert compat.should_use_markdown() is True
        assert compat.get_parse_mode() == "Markdown"

    def test_zapry_mode(self):
        compat = ZapryCompat(is_zapry=True)
        assert compat.should_use_markdown() is False
        assert compat.get_parse_mode() is None

    def test_clean_markdown(self):
        compat = ZapryCompat(is_zapry=True)
        assert compat.clean_markdown("**bold**") == "bold"
        assert compat.clean_markdown("`code`") == "code"
        assert compat.clean_markdown("### heading") == "heading"

    def test_clean_markdown_no_op_telegram(self):
        compat = ZapryCompat(is_zapry=False)
        assert compat.clean_markdown("**bold**") == "**bold**"
