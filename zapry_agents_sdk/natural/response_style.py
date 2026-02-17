"""Response Style Controller — local post-processing (no LLM cost)."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Tuple


NATURAL_ENDINGS = ["先说到这儿。", "大概就是这样。", "就先聊这些吧。", "回头再细说。"]

DEFAULT_FORBIDDEN = [
    "作为一个AI", "作为AI助手", "作为一个人工智能",
    "我是一个AI", "我是AI助手",
    "有什么我可以帮你的", "还有什么需要帮忙的",
    "请问还有什么", "很高兴为你服务",
    "希望对你有帮助", "如果你有任何问题",
]


@dataclass
class StyleConfig:
    max_length: int = 300
    min_preserve: int = 40
    preferred_length: int = 150
    forbidden_phrases: List[str] = field(default_factory=lambda: list(DEFAULT_FORBIDDEN))
    end_style: str = "no_question"
    enable_retry: bool = False


def DefaultStyleConfig() -> StyleConfig:
    return StyleConfig()


class ResponseStyleController:
    def __init__(self, config: StyleConfig | None = None) -> None:
        self.config = config or DefaultStyleConfig()

    def build_style_prompt(self) -> str:
        parts = []
        if self.config.preferred_length > 0:
            parts.append(f"回复请控制在{self.config.preferred_length}字以内，简洁为主。")
        if self.config.end_style == "no_question":
            parts.append("回复结尾不要以问句结束。")
        if not parts:
            return ""
        return "[回复风格] " + " ".join(parts)

    def post_process(self, output: str) -> Tuple[str, bool, List[str]]:
        result = output
        changed = False
        violations: List[str] = []

        for phrase in self.config.forbidden_phrases:
            if phrase in result:
                result = result.replace(phrase, "")
                violations.append(f"style.forbidden_removed:{phrase}")
                changed = True

        if changed:
            while "  " in result:
                result = result.replace("  ", " ")
            while "\n\n\n" in result:
                result = result.replace("\n\n\n", "\n\n")

        rune_count = len(result)
        if (
            self.config.max_length > 0
            and rune_count > self.config.max_length
            and rune_count > self.config.min_preserve
        ):
            truncated = _truncate_natural(result, self.config.max_length)
            if truncated != result:
                result = truncated
                violations.append(f"style.truncated:exceeded_{self.config.max_length}")
                changed = True

        if self.config.end_style == "no_question":
            trimmed = result.strip()
            if trimmed.endswith("？"):
                result = trimmed[:-1] + "。"
                violations.append("style.end_question_fixed")
                changed = True
            elif trimmed.endswith("?"):
                result = trimmed[:-1] + "."
                violations.append("style.end_question_fixed")
                changed = True

        return result.strip(), changed, violations

    def build_retry_prompt(self, output: str, violations: List[str]) -> str:
        hints = []
        for v in violations:
            if v.startswith("style.truncated"):
                hints.append(f"请将回复控制在{self.config.max_length}字以内")
            if v.startswith("style.forbidden"):
                hints.append("不要使用套话，直接回答")
            if v == "style.end_question_fixed":
                hints.append("回复结尾不要以问号结束")
        if not hints:
            return ""
        return "[重新生成] 上一次回复不满足风格要求：" + "；".join(hints) + "。请重新回复。"


def _truncate_natural(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text

    sentence_ends = set("。！？.!?\n")
    best_cut = max_len
    for i in range(max_len - 1, max_len // 2, -1):
        if text[i] in sentence_ends:
            best_cut = i + 1
            break

    truncated = text[:best_cut].strip()
    ending = random.choice(NATURAL_ENDINGS)
    return truncated + ending
