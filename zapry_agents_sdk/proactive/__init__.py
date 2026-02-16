"""
主动触发 & 自我反思模块

提供可插拔的主动消息调度器和反馈检测框架，
让 Bot 不再只是被动回复，而是主动关心用户、自动调整回复风格。

Quick Start::

    from zapry_agents_sdk.proactive import ProactiveScheduler, FeedbackDetector

    # --- 主动调度器 ---
    scheduler = ProactiveScheduler(interval=60)

    @scheduler.trigger("daily_greeting")
    async def check_greeting(ctx):
        if ctx.now.hour == 12:
            return ["user_001", "user_002"]
        return []

    @check_greeting.message
    async def greeting_message(ctx, user_id):
        return f"中午好~ 今天状态怎么样？"

    await scheduler.start()

    # --- 反馈检测 ---
    detector = FeedbackDetector()
    result = detector.detect("太长了，说重点")
    # result => {"style": "concise"}
"""

from zapry_agents_sdk.proactive.scheduler import ProactiveScheduler, TriggerContext
from zapry_agents_sdk.proactive.feedback import (
    FeedbackDetector,
    FeedbackResult,
    build_preference_prompt,
)

__all__ = [
    "ProactiveScheduler",
    "TriggerContext",
    "FeedbackDetector",
    "FeedbackResult",
    "build_preference_prompt",
]
