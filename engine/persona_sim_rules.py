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
        source_detail="体力自然消耗，未及时休息",
        decay_style="slow",
        recovery_style="rest",
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
        source_detail="持续的情绪下行",
        decay_style="gradual",
        recovery_style="positive_event",
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
        source_detail="长时间没有社交互动",
        decay_style="slow",
        recovery_style="social_connection",
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
        source_detail="饱腹感持续下降",
        decay_style="steady",
        recovery_style="eating",
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
        source_detail="遭遇不愉快互动或持续压力",
        decay_style="responsive",
        recovery_style="calm_environment",
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
        source_detail="被忽视或遭受尴尬/负面互动",
        decay_style="sticky",
        recovery_style="being_heard",
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
        source_detail="精力持续不足且未得到充分休息",
        decay_style="accelerating",
        recovery_style="sleep",
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
        source_detail="近期有多次互动接触",
        decay_style="quick",
        recovery_style="satisfying_interaction",
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
        source_detail="在低落之后被正面互动接住",
        decay_style="gentle",
        recovery_style="sustained_positive",
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
        source_detail="互动质量好，预期被满足",
        decay_style="moderate",
        recovery_style="continued_positive",
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
        source_detail="体力透支，早该休息了",
        decay_style="accelerating",
        recovery_style="sleep",
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
        source_detail="精力和心情同时处于高位",
        decay_style="slow",
        recovery_style="maintaining_balance",
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
    recent_events: list[PersonaEvent],
    now: float,
) -> list[PersonaEffect]:
    """根据状态+近期事件组合条件，判断是否应触发 effect。"""
    triggered: list[PersonaEffect] = []
    duration = 2.0 * HOUR

    recent_bad = any(e.event_type == EventType.INTERACTION and "bad" in e.causes for e in recent_events[-3:])
    recent_good = any(e.event_type == EventType.INTERACTION and "good" in e.causes for e in recent_events[-3:])
    recent_connected = any(
        e.event_type == EventType.INTERACTION and getattr(e, "interaction_outcome", "") == "connected"
        for e in recent_events[-3:]
    )
    recent_missed = any(
        e.event_type == EventType.INTERACTION and getattr(e, "interaction_outcome", "") == "missed"
        for e in recent_events[-3:]
    )
    recent_interaction_hours = (now - state.last_interaction_at) / 3600.0 if state.last_interaction_at > 0 else 999.0

    if state.energy < 30 and "low_energy" not in active_ids:
        e = copy.copy(EFFECT_BY_ID["low_energy"])
        e.started_at = now
        e.expires_at = now + duration
        e.prompt_hint = "提不起劲，什么都懒得做"
        e.source_detail = f"体力跌至{state.energy:.0f}，自然消耗累积"
        triggered.append(e)

    if state.mood < 30 and "low_mood" not in active_ids:
        e = copy.copy(EFFECT_BY_ID["low_mood"])
        e.started_at = now
        e.expires_at = now + duration
        e.prompt_hint = "心情有点低落，什么都提不起兴趣"
        e.source_detail = f"心情持续低迷，当前{state.mood:.0f}"
        triggered.append(e)

    if state.social_need > 70 and recent_interaction_hours > 2.0 and "lonely" not in active_ids:
        e = copy.copy(EFFECT_BY_ID["lonely"])
        e.started_at = now
        e.expires_at = now + duration
        e.prompt_hint = "好久没和人聊了，有点想找人说说笑"
        e.source_detail = f"超过{recent_interaction_hours:.1f}小时无互动，社交需求积累"
        triggered.append(e)

    if state.satiety < 30 and "hungry" not in active_ids:
        e = copy.copy(EFFECT_BY_ID["hungry"])
        e.started_at = now
        e.expires_at = now + duration
        e.source_detail = f"饱腹感降至{state.satiety:.0f}"
        triggered.append(e)

    if state.mood < 20 and "irritated" not in active_ids:
        e = copy.copy(EFFECT_BY_ID["irritated"])
        e.started_at = now
        e.expires_at = now + duration
        e.prompt_hint = "最近有点烦躁，对什么都提不起耐心"
        e.source_detail = "心情极低引发的烦躁"
        triggered.append(e)
    elif state.mood < 30 and recent_bad and "irritated" not in active_ids:
        e = copy.copy(EFFECT_BY_ID["irritated"])
        e.started_at = now
        e.expires_at = now + duration
        e.prompt_hint = "受了点委屈，现在有点不耐烦"
        e.source_detail = "负面互动引发的烦躁感"
        triggered.append(e)

    if state.energy < 50 and recent_interaction_hours > 3.0 and "sleepy" not in active_ids:
        e = copy.copy(EFFECT_BY_ID["sleepy"])
        e.started_at = now
        e.expires_at = now + duration
        e.prompt_hint = "有点困了，脑子转不动"
        e.source_detail = "精力低下合并长期未休息"
        triggered.append(e)

    if state.energy > 80 and state.mood > 80 and "thriving" not in active_ids:
        e = copy.copy(EFFECT_BY_ID["thriving"])
        e.started_at = now
        e.expires_at = now + duration
        e.prompt_hint = "状态很好，什么都挺顺的"
        e.source_detail = f"精力{state.energy:.0f}、心情{state.mood:.0f}双高"
        triggered.append(e)

    if recent_bad and "wronged" not in active_ids:
        e = copy.copy(EFFECT_BY_ID["wronged"])
        e.started_at = now
        e.expires_at = now + duration
        e.prompt_hint = "刚才的互动有点受挫，心里不太舒服"
        bad_event = next(
            (ev for ev in recent_events[-3:] if ev.event_type == EventType.INTERACTION and "bad" in ev.causes), None
        )
        if bad_event:
            outcome = getattr(bad_event, "interaction_outcome", "")
            mode = getattr(bad_event, "interaction_mode", "")
            if outcome == "missed":
                e.source_detail = "主动搭话但被冷落，期望落空"
            elif outcome == "connected" and mode == "passive":
                e.source_detail = "被动接受负面互动，有苦说不出"
            else:
                e.source_detail = "遭遇负面互动，体验受挫"
        else:
            e.source_detail = "负面互动导致的委屈感"
        triggered.append(e)

    if recent_good and "low_mood" in active_ids and "relieved" not in active_ids:
        e = copy.copy(EFFECT_BY_ID["relieved"])
        e.started_at = now
        e.expires_at = now + duration
        e.prompt_hint = "心情终于好起来了，轻松了不少"
        e.source_detail = "低落之后被正面互动接住"
        triggered.append(e)

    if recent_interaction_hours < 1.0 and "curious" not in active_ids:
        e = copy.copy(EFFECT_BY_ID["curious"])
        e.started_at = now
        e.expires_at = now + duration
        e.prompt_hint = "最近聊得挺多的，对什么都有点好奇"
        e.source_detail = "短时间内有多次互动，接触面扩大"
        triggered.append(e)

    if recent_good and "satisfied" not in active_ids:
        e = copy.copy(EFFECT_BY_ID["satisfied"])
        e.started_at = now
        e.expires_at = now + duration
        e.prompt_hint = "刚才的互动挺愉快的，心里挺满足"
        e.source_detail = "高质量互动带来的满足感"
        triggered.append(e)

    if "low_energy" in active_ids and recent_interaction_hours > 4.0 and "tired" not in active_ids:
        e = copy.copy(EFFECT_BY_ID["tired"])
        e.started_at = now
        e.expires_at = now + duration
        e.prompt_hint = "一直没怎么休息，有点撑不住了"
        e.source_detail = "精力持续透支，支撑已达极限"
        triggered.append(e)

    return triggered


def apply_interaction(
    state: PersonaState, quality: str = "normal", mode: str = "passive", outcome: str = "connected"
) -> PersonaState:
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
    elif quality == "brief":
        mood_boost = 3.0
        social_boost = -5.0
        energy_cost = -1.0
    elif quality == "awkward":
        mood_boost = -5.0
        social_boost = -5.0
        energy_cost = -2.0
    elif quality == "relief":
        mood_boost = 20.0
        social_boost = -10.0
        energy_cost = -3.0
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


def generate_todos(
    state: PersonaState,
    active_effects: list[PersonaEffect],
    recent_events: list[PersonaEvent] | None = None,
) -> list[PersonaTodo]:
    """根据当前状态、active effects 和近期事件生成角色脑内待办。委托给 persona_sim_todo。"""
    from .persona_sim_todo import make_todos

    return make_todos(state, active_effects, recent_events)


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))
