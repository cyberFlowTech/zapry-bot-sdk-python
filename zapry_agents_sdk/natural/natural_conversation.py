"""NaturalConversation — unified entry point for all natural dialogue enhancements."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from zapry_agents_sdk.natural.prompt_fragments import PromptFragments
from zapry_agents_sdk.natural.conversation_state import ConversationStateTracker
from zapry_agents_sdk.natural.emotional_tone import EmotionalToneDetector
from zapry_agents_sdk.natural.response_style import (
    ResponseStyleController,
    StyleConfig,
    DefaultStyleConfig,
)
from zapry_agents_sdk.natural.conversation_opener import OpenerConfig, OpenerGenerator
from zapry_agents_sdk.natural.context_compressor import CompressorConfig, ContextCompressor


@dataclass
class NaturalConversationConfig:
    # Recommended (default ON)
    state_tracking: bool = True
    emotion_detection: bool = True
    style_post_process: bool = True

    # Advanced (default OFF)
    opener_generation: bool = False
    context_compress: bool = False
    style_retry: bool = False

    # Sub-configs
    style_config: StyleConfig = field(default_factory=DefaultStyleConfig)
    opener_config: OpenerConfig = field(default_factory=OpenerConfig)
    compressor_config: CompressorConfig = field(default_factory=CompressorConfig)
    summarize_fn: Optional[Callable] = None
    timezone: str = "Asia/Shanghai"
    followup_window: float = 60.0


def DefaultNaturalConversationConfig() -> NaturalConversationConfig:
    return NaturalConversationConfig()


class NaturalConversation:
    def __init__(self, config: NaturalConversationConfig) -> None:
        self.config = config
        self._state_tracker = (
            ConversationStateTracker(config.timezone, config.followup_window)
            if config.state_tracking
            else None
        )
        self._emotion_det = EmotionalToneDetector() if config.emotion_detection else None
        self._style_ctrl = (
            ResponseStyleController(config.style_config)
            if config.style_post_process or config.style_retry
            else None
        )
        self._opener = (
            OpenerGenerator(config.opener_config) if config.opener_generation else None
        )
        self._compressor = (
            ContextCompressor(config.summarize_fn, config.compressor_config)
            if config.context_compress and config.summarize_fn
            else None
        )

    async def enhance(
        self,
        session: Any,
        user_input: str,
        history: Optional[List[Dict]] = None,
        now: Optional[datetime] = None,
    ) -> tuple:
        """Run all pre-processing. Returns (PromptFragments, enhanced_history)."""
        if now is None:
            now = datetime.now(timezone.utc)
        fragments = PromptFragments()
        enhanced_history = history or []

        state = None
        if self._state_tracker:
            state = await self._state_tracker.track(session, user_input, now)
            if state.turn_index == 1:
                await self._state_tracker.touch_session(session, now)
            fragments.add_system(state.format_for_prompt())
            for k, v in state.to_kv().items():
                fragments.set_kv(k, v)
            fragments.add_warning("state.tracked")

        if self._emotion_det:
            tone = self._emotion_det.detect(user_input, state)
            prompt = tone.format_for_prompt()
            if prompt:
                fragments.add_system(prompt)
                fragments.add_warning(f"tone.{tone.tone}:{tone.confidence:.2f}")
            fragments.set_kv("sdk.user.emotion_tone", tone.tone)
            fragments.set_kv("sdk.user.emotion_confidence", tone.confidence)

        if self._opener and state:
            opener_count = session.working.get("sdk.opener_count") or 0
            strategy = self._opener.generate(state, opener_count)
            prompt = strategy.format_for_prompt()
            if prompt:
                fragments.add_system(prompt)
                session.working.set("sdk.opener_count", opener_count + 1)
                fragments.add_warning(f"opener.{strategy.situation}")

        if self._style_ctrl:
            prompt = self._style_ctrl.build_style_prompt()
            if prompt:
                fragments.add_system(prompt)
                fragments.add_warning(f"style.prompt:preferred_{self.config.style_config.preferred_length}")

        if self._compressor and enhanced_history:
            compressed = await self._compressor.compress(enhanced_history, session.working)
            if len(compressed) != len(enhanced_history):
                enhanced_history = compressed
                fragments.add_warning("compressor.summarized")

        return fragments, enhanced_history

    def post_process(self, output: str) -> tuple:
        """Apply local style corrections. Returns (corrected, changed)."""
        if not self._style_ctrl:
            return output, False
        result, changed, _ = self._style_ctrl.post_process(output)
        return result, changed

    def build_retry_prompt(self, output: str) -> Optional[str]:
        if not self._style_ctrl or not self.config.style_retry:
            return None
        _, _, violations = self._style_ctrl.post_process(output)
        if not violations:
            return None
        prompt = self._style_ctrl.build_retry_prompt(output, violations)
        return prompt or None

    def wrap_loop(self, loop: Any) -> "NaturalAgentLoop":
        return NaturalAgentLoop(loop, self)


class NaturalAgentLoop:
    def __init__(self, inner: Any, nc: NaturalConversation) -> None:
        self._inner = inner
        self._nc = nc
        self._last_fragments: Optional[PromptFragments] = None

    async def run(self, session: Any, user_input: str, history: Optional[List[Dict]] = None) -> Any:
        fragments, enhanced_history = await self._nc.enhance(session, user_input, history)
        self._last_fragments = fragments

        result = await self._inner.run(user_input, enhanced_history, fragments.text())

        if getattr(result, "stopped_reason", "") == "completed" and getattr(result, "final_output", ""):
            corrected, changed = self._nc.post_process(result.final_output)
            if changed:
                result.final_output = corrected

        return result

    @property
    def last_fragments(self) -> Optional[PromptFragments]:
        return self._last_fragments
