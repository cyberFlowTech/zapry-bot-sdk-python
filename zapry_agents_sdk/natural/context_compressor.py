"""Context Compressor — intelligent conversation history compression."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional


@dataclass
class CompressorConfig:
    window_size: int = 6
    token_threshold: int = 6000
    summary_version: str = "v1"
    estimate_tokens_fn: Optional[Callable[[List[Dict]], int]] = None


class ContextCompressor:
    def __init__(
        self,
        summarize_fn: Callable[[List[Dict]], Awaitable[str]],
        config: Optional[CompressorConfig] = None,
    ) -> None:
        self.config = config or CompressorConfig()
        self._summarize_fn = summarize_fn

    async def compress(
        self,
        history: List[Dict[str, Any]],
        working: Any,
    ) -> List[Dict[str, Any]]:
        if not history:
            return history

        tokens = self._estimate_tokens(history)
        if tokens < self.config.token_threshold:
            return history

        cache_key = f"sdk.context_summary:{self.config.summary_version}"
        cached = working.get(cache_key)
        if cached:
            return self._build_compressed(cached, history)

        split_idx = len(history) - self.config.window_size
        if split_idx <= 0:
            return history

        old_messages = history[:split_idx]

        try:
            summary = await self._summarize_fn(old_messages)
        except Exception:
            return history

        working.set(cache_key, summary)
        return self._build_compressed(summary, history)

    def _build_compressed(self, summary: str, history: List[Dict]) -> List[Dict]:
        tagged = f"[sdk.summary:{self.config.summary_version}] {summary}"
        recent_start = max(0, len(history) - self.config.window_size)
        result = [{"role": "system", "content": tagged}]
        result.extend(history[recent_start:])
        return result

    def _estimate_tokens(self, history: List[Dict]) -> int:
        if self.config.estimate_tokens_fn:
            return self.config.estimate_tokens_fn(history)
        return _default_estimate_tokens(history)


def _default_estimate_tokens(history: List[Dict]) -> int:
    total = 0
    for msg in history:
        content = msg.get("content", "")
        chars = len(content)
        if "```" in content:
            chars = int(chars * 1.5)
        total += chars
    return int(total / 2.7)
