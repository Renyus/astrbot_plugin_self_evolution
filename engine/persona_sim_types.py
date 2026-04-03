"""
Persona Sim Types - 所有类型定义

dataclass / enum / 常量，不含业务逻辑。
"""

import time
from dataclasses import dataclass, field
from enum import Enum


class EffectType(Enum):
    BUFF = "buff"
    DEBUFF = "debuff"
    NEUTRAL = "neutral"


class EventType(Enum):
    NATURAL = "natural"
    INTERACTION = "interaction"
    EFFECT_TRIGGER = "effect_trigger"
    CONSOLIDATED = "consolidated"


class TodoType(Enum):
    INTERNAL = "internal"
    SOCIAL = "social"


class InteractionQuality(Enum):
    GOOD = "good"
    NORMAL = "normal"
    BAD = "bad"
    BRIEF = "brief"
    AWKWARD = "awkward"
    RELIEF = "relief"


class InteractionMode(Enum):
    ACTIVE = "active"
    PASSIVE = "passive"


class InteractionOutcome(Enum):
    CONNECTED = "connected"
    MISSED = "missed"


# 四个核心数值，范围 0~100
@dataclass
class PersonaState:
    energy: float = 80.0
    mood: float = 70.0
    social_need: float = 50.0
    satiety: float = 80.0
    last_tick_at: float = field(default_factory=time.time)
    last_interaction_at: float = field(default_factory=time.time)
    thought_process: str = ""


@dataclass
class PersonaEffect:
    effect_id: str
    effect_type: EffectType
    name: str
    source: str
    intensity: int
    started_at: float
    expires_at: float = 0.0
    prompt_hint: str = ""
    tags: list[str] = field(default_factory=list)
    source_detail: str = ""
    decay_style: str = "gradual"
    recovery_style: str = "passive"

    def is_active(self, now: float) -> bool:
        if self.expires_at <= 0:
            return True
        return now < self.expires_at


@dataclass
class PersonaEvent:
    event_type: EventType
    summary: str
    causes: list[str] = field(default_factory=list)
    effects_applied: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    interaction_mode: str = ""
    interaction_outcome: str = ""


@dataclass
class PersonaTodo:
    todo_type: TodoType
    title: str
    reason: str
    priority: int = 5
    mood_bias: float = 0.0
    expires_at: float = 0.0


@dataclass
class PersonaSnapshot:
    state: PersonaState
    active_effects: list[PersonaEffect]
    pending_todos: list[PersonaTodo]
    recent_events: list[PersonaEvent]
    snapshot_at: float = field(default_factory=time.time)
