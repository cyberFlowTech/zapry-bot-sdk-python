"""
MemoryExtractor — 可插拔的记忆提取器接口 + 内置 LLM 提取器。

从对话缓冲中提取结构化记忆增量，由 LongTermMemory 深度合并。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger("zapry_bot_sdk.memory")

# LLM 调用函数签名: async def llm_fn(prompt: str) -> str
LLMCallFn = Callable[[str], Awaitable[str]]


@runtime_checkable
class MemoryExtractor(Protocol):
    """Interface for memory extraction from conversations."""

    async def extract(
        self,
        conversations: List[Dict],
        current_memory: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Extract structured memory delta from conversations.

        Parameters:
            conversations: List of ``{"role": ..., "content": ..., "timestamp": ...}``
            current_memory: The user's existing long-term memory.

        Returns:
            A dict of extracted fields to be deep-merged into long-term memory.
            Return empty dict if nothing was extracted.
        """
        ...


# ──────────────────────────────────────────────
# Default extraction prompt template
# ──────────────────────────────────────────────

DEFAULT_EXTRACTION_PROMPT = """你是一个记忆提取助手。请从以下对话中提取关于用户的结构化信息。

规则：
1. 只提取用户自己说的信息，不要把 AI 助手的信息当作用户的
2. 不要推测或编造，只提取明确提到的信息
3. 如果没有新信息，对应字段留空或返回空对象
4. 输出严格的 JSON 格式

当前已有的用户档案：
{current_memory}

待提取的对话：
{conversations}

请提取以下字段（只返回有新信息的字段）：
{{
  "basic_info": {{"age": null, "gender": null, "location": null, "occupation": null}},
  "personality": {{"traits": [], "values": []}},
  "life_context": {{"concerns": [], "goals": [], "recent_events": []}},
  "interests": [],
  "summary": ""
}}

只返回 JSON，不要其他文字："""


class LLMMemoryExtractor:
    """LLM-based memory extractor.

    Uses an LLM to extract structured information from conversations.

    Parameters:
        llm_fn: Async function that calls an LLM.
            Signature: ``async def llm_fn(prompt: str) -> str``
        prompt_template: Custom prompt template (optional).
            Must contain ``{current_memory}`` and ``{conversations}`` placeholders.
    """

    def __init__(
        self,
        llm_fn: LLMCallFn,
        prompt_template: Optional[str] = None,
    ) -> None:
        self._llm_fn = llm_fn
        self._prompt_template = prompt_template or DEFAULT_EXTRACTION_PROMPT

    async def extract(
        self,
        conversations: List[Dict],
        current_memory: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Extract structured memory from conversations using LLM."""
        if not conversations:
            return {}

        conv_text = _format_conversations(conversations)
        memory_text = json.dumps(current_memory, ensure_ascii=False, indent=2)

        prompt = self._prompt_template.format(
            current_memory=memory_text,
            conversations=conv_text,
        )

        try:
            response = await self._llm_fn(prompt)
            return _parse_json_response(response)
        except Exception as e:
            logger.error("Memory extraction failed: %s", e)
            return {}


def _format_conversations(conversations: List[Dict]) -> str:
    """Format conversation list into readable text."""
    lines = []
    for msg in conversations:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        label = "用户" if role == "user" else "助手"
        lines.append(f"{label}: {content}")
    return "\n".join(lines)


def _parse_json_response(text: str) -> Dict[str, Any]:
    """Parse JSON from LLM response, handling code blocks."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in text
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    return {}
