"""PromptFragments — structured output from NaturalConversation.Enhance."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class PromptFragments:
    """Collects prompt additions, structured metadata, and debug warnings."""

    system_additions: List[str] = field(default_factory=list)
    kv: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def text(self) -> str:
        """Return all system_additions joined for LLM injection."""
        return "\n\n".join(a for a in self.system_additions if a)

    def add_system(self, text: str) -> None:
        if text:
            self.system_additions.append(text)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def set_kv(self, key: str, value: Any) -> None:
        self.kv[key] = value
