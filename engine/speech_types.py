from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class InteractionKind(Enum):
    PASSIVE_TEXT = "passive_text"
    PASSIVE_EMOJI = "passive_emoji"
    PASSIVE_REACTION = "passive_reaction"
    ACTIVE_TEXT = "active_text"
    ACTIVE_EMOJI = "active_emoji"
    ACTIVE_REACTION = "active_reaction"
    SKIP = "skip"
    GUARD_BLOCKED = "guard_blocked"
    DEGRADED = "degraded"


class OpportunityKind(Enum):
    DIRECT_REPLY = "direct_reply"
    MENTION_REPLY = "mention_reply"
    PRIVATE_REPLY = "private_reply"
    ACTIVE_CONTINUATION = "active_continuation"
    TOPIC_HOOK = "topic_hook"
    EMOJI_REACT = "emoji_react"
    IGNORE = "ignore"


class AnchorType(Enum):
    TOPIC_CONCLUSION = "topic_conclusion"
    QUESTION_UNANSWERED = "question_unanswered"
    PERSONA_HOOK = "persona_hook"
    MEMORABLE_HOOK = "memorable_hook"
    NATURAL_LANDING = "natural_landing"
    REPLY_TO_BOT = "reply_to_bot"
    MENTION = "mention"
    NONE = "none"


@dataclass
class ChatEvent:
    source_type: str
    scope_id: str
    umo: str = ""
    is_group: bool = True
    sender_id: str = ""
    sender_name: str = "群成员"
    message_text: str = ""
    components: list = field(default_factory=list)
    mentions_bot: bool = False
    replies_to_bot: bool = False
    quoted_text: str = ""
    quoted_sender_id: str = ""
    timestamp: float = 0.0
    role: str = "member"
    platform: str = ""

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = datetime.now().timestamp()


@dataclass
class SpeechOpportunity:
    scope_id: str
    kind: OpportunityKind
    anchor_type: AnchorType
    confidence: float
    reason: str
    anchor_text: str = ""
    target_user_id: str = ""
    target_user_name: str = ""
    source_event: Optional[ChatEvent] = None

    @classmethod
    def ignore(cls, scope_id: str, reason: str) -> "SpeechOpportunity":
        return cls(
            scope_id=scope_id,
            kind=OpportunityKind.IGNORE,
            anchor_type=AnchorType.NONE,
            confidence=0.0,
            reason=reason,
        )

    @classmethod
    def emoji_react(cls, scope_id: str, reason: str, confidence: float = 0.5) -> "SpeechOpportunity":
        return cls(
            scope_id=scope_id,
            kind=OpportunityKind.EMOJI_REACT,
            anchor_type=AnchorType.NONE,
            confidence=confidence,
            reason=reason,
        )


@dataclass
class SpeechDecision:
    delivery_mode: str
    text_mode: str = ""
    target_kind: OpportunityKind = OpportunityKind.IGNORE
    anchor_type: AnchorType = AnchorType.NONE
    confidence: float = 0.0
    reason: str = ""
    max_chars: int = 200
    allow_new_topic: bool = False
    must_follow_thread: bool = True
    anchor_text: str = ""

    IGNORE = "ignore"
    EMOJI = "emoji"
    TEXT = "text"

    TEXT_MODES = ["reply", "interject", "correction", "disengage"]

    @classmethod
    def ignore(cls, reason: str) -> "SpeechDecision":
        return cls(
            delivery_mode="ignore",
            text_mode="",
            target_kind=OpportunityKind.IGNORE,
            anchor_type=AnchorType.NONE,
            confidence=0.0,
            reason=reason,
        )

    @classmethod
    def emoji(cls, reason: str, confidence: float = 0.5) -> "SpeechDecision":
        return cls(
            delivery_mode="emoji",
            text_mode="",
            target_kind=OpportunityKind.EMOJI_REACT,
            anchor_type=AnchorType.NONE,
            confidence=confidence,
            reason=reason,
        )

    @classmethod
    def text(
        cls,
        text_mode: str,
        anchor_type: AnchorType,
        confidence: float,
        reason: str,
        max_chars: int = 200,
        allow_new_topic: bool = False,
        must_follow_thread: bool = True,
        anchor_text: str = "",
    ) -> "SpeechDecision":
        return cls(
            delivery_mode="text",
            text_mode=text_mode,
            target_kind=OpportunityKind.IGNORE,
            anchor_type=anchor_type,
            confidence=confidence,
            reason=reason,
            max_chars=max_chars,
            allow_new_topic=allow_new_topic,
            must_follow_thread=must_follow_thread,
            anchor_text=anchor_text,
        )


@dataclass
class GenerationSpec:
    system_prompt: str
    user_prompt: str
    mode: str
    max_chars: int = 200
    strict_output_rules: bool = True
    decision: Optional[SpeechDecision] = None


@dataclass
class ThreadAnchor:
    anchor_type: AnchorType
    anchor_text: str
    confidence: float
    topic_keywords: set = field(default_factory=set)
    message_ids: list = field(default_factory=list)

    def is_sufficient(self) -> bool:
        return self.confidence >= 0.4 and bool(self.topic_keywords or self.anchor_text)


@dataclass
class OutputResult:
    status: str
    text: str = ""
    reason: str = ""
    fallback_action: str = ""

    PASS = "pass"
    RETRY_SHORTER = "retry_shorter"
    DOWNGRADE_TO_EMOJI = "downgrade_to_emoji"
    DROP = "drop"
