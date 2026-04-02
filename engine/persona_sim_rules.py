"""
Persona Sim Rules - 模拟人生核心规则

所有"数值怎么变"硬逻辑都放这里。
"""

import copy
import time
from typing import Optional

from .persona_sim_types import (
    EffectType,
    EventType,
    PersonaEffect,
    PersonaEvent,
    PersonaSnapshot,
    PersonaState,
    PersonaTodo,
    TodoType,
)

DEFAULT_EFFECTS: list[PersonaEffect] = [
    PersonaEffect(
        effect_id="low_energy",
        effect_type=EffectType.DEBUFF,
        name="疲惫",
        source="rule",
        intensity=2,
        started_at=0,
        expires_at=0,
        prompt_hint="有点累，懒得动",
        tags=["energy"],
    ),
    PersonaEffect(
        effect_id="low_mood",
        effect_type=EffectType.DEBUFF,
        name="低落",
        source="rule",
        intensity=2,
        started_at=0,
        expires_at=0,
        prompt_hint="心情不太好",
        tags=["mood"],
    ),
    PersonaEffect(
        effect_id="lonely",
        effect_type=EffectType.DEBUFF,
        name="孤独",
        source="rule",
        intensity=2,
        started_at=0,
        expires_at=0,
        prompt_hint="想和人聊聊天",
        tags=["social"],
    ),
    PersonaEffect(
        effect_id="hungry",
        effect_type=EffectType.DEBUFF,
        name="饿了",
        source="rule",
        intensity=1,
        started_at=0,
        expires_at=0,
        prompt_hint="有点饿",
        tags=["satiety"],
    ),
    PersonaEffect(
        effect_id="irritated",
        effect_type=EffectType.DEBUFF,
        name="烦躁",
        source="rule",
        intensity=3,
        started_at=0,
        expires_at=0,
        prompt_hint="有点烦躁",
        tags=["mood"],
    ),
    PersonaEffect(
        effect_id="wronged",
        effect_type=EffectType.DEBUFF,
        name="委屈",
        source="rule",
        intensity=3,
        started_at=0,
        expires_at=0,
        prompt_hint="心里有点委屈",
        tags=["mood"],
    ),
    PersonaEffect(
        effect_id="tired",
        effect_type=EffectType.DEBUFF,
        name="困倦",
        source="rule",
        intensity=2,
        started_at=0,
        expires_at=0,
        prompt_hint="好困",
        tags=["energy"],
    ),
    PersonaEffect(
        effect_id="curious",
        effect_type=EffectType.BUFF,
        name="好奇",
        source="rule",
        intensity=1,
        started_at=0,
        expires_at=0,
        prompt_hint="有点好奇",
        tags=["mood"],
    ),
    PersonaEffect(
        effect_id="relieved",
        effect_type=EffectType.BUFF,
        name="轻松",
        source="rule",
        intensity=1,
        started_at=0,
        expires_at=0,
        prompt_hint="感觉轻松",
        tags=["mood"],
    ),
    PersonaEffect(
        effect_id="satisfied",
        effect_type=EffectType.BUFF,
        name="满足",
        source="rule",
        intensity=2,
        started_at=0,
        expires_at=0,
        prompt_hint="心情不错",
        tags=["mood"],
    ),
    PersonaEffect(
        effect_id="sleepy",
        effect_type=EffectType.DEBUFF,
        name="想睡",
        source="rule",
        intensity=2,
        started_at=0,
        expires_at=0,
        prompt_hint="困了",
        tags=["energy"],
    ),
    PersonaEffect(
        effect_id="thriving",
        effect_type=EffectType.BUFF,
        name="神清气爽",
        source="rule",
        intensity=2,
        started_at=0,
        expires_at=0,
        prompt_hint="状态很好",
        tags=["energy", "mood"],
    ),
]

EFFECT_BY_ID = {e.effect_id: e for e in DEFAULT_EFFECTS}

NO_INTERACTION_THRESHOLD_HOURS = 6.0
LONELY_THRESHOLD_HOURS = 12.0
HUNGRY_THRESHOLD_HOURS = 8.0

HOUR = 3600.0


def calc_time_delta_hours(last_tick: float, now: float) -> float:
    return max(0.0, now - last_tick) / HOUR


def calc_state_delta(
    state: PersonaState,
    elapsed_hours: float,
    interaction_recent: bool,
) -> tuple[PersonaState, list[PersonaEvent], list[PersonaEffect]]:
    """根据时间流逝计算状态变化，返回（新状态，触发的事件，新增的 effect）。"""
    if elapsed_hours <= 0:
        return state, [], []

    events: list[PersonaEvent] = []
    new_effects: list[PersonaEffect] = []
    now = time.time()

    energy_delta = -elapsed_hours * 1.5
    if interaction_recent:
        energy_delta += elapsed_hours * 0.5

    mood_delta = -elapsed_hours * 0.8
    if interaction_recent:
        mood_delta += elapsed_hours * 1.0

    social_need_delta = elapsed_hours * 2.0
    if interaction_recent:
        social_need_delta -= elapsed_hours * 1.5

    satiety_delta = -elapsed_hours * 1.0

    new_energy = _clamp(state.energy + energy_delta)
    new_mood = _clamp(state.mood + mood_delta)
    new_social_need = _clamp(state.social_need + social_need_delta)
    new_satiety = _clamp(state.satiety + satiety_delta)

    if elapsed_hours >= 1.0:
        events.append(
            PersonaEvent(
                event_type=EventType.NATURAL,
                summary=f"自然时间流逝 {elapsed_hours:.1f}h",
                causes=[f"elapsed={elapsed_hours:.1f}h"],
                effects_applied=[],
            )
        )

    new_state = PersonaState(
        energy=new_energy,
        mood=new_mood,
        social_need=new_social_need,
        satiety=new_satiety,
        last_tick_at=now,
        last_interaction_at=state.last_interaction_at,
    )

    return new_state, events, new_effects


def eval_effect_triggers(
    state: PersonaState,
    active_ids: set[str],
    now: float,
) -> list[PersonaEffect]:
    """根据当前状态阈值，判断是否应触发/移除某些 effect。"""
    triggered: list[PersonaEffect] = []
    duration = 2.0 * HOUR

    if state.energy < 30 and "low_energy" not in active_ids:
        e = copy.copy(EFFECT_BY_ID["low_energy"])
        e.started_at = now
        e.expires_at = now + duration
        triggered.append(e)

    if state.mood < 30 and "low_mood" not in active_ids:
        e = copy.copy(EFFECT_BY_ID["low_mood"])
        e.started_at = now
        e.expires_at = now + duration
        triggered.append(e)

    if state.social_need > 70 and "lonely" not in active_ids:
        e = copy.copy(EFFECT_BY_ID["lonely"])
        e.started_at = now
        e.expires_at = now + duration
        triggered.append(e)

    if state.satiety < 30 and "hungry" not in active_ids:
        e = copy.copy(EFFECT_BY_ID["hungry"])
        e.started_at = now
        e.expires_at = now + duration
        triggered.append(e)

    if state.mood < 20 and "irritated" not in active_ids:
        e = copy.copy(EFFECT_BY_ID["irritated"])
        e.started_at = now
        e.expires_at = now + duration
        triggered.append(e)

    if state.energy < 50 and "sleepy" not in active_ids:
        e = copy.copy(EFFECT_BY_ID["sleepy"])
        e.started_at = now
        e.expires_at = now + duration
        triggered.append(e)

    if state.energy > 80 and state.mood > 80 and "thriving" not in active_ids:
        e = copy.copy(EFFECT_BY_ID["thriving"])
        e.started_at = now
        e.expires_at = now + duration
        triggered.append(e)

    return triggered


def apply_interaction(state: PersonaState, quality: str = "normal") -> PersonaState:
    """用户互动后刷新状态，返回更新后的 state（不持久化 last_interaction_at）。"""
    now = time.time()
    if quality == "good":
        mood_boost = 15.0
        social_boost = -20.0
        energy_cost = -5.0
    elif quality == "bad":
        mood_boost = -10.0
        social_boost = -10.0
        energy_cost = -5.0
    else:
        mood_boost = 5.0
        social_boost = -15.0
        energy_cost = -3.0

    return PersonaState(
        energy=_clamp(state.energy + energy_cost),
        mood=_clamp(state.mood + mood_boost),
        social_need=_clamp(state.social_need + social_boost),
        satiety=_clamp(state.satiety - 2.0),
        last_tick_at=state.last_tick_at,
        last_interaction_at=now,
    )


def generate_todos(state: PersonaState, active_effects: list[PersonaEffect]) -> list[PersonaTodo]:
    """根据当前状态和 active effects 生成角色脑内待办。"""
    todos: list[PersonaTodo] = []
    now = time.time()
    active_ids = {e.effect_id for e in active_effects}

    if "hungry" in active_ids or state.satiety < 40:
        todos.append(
            PersonaTodo(
                todo_type=TodoType.INTERNAL,
                title="想找点吃的",
                reason="satiety 低于阈值",
                priority=7,
                mood_bias=-2.0,
                expires_at=now + 3.0 * HOUR,
            )
        )

    if "lonely" in active_ids or state.social_need > 60:
        todos.append(
            PersonaTodo(
                todo_type=TodoType.SOCIAL,
                title="想和人聊聊天",
                reason="social_need 较高",
                priority=6,
                mood_bias=1.0,
                expires_at=now + 6.0 * HOUR,
            )
        )

    if "low_energy" in active_ids or "sleepy" in active_ids or state.energy < 40:
        todos.append(
            PersonaTodo(
                todo_type=TodoType.INTERNAL,
                title="想休息一下",
                reason="energy 较低",
                priority=8,
                mood_bias=-1.0,
                expires_at=now + 2.0 * HOUR,
            )
        )

    if "curious" in active_ids:
        todos.append(
            PersonaTodo(
                todo_type=TodoType.INTERNAL,
                title="想探索新话题",
                reason="处于好奇状态",
                priority=4,
                mood_bias=2.0,
                expires_at=now + 4.0 * HOUR,
            )
        )

    if "low_mood" in active_ids or state.mood < 40:
        todos.append(
            PersonaTodo(
                todo_type=TodoType.INTERNAL,
                title="想做点让自己开心的事",
                reason="mood 偏低",
                priority=6,
                mood_bias=2.0,
                expires_at=now + 5.0 * HOUR,
            )
        )

    todos.sort(key=lambda t: t.priority, reverse=True)
    return todos[:5]


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))
