"""
MemorySession — 便捷 API，一行代码管理三层记忆。

自动加载/保存/缓冲/提取，适合快速开发。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from zapry_bot_sdk.memory.buffer import ConversationBuffer
from zapry_bot_sdk.memory.extractor import MemoryExtractor
from zapry_bot_sdk.memory.formatter import format_memory_for_prompt
from zapry_bot_sdk.memory.long_term import LongTermMemory
from zapry_bot_sdk.memory.short_term import ShortTermMemory
from zapry_bot_sdk.memory.store import MemoryStore
from zapry_bot_sdk.memory.types import MemoryContext, Message
from zapry_bot_sdk.memory.working import WorkingMemory

logger = logging.getLogger("zapry_bot_sdk.memory")


class MemorySession:
    """High-level convenience API for managing all three memory layers.

    Handles loading, saving, buffering, and extraction in a unified interface.

    Parameters:
        agent_id: The agent identifier (for namespace isolation).
        user_id: The user identifier.
        store: Storage backend (InMemoryStore, SQLiteMemoryStore, etc.).
        max_messages: Max short-term messages to retain (default 40).
        extractor: Optional MemoryExtractor for automatic extraction.
        trigger_count: Buffer messages before extraction trigger (default 5).
        trigger_interval: Seconds between extraction triggers (default 86400).
        cache_ttl: Long-term memory cache TTL in seconds (default 300).

    Usage::

        session = MemorySession("my_agent", "user_123", SQLiteMemoryStore("mem.db"))
        ctx = await session.load()

        await session.add_message("user", "Hello!")
        await session.add_message("assistant", "Hi there!")

        prompt = session.format_for_prompt()
        await session.extract_if_needed()
    """

    def __init__(
        self,
        agent_id: str,
        user_id: str,
        store: MemoryStore,
        max_messages: int = 40,
        extractor: Optional[MemoryExtractor] = None,
        trigger_count: int = 5,
        trigger_interval: int = 86400,
        cache_ttl: int = 300,
    ) -> None:
        self.agent_id = agent_id
        self.user_id = user_id
        self.namespace = f"{agent_id}:{user_id}"

        self._store = store

        self.working = WorkingMemory()
        self.short_term = ShortTermMemory(store, self.namespace, max_messages)
        self.long_term = LongTermMemory(store, self.namespace, cache_ttl=cache_ttl)
        self.buffer = ConversationBuffer(
            store, self.namespace,
            trigger_count=trigger_count,
            trigger_interval=trigger_interval,
        )
        self._extractor = extractor

    @property
    def store(self) -> MemoryStore:
        return self._store

    @property
    def extractor(self) -> Optional[MemoryExtractor]:
        return self._extractor

    @extractor.setter
    def extractor(self, value: Optional[MemoryExtractor]) -> None:
        self._extractor = value

    async def load(self) -> MemoryContext:
        """Load all memory layers and return a MemoryContext snapshot.

        Returns:
            MemoryContext with working, short_term, and long_term data.
        """
        history = await self.short_term.get_history()
        lt_data = await self.long_term.get()

        return MemoryContext(
            working=self.working.to_dict(),
            short_term=history,
            long_term=lt_data,
        )

    async def add_message(self, role: str, content: str) -> None:
        """Add a message to short-term history and conversation buffer.

        The message is persisted in both:
        - Short-term memory (for LLM context)
        - Conversation buffer (for future extraction)
        """
        await self.short_term.add_message(role, content)
        await self.buffer.add(role, content)

    async def extract_if_needed(self) -> Optional[Dict[str, Any]]:
        """Check buffer trigger conditions and extract memory if needed.

        Requires an ``extractor`` to be set. Returns the extracted delta
        dict, or None if extraction was not triggered or no extractor is set.
        """
        if not self._extractor:
            return None

        if not await self.buffer.should_extract():
            return None

        conversations = await self.buffer.get_and_clear()
        if not conversations:
            return None

        current = await self.long_term.get()
        extracted = await self._extractor.extract(conversations, current)

        if extracted:
            await self.long_term.update(extracted)
            logger.info(
                "Memory extracted | ns=%s | keys=%s",
                self.namespace,
                list(extracted.keys()),
            )

        return extracted

    def format_for_prompt(
        self,
        template: Optional[str] = None,
    ) -> Optional[str]:
        """Format current memory state for LLM system prompt injection.

        Uses the cached long-term data (call ``load()`` first).
        """
        lt_data = self.long_term._cache or {}
        return format_memory_for_prompt(
            long_term=lt_data,
            working=self.working.to_dict() or None,
            template=template,
        )

    async def save_long_term(self) -> None:
        """Explicitly save the current long-term memory."""
        data = await self.long_term.get()
        await self.long_term.save(data)

    async def update_long_term(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update long-term memory with incremental changes."""
        return await self.long_term.update(updates)

    async def clear_history(self) -> None:
        """Clear short-term history only."""
        await self.short_term.clear()

    async def clear_buffer(self) -> None:
        """Clear the conversation buffer only."""
        await self.buffer.clear()

    async def clear_all(self) -> None:
        """Clear all memory (working + short-term + long-term + buffer)."""
        self.working.clear()
        await self.short_term.clear()
        await self.long_term.delete()
        await self.buffer.clear()
