"""
ConversationBuffer — 对话缓冲区，暂存未提取记忆的对话。

基于可配置的触发条件（消息数 / 时间间隔）决定何时触发记忆提取。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional

from zapry_agents_sdk.memory.store import MemoryStore

logger = logging.getLogger("zapry_agents_sdk.memory")

_BUF_LIST_KEY = "buffer"
_BUF_META_KEY = "buffer_meta"


class ConversationBuffer:
    """Manages a conversation buffer and triggers memory extraction.

    Parameters:
        store: The storage backend.
        namespace: Isolation namespace.
        trigger_count: Extract when buffer reaches this many messages (default 5).
        trigger_interval: Extract if this many seconds have passed since last
            extraction (default 86400 = 24h).
    """

    def __init__(
        self,
        store: MemoryStore,
        namespace: str,
        trigger_count: int = 5,
        trigger_interval: int = 86400,
    ) -> None:
        self._store = store
        self._namespace = namespace
        self._trigger_count = trigger_count
        self._trigger_interval = trigger_interval

    async def add(self, role: str, content: str) -> None:
        """Add a message to the buffer."""
        entry = json.dumps(
            {"role": role, "content": content, "timestamp": datetime.now().isoformat()},
            ensure_ascii=False,
        )
        await self._store.append(self._namespace, _BUF_LIST_KEY, entry)

    async def should_extract(self) -> bool:
        """Check whether extraction should be triggered.

        Returns True if:
        - Buffer size >= ``trigger_count``, OR
        - Time since last extraction >= ``trigger_interval`` and buffer is not empty.
        """
        buf_len = await self._store.list_length(self._namespace, _BUF_LIST_KEY)
        if buf_len == 0:
            return False

        if buf_len >= self._trigger_count:
            return True

        meta_raw = await self._store.get(self._namespace, _BUF_META_KEY)
        if meta_raw:
            try:
                meta = json.loads(meta_raw)
                last_ts = meta.get("last_extraction_ts", 0)
                if time.time() - last_ts >= self._trigger_interval:
                    return True
            except json.JSONDecodeError:
                return True
        else:
            return True

        return False

    async def get_and_clear(self) -> List[Dict]:
        """Atomically retrieve all buffered messages and clear the buffer.

        Also records the extraction timestamp in metadata.
        """
        raw_items = await self._store.get_list(self._namespace, _BUF_LIST_KEY)
        await self._store.clear_list(self._namespace, _BUF_LIST_KEY)

        meta = json.dumps({
            "last_extraction_ts": time.time(),
            "last_extraction_at": datetime.now().isoformat(),
        })
        await self._store.set(self._namespace, _BUF_META_KEY, meta)

        messages = []
        for raw in raw_items:
            try:
                messages.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return messages

    async def count(self) -> int:
        """Return current buffer size."""
        return await self._store.list_length(self._namespace, _BUF_LIST_KEY)

    async def clear(self) -> None:
        """Clear the buffer without recording extraction."""
        await self._store.clear_list(self._namespace, _BUF_LIST_KEY)
