"""
WorkingMemory — 当前会话的临时上下文。

纯内存，会话结束即丢弃。用于存放当前意图、中间状态等。
"""

from __future__ import annotations

from typing import Any, Dict


class WorkingMemory:
    """Ephemeral in-memory store for the current session.

    Data is **not** persisted — it only lives for the lifetime of this object.
    """

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)

    def update(self, data: Dict[str, Any]) -> None:
        self._data.update(data)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)
