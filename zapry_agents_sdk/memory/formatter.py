"""
MemoryFormatter — 将三层记忆格式化为可注入 LLM system prompt 的文本。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from zapry_agents_sdk.memory.types import Message


DEFAULT_TEMPLATE = """以下是该用户的个人信息（不是你自己的信息）。
当用户问关于自己的问题时，必须根据以下档案回答：

{long_term_text}"""


def format_memory_for_prompt(
    long_term: Dict[str, Any],
    short_term: Optional[List[Message]] = None,
    working: Optional[Dict[str, Any]] = None,
    template: Optional[str] = None,
) -> Optional[str]:
    """Format memory layers into a text block for LLM system prompt injection.

    Parameters:
        long_term: The user's long-term memory dict.
        short_term: Recent conversation (usually handled separately as messages).
        working: Current session working memory.
        template: Custom template with ``{long_term_text}`` placeholder.

    Returns:
        Formatted prompt string, or None if there's no meaningful content.
    """
    parts: List[str] = []

    # Long-term memory
    lt_text = _format_long_term(long_term)
    if lt_text:
        parts.append(lt_text)

    # Working memory
    if working:
        wm_items = [f"- {k}: {v}" for k, v in working.items() if v]
        if wm_items:
            parts.append("当前会话上下文：\n" + "\n".join(wm_items))

    if not parts:
        return None

    combined = "\n\n".join(parts)

    if template:
        return template.format(long_term_text=combined)
    return DEFAULT_TEMPLATE.format(long_term_text=combined)


def _format_long_term(memory: Dict[str, Any]) -> str:
    """Format long-term memory dict into human-readable text."""
    lines: List[str] = []

    # Basic info
    basic = memory.get("basic_info", {})
    if basic and any(v for v in basic.values() if v):
        lines.append("用户基本信息：")
        _FIELD_LABELS = {
            "age": "年龄", "gender": "性别", "location": "位置",
            "occupation": "职业", "school": "学校", "major": "专业",
            "nickname": "昵称", "birthday": "生日",
        }
        for field, label in _FIELD_LABELS.items():
            val = basic.get(field)
            if val:
                lines.append(f"  - {label}: {val}")

    # Personality
    personality = memory.get("personality", {})
    traits = personality.get("traits", [])
    if traits:
        lines.append(f"性格特点: {', '.join(traits)}")
    values = personality.get("values", [])
    if values:
        lines.append(f"价值观: {', '.join(values)}")

    # Life context
    life = memory.get("life_context", {})
    if life:
        concerns = life.get("concerns", [])
        if concerns:
            lines.append(f"当前困扰: {', '.join(concerns)}")
        goals = life.get("goals", [])
        if goals:
            lines.append(f"目标: {', '.join(goals)}")
        events = life.get("recent_events", [])
        if events:
            lines.append(f"近期事件: {', '.join(events)}")

    # Interests
    interests = memory.get("interests", [])
    if interests:
        lines.append(f"兴趣爱好: {', '.join(interests)}")

    # Summary
    summary = memory.get("summary", "")
    if summary:
        lines.append(f"用户特点: {summary}")

    # Conversation count
    meta = memory.get("meta", {})
    count = meta.get("conversation_count", 0)
    if count:
        lines.append(f"（已对话 {count} 次）")

    return "\n".join(lines)
