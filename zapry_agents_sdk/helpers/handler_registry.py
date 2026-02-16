"""
Handler 注册工具。

提供两种 handler 注册方式:

1. 装饰器模式 — 适合分模块开发::

    from zapry_agents_sdk import command, callback_query

    @command("start")
    async def start(update, context):
        await update.message.reply_text("Hello!")

    @callback_query("^confirm_")
    async def on_confirm(update, context):
        ...

2. Registry 模式 — 适合集中管理::

    registry = HandlerRegistry()
    registry.add_command("start", start_handler)
    registry.add_callback("^confirm_", confirm_handler)

    # 然后一次性注册到 ZapryAgent
    bot.register(registry)

两种模式可以混用。
"""

from __future__ import annotations

from typing import Any, Callable, List, Union


# ── 全局 Handler 收集器（装饰器模式）──

_global_commands: List[tuple] = []
_global_callbacks: List[tuple] = []
_global_messages: List[tuple] = []


def command(name: Union[str, List[str]], **kwargs: Any) -> Callable:
    """
    装饰器: 注册命令 handler。

    Usage::

        @command("start")
        async def start(update, context):
            ...

        @command(["help", "info"])
        async def help_cmd(update, context):
            ...
    """
    def decorator(func: Callable) -> Callable:
        _global_commands.append((name, func, kwargs))
        return func
    return decorator


def callback_query(pattern: str, **kwargs: Any) -> Callable:
    """
    装饰器: 注册 callback query handler。

    Usage::

        @callback_query("^confirm_")
        async def on_confirm(update, context):
            ...
    """
    def decorator(func: Callable) -> Callable:
        _global_callbacks.append((pattern, func, kwargs))
        return func
    return decorator


def message(filter_obj: Any = None, **kwargs: Any) -> Callable:
    """
    装饰器: 注册消息 handler。

    Usage::

        from telegram.ext import filters

        @message(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND)
        async def on_private_msg(update, context):
            ...
    """
    def decorator(func: Callable) -> Callable:
        _global_messages.append((filter_obj, func, kwargs))
        return func
    return decorator


def get_global_handlers() -> tuple[list, list, list]:
    """获取通过装饰器注册的所有全局 handler。"""
    return _global_commands, _global_callbacks, _global_messages


def clear_global_handlers() -> None:
    """清空全局 handler（主要用于测试）。"""
    _global_commands.clear()
    _global_callbacks.clear()
    _global_messages.clear()


# ── Registry 模式 ──

class HandlerRegistry:
    """
    Handler 注册表。

    适合在独立模块中收集 handler，然后批量注册到 ZapryAgent。

    Usage::

        # handlers/tarot.py
        tarot_handlers = HandlerRegistry()

        @tarot_handlers.command("tarot")
        async def tarot_command(update, context):
            ...

        @tarot_handlers.callback("^reveal_card_")
        async def reveal_card(update, context):
            ...

        # main.py
        from handlers.tarot import tarot_handlers
        bot.register(tarot_handlers)
    """

    def __init__(self) -> None:
        self.commands: List[tuple] = []
        self.callbacks: List[tuple] = []
        self.messages: List[tuple] = []

    def command(self, name: Union[str, List[str]], **kwargs: Any) -> Callable:
        """装饰器: 注册命令 handler。"""
        def decorator(func: Callable) -> Callable:
            self.commands.append((name, func, kwargs))
            return func
        return decorator

    def callback(self, pattern: str, **kwargs: Any) -> Callable:
        """装饰器: 注册 callback query handler。"""
        def decorator(func: Callable) -> Callable:
            self.callbacks.append((pattern, func, kwargs))
            return func
        return decorator

    def message(self, filter_obj: Any = None, **kwargs: Any) -> Callable:
        """装饰器: 注册消息 handler。"""
        def decorator(func: Callable) -> Callable:
            self.messages.append((filter_obj, func, kwargs))
            return func
        return decorator

    def add_command(
        self, name: Union[str, List[str]], handler: Callable, **kwargs: Any
    ) -> None:
        """手动添加命令 handler。"""
        self.commands.append((name, handler, kwargs))

    def add_callback(
        self, pattern: str, handler: Callable, **kwargs: Any
    ) -> None:
        """手动添加 callback query handler。"""
        self.callbacks.append((pattern, handler, kwargs))

    def add_message(
        self, filter_obj: Any, handler: Callable, **kwargs: Any
    ) -> None:
        """手动添加消息 handler。"""
        self.messages.append((filter_obj, handler, kwargs))
