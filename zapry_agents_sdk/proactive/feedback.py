"""
FeedbackDetector — 用户反馈检测 & 偏好注入框架。

从用户消息中检测反馈信号（如"太长了"→ style:concise），
自动调整偏好参数，并提供通用的 prompt 拼装工具。

抽象自 fortune_master/handlers/chat.py 中的
_detect_and_adapt() + _FEEDBACK_PATTERNS 以及
fortune_master/services/ai_chat.py 中的偏好注入逻辑。

Usage::

    from zapry_agents_sdk.proactive import FeedbackDetector, build_preference_prompt

    # --- 反馈检测 ---
    detector = FeedbackDetector()

    # 使用默认中文关键词
    result = detector.detect("太长了，说重点")
    # result.changes => {"style": "concise"}
    # result.matched => True

    # 自定义关键词（覆盖默认）
    detector.set_patterns({
        "style": {
            "concise": ["too long", "be brief", "tldr"],
            "detailed": ["tell me more", "elaborate"],
        },
    })

    # 更新偏好
    if result.matched:
        preferences.update(result.changes)

    # --- 偏好注入 prompt ---
    prompt = build_preference_prompt({"style": "concise", "tone": "casual"})
    # prompt => "回复风格偏好：\\n这位用户偏好简洁的回复..."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("zapry_agents_sdk.proactive")


# ──────────────────────────────────────────────
# 默认中文反馈关键词（可覆盖）
# ──────────────────────────────────────────────

DEFAULT_FEEDBACK_PATTERNS: Dict[str, Dict[str, List[str]]] = {
    "style": {
        "concise": ["太长了", "啰嗦", "简短点", "说重点", "太多了", "精简", "简洁"],
        "detailed": ["详细说说", "展开讲讲", "多说一些", "说详细点", "具体讲讲"],
    },
    "tone": {
        "casual": ["说人话", "白话", "通俗点", "别那么正式", "轻松一点"],
        "formal": ["专业一些", "正式一些", "文雅一些"],
    },
}


# ──────────────────────────────────────────────
# 默认偏好 → prompt 映射
# ──────────────────────────────────────────────

DEFAULT_PREFERENCE_PROMPTS: Dict[str, Dict[str, str]] = {
    "style": {
        "concise": "这位用户偏好简洁的回复，请控制在 100 字以内，直接说重点。",
        "detailed": "这位用户喜欢详细的解读，可以展开讲解，不用担心太长。",
    },
    "tone": {
        "casual": "这位用户喜欢轻松口语化的表达，少用正式或文言风格。",
        "formal": "这位用户喜欢专业正式的表达风格。",
    },
}


# ──────────────────────────────────────────────
# 类型
# ──────────────────────────────────────────────


@dataclass
class FeedbackResult:
    """反馈检测结果。

    Attributes:
        matched: 是否检测到了反馈信号。
        changes: 偏好变更 ``{pref_key: new_value}``。
        triggers: 命中的关键词 ``{pref_key: keyword}``。
    """

    matched: bool = False
    changes: Dict[str, str] = field(default_factory=dict)
    triggers: Dict[str, str] = field(default_factory=dict)


# ──────────────────────────────────────────────
# FeedbackDetector
# ──────────────────────────────────────────────


class FeedbackDetector:
    """用户反馈检测器。

    Parameters:
        patterns: 反馈关键词映射。默认使用中文关键词。
            结构: ``{pref_key: {pref_value: [keywords]}}``
        max_length: 超过此长度的消息不做检测（长消息不太可能是反馈）。
        on_change: 偏好变更回调 ``async def callback(user_id, changes)``。
    """

    def __init__(
        self,
        patterns: Optional[Dict[str, Dict[str, List[str]]]] = None,
        max_length: int = 50,
        on_change: Optional[
            Callable[[str, Dict[str, str]], Any]
        ] = None,
    ) -> None:
        self._patterns = patterns or DEFAULT_FEEDBACK_PATTERNS.copy()
        self._max_length = max_length
        self._on_change = on_change

    @property
    def patterns(self) -> Dict[str, Dict[str, List[str]]]:
        """当前反馈关键词映射。"""
        return self._patterns

    def set_patterns(
        self, patterns: Dict[str, Dict[str, List[str]]]
    ) -> None:
        """完全替换关键词映射。"""
        self._patterns = patterns

    def add_pattern(
        self,
        pref_key: str,
        pref_value: str,
        keywords: List[str],
    ) -> None:
        """追加关键词。

        Example::

            detector.add_pattern("language", "english", ["speak english", "in english"])
        """
        if pref_key not in self._patterns:
            self._patterns[pref_key] = {}
        if pref_value not in self._patterns[pref_key]:
            self._patterns[pref_key][pref_value] = []
        self._patterns[pref_key][pref_value].extend(keywords)

    def detect(
        self,
        message: str,
        current_preferences: Optional[Dict[str, str]] = None,
    ) -> FeedbackResult:
        """从消息中检测反馈信号。

        Parameters:
            message: 用户消息文本。
            current_preferences: 用户当前偏好（用于去重，只有值变化时才返回）。

        Returns:
            FeedbackResult，其中 changes 只包含实际变化的偏好。
        """
        msg = message.strip()
        if not msg or len(msg) > self._max_length:
            return FeedbackResult()

        current = current_preferences or {}
        result = FeedbackResult()

        for pref_key, value_map in self._patterns.items():
            for pref_value, keywords in value_map.items():
                for kw in keywords:
                    if kw in msg:
                        old_val = current.get(pref_key)
                        if old_val != pref_value:
                            result.matched = True
                            result.changes[pref_key] = pref_value
                            result.triggers[pref_key] = kw
                        break
                if pref_key in result.changes:
                    break

        return result

    async def detect_and_adapt(
        self,
        user_id: str,
        message: str,
        preferences: Dict[str, str],
    ) -> FeedbackResult:
        """检测反馈并自动更新偏好字典 + 触发回调。

        这是一个方便方法，组合了 detect() + 就地更新 + on_change 回调。

        Parameters:
            user_id: 用户标识。
            message: 用户消息文本。
            preferences: 用户偏好字典（会被就地更新）。

        Returns:
            FeedbackResult
        """
        result = self.detect(message, preferences)
        if result.matched:
            preferences.update(result.changes)
            preferences["updated_at"] = datetime.now().isoformat()

            for pref_key, kw in result.triggers.items():
                logger.info(
                    "Preference adapted | user=%s | %s: %s -> %s | keyword=%s",
                    user_id,
                    pref_key,
                    preferences.get(pref_key, "?"),
                    result.changes[pref_key],
                    kw,
                )

            if self._on_change:
                await self._on_change(user_id, result.changes)

        return result


# ──────────────────────────────────────────────
# 偏好注入 prompt 工具
# ──────────────────────────────────────────────


def build_preference_prompt(
    preferences: Dict[str, str],
    prompt_map: Optional[Dict[str, Dict[str, str]]] = None,
    header: str = "回复风格偏好：",
) -> Optional[str]:
    """根据用户偏好生成注入 system prompt 的文本。

    Parameters:
        preferences: 用户偏好 ``{"style": "concise", "tone": "casual"}``。
        prompt_map: 偏好值 → prompt 文本映射，默认使用中文内置版本。
        header: 提示文本的标题行。

    Returns:
        拼装好的 prompt 文本，若无有效偏好则返回 None。

    Example::

        prompt = build_preference_prompt({"style": "concise", "tone": "casual"})
        if prompt:
            system_messages.append({"role": "system", "content": prompt})
    """
    mapping = prompt_map or DEFAULT_PREFERENCE_PROMPTS
    hints: List[str] = []

    for pref_key, pref_value in preferences.items():
        # 跳过元数据字段
        if pref_key in ("updated_at",):
            continue
        value_prompts = mapping.get(pref_key, {})
        text = value_prompts.get(pref_value)
        if text:
            hints.append(text)

    if not hints:
        return None

    return header + "\n" + "\n".join(hints)
