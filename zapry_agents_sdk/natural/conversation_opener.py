"""Conversation Opener Generator — strategy hints for natural openings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class OpenerStrategy:
    situation: str = "normal"
    hint: str = ""

    def format_for_prompt(self) -> str:
        if not self.hint:
            return ""
        return f"[开场策略] {self.hint}"


@dataclass
class OpenerConfig:
    max_mentions_per_session: int = 1
    long_absence_days: int = 3


class OpenerGenerator:
    def __init__(self, config: Optional[OpenerConfig] = None) -> None:
        self.config = config or OpenerConfig()

    def generate(self, state: Any, session_opener_count: int = 0) -> OpenerStrategy:
        if session_opener_count >= self.config.max_mentions_per_session:
            return OpenerStrategy(situation="normal", hint="")

        if getattr(state, "is_followup", False):
            return OpenerStrategy(
                situation="followup",
                hint="用户在追问，不要寒暄，直接回应上一个问题。",
            )

        if getattr(state, "is_first_conversation", False):
            return OpenerStrategy(
                situation="first_meeting",
                hint="这是你们第一次对话，自然地打个招呼，不要问「有什么可以帮你的」。",
            )

        days = getattr(state, "days_since_last", 0)
        if days >= self.config.long_absence_days:
            return OpenerStrategy(
                situation="long_absence",
                hint=f"距离上次对话已经{days}天了，可以自然地表达「好久没聊了」的意思，但不要太正式。",
            )

        if getattr(state, "time_of_day", "") == "late_night":
            return OpenerStrategy(
                situation="late_night",
                hint="现在是深夜，语气可以更轻松随意，如果用户聊到很晚可以温柔提醒。",
            )

        return OpenerStrategy(situation="normal", hint="")
