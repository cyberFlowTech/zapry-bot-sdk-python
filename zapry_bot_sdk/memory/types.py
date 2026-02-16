"""
Memory 数据类型定义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class Message:
    """A single conversation message."""

    role: str  # "user", "assistant", "system"
    content: str
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content, "timestamp": self.timestamp}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Message":
        return cls(
            role=d.get("role", "user"),
            content=d.get("content", ""),
            timestamp=d.get("timestamp", ""),
        )


@dataclass
class MemoryContext:
    """Loaded memory context from all three layers."""

    working: Dict[str, Any] = field(default_factory=dict)
    short_term: List[Message] = field(default_factory=list)
    long_term: Dict[str, Any] = field(default_factory=dict)


DEFAULT_MEMORY_SCHEMA: Dict[str, Any] = {
    "basic_info": {},
    "personality": {},
    "life_context": {},
    "interests": [],
    "summary": "",
    "preferences": {},
    "meta": {
        "conversation_count": 0,
        "created_at": "",
        "updated_at": "",
    },
}
