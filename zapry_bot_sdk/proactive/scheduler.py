"""
ProactiveScheduler — 主动消息调度器框架。

提供 start()/stop() 生命周期 + 定时检查 + 触发器注册接口，
让开发者可以自定义触发规则和消息内容。

抽象自 fortune_master/services/proactive.py，
去掉了业务逻辑（塔罗、节气等），只保留通用调度框架。

Usage::

    from zapry_bot_sdk.proactive import ProactiveScheduler

    scheduler = ProactiveScheduler(interval=60)

    # 方式 1：装饰器注册
    @scheduler.trigger("daily_greeting")
    async def check_greeting(ctx):
        if ctx.now.hour == 12 and ctx.now.minute <= 30:
            return ["user_001", "user_002"]
        return []

    @check_greeting.message
    async def greeting_msg(ctx, user_id):
        return "中午好~ 今天状态怎么样？"

    # 方式 2：直接注册
    scheduler.add_trigger(
        name="birthday",
        check_fn=my_check_fn,
        message_fn=my_message_fn,
    )

    # 启动 / 停止
    await scheduler.start()
    await scheduler.stop()

    # 用户级开关
    await scheduler.enable_user("user_001")
    await scheduler.disable_user("user_001")
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    Set,
    Union,
)

logger = logging.getLogger("zapry_bot_sdk.proactive")


# ──────────────────────────────────────────────
# 类型定义
# ──────────────────────────────────────────────


@dataclass
class TriggerContext:
    """传递给 check_fn / message_fn 的上下文。

    Attributes:
        now: 当前 datetime（每次轮询刷新）。
        today: 当前 date。
        scheduler: 调度器实例（可用于访问 state / send_fn）。
        state: 开发者可自由读写的字典，生命周期与 scheduler 一致。
    """

    now: datetime = field(default_factory=datetime.now)
    today: date = field(default_factory=date.today)
    scheduler: Optional["ProactiveScheduler"] = None
    state: Dict[str, Any] = field(default_factory=dict)


# check_fn 返回需要发送消息的 user_id 列表
CheckFn = Callable[[TriggerContext], Awaitable[List[str]]]

# message_fn 返回要发送的文本（可为 None 表示跳过）
MessageFn = Callable[[TriggerContext, str], Awaitable[Optional[str]]]

# send_fn 由调用方注入，负责把文本投递给用户
SendFn = Callable[[str, str], Awaitable[None]]


class UserStore(Protocol):
    """用户启用/禁用持久化接口（可选实现）。

    如果不提供，scheduler 会使用内存 set 管理。
    """

    async def is_enabled(self, user_id: str, trigger_name: str) -> bool: ...
    async def enable(self, user_id: str, trigger_name: str) -> None: ...
    async def disable(self, user_id: str, trigger_name: str) -> None: ...
    async def get_enabled_users(self, trigger_name: str) -> List[str]: ...
    async def record_sent(
        self, user_id: str, trigger_name: str, sent_at: datetime
    ) -> None: ...
    async def already_sent_today(
        self, user_id: str, trigger_name: str
    ) -> bool: ...


# ──────────────────────────────────────────────
# 内置内存 UserStore
# ──────────────────────────────────────────────


class InMemoryUserStore:
    """基于内存的用户启停管理（无持久化，重启后丢失）。"""

    def __init__(self) -> None:
        # trigger_name -> set of user_ids
        self._enabled: Dict[str, Set[str]] = {}
        # (user_id, trigger_name) -> last sent date string
        self._sent_today: Dict[tuple, str] = {}

    async def is_enabled(self, user_id: str, trigger_name: str) -> bool:
        return user_id in self._enabled.get(trigger_name, set())

    async def enable(self, user_id: str, trigger_name: str) -> None:
        self._enabled.setdefault(trigger_name, set()).add(user_id)

    async def disable(self, user_id: str, trigger_name: str) -> None:
        if trigger_name in self._enabled:
            self._enabled[trigger_name].discard(user_id)

    async def get_enabled_users(self, trigger_name: str) -> List[str]:
        return list(self._enabled.get(trigger_name, set()))

    async def record_sent(
        self, user_id: str, trigger_name: str, sent_at: datetime
    ) -> None:
        self._sent_today[(user_id, trigger_name)] = sent_at.date().isoformat()

    async def already_sent_today(
        self, user_id: str, trigger_name: str
    ) -> bool:
        key = (user_id, trigger_name)
        return self._sent_today.get(key) == date.today().isoformat()


# ──────────────────────────────────────────────
# TriggerHandle（装饰器辅助）
# ──────────────────────────────────────────────


class TriggerHandle:
    """trigger 装饰器返回的句柄，可继续链式注册 message_fn。"""

    def __init__(self, name: str, check_fn: CheckFn) -> None:
        self.name = name
        self.check_fn = check_fn
        self.message_fn: Optional[MessageFn] = None

    def message(self, fn: MessageFn) -> MessageFn:
        """装饰器：为该 trigger 注册 message_fn。"""
        self.message_fn = fn
        return fn


# ──────────────────────────────────────────────
# ProactiveScheduler 主类
# ──────────────────────────────────────────────


class ProactiveScheduler:
    """主动消息调度器框架。

    Parameters:
        interval: 轮询间隔（秒），默认 60。
        send_fn: 发送消息回调 ``async def send(user_id, text)``。
        user_store: 用户启停管理，默认使用内存实现。
    """

    def __init__(
        self,
        interval: int = 60,
        send_fn: Optional[SendFn] = None,
        user_store: Optional[UserStore] = None,
    ) -> None:
        self.interval = interval
        self.send_fn = send_fn
        self.user_store: UserStore = user_store or InMemoryUserStore()

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._triggers: Dict[str, TriggerHandle] = {}

        # 跨轮次持久状态（可在 check_fn 中通过 ctx.state 访问）
        self.state: Dict[str, Any] = {}

    # ─── 触发器注册 ───

    def trigger(self, name: str) -> Callable[[CheckFn], TriggerHandle]:
        """装饰器：注册一个触发器的 check_fn。

        Example::

            @scheduler.trigger("daily_greeting")
            async def check(ctx):
                if ctx.now.hour == 12:
                    return ["user_001"]
                return []

            @check.message
            async def msg(ctx, user_id):
                return "中午好~"
        """

        def decorator(fn: CheckFn) -> TriggerHandle:
            handle = TriggerHandle(name, fn)
            self._triggers[name] = handle
            logger.debug("Trigger registered: %s", name)
            return handle

        return decorator

    def add_trigger(
        self,
        name: str,
        check_fn: CheckFn,
        message_fn: MessageFn,
    ) -> None:
        """编程式注册触发器。

        Parameters:
            name: 触发器名称（唯一标识）。
            check_fn: 检查函数，返回需要发送的 user_id 列表。
            message_fn: 消息生成函数，返回文本或 None。
        """
        handle = TriggerHandle(name, check_fn)
        handle.message_fn = message_fn
        self._triggers[name] = handle
        logger.debug("Trigger added: %s", name)

    def remove_trigger(self, name: str) -> None:
        """移除触发器。"""
        self._triggers.pop(name, None)

    # ─── 生命周期 ───

    async def start(self) -> None:
        """启动调度循环。"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("ProactiveScheduler started (interval=%ds)", self.interval)

    async def stop(self) -> None:
        """停止调度循环。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("ProactiveScheduler stopped")

    # ─── 用户级开关 ───

    async def enable_user(self, user_id: str, triggers: Optional[List[str]] = None) -> None:
        """为用户启用指定（或全部）触发器。"""
        names = triggers or list(self._triggers.keys())
        for name in names:
            await self.user_store.enable(user_id, name)

    async def disable_user(self, user_id: str, triggers: Optional[List[str]] = None) -> None:
        """为用户关闭指定（或全部）触发器。"""
        names = triggers or list(self._triggers.keys())
        for name in names:
            await self.user_store.disable(user_id, name)

    async def is_user_enabled(self, user_id: str, trigger_name: Optional[str] = None) -> bool:
        """检查用户是否启用了触发器。若不指定 trigger_name，则检查任意一个。"""
        if trigger_name:
            return await self.user_store.is_enabled(user_id, trigger_name)
        for name in self._triggers:
            if await self.user_store.is_enabled(user_id, name):
                return True
        return False

    # ─── 核心循环 ───

    async def _poll_loop(self) -> None:
        """定时检查所有触发器。"""
        while self._running:
            try:
                ctx = TriggerContext(
                    now=datetime.now(),
                    today=date.today(),
                    scheduler=self,
                    state=self.state,
                )

                for name, handle in self._triggers.items():
                    await self._run_trigger(ctx, name, handle)

            except Exception as e:
                logger.error("ProactiveScheduler poll error: %s", e, exc_info=True)

            await asyncio.sleep(self.interval)

    async def _run_trigger(
        self, ctx: TriggerContext, name: str, handle: TriggerHandle
    ) -> None:
        """执行单个触发器的检查和消息发送。"""
        try:
            user_ids = await handle.check_fn(ctx)
            if not user_ids:
                return

            if not handle.message_fn:
                logger.warning(
                    "Trigger %r returned users but has no message_fn", name
                )
                return

            for user_id in user_ids:
                # 检查今天是否已发送
                if await self.user_store.already_sent_today(user_id, name):
                    continue

                # 生成消息
                text = await handle.message_fn(ctx, user_id)
                if not text:
                    continue

                # 发送
                await self._send(user_id, text)

                # 记录
                await self.user_store.record_sent(user_id, name, ctx.now)
                logger.info(
                    "Proactive message sent | trigger=%s | user=%s", name, user_id
                )

        except Exception as e:
            logger.error(
                "Trigger %r error: %s", name, e, exc_info=True
            )

    async def _send(self, user_id: str, text: str) -> None:
        """调用外部 send_fn 发送消息。"""
        if not self.send_fn:
            logger.warning(
                "send_fn not set, cannot send proactive message to %s", user_id
            )
            return
        try:
            await self.send_fn(user_id, text)
        except Exception as e:
            logger.error(
                "Failed to send proactive message | user=%s | error=%s",
                user_id,
                e,
            )
