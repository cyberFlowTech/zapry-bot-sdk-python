"""
ShortTermMemory — 对话历史（最近 N 轮），直接传给 LLM 作为上下文。
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional

from zapry_bot_sdk.memory.store import MemoryStore
from zapry_bot_sdk.memory.types import Message

logger = logging.getLogger("zapry_bot_sdk.memory")

_LIST_KEY = "short_term"


class ShortTermMemory:
    """Manages recent conversation history with automatic trimming.

    Parameters:
        store: The storage backend.
        namespace: Isolation namespace (``{agent_id}:{user_id}``).
        max_messages: Maximum messages to retain (default 40).
    """

    def __init__(
        self,
        store: MemoryStore,
        namespace: str,
        max_messages: int = 40,
    ) -> None:
        self._store = store
        self._namespace = namespace
        self._max_messages = max_messages

    async def add_message(self, role: str, content: str) -> None:
        """Append a message and auto-trim if over capacity."""
        msg = Message(role=role, content=content)
        await self._store.append(self._namespace, _LIST_KEY, json.dumps(msg.to_dict(), ensure_ascii=False))
        await self._store.trim_list(self._namespace, _LIST_KEY, self._max_messages)

    async def get_history(self, limit: int = 0) -> List[Message]:
        """Get recent messages (oldest first)."""
        actual_limit = limit if limit > 0 else self._max_messages
        raw = await self._store.get_list(self._namespace, _LIST_KEY, limit=actual_limit)
        messages = []
        for item in raw:
            try:
                messages.append(Message.from_dict(json.loads(item)))
            except (json.JSONDecodeError, KeyError):
                continue
        return messages

    async def get_history_dicts(self, limit: int = 0) -> List[dict]:
        """Get history as list of dicts (ready for OpenAI messages)."""
        msgs = await self.get_history(limit)
        return [{"role": m.role, "content": m.content} for m in msgs]

    async def clear(self) -> None:
        """Remove all messages."""
        await self._store.clear_list(self._namespace, _LIST_KEY)

    async def count(self) -> int:
        """Return the number of stored messages."""
        return await self._store.list_length(self._namespace, _LIST_KEY)
