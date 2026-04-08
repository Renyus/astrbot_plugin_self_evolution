import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import time


class MotiveType(Enum):
    SEEK_CONNECTION = "seek_connection"
    CONTINUE_THREAD = "continue_thread"
    LIGHT_RELIEF = "light_relief"
    AVOID_SILENCE = "avoid_silence"
    CURIOUS_PROBE = "curious_probe"
    SELF_PROTECTIVE = "self_protective"
    NONE = "none"


@dataclass
class OpportunityScore:
    total: float = 0.0

    question: float = 0.0
    thread: float = 0.0
    topic_hook: float = 0.0
    natural_landing: float = 0.0
    emotion: float = 0.0
    persona_drive: float = 0.0
    bot_activity: float = 0.0
    relation: float = 0.0
    novelty: float = 0.0

    negative_override: float = 0.0

    @property
    def is_blocked(self) -> bool:
        return self.negative_override < 0.0

    def level_from_score(self) -> str:
        if self.is_blocked or self.total < 0.15:
            return "ignore"
        if self.total < 0.25:
            return "react"
        if self.total < 0.35:
            return "text_lite"
        return "full"


@dataclass
class ActiveMotive:
    motive: MotiveType
    strength: float
    source: str


@dataclass
class PendingOpportunity:
    scope_id: str
    score: OpportunityScore
    anchor_text: str
    anchor_type: str
    motive: ActiveMotive
    created_at: float
    expires_at: float
    message_ids: list[str] = field(default_factory=list)
    trigger_reason: str = ""
    trigger_user_id: str = ""
    trigger_user_name: str = ""

    def is_expired(self, now: float | None = None) -> bool:
        if now is None:
            now = time.time()
        return now > self.expires_at

    def is_high_score(self) -> bool:
        return self.score.total >= 0.45


class OpportunityCache:
    _MAX_PER_SCOPE: int = 3
    _DEFAULT_TTL: float = 180.0

    def __init__(self):
        self._data: dict[str, list[PendingOpportunity]] = {}
        self._lock = asyncio.Lock()

    async def warm(
        self,
        scope_id: str,
        score: OpportunityScore,
        anchor_text: str,
        anchor_type: str,
        motive: ActiveMotive,
        message_ids: list[str],
        trigger_reason: str,
        ttl: float | None = None,
        trigger_user_id: str = "",
        trigger_user_name: str = "",
    ) -> None:
        if score.is_blocked:
            return
        async with self._lock:
            now = time.time()
            opp = PendingOpportunity(
                scope_id=scope_id,
                score=score,
                anchor_text=anchor_text,
                anchor_type=anchor_type,
                motive=motive,
                created_at=now,
                expires_at=now + (ttl if ttl is not None else self._DEFAULT_TTL),
                message_ids=message_ids,
                trigger_reason=trigger_reason,
                trigger_user_id=trigger_user_id,
                trigger_user_name=trigger_user_name,
            )
            if scope_id not in self._data:
                self._data[scope_id] = []
            lst = self._data[scope_id]
            lst.append(opp)
            lst.sort(key=lambda x: x.score.total, reverse=True)
            if len(lst) > self._MAX_PER_SCOPE:
                self._data[scope_id] = lst[: self._MAX_PER_SCOPE]

    async def remove_one(self, scope_id: str, opp: PendingOpportunity) -> None:
        async with self._lock:
            if scope_id not in self._data:
                return
            self._data[scope_id] = [o for o in self._data[scope_id] if o is not opp]

    async def remove_by_anchor(self, scope_id: str, anchor_type: str, anchor_text: str) -> None:
        async with self._lock:
            if scope_id not in self._data:
                return
            self._data[scope_id] = [
                o for o in self._data[scope_id] if not (o.anchor_type == anchor_type and o.anchor_text == anchor_text)
            ]

    async def consume(self, scope_id: str) -> list[PendingOpportunity]:
        async with self._lock:
            now = time.time()
            if scope_id not in self._data:
                return []
            valid = [o for o in self._data[scope_id] if not o.is_expired(now)]
            self._data[scope_id] = []
            return valid

    async def peek(self, scope_id: str) -> list[PendingOpportunity]:
        now = time.time()
        async with self._lock:
            if scope_id not in self._data:
                return []
            return [o for o in self._data[scope_id] if not o.is_expired(now)]

    async def has_any(self, scope_id: str) -> bool:
        return len(await self.peek(scope_id)) > 0

    async def remove_expired(self, scope_id: str) -> None:
        async with self._lock:
            if scope_id not in self._data:
                return
            now = time.time()
            self._data[scope_id] = [o for o in self._data[scope_id] if not o.is_expired(now)]
