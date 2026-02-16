"""
Memory 持久化框架 — 三层记忆模型 + 可插拔存储 + 对话缓冲 + 自动提取。

Quick Start::

    from zapry_agents_sdk.memory import MemorySession, InMemoryStore

    session = MemorySession("my_agent", "user_123", InMemoryStore())
    ctx = await session.load()
    await session.add_message("user", "Hello!")
    prompt = session.format_for_prompt()
"""

from zapry_agents_sdk.memory.types import Message, MemoryContext, DEFAULT_MEMORY_SCHEMA
from zapry_agents_sdk.memory.store import MemoryStore, InMemoryStore
from zapry_agents_sdk.memory.store_sqlite import SQLiteMemoryStore
from zapry_agents_sdk.memory.working import WorkingMemory
from zapry_agents_sdk.memory.short_term import ShortTermMemory
from zapry_agents_sdk.memory.long_term import LongTermMemory
from zapry_agents_sdk.memory.buffer import ConversationBuffer
from zapry_agents_sdk.memory.extractor import MemoryExtractor, LLMMemoryExtractor
from zapry_agents_sdk.memory.formatter import format_memory_for_prompt
from zapry_agents_sdk.memory.session import MemorySession

__all__ = [
    "Message",
    "MemoryContext",
    "DEFAULT_MEMORY_SCHEMA",
    "MemoryStore",
    "InMemoryStore",
    "SQLiteMemoryStore",
    "WorkingMemory",
    "ShortTermMemory",
    "LongTermMemory",
    "ConversationBuffer",
    "MemoryExtractor",
    "LLMMemoryExtractor",
    "format_memory_for_prompt",
    "MemorySession",
]
