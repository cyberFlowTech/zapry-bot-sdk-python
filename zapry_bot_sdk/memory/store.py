"""
MemoryStore — 可插拔的存储后端接口 + InMemoryStore 实现。
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class MemoryStore(Protocol):
    """Pluggable storage backend for the memory framework.

    All data is organized by ``namespace`` (typically ``{agent_id}:{user_id}``)
    and ``key`` (e.g. ``"long_term"``, ``"short_term"``).

    Two storage patterns:
    - **KV**: ``get/set/delete`` for single values (long-term memory, metadata).
    - **List**: ``append/get_list/trim_list/clear_list`` for ordered sequences
      (chat history, conversation buffer).
    """

    # ── KV operations ──

    async def get(self, namespace: str, key: str) -> Optional[str]: ...

    async def set(self, namespace: str, key: str, value: str) -> None: ...

    async def delete(self, namespace: str, key: str) -> None: ...

    async def list_keys(self, namespace: str) -> List[str]: ...

    # ── List operations ──

    async def append(self, namespace: str, key: str, value: str) -> None: ...

    async def get_list(
        self, namespace: str, key: str, limit: int = 0, offset: int = 0
    ) -> List[str]: ...

    async def trim_list(self, namespace: str, key: str, max_size: int) -> None: ...

    async def clear_list(self, namespace: str, key: str) -> None: ...

    async def list_length(self, namespace: str, key: str) -> int: ...


class InMemoryStore:
    """In-memory MemoryStore implementation for development and testing.

    Thread-safe but **not persistent** — data is lost on restart.
    """

    def __init__(self) -> None:
        self._kv: Dict[str, Dict[str, str]] = defaultdict(dict)
        self._lists: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
        self._lock = threading.Lock()

    # ── KV ──

    async def get(self, namespace: str, key: str) -> Optional[str]:
        with self._lock:
            return self._kv[namespace].get(key)

    async def set(self, namespace: str, key: str, value: str) -> None:
        with self._lock:
            self._kv[namespace][key] = value

    async def delete(self, namespace: str, key: str) -> None:
        with self._lock:
            self._kv[namespace].pop(key, None)

    async def list_keys(self, namespace: str) -> List[str]:
        with self._lock:
            kv_keys = list(self._kv.get(namespace, {}).keys())
            list_keys = list(self._lists.get(namespace, {}).keys())
            return list(set(kv_keys + list_keys))

    # ── List ──

    async def append(self, namespace: str, key: str, value: str) -> None:
        with self._lock:
            self._lists[namespace][key].append(value)

    async def get_list(
        self, namespace: str, key: str, limit: int = 0, offset: int = 0
    ) -> List[str]:
        with self._lock:
            items = self._lists[namespace][key]
            if offset:
                items = items[offset:]
            if limit > 0:
                items = items[:limit]
            return list(items)

    async def trim_list(self, namespace: str, key: str, max_size: int) -> None:
        with self._lock:
            lst = self._lists[namespace][key]
            if len(lst) > max_size:
                self._lists[namespace][key] = lst[-max_size:]

    async def clear_list(self, namespace: str, key: str) -> None:
        with self._lock:
            self._lists[namespace][key] = []

    async def list_length(self, namespace: str, key: str) -> int:
        with self._lock:
            return len(self._lists[namespace][key])
