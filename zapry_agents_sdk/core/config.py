"""
Bot 配置管理。

支持从环境变量 (.env) 或代码直接构造。
Platform: "telegram" (官方) 或 "zapry" (Zapry 私有化 API)。
Runtime: "webhook" 或 "polling"。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv


def _to_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class AgentConfig:
    """Bot 运行配置。"""

    # ── 平台 ──
    platform: str = "telegram"  # "telegram" | "zapry"
    bot_token: str = ""
    api_base_url: str = ""  # Zapry 自定义 API 地址

    # ── 运行模式 ──
    runtime_mode: str = "webhook"  # "webhook" | "polling"

    # ── Webhook ──
    webhook_url: str = ""
    webhook_path: str = ""
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8443
    webhook_secret: str = ""

    # ── 调试 ──
    debug: bool = False
    log_file: str = ""

    # ── Hello World 调试页面 ──
    hello_enabled: bool = False
    hello_port: int = 8080
    hello_text: str = "hello world"

    # ── 扩展配置 (业务层自行使用) ──
    extras: dict = field(default_factory=dict)

    @property
    def is_zapry(self) -> bool:
        return self.platform == "zapry"

    @classmethod
    def from_env(cls, env_file: str = ".env") -> AgentConfig:
        """
        从 .env 文件和环境变量中加载配置。

        环境变量优先级高于 .env 文件。
        """
        load_dotenv(env_file, override=False)

        platform = os.getenv("TG_PLATFORM", "telegram").strip().lower()
        if platform not in {"telegram", "zapry"}:
            platform = "telegram"

        # 根据平台选择 token 和 base_url
        if platform == "zapry":
            bot_token = os.getenv("ZAPRY_BOT_TOKEN", "")
            api_base_url = os.getenv(
                "ZAPRY_API_BASE_URL", "https://openapi.mimo.immo/bot"
            ).strip()
        else:
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
            api_base_url = ""

        runtime_mode = os.getenv("RUNTIME_MODE", "webhook").strip().lower()
        if runtime_mode not in {"webhook", "polling"}:
            runtime_mode = "webhook"

        # Webhook URL 也根据平台自动选择
        if platform == "zapry":
            webhook_url = os.getenv("ZAPRY_WEBHOOK_URL", "").strip()
        else:
            webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", "").strip()

        return cls(
            platform=platform,
            bot_token=bot_token,
            api_base_url=api_base_url,
            runtime_mode=runtime_mode,
            webhook_url=webhook_url,
            webhook_path=os.getenv("WEBHOOK_PATH", "").strip(),
            webhook_host=os.getenv("WEBAPP_HOST", "0.0.0.0").strip(),
            webhook_port=int(os.getenv("WEBAPP_PORT", "8443")),
            webhook_secret=os.getenv("WEBHOOK_SECRET_TOKEN", "").strip(),
            debug=_to_bool(os.getenv("DEBUG")),
            log_file=os.getenv("LOG_FILE", "").strip(),
            hello_enabled=_to_bool(os.getenv("HELLO_WORLD_ENABLED")),
            hello_port=int(os.getenv("HELLO_WORLD_PORT", "8080")),
            hello_text=os.getenv("HELLO_WORLD_TEXT", "hello world"),
        )

    def summary(self) -> str:
        """返回配置摘要（敏感信息脱敏）。"""
        token_display = (
            f"{self.bot_token[:20]}..." if self.bot_token else "未配置"
        )
        return (
            f"Platform: {self.platform.upper()}\n"
            f"Token: {token_display}\n"
            f"API Base: {self.api_base_url or '官方 API'}\n"
            f"Runtime: {self.runtime_mode.upper()}\n"
            f"Webhook: {self.webhook_url[:50]}...\n"
            f"Port: {self.webhook_port}\n"
            f"Debug: {self.debug}"
        )
