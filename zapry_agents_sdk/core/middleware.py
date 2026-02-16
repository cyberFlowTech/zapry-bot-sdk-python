"""
Middleware — 洋葱模型中间件管道。

每个 middleware 包裹下一层，通过调用 next_fn() 决定是否继续。
可在 handler 前后都执行逻辑（before / after）。

Usage::

    from zapry_agents_sdk import ZapryAgent, AgentConfig

    bot = ZapryAgent(AgentConfig.from_env())

    async def auth_middleware(ctx, next_fn):
        if not is_authorized(ctx.update):
            return  # 拦截，不调用 next_fn
        ctx.extra["user_role"] = "admin"
        await next_fn()
        # after handler
        log_request(ctx)

    bot.use(auth_middleware)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("zapry_agents_sdk.middleware")

# ──────────────────────────────────────────────
# Types
# ──────────────────────────────────────────────

# next_fn: call to proceed to the next middleware / handler
NextFn = Callable[[], Awaitable[None]]

# middleware signature: async def mw(ctx, next_fn) -> None
MiddlewareFunc = Callable[["MiddlewareContext", NextFn], Awaitable[None]]


# ──────────────────────────────────────────────
# MiddlewareContext
# ──────────────────────────────────────────────


@dataclass
class MiddlewareContext:
    """Shared context that flows through the entire middleware pipeline.

    Attributes:
        update: The incoming Telegram Update object.
        bot: The underlying bot instance (telegram.Bot).
        extra: Arbitrary dict for middleware to attach data
               (e.g. ``ctx.extra["user_role"] = "admin"``).
    """

    update: Any = None
    bot: Any = None
    extra: Dict[str, Any] = field(default_factory=dict)


# ──────────────────────────────────────────────
# MiddlewarePipeline
# ──────────────────────────────────────────────


class MiddlewarePipeline:
    """Onion-model middleware pipeline.

    Builds a call chain where each middleware wraps the next one.
    The innermost layer is the ``core_handler`` (the original dispatch logic).

    Example pipeline with 2 middlewares::

        mw_1 before → mw_2 before → core_handler → mw_2 after → mw_1 after
    """

    def __init__(self) -> None:
        self._middlewares: List[MiddlewareFunc] = []

    def use(self, mw: MiddlewareFunc) -> None:
        """Append a middleware to the pipeline."""
        self._middlewares.append(mw)

    @property
    def middlewares(self) -> List[MiddlewareFunc]:
        return list(self._middlewares)

    def __len__(self) -> int:
        return len(self._middlewares)

    async def execute(
        self,
        ctx: MiddlewareContext,
        core_handler: Callable[[], Awaitable[None]],
    ) -> None:
        """Run the full pipeline, ending with *core_handler*.

        Parameters:
            ctx: Shared context available to every middleware.
            core_handler: The innermost function (e.g. router dispatch).
        """
        if not self._middlewares:
            await core_handler()
            return

        # Build the onion chain from inside out.
        # chain[0] = core_handler
        # chain[1] = mw[-1] wrapping chain[0]
        # chain[2] = mw[-2] wrapping chain[1]
        # ...
        chain = core_handler
        for mw in reversed(self._middlewares):
            chain = _wrap(mw, ctx, chain)

        await chain()


def _wrap(
    mw: MiddlewareFunc,
    ctx: MiddlewareContext,
    next_fn: Callable[[], Awaitable[None]],
) -> Callable[[], Awaitable[None]]:
    """Create a closure that calls ``mw(ctx, next_fn)``."""

    async def wrapped() -> None:
        await mw(ctx, next_fn)

    return wrapped
