"""
Zapry 平台兼容层。

Zapry 使用 Telegram Bot API 的私有化实现，返回数据格式与官方 API
存在差异。此模块通过 Monkey Patch 方式自动修复这些差异，让开发者
无需关心底层兼容性问题。

已知问题状态 (2026-02):
  已修复 (Zapry 侧): 1(first_name), 2(is_bot), 5(私聊chat.id), 6(chat.type), 8(entities)
  仍需兼容: 3(ID字符串), 4(username), 7(g_前缀), 9-14(API方法差异)

迁移自:
  - fortune_master/utils/private_api_bot.py
  - fortune_master/utils/zapry_compat.py
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from telegram import Chat, Update, User
from telegram.ext import ExtBot

logger = logging.getLogger("zapry_bot_sdk.compat")


# ═══════════════════════════════════════════════════
# 一、Monkey Patch — 自动修复 Zapry 数据格式
# ═══════════════════════════════════════════════════

# 保存原始 de_json 方法（仅在第一次加载时保存）
_original_user_de_json = User.de_json
_original_chat_de_json = Chat.de_json
_original_update_de_json = Update.de_json
_patched = False


def apply_zapry_compatibility() -> None:
    """
    应用 Zapry 兼容性补丁。

    通过 Monkey Patch 替换 User/Chat/Update 的 de_json 方法，
    在 JSON 反序列化前自动修复数据格式。

    必须在创建 Application 之前调用。幂等 — 多次调用无副作用。
    """
    global _patched
    if _patched:
        return

    User.de_json = staticmethod(_patched_user_de_json)
    Chat.de_json = staticmethod(_patched_chat_de_json)
    Update.de_json = classmethod(_patched_update_de_json)
    _patched = True

    logger.info("✅ Zapry 兼容层已启用（防御性模式）")
    logger.info("   - User/Chat 数据自动规范化")
    logger.info("   - 群聊 g_ 前缀 ID 自动转换")
    logger.info("   - 命令 entities 防御性补全")


# ── User 规范化 ──

_USER_FIELDS = {
    "id", "first_name", "is_bot", "last_name", "username",
    "language_code", "can_join_groups", "can_read_all_group_messages",
    "supports_inline_queries", "is_premium",
    "added_to_attachment_menu", "api_kwargs",
}

_FIELD_ALIASES = {
    "bot_id": "id",
    "user_id": "id",
    "name": "first_name",
}


def _normalize_user_data(data: dict) -> dict:
    """
    将 Zapry API 返回的 User 格式转换为标准 Telegram 格式。

    处理: 嵌套 user 对象、字段名映射、ID 类型转换、缺失字段补全。
    """
    if not isinstance(data, dict):
        return data
    data = dict(data)

    # 提取嵌套的 user 对象
    if "user" in data and isinstance(data["user"], dict):
        data = data["user"].copy()

    # 字段名映射
    for old_key, new_key in _FIELD_ALIASES.items():
        if old_key in data and new_key not in data:
            data[new_key] = data.pop(old_key)

    # ID → int
    if "id" in data and isinstance(data["id"], str):
        try:
            data["id"] = int(data["id"])
        except ValueError:
            logger.warning("⚠️  User ID 无法转换为整数: %s", data["id"])

    # 防御性补全 first_name
    if not data.get("first_name"):
        fallback = (
            data.get("username")
            or data.get("last_name")
            or data.get("name")
            or (str(data["id"]) if data.get("is_bot") and "id" in data else "")
        )
        data["first_name"] = fallback or ""
        if fallback:
            logger.debug("🔧 补全 first_name: %s", fallback)

    # 防御性补全 is_bot
    if "is_bot" not in data:
        data["is_bot"] = False
        logger.debug("🔧 补全 is_bot: False")

    return {k: v for k, v in data.items() if k in _USER_FIELDS}


# ── Chat 规范化 ──

_CHAT_FIELDS = {
    "id", "type", "title", "username", "first_name", "last_name",
    "is_forum", "photo", "active_usernames",
    "emoji_status_custom_emoji_id", "bio",
    "has_private_forwards", "has_restricted_voice_and_video_messages",
    "join_to_send_messages", "join_by_request", "description",
    "invite_link", "pinned_message", "permissions",
    "slow_mode_delay", "message_auto_delete_time",
    "has_aggressive_anti_spam_enabled", "has_hidden_members",
    "has_protected_content", "sticker_set_name", "can_set_sticker_set",
    "linked_chat_id", "location", "api_kwargs",
}


def _normalize_chat_data(data: dict) -> dict:
    """
    将 Zapry API 返回的 Chat 格式转换为标准格式。

    处理: g_ 前缀群组 ID、ID 字符串→整数、缺失 type 补全。
    """
    if not isinstance(data, dict):
        return data
    data = dict(data)

    if "id" in data:
        chat_id = data["id"]
        if isinstance(chat_id, str):
            if chat_id.startswith("g_"):
                raw_id = chat_id[2:]
                try:
                    data["id"] = int(raw_id)
                    logger.debug("🔧 群组 Chat ID: '%s' -> %s", chat_id, data["id"])
                except ValueError:
                    logger.warning("⚠️  群组 Chat ID 转换失败: %s", chat_id)
                if not data.get("type") or data["type"] == "private":
                    data["type"] = "group"
            else:
                try:
                    data["id"] = int(chat_id)
                    logger.debug("🔧 Chat ID: '%s' -> %s", chat_id, data["id"])
                except ValueError:
                    logger.warning("⚠️  Chat ID 无法转换: %s", chat_id)

    if not data.get("type"):
        data["type"] = "private"
        logger.debug("🔧 补全 Chat.type: private")

    return {k: v for k, v in data.items() if k in _CHAT_FIELDS}


# ── Update 规范化 ──

def _normalize_update_data(update_data: dict) -> dict:
    """递归规范化 Update 中的所有 User/Chat 对象。"""
    if not isinstance(update_data, dict):
        return update_data

    normalized = {}
    for key, value in update_data.items():
        if key == "message" and isinstance(value, dict):
            normalized[key] = _fix_message_data(value)
        elif key == "callback_query" and isinstance(value, dict):
            normalized[key] = _fix_callback_query_data(value)
        elif key in ("from", "user", "forward_from", "via_bot"):
            normalized[key] = (
                _normalize_user_data(value) if isinstance(value, dict) else value
            )
        elif key == "chat":
            normalized[key] = (
                _normalize_chat_data(value) if isinstance(value, dict) else value
            )
        elif isinstance(value, dict):
            normalized[key] = _normalize_update_data(value)
        elif isinstance(value, list):
            normalized[key] = [
                _normalize_update_data(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            normalized[key] = value

    return normalized


def _fix_message_data(msg: dict) -> dict:
    """修复 Zapry message 数据。"""
    msg = dict(msg)

    # 修复 chat
    if "chat" in msg and isinstance(msg["chat"], dict):
        chat = dict(msg["chat"])
        chat_id = chat.get("id")
        chat_type = (chat.get("type") or "").lower()

        if isinstance(chat_id, str):
            if chat_id.startswith("g_"):
                raw_id = chat_id[2:]
                try:
                    chat["id"] = int(raw_id)
                except ValueError:
                    pass
                if not chat_type or chat_type == "private":
                    chat["type"] = "group"
            else:
                try:
                    chat["id"] = int(chat_id)
                except ValueError:
                    if "from" in msg and isinstance(msg["from"], dict):
                        real_uid = msg["from"].get("id")
                        if real_uid:
                            chat["id"] = real_uid
                    if not chat_type:
                        chat["type"] = "private"

        if not chat.get("type"):
            chat["type"] = "private"
        msg["chat"] = chat

    # 修复缺失的 entities
    text = msg.get("text", "")
    if text and text.startswith("/") and "entities" not in msg:
        cmd_end = text.find(" ") if " " in text else len(text)
        msg["entities"] = [{
            "type": "bot_command",
            "offset": 0,
            "length": cmd_end,
        }]
        logger.debug("🔧 补全 entities: %s", text[:cmd_end])

    return msg


def _fix_callback_query_data(cq: dict) -> dict:
    """修复 callback_query 中的 message。"""
    cq = dict(cq)
    if "message" in cq and isinstance(cq["message"], dict):
        cq["message"] = _fix_message_data(cq["message"])
    return cq


# ── Patched de_json 方法 ──

def _patched_user_de_json(
    data: Optional[Dict[str, Any]], bot=None
) -> Optional[User]:
    if data is None:
        return None
    return _original_user_de_json(_normalize_user_data(data), bot)


def _patched_chat_de_json(
    data: Optional[Dict[str, Any]], bot=None
) -> Optional[Chat]:
    if data is None:
        return None
    return _original_chat_de_json(_normalize_chat_data(data), bot)


def _patched_update_de_json(
    cls, data: Optional[Dict[str, Any]], bot=None
) -> Optional[Update]:
    if data is None:
        return None
    return _original_update_de_json(_normalize_update_data(data), bot)


# ═══════════════════════════════════════════════════
# 二、PrivateAPIExtBot — 自定义 Base URL 的 Bot
# ═══════════════════════════════════════════════════

class PrivateAPIExtBot(ExtBot):
    """
    兼容 Zapry 私有化 API 的 ExtBot。

    覆盖 get_me 和 answer_callback_query 以处理
    Zapry 特有的数据格式和缺失字段。
    """

    async def get_me(self, *, read_timeout=None, write_timeout=None,
                     connect_timeout=None, pool_timeout=None,
                     api_kwargs=None):
        result = await self._post(
            "getMe",
            read_timeout=read_timeout,
            write_timeout=write_timeout,
            connect_timeout=connect_timeout,
            pool_timeout=pool_timeout,
            api_kwargs=api_kwargs,
        )
        result = _normalize_user_data(result)
        self._bot_user = User.de_json(result, self)
        return self._bot_user

    async def answer_callback_query(
        self, callback_query_id: str, text: str = None,
        show_alert: bool = None, url: str = None,
        cache_time: int = None, *, read_timeout=None,
        write_timeout=None, connect_timeout=None,
        pool_timeout=None, api_kwargs=None,
    ):
        try:
            return await super().answer_callback_query(
                callback_query_id=callback_query_id,
                text=text, show_alert=show_alert, url=url,
                cache_time=cache_time, read_timeout=read_timeout,
                write_timeout=write_timeout,
                connect_timeout=connect_timeout,
                pool_timeout=pool_timeout, api_kwargs=api_kwargs,
            )
        except Exception as e:
            logger.warning("⚠️  answerCallbackQuery 失败: %s", e)
            return True


# ═══════════════════════════════════════════════════
# 三、ZapryCompat — 平台差异工具类
# ═══════════════════════════════════════════════════

class ZapryCompat:
    """
    Zapry 平台差异处理工具类。

    提供统一的方法来处理 Zapry 和 Telegram 平台
    在消息格式、API 支持等方面的差异。
    """

    # Zapry 平台已知限制
    LIMITATIONS = {
        "supports_markdown": False,
        "supports_edit_message": False,
        "supports_answer_callback": False,
        "supports_chat_action": False,
        "id_fields_are_strings": True,
        "group_id_has_prefix": True,
        "user_missing_username": True,
    }

    def __init__(self, is_zapry: bool = False) -> None:
        self._is_zapry = is_zapry

    @property
    def is_zapry(self) -> bool:
        return self._is_zapry

    def should_use_markdown(self) -> bool:
        return not self._is_zapry

    def should_edit_message(self) -> bool:
        return not self._is_zapry

    def get_parse_mode(self) -> Optional[str]:
        return None if self._is_zapry else "Markdown"

    def clean_markdown(self, text: str) -> str:
        """
        清理 Markdown 标记。

        Zapry 不支持 Markdown 渲染，AI 回复中的标记会原样显示。
        """
        if not self._is_zapry:
            return text
        # **bold** → bold
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        # __bold__ → bold
        text = re.sub(r"__(.+?)__", r"\1", text)
        # *italic* → italic
        text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)
        # _italic_ → italic
        text = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"\1", text)
        # `code` → code
        text = re.sub(r"`(.+?)`", r"\1", text)
        # ### heading → heading
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
        return text
