"""Conversation State Tracker — automatic dialogue metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

try:
    import zoneinfo
    def _load_tz(name: str) -> Any:
        return zoneinfo.ZoneInfo(name)
except ImportError:
    from datetime import timezone as _tz
    def _load_tz(name: str) -> Any:
        return _tz.utc


_STATE_META_KEY = "sdk.conversation_meta"
_TURN_KEY = "sdk.session.turn_index"
_LAST_MSG_AT_KEY = "sdk.session.last_msg_at"
_SESSION_START_KEY = "sdk.session.start_at"


@dataclass
class ConversationState:
    turn_index: int = 0
    is_followup: bool = False
    is_first_conversation: bool = True
    session_duration_sec: float = 0.0
    days_since_last: int = -1
    total_sessions: int = 0
    time_of_day: str = "morning"
    user_msg_length: str = "medium"
    local_time: str = ""

    def format_for_prompt(self) -> str:
        lines = ["[对话状态]"]
        if self.is_first_conversation:
            lines.append("- 这是你们的第一次对话")
        else:
            lines.append(f"- 这是你们的第{self.total_sessions}次对话，本次会话第{self.turn_index}轮")
            if self.days_since_last > 0:
                lines.append(f"- 距离上次对话已过去{self.days_since_last}天")

        if self.is_followup:
            lines.append("- 用户正在追问，请直接回应，不要寒暄")

        tod_map = {"late_night": "深夜", "morning": "上午", "evening": "晚上"}
        if self.time_of_day in tod_map:
            lines.append(f"- 当前时间：{tod_map[self.time_of_day]}")

        if self.user_msg_length == "short":
            lines.append("- 用户消息较短，回复也保持简短")
        elif self.user_msg_length == "long":
            lines.append("- 用户消息较长，可以给出详细回复")

        return "\n".join(lines)

    def to_kv(self) -> Dict[str, Any]:
        return {
            "sdk.conversation.days_since_last": self.days_since_last,
            "sdk.conversation.total_sessions": self.total_sessions,
            "sdk.conversation.is_first": self.is_first_conversation,
            "sdk.session.turn_index": self.turn_index,
            "sdk.session.duration_sec": int(self.session_duration_sec),
            "sdk.user.is_followup": self.is_followup,
            "sdk.user.msg_length": self.user_msg_length,
            "sdk.runtime.time_of_day": self.time_of_day,
            "sdk.runtime.local_time": self.local_time,
        }


class ConversationStateTracker:
    def __init__(self, tz: str = "Asia/Shanghai", followup_window: float = 60.0):
        self._tz = _load_tz(tz)
        self._followup_window = followup_window

    async def track(self, session: Any, user_input: str, now: Optional[datetime] = None) -> ConversationState:
        if now is None:
            now = datetime.now(timezone.utc)
        wm = session.working
        local_now = now.astimezone(self._tz) if now.tzinfo else now

        turn_index = (wm.get(_TURN_KEY) or 0) + 1
        wm.set(_TURN_KEY, turn_index)

        if not wm.get(_SESSION_START_KEY):
            wm.set(_SESSION_START_KEY, now.isoformat())

        session_duration = 0.0
        start_str = wm.get(_SESSION_START_KEY)
        if start_str:
            try:
                start_time = datetime.fromisoformat(start_str)
                session_duration = (now - start_time).total_seconds()
            except (ValueError, TypeError):
                pass

        is_followup = False
        last_str = wm.get(_LAST_MSG_AT_KEY)
        if last_str:
            try:
                last_time = datetime.fromisoformat(last_str)
                if (now - last_time).total_seconds() <= self._followup_window:
                    is_followup = True
            except (ValueError, TypeError):
                pass
        wm.set(_LAST_MSG_AT_KEY, now.isoformat())

        meta = await self._load_meta(session)

        days_since_last = -1
        if meta.get("last_at"):
            try:
                last_at = datetime.fromisoformat(meta["last_at"])
                days_since_last = max(0, int((now - last_at).total_seconds() / 86400))
            except (ValueError, TypeError):
                pass

        hour = local_now.hour
        time_of_day = _classify_time_of_day(hour)
        msg_len = len(user_input)
        user_msg_length = _classify_msg_length(msg_len)

        return ConversationState(
            turn_index=turn_index,
            is_followup=is_followup,
            is_first_conversation=(days_since_last == -1),
            session_duration_sec=session_duration,
            days_since_last=days_since_last,
            total_sessions=meta.get("total_sessions", 0),
            time_of_day=time_of_day,
            user_msg_length=user_msg_length,
            local_time=local_now.isoformat(),
        )

    async def touch_session(self, session: Any, now: Optional[datetime] = None) -> None:
        if now is None:
            now = datetime.now(timezone.utc)
        meta = await self._load_meta(session)
        meta["total_sessions"] = meta.get("total_sessions", 0) + 1
        meta["last_at"] = now.isoformat()
        await self._save_meta(session, meta)

    async def _load_meta(self, session: Any) -> dict:
        raw = await session._store.get(session.namespace, _STATE_META_KEY)
        if raw:
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass
        return {}

    async def _save_meta(self, session: Any, meta: dict) -> None:
        await session._store.set(session.namespace, _STATE_META_KEY, json.dumps(meta))


def _classify_time_of_day(hour: int) -> str:
    if 6 <= hour < 12:
        return "morning"
    elif 12 <= hour < 18:
        return "afternoon"
    elif 18 <= hour < 23:
        return "evening"
    return "late_night"


def _classify_msg_length(char_count: int) -> str:
    if char_count < 20:
        return "short"
    elif char_count <= 120:
        return "medium"
    return "long"
