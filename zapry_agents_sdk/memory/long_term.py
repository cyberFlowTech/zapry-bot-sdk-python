"""
LongTermMemory — 跨会话的用户档案/偏好，结构化 JSON 存储。

特性:
- 深度合并（增量更新，不覆盖已有字段）
- TTL 缓存（减少存储查询）
- 自由 schema（开发者可自定义）
"""

from __future__ import annotations

import copy
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional

from zapry_agents_sdk.memory.store import MemoryStore
from zapry_agents_sdk.memory.types import DEFAULT_MEMORY_SCHEMA

logger = logging.getLogger("zapry_agents_sdk.memory")

_KV_KEY = "long_term"


class LongTermMemory:
    """Persistent user profile / preferences with caching and deep merge.

    Parameters:
        store: The storage backend.
        namespace: Isolation namespace (``{agent_id}:{user_id}``).
        default_schema: Initial template for new users (default provided).
        cache_ttl: Cache TTL in seconds (default 300 = 5 min). Set 0 to disable.
    """

    def __init__(
        self,
        store: MemoryStore,
        namespace: str,
        default_schema: Optional[Dict[str, Any]] = None,
        cache_ttl: int = 300,
    ) -> None:
        self._store = store
        self._namespace = namespace
        self._default_schema = default_schema or copy.deepcopy(DEFAULT_MEMORY_SCHEMA)
        self._cache_ttl = cache_ttl
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_ts: float = 0

    async def get(self) -> Dict[str, Any]:
        """Load the long-term memory, using cache if available."""
        if self._cache is not None and self._cache_ttl > 0:
            if time.time() - self._cache_ts < self._cache_ttl:
                return self._cache

        raw = await self._store.get(self._namespace, _KV_KEY)
        if raw:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = copy.deepcopy(self._default_schema)
        else:
            data = copy.deepcopy(self._default_schema)
            now = datetime.now().isoformat()
            if "meta" in data:
                data["meta"]["created_at"] = now

        self._cache = data
        self._cache_ts = time.time()
        return data

    async def save(self, data: Dict[str, Any]) -> None:
        """Overwrite the entire long-term memory."""
        if "meta" in data:
            data["meta"]["updated_at"] = datetime.now().isoformat()
        raw = json.dumps(data, ensure_ascii=False)
        await self._store.set(self._namespace, _KV_KEY, raw)
        self._cache = data
        self._cache_ts = time.time()

    async def update(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Deep-merge *updates* into existing memory and save.

        Returns the merged result.
        """
        current = await self.get()
        merged = _deep_merge(current, updates)
        if "meta" in merged:
            merged["meta"]["conversation_count"] = merged["meta"].get("conversation_count", 0) + 1
            merged["meta"]["updated_at"] = datetime.now().isoformat()
        await self.save(merged)
        return merged

    async def delete(self) -> None:
        """Delete all long-term memory for this namespace."""
        await self._store.delete(self._namespace, _KV_KEY)
        self._cache = None
        self._cache_ts = 0

    def invalidate_cache(self) -> None:
        """Force next ``get()`` to reload from store."""
        self._cache = None
        self._cache_ts = 0


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge *override* into *base* (non-destructive).

    - Dicts are merged recursively.
    - Lists are extended (deduped for simple values).
    - Scalars in *override* overwrite *base*.
    - ``None`` values in *override* are skipped.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if value is None:
            continue
        if key in result:
            if isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = _deep_merge(result[key], value)
            elif isinstance(result[key], list) and isinstance(value, list):
                existing = set(str(v) for v in result[key])
                for item in value:
                    if str(item) not in existing:
                        result[key].append(item)
                        existing.add(str(item))
            else:
                result[key] = value
        else:
            result[key] = copy.deepcopy(value)
    return result
