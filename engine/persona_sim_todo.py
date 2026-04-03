"""
Persona Sim Todo - 待办生成层

从 rules.py 里提取的 generate_todos 独立成层。
不存储，不持久化，只负责"根据状态和 active effects 生成 todo"。
"""

import time
from .persona_sim_rules import HOUR
from .persona_sim_types import EventType, PersonaEffect, PersonaEvent, PersonaState, PersonaTodo, TodoType


def _get_interaction_context(recent_events):
    """从近期事件中提取更完整的互动语义。"""
    if not recent_events:
        return {
            "last_quality": None,
            "last_mode": None,
            "last_outcome": None,
            "recent_bad": False,
            "recent_good": False,
            "recent_missed": False,
            "recent_connected": False,
            "last_event": None,
        }
    recent = recent_events[-5:] if len(recent_events) > 5 else recent_events
    interaction_events = [e for e in recent if e.event_type == EventType.INTERACTION]
    if not interaction_events:
        return {
            "last_quality": None,
            "last_mode": None,
            "last_outcome": None,
            "recent_bad": False,
            "recent_good": False,
            "recent_missed": False,
            "recent_connected": False,
            "last_event": None,
        }
    last = interaction_events[-1]
    qualities = [c.split("=")[1] for c in last.causes if c.startswith("quality=")]
    modes = [getattr(last, "interaction_mode", "")] or [c.split("=")[1] for c in last.causes if c.startswith("mode=")]
    outcomes = [getattr(last, "interaction_outcome", "")] or [
        c.split("=")[1] for c in last.causes if c.startswith("outcome=")
    ]
    return {
        "last_quality": qualities[-1] if qualities else None,
        "last_mode": modes[-1] if modes else "",
        "last_outcome": outcomes[-1] if outcomes else "",
        "recent_bad": any("bad" in e.causes or "awkward" in e.causes for e in interaction_events),
        "recent_good": any("good" in e.causes or "relief" in e.causes for e in interaction_events),
        "recent_missed": any(getattr(e, "interaction_outcome", "") == "missed" for e in interaction_events),
        "recent_connected": any(getattr(e, "interaction_outcome", "") == "connected" for e in interaction_events),
        "last_event": last,
    }


def make_todos(
    state: PersonaState,
    active_effects: list[PersonaEffect],
    recent_events: list[PersonaEvent] | None = None,
) -> list[PersonaTodo]:
    """根据当前状态、active effects 和近期事件生成角色脑内挂念（最多3条）。

    目标：不是功能型任务清单，而是更像"角色脑内真实挂着的事"。
    - need_todo（TodoType.INTERNAL）：生理需求型，如饿、累、想安静
    - social_todo（TodoType.SOCIAL）：关系型，如想把话接上、想找人聊、想躲热闹
    """
    todos: list[PersonaTodo] = []
    active_ids = {e.effect_id for e in active_effects}
    ctx = _get_interaction_context(recent_events)
    recent_bad = ctx["recent_bad"]
    recent_good = ctx["recent_good"]
    recent_missed = ctx["recent_missed"]
    last_mode = ctx["last_mode"]
    last_outcome = ctx["last_outcome"]

    if "hungry" in active_ids or state.satiety < 40:
        todos.append(
            PersonaTodo(
                todo_type=TodoType.INTERNAL,
                title="想找点东西吃",
                reason="肚子空了",
                priority=7,
                mood_bias=-2.0,
                expires_at=time.time() + 3.0 * HOUR,
            )
        )

    if "low_energy" in active_ids or "sleepy" in active_ids or state.energy < 40:
        todos.append(
            PersonaTodo(
                todo_type=TodoType.INTERNAL,
                title="想眯一会儿",
                reason="有点撑不住了",
                priority=8,
                mood_bias=-1.0,
                expires_at=time.time() + 2.0 * HOUR,
            )
        )

    if "wronged" in active_ids:
        if recent_missed and last_mode == "active":
            todos.append(
                PersonaTodo(
                    todo_type=TodoType.SOCIAL,
                    title="想找机会把当时没说完的话接上",
                    reason="主动搭话但被冷落，还挂着",
                    priority=7,
                    mood_bias=-1.0,
                    expires_at=time.time() + 4.0 * HOUR,
                )
            )
        elif last_mode == "passive":
            todos.append(
                PersonaTodo(
                    todo_type=TodoType.INTERNAL,
                    title="算了，懒得想了",
                    reason="被动受了委屈，不太想再提",
                    priority=5,
                    mood_bias=-1.0,
                    expires_at=time.time() + 4.0 * HOUR,
                )
            )
        else:
            todos.append(
                PersonaTodo(
                    todo_type=TodoType.INTERNAL,
                    title="想把刚才那口气顺过来",
                    reason="心里还堵着",
                    priority=6,
                    mood_bias=-1.0,
                    expires_at=time.time() + 4.0 * HOUR,
                )
            )

    if "lonely" in active_ids:
        if ctx["recent_connected"]:
            todos.append(
                PersonaTodo(
                    todo_type=TodoType.SOCIAL,
                    title="刚才那通还没聊够",
                    reason="好不容易聊上了，还想继续",
                    priority=7,
                    mood_bias=1.0,
                    expires_at=time.time() + 3.0 * HOUR,
                )
            )
        elif state.social_need > 80:
            todos.append(
                PersonaTodo(
                    todo_type=TodoType.SOCIAL,
                    title="想找个人说说话",
                    reason="空得有点久了",
                    priority=7,
                    mood_bias=1.0,
                    expires_at=time.time() + 3.0 * HOUR,
                )
            )
        else:
            todos.append(
                PersonaTodo(
                    todo_type=TodoType.SOCIAL,
                    title="有点想找人唠叨两句",
                    reason="闷了一阵子了",
                    priority=6,
                    mood_bias=1.0,
                    expires_at=time.time() + 5.0 * HOUR,
                )
            )

    if "curious" in active_ids and last_mode == "active":
        todos.append(
            PersonaTodo(
                todo_type=TodoType.SOCIAL,
                title="话头还没尽兴，想继续聊",
                reason="刚开了个头就收住了，还想往下说",
                priority=5,
                mood_bias=2.0,
                expires_at=time.time() + 3.0 * HOUR,
            )
        )
    elif "curious" in active_ids:
        todos.append(
            PersonaTodo(
                todo_type=TodoType.INTERNAL,
                title="最近聊得有点意思，还想继续探探",
                reason="接触多了，对什么都想多知道点",
                priority=4,
                mood_bias=2.0,
                expires_at=time.time() + 4.0 * HOUR,
            )
        )

    if "relieved" in active_ids:
        todos.append(
            PersonaTodo(
                todo_type=TodoType.INTERNAL,
                title="想就这么轻松地待着",
                reason="好不容易缓过来，别再折腾了",
                priority=3,
                mood_bias=1.0,
                expires_at=time.time() + 3.0 * HOUR,
            )
        )

    if "satisfied" in active_ids and "low_mood" not in active_ids:
        todos.append(
            PersonaTodo(
                todo_type=TodoType.INTERNAL,
                title="趁状态好多做点什么",
                reason="心情正好，不想浪费",
                priority=4,
                mood_bias=1.0,
                expires_at=time.time() + 4.0 * HOUR,
            )
        )

    if "thriving" in active_ids:
        todos.append(
            PersonaTodo(
                todo_type=TodoType.SOCIAL,
                title="想把这份好状态分享出去",
                reason="最近什么都顺，想分享",
                priority=4,
                mood_bias=2.0,
                expires_at=time.time() + 5.0 * HOUR,
            )
        )

    if "low_mood" in active_ids or state.mood < 40:
        if recent_bad and not todos:
            todos.append(
                PersonaTodo(
                    todo_type=TodoType.INTERNAL,
                    title="想自己静一下",
                    reason="刚才的事还在脑子里转",
                    priority=6,
                    mood_bias=1.0,
                    expires_at=time.time() + 4.0 * HOUR,
                )
            )
        elif recent_good and not todos:
            todos.append(
                PersonaTodo(
                    todo_type=TodoType.INTERNAL,
                    title="想把这份好心情留着",
                    reason="难得舒服，想多待一会儿",
                    priority=5,
                    mood_bias=2.0,
                    expires_at=time.time() + 3.0 * HOUR,
                )
            )
        elif not todos:
            todos.append(
                PersonaTodo(
                    todo_type=TodoType.INTERNAL,
                    title="想做点什么转移一下注意力",
                    reason="有点闷，说不上来",
                    priority=6,
                    mood_bias=2.0,
                    expires_at=time.time() + 5.0 * HOUR,
                )
            )

    todos.sort(key=lambda t: t.priority, reverse=True)
    return todos[:3]
