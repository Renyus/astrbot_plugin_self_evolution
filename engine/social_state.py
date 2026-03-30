from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from .speech_types import AnchorType, OpportunityKind, SpeechDecision, SpeechOpportunity


class EngagementLevel(Enum):
    IGNORE = "ignore"
    REACT = "react"
    FULL = "full"


class SceneType(Enum):
    IDLE = "idle"
    CASUAL = "casual"
    HELP = "help"
    DEBATE = "debate"


@dataclass
class GroupSocialState:
    scope_id: str
    last_message_time: float = 0.0
    last_bot_message_time: float = 0.0
    recent_bot_engagements: deque = field(default_factory=lambda: deque(maxlen=10))
    last_seen_message_seq: Optional[int] = None
    active_user_count: int = 0
    message_count_window: int = 0
    question_count_window: int = 0
    emotion_count_window: int = 0
    mention_bot_recently: bool = False
    scene: SceneType = SceneType.CASUAL
    consecutive_bot_replies: int = 0


@dataclass
class EngagementEligibility:
    allowed: bool
    reason_code: str
    reason_text: str
    new_message_count: int = 0
    silence_seconds: float = 0.0


@dataclass
class EngagementPlan:
    level: EngagementLevel
    reason: str
    confidence: float
    scene: SceneType
    suggested_text: str = ""
    use_sticker: bool = False
    sticker_id: Optional[str] = None
    anchor_type: AnchorType = AnchorType.NONE
    anchor_text: str = ""

    def to_speech_decision(self) -> SpeechDecision:
        if self.level == EngagementLevel.IGNORE:
            return SpeechDecision.ignore(self.reason)
        if self.level == EngagementLevel.REACT:
            return SpeechDecision.emoji(self.reason, self.confidence)
        return SpeechDecision.text(
            text_mode="reply",
            anchor_type=self.anchor_type,
            confidence=self.confidence,
            reason=self.reason,
            max_chars=200,
            must_follow_thread=True,
            anchor_text=self.anchor_text,
        )


@dataclass
class EngagementExecutionResult:
    executed: bool
    level: EngagementLevel
    action: str
    reason: str
    actual_text: str = ""
