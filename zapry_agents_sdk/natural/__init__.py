"""Natural Conversation — make agent dialogue more human-like."""

from zapry_agents_sdk.natural.prompt_fragments import PromptFragments
from zapry_agents_sdk.natural.conversation_state import (
    ConversationState,
    ConversationStateTracker,
)
from zapry_agents_sdk.natural.emotional_tone import (
    EmotionalTone,
    EmotionalToneDetector,
)
from zapry_agents_sdk.natural.response_style import (
    StyleConfig,
    DefaultStyleConfig,
    ResponseStyleController,
)
from zapry_agents_sdk.natural.conversation_opener import (
    OpenerStrategy,
    OpenerConfig,
    OpenerGenerator,
)
from zapry_agents_sdk.natural.context_compressor import (
    CompressorConfig,
    ContextCompressor,
)
from zapry_agents_sdk.natural.natural_conversation import (
    NaturalConversationConfig,
    DefaultNaturalConversationConfig,
    NaturalConversation,
    NaturalAgentLoop,
)

__all__ = [
    "PromptFragments",
    "ConversationState",
    "ConversationStateTracker",
    "EmotionalTone",
    "EmotionalToneDetector",
    "StyleConfig",
    "DefaultStyleConfig",
    "ResponseStyleController",
    "OpenerStrategy",
    "OpenerConfig",
    "OpenerGenerator",
    "CompressorConfig",
    "ContextCompressor",
    "NaturalConversationConfig",
    "DefaultNaturalConversationConfig",
    "NaturalConversation",
    "NaturalAgentLoop",
]
