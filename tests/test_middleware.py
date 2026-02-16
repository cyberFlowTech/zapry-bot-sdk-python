"""
测试 Middleware 洋葱管道。
"""

import pytest
from zapry_bot_sdk.core.middleware import (
    MiddlewareContext,
    MiddlewarePipeline,
)


class TestMiddlewarePipeline:
    """MiddlewarePipeline 核心功能测试。"""

    @pytest.mark.asyncio
    async def test_empty_pipeline(self):
        """空管道直接执行 core handler。"""
        pipeline = MiddlewarePipeline()
        called = False

        async def core():
            nonlocal called
            called = True

        ctx = MiddlewareContext()
        await pipeline.execute(ctx, core)
        assert called is True

    @pytest.mark.asyncio
    async def test_single_middleware_before_after(self):
        """单个 middleware 的 before/after 执行。"""
        order = []

        async def mw(ctx, next_fn):
            order.append("before")
            await next_fn()
            order.append("after")

        pipeline = MiddlewarePipeline()
        pipeline.use(mw)

        async def core():
            order.append("core")

        await pipeline.execute(MiddlewareContext(), core)
        assert order == ["before", "core", "after"]

    @pytest.mark.asyncio
    async def test_onion_order(self):
        """多个 middleware 按洋葱模型执行。"""
        order = []

        async def mw1(ctx, next_fn):
            order.append("mw1-before")
            await next_fn()
            order.append("mw1-after")

        async def mw2(ctx, next_fn):
            order.append("mw2-before")
            await next_fn()
            order.append("mw2-after")

        pipeline = MiddlewarePipeline()
        pipeline.use(mw1)
        pipeline.use(mw2)

        async def core():
            order.append("core")

        await pipeline.execute(MiddlewareContext(), core)
        assert order == [
            "mw1-before",
            "mw2-before",
            "core",
            "mw2-after",
            "mw1-after",
        ]

    @pytest.mark.asyncio
    async def test_intercept_no_next(self):
        """middleware 不调用 next_fn 可以拦截请求。"""
        core_called = False

        async def blocker(ctx, next_fn):
            pass  # 不调用 next_fn

        pipeline = MiddlewarePipeline()
        pipeline.use(blocker)

        async def core():
            nonlocal core_called
            core_called = True

        await pipeline.execute(MiddlewareContext(), core)
        assert core_called is False

    @pytest.mark.asyncio
    async def test_context_shared(self):
        """middleware 之间通过 ctx.extra 共享数据。"""
        async def writer(ctx, next_fn):
            ctx.extra["user_id"] = "u123"
            await next_fn()

        async def reader(ctx, next_fn):
            assert ctx.extra["user_id"] == "u123"
            ctx.extra["checked"] = True
            await next_fn()

        pipeline = MiddlewarePipeline()
        pipeline.use(writer)
        pipeline.use(reader)

        ctx = MiddlewareContext()
        await pipeline.execute(ctx, _noop_core)
        assert ctx.extra["checked"] is True

    @pytest.mark.asyncio
    async def test_exception_propagation(self):
        """middleware 中的异常应该传播。"""
        async def bad_mw(ctx, next_fn):
            raise ValueError("test error")

        pipeline = MiddlewarePipeline()
        pipeline.use(bad_mw)

        with pytest.raises(ValueError, match="test error"):
            await pipeline.execute(MiddlewareContext(), _noop_core)

    @pytest.mark.asyncio
    async def test_exception_in_core(self):
        """core handler 异常应该传播到 middleware。"""
        caught = []

        async def catcher(ctx, next_fn):
            try:
                await next_fn()
            except RuntimeError as e:
                caught.append(str(e))

        pipeline = MiddlewarePipeline()
        pipeline.use(catcher)

        async def bad_core():
            raise RuntimeError("core error")

        await pipeline.execute(MiddlewareContext(), bad_core)
        assert caught == ["core error"]

    @pytest.mark.asyncio
    async def test_len(self):
        pipeline = MiddlewarePipeline()
        assert len(pipeline) == 0
        pipeline.use(_dummy_mw)
        assert len(pipeline) == 1
        pipeline.use(_dummy_mw)
        assert len(pipeline) == 2

    @pytest.mark.asyncio
    async def test_three_middlewares(self):
        """验证三层洋葱。"""
        order = []

        async def a(ctx, n):
            order.append("a>")
            await n()
            order.append("<a")

        async def b(ctx, n):
            order.append("b>")
            await n()
            order.append("<b")

        async def c(ctx, n):
            order.append("c>")
            await n()
            order.append("<c")

        pipeline = MiddlewarePipeline()
        pipeline.use(a)
        pipeline.use(b)
        pipeline.use(c)

        async def core():
            order.append("CORE")

        await pipeline.execute(MiddlewareContext(), core)
        assert order == ["a>", "b>", "c>", "CORE", "<c", "<b", "<a"]


# helpers
async def _noop_core():
    pass

async def _dummy_mw(ctx, next_fn):
    await next_fn()
