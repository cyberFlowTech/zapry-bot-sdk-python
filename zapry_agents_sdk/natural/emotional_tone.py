"""Emotional Tone Detector — lightweight rule-based scoring (bilingual)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EmotionalTone:
    tone: str = "neutral"
    confidence: float = 0.0
    scores: Dict[str, float] = field(default_factory=dict)

    def format_for_prompt(self) -> str:
        if self.tone == "neutral" or self.confidence < 0.3:
            return ""
        hints = {
            "angry": "用户语气较为强烈，请保持耐心，注意措辞温和",
            "anxious": "用户语气偏急促，请简洁直接回应，不要废话",
            "happy": "用户心情不错，可以轻松互动",
            "sad": "用户情绪偏低落，请语气温和关切",
        }
        hint = hints.get(self.tone, "")
        return f"[用户情绪] {hint}" if hint else ""


_WeightedKW = List[tuple]  # [(keyword, weight), ...]


def _default_patterns() -> Dict[str, _WeightedKW]:
    return {
        "angry": [
            ("什么破", 0.5), ("垃圾", 0.5), ("搞什么", 0.5), ("有病", 0.5),
            ("废物", 0.5), ("能不能正常", 0.5),
            ("bullshit", 0.5), ("wtf", 0.5), ("terrible", 0.4), ("useless", 0.4),
        ],
        "anxious": [
            ("快点", 0.4), ("赶紧", 0.4), ("急", 0.4), ("等不了", 0.4),
            ("尽快", 0.4), ("马上", 0.3),
            ("asap", 0.4), ("hurry", 0.4), ("quick", 0.3), ("urgent", 0.4),
        ],
        "happy": [
            ("太好了", 0.3), ("哈哈", 0.3), ("棒", 0.3), ("开心", 0.3),
            ("nice", 0.3), ("awesome", 0.3), ("great", 0.3), ("love it", 0.3),
        ],
        "sad": [
            ("唉", 0.4), ("算了", 0.4), ("难过", 0.4), ("失望", 0.4),
            ("无所谓了", 0.4),
            ("sigh", 0.4), ("forget it", 0.4), ("disappointed", 0.4),
        ],
    }


class EmotionalToneDetector:
    def __init__(self) -> None:
        self._patterns = _default_patterns()

    def detect(self, user_input: str, state: Optional[Any] = None) -> EmotionalTone:
        lower = user_input.lower()
        scores: Dict[str, float] = {
            "neutral": 0, "angry": 0, "anxious": 0, "happy": 0, "sad": 0,
        }

        for tone, keywords in self._patterns.items():
            for kw, weight in keywords:
                if kw.lower() in lower:
                    scores[tone] += weight

        if state and getattr(state, "is_followup", False) and getattr(state, "user_msg_length", "") == "short":
            scores["anxious"] += 0.2

        exclam = user_input.count("!") + user_input.count("！")
        if exclam >= 2:
            boost = min(exclam * 0.1, 0.2)
            max_tone = max((s, t) for t, s in scores.items() if t != "neutral")
            if max_tone[0] > 0:
                scores[max_tone[1]] += boost

        top_tone = "neutral"
        top_score = 0.0
        for tone, score in scores.items():
            if tone == "neutral":
                continue
            if score > top_score:
                top_score = score
                top_tone = tone

        confidence = min(top_score, 1.0)
        if confidence < 0.3:
            top_tone = "neutral"
            confidence = 0.0

        return EmotionalTone(tone=top_tone, confidence=confidence, scores=scores)
