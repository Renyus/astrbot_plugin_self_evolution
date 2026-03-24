from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class MemoryWriteTarget(Enum):
    PROFILE = "profile"
    SESSION_EVENT = "session_event"
    REFLECTION_HINT = "reflection_hint"
    DROP = "drop"


class MemoryQueryIntent(Enum):
    RECENT_CONTEXT = "recent_context"
    DAILY_SUMMARY = "daily_summary"
    SESSION_EVENT = "session_event"
    USER_PROFILE = "user_profile"
    USER_MESSAGE_HISTORY = "user_message_history"
    FALLBACK_KB = "fallback_kb"


@dataclass
class MemoryWriteRequest:
    scope_id: str
    user_id: str
    content: str
    category: Optional[str] = None
    fact_type: Optional[str] = None
    nickname: str = ""
    source: str = "manual"


@dataclass
class MemoryWriteDecision:
    target: MemoryWriteTarget
    fact_type: Optional[str] = None
    reason: str = ""
    confidence: float = 1.0


@dataclass
class MemoryQueryRequest:
    scope_id: str
    user_id: str
    query: str
    intent: MemoryQueryIntent
    limit: int = 3
    date: Optional[str] = None


@dataclass
class MemoryQueryResult:
    intent: MemoryQueryIntent
    text: str
    source: str
    hit_count: int = 0


@dataclass
class ProfileFact:
    user_id: str
    scope_id: str
    content: str
    category: str
    source: str = "manual"
    created_at: Optional[str] = None


@dataclass
class SessionEvent:
    scope_id: str
    user_id: str
    event_type: str
    content: str
    timestamp: str
    date: str


@dataclass
class DailySummary:
    scope_id: str
    date: str
    overview: str
    key_facts: list[str] = field(default_factory=list)
    key_entities: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
