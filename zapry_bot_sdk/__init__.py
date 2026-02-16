"""
Zapry Bot SDK — 轻量级 Python SDK，用于在 Zapry 平台构建 Bot。

基于 python-telegram-bot，提供 Zapry 平台兼容层、
配置管理、Handler 注册装饰器等开发便利工具。

Quick Start:
    from zapry_bot_sdk import ZapryBot, BotConfig

    config = BotConfig.from_env()
    bot = ZapryBot(config)

    @bot.command("start")
    async def start(update, context):
        await update.message.reply_text("Hello!")

    bot.run()
"""

__version__ = "0.3.0"

from zapry_bot_sdk.core.config import BotConfig
from zapry_bot_sdk.core.bot import ZapryBot
from zapry_bot_sdk.core.middleware import MiddlewareContext, MiddlewarePipeline
from zapry_bot_sdk.helpers.handler_registry import command, callback_query, message
from zapry_bot_sdk.proactive.scheduler import ProactiveScheduler, TriggerContext
from zapry_bot_sdk.proactive.feedback import (
    FeedbackDetector,
    FeedbackResult,
    build_preference_prompt,
)
from zapry_bot_sdk.tools.registry import ToolRegistry, ToolDef, ToolContext, tool
from zapry_bot_sdk.memory.session import MemorySession
from zapry_bot_sdk.memory.store import InMemoryStore
from zapry_bot_sdk.memory.store_sqlite import SQLiteMemoryStore

__all__ = [
    "ZapryBot",
    "BotConfig",
    "MiddlewareContext",
    "MiddlewarePipeline",
    "command",
    "callback_query",
    "message",
    "ProactiveScheduler",
    "TriggerContext",
    "FeedbackDetector",
    "FeedbackResult",
    "build_preference_prompt",
    "ToolRegistry",
    "ToolDef",
    "ToolContext",
    "tool",
    "MemorySession",
    "InMemoryStore",
    "SQLiteMemoryStore",
    "__version__",
]
