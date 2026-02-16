"""
ZapryAgent — SDK 核心入口。

封装 python-telegram-bot 的 Application，自动完成：
  - Zapry 兼容层初始化（Monkey Patch）
  - PrivateAPIExtBot 创建（当使用自定义 base_url 时）
  - Handler 注册（通过装饰器收集或手动添加）
  - Webhook / Polling 启动
  - 可选的 Hello World 调试页面
"""

from __future__ import annotations

import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, List, Optional, Sequence, Union

from telegram import Update
from telegram.error import NetworkError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from zapry_agents_sdk.core.config import AgentConfig
from zapry_agents_sdk.core.middleware import (
    MiddlewareContext,
    MiddlewareFunc,
    MiddlewarePipeline,
)
from zapry_agents_sdk.helpers.handler_registry import (
    HandlerRegistry,
    get_global_handlers,
)
from zapry_agents_sdk.utils.telegram_compat import (
    PrivateAPIExtBot,
    apply_zapry_compatibility,
)

logger = logging.getLogger("zapry_agents_sdk")


class ZapryAgent:
    """
    Zapry Bot SDK 的主入口。

    Usage::

        from zapry_agents_sdk import ZapryAgent, AgentConfig

        config = AgentConfig.from_env()
        bot = ZapryAgent(config)

        @bot.command("start")
        async def start(update, context):
            await update.message.reply_text("Hello!")

        bot.run()
    """

    def __init__(self, config: AgentConfig) -> None:
        self._config = config
        self._application: Optional[Application] = None
        self._command_handlers: List[tuple] = []
        self._callback_handlers: List[tuple] = []
        self._message_handlers: List[tuple] = []
        self._error_handler: Optional[Callable] = None
        self._post_init_hooks: List[Callable] = []
        self._post_shutdown_hooks: List[Callable] = []
        self._input_logger_enabled: bool = True
        self._middleware_pipeline: MiddlewarePipeline = MiddlewarePipeline()

        # 如果是 Zapry 平台，应用兼容层
        if config.is_zapry:
            apply_zapry_compatibility()

    @property
    def config(self) -> AgentConfig:
        return self._config

    @property
    def application(self) -> Optional[Application]:
        return self._application

    # ─── Handler 注册 (装饰器) ───

    def command(
        self, name: Union[str, List[str]], **kwargs: Any
    ) -> Callable:
        """注册命令 handler 的装饰器。"""
        def decorator(func: Callable) -> Callable:
            self._command_handlers.append((name, func, kwargs))
            return func
        return decorator

    def callback_query(self, pattern: str, **kwargs: Any) -> Callable:
        """注册 callback query handler 的装饰器。"""
        def decorator(func: Callable) -> Callable:
            self._callback_handlers.append((pattern, func, kwargs))
            return func
        return decorator

    def message(
        self,
        filter_obj: Any = None,
        **kwargs: Any,
    ) -> Callable:
        """注册消息 handler 的装饰器。"""
        def decorator(func: Callable) -> Callable:
            self._message_handlers.append((filter_obj, func, kwargs))
            return func
        return decorator

    def on_error(self, func: Callable) -> Callable:
        """注册全局错误 handler。"""
        self._error_handler = func
        return func

    def on_post_init(self, func: Callable) -> Callable:
        """注册 Application post_init 钩子。"""
        self._post_init_hooks.append(func)
        return func

    def on_post_shutdown(self, func: Callable) -> Callable:
        """注册 Application post_shutdown 钩子。"""
        self._post_shutdown_hooks.append(func)
        return func

    # ─── Middleware ───

    def use(self, middleware: MiddlewareFunc) -> None:
        """Register a global middleware (onion model).

        Middlewares execute in registration order, wrapping the handler
        dispatch.  Each middleware receives ``(ctx, next_fn)`` and **must**
        call ``await next_fn()`` to proceed to the next layer.

        Example::

            async def timer(ctx, next_fn):
                import time
                start = time.time()
                await next_fn()
                print(f"took {time.time() - start:.3f}s")

            bot.use(timer)
        """
        self._middleware_pipeline.use(middleware)

    # ─── 手动注册 handler (从外部模块批量导入) ───

    def add_command(
        self, name: Union[str, List[str]], handler: Callable, **kwargs: Any
    ) -> None:
        self._command_handlers.append((name, handler, kwargs))

    def add_callback_query(
        self, pattern: str, handler: Callable, **kwargs: Any
    ) -> None:
        self._callback_handlers.append((pattern, handler, kwargs))

    def add_message(
        self, filter_obj: Any, handler: Callable, **kwargs: Any
    ) -> None:
        self._message_handlers.append((filter_obj, handler, kwargs))

    def register(self, registry: HandlerRegistry) -> None:
        """从 HandlerRegistry 批量导入 handler。"""
        self._command_handlers.extend(registry.commands)
        self._callback_handlers.extend(registry.callbacks)
        self._message_handlers.extend(registry.messages)

    # ─── 构建 Application ───

    def build(self) -> Application:
        """构建 python-telegram-bot Application 实例。"""
        # 收集通过全局装饰器注册的 handler
        g_cmds, g_cbs, g_msgs = get_global_handlers()
        self._command_handlers.extend(g_cmds)
        self._callback_handlers.extend(g_cbs)
        self._message_handlers.extend(g_msgs)

        cfg = self._config

        if not cfg.bot_token:
            raise ValueError(
                "bot_token 为空！请在 .env 中配置 TELEGRAM_BOT_TOKEN "
                "或 ZAPRY_BOT_TOKEN。"
            )

        # 创建 Bot 实例
        if cfg.api_base_url:
            bot = PrivateAPIExtBot(
                token=cfg.bot_token,
                base_url=cfg.api_base_url,
                base_file_url=cfg.api_base_url.replace("/bot", "/file/bot"),
            )
            builder = ApplicationBuilder().bot(bot)
        else:
            builder = ApplicationBuilder().token(cfg.bot_token)

        # 生命周期钩子
        async def _post_init(app: Application) -> None:
            for hook in self._post_init_hooks:
                await hook(app)

        async def _post_shutdown(app: Application) -> None:
            for hook in self._post_shutdown_hooks:
                await hook(app)

        builder.post_init(_post_init)
        builder.post_shutdown(_post_shutdown)

        application = builder.build()

        # 输入日志
        if self._input_logger_enabled:
            application.add_handler(
                TypeHandler(Update, _log_user_input), group=-1
            )

        # Middleware pipeline — runs at group=-2 (before logging at -1).
        # The pipeline wraps all subsequent handler dispatch.  When the
        # innermost ``next_fn`` is called, execution continues to the
        # normal handler groups (0+).  If a middleware does NOT call
        # ``next_fn``, the update is intercepted and handlers are skipped.
        if len(self._middleware_pipeline) > 0:
            pipeline = self._middleware_pipeline

            async def _middleware_handler(
                update: Update, context: ContextTypes.DEFAULT_TYPE
            ) -> None:
                from telegram.ext import ApplicationHandlerStop

                ctx = MiddlewareContext(
                    update=update,
                    bot=context.bot,
                )
                # Store context on context.user_data so handlers can access it
                if hasattr(context, "user_data") and context.user_data is not None:
                    context.user_data["_middleware_ctx"] = ctx

                proceeded = False

                async def core() -> None:
                    nonlocal proceeded
                    proceeded = True

                await pipeline.execute(ctx, core)

                if not proceeded:
                    # A middleware intercepted — stop further processing
                    raise ApplicationHandlerStop()

            application.add_handler(
                TypeHandler(Update, _middleware_handler), group=-2
            )

        # 注册 command handlers
        for name, handler, kwargs in self._command_handlers:
            group = kwargs.pop("group", 0)
            if isinstance(name, list):
                for n in name:
                    application.add_handler(
                        CommandHandler(n, handler, **kwargs), group=group
                    )
            else:
                application.add_handler(
                    CommandHandler(name, handler, **kwargs), group=group
                )

        # 注册 callback query handlers
        for pattern, handler, kwargs in self._callback_handlers:
            group = kwargs.pop("group", 0)
            application.add_handler(
                CallbackQueryHandler(handler, pattern=pattern, **kwargs),
                group=group,
            )

        # 注册 message handlers
        for filter_obj, handler, kwargs in self._message_handlers:
            group = kwargs.pop("group", 0)
            if filter_obj is not None:
                application.add_handler(
                    MessageHandler(filter_obj, handler, **kwargs),
                    group=group,
                )
            else:
                application.add_handler(
                    MessageHandler(filters.ALL, handler, **kwargs),
                    group=group,
                )

        # 错误 handler
        if self._error_handler:
            application.add_error_handler(self._error_handler)
        else:
            application.add_error_handler(_default_error_handler)

        self._application = application
        return application

    # ─── 运行 ───

    def run(self) -> None:
        """构建并启动 Bot。"""
        cfg = self._config
        application = self.build()

        logger.info("Zapry Bot SDK v%s", _get_version())
        logger.info(cfg.summary())

        # Hello World 调试页面
        should_hello = cfg.hello_enabled or cfg.runtime_mode == "polling"
        if should_hello:
            try:
                _start_hello_server(cfg.hello_port, cfg.hello_text)
                logger.info(
                    "Hello 页面: http://127.0.0.1:%s/", cfg.hello_port
                )
            except OSError as exc:
                logger.warning("Hello 页面启动失败: %s", exc)

        if cfg.runtime_mode == "webhook":
            if not cfg.webhook_url:
                raise ValueError(
                    "runtime_mode=webhook 但 webhook_url 为空！"
                )
            webhook_full = cfg.webhook_url.rstrip("/")
            if cfg.webhook_path:
                webhook_full += "/" + cfg.webhook_path.strip("/")
            logger.info("启动 Webhook: %s", webhook_full)
            application.run_webhook(
                listen=cfg.webhook_host,
                port=cfg.webhook_port,
                url_path=cfg.webhook_path.strip("/") if cfg.webhook_path else "",
                webhook_url=webhook_full,
                secret_token=cfg.webhook_secret or None,
            )
        else:
            logger.info("启动 Polling 模式")
            application.run_polling()


# ─── 内部辅助 ───


def _get_version() -> str:
    try:
        from zapry_agents_sdk import __version__
        return __version__
    except ImportError:
        return "unknown"


async def _log_user_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """记录用户输入（仅日志，不阻断）。"""
    user = update.effective_user
    user_info = f"{user.first_name}(id:{user.id})" if user else "?"
    chat_id = update.effective_chat.id if update.effective_chat else "?"

    if update.message and update.message.text:
        logger.info(
            "[input] chat=%s user=%s text=%s",
            chat_id,
            user_info,
            update.message.text.strip(),
        )
    elif update.callback_query:
        logger.info(
            "[input] chat=%s user=%s callback=%s",
            chat_id,
            user_info,
            update.callback_query.data or "",
        )


async def _default_error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """默认错误处理器。"""
    err = context.error
    if isinstance(err, NetworkError) and "provider not found" in str(err):
        logger.warning("Zapry API 错误: %s", err)
    else:
        logger.exception("处理更新时出错: %s", err)


def _start_hello_server(port: int, text: str) -> ThreadingHTTPServer:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
