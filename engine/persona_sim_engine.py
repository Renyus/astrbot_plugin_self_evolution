"""
Persona Sim Engine - 总入口

tick() 是唯一对外入口：
  tick(scope_id, now) -> PersonaSnapshot

调用顺序：
  1. 从 DAO 加载上次状态
  2. 计算时间差
  3. Rules.calc_state_delta()
  4. Rules.eval_effect_triggers()
  5. Rules.generate_todos()
  6. 保存新状态到 DAO
  7. 返回 PersonaSnapshot
"""

import logging
import time
from typing import Optional

from .persona_sim_rules import (
    apply_interaction,
    calc_state_delta,
    calc_time_delta_hours,
    eval_effect_triggers,
    generate_todos,
)
from .persona_sim_types import (
    EffectType,
    EventType,
    PersonaEffect,
    PersonaEvent,
    PersonaSnapshot,
    PersonaState,
    PersonaTodo,
)

logger = logging.getLogger("astrbot")


class PersonaSimEngine:
    def __init__(self, plugin):
        self.plugin = plugin
        self._dao = getattr(plugin, "dao", None)

    async def tick_time_only(self, scope_id: str, now: float | None = None) -> PersonaSnapshot:
        """只推进时间，不应用真实互动。用于被动观察消息时的自动 tick。"""
        return await self.tick(scope_id, now=now, interaction_quality="none")

    async def tick(
        self,
        scope_id: str,
        now: float | None = None,
        interaction_quality: str = "normal",
    ) -> PersonaSnapshot:
        """执行一次 tick 推演，返回当前 snapshot。"""
        now = now or time.time()

        state_row = await self._dao.get_persona_state(scope_id) if self._dao else None
        if state_row:
            state = PersonaState(
                energy=float(state_row["energy"]),
                mood=float(state_row["mood"]),
                social_need=float(state_row["social_need"]),
                satiety=float(state_row["satiety"]),
                last_tick_at=float(state_row["last_tick_at"]),
                last_interaction_at=float(state_row["last_interaction_at"]),
            )
        else:
            state = PersonaState()

        elapsed = calc_time_delta_hours(state.last_tick_at, now)
        interaction_recent = (now - state.last_interaction_at) < 6.0 * 3600.0

        state, time_events, _ = calc_state_delta(state, elapsed, interaction_recent)

        active_rows = await self._dao.get_active_persona_effects(scope_id) if self._dao else []
        active_effects: list[PersonaEffect] = []
        active_ids: set[str] = set()
        for row in active_rows:
            e = PersonaEffect(
                effect_id=row["effect_id"],
                effect_type=EffectType(row["effect_type"]),
                name=row["name"],
                source=row["source"],
                intensity=int(row["intensity"]),
                started_at=float(row["started_at"]),
                expires_at=float(row["expires_at"]),
                prompt_hint=row.get("prompt_hint", ""),
                tags=row.get("tags", "").split(",") if row.get("tags") else [],
            )
            if e.is_active(now):
                active_effects.append(e)
                active_ids.add(e.effect_id)

        expired_ids = {row["effect_id"] for row in active_rows if now >= float(row["expires_at"]) > 0}
        if expired_ids and self._dao:
            await self._dao.deactivate_persona_effects(scope_id, list(expired_ids))

        triggered = eval_effect_triggers(state, active_ids, now)
        effect_events: list[PersonaEvent] = []
        for e in triggered:
            active_effects.append(e)
            active_ids.add(e.effect_id)
            effect_events.append(
                PersonaEvent(
                    event_type=EventType.EFFECT_TRIGGER,
                    summary=f"触发 effect: {e.effect_id}",
                    causes=[],
                    effects_applied=[e.effect_id],
                )
            )
            if self._dao:
                await self._dao.add_persona_effect(scope_id, e)

        if interaction_quality != "none":
            state = apply_interaction(state, interaction_quality)

        pending_todos = generate_todos(state, active_effects)
        if self._dao:
            await self._dao.clear_persona_todos(scope_id)
        for td in pending_todos:
            if self._dao:
                await self._dao.add_persona_todo(scope_id, td)

        event_rows = await self._dao.get_recent_persona_events(scope_id, limit=5) if self._dao else []
        recent_events: list[PersonaEvent] = [
            PersonaEvent(
                event_type=EventType(e.get("event_type", "natural")),
                summary=e.get("summary", ""),
                causes=e.get("causes", "").split("|") if e.get("causes") else [],
                effects_applied=e.get("effects_applied", "").split("|") if e.get("effects_applied") else [],
                timestamp=float(e.get("timestamp", 0)),
            )
            for e in event_rows
        ]

        all_events = time_events + effect_events
        for ev in all_events:
            if self._dao:
                await self._dao.add_persona_event(scope_id, ev)

        if self._dao:
            await self._dao.upsert_persona_state(scope_id, state)

        snapshot = PersonaSnapshot(
            state=state,
            active_effects=active_effects,
            pending_todos=pending_todos,
            recent_events=recent_events,
            snapshot_at=now,
        )

        return snapshot

    async def get_snapshot(self, scope_id: str) -> Optional[PersonaSnapshot]:
        """只读取，不 tick。状态不存在返回 None。"""
        if not self._dao:
            return None
        now = time.time()
        state_row = await self._dao.get_persona_state(scope_id)
        if not state_row:
            return None
        state = PersonaState(
            energy=float(state_row["energy"]),
            mood=float(state_row["mood"]),
            social_need=float(state_row["social_need"]),
            satiety=float(state_row["satiety"]),
            last_tick_at=float(state_row["last_tick_at"]),
            last_interaction_at=float(state_row["last_interaction_at"]),
        )
        active_rows = await self._dao.get_active_persona_effects(scope_id)

        def _row_to_effect(row: dict) -> PersonaEffect:
            return PersonaEffect(
                effect_id=row["effect_id"],
                effect_type=EffectType(row["effect_type"]),
                name=row["name"],
                source=row["source"],
                intensity=int(row["intensity"]),
                started_at=float(row["started_at"]),
                expires_at=float(row["expires_at"]),
                prompt_hint=row.get("prompt_hint", ""),
                tags=row.get("tags", "").split(",") if row.get("tags") else [],
            )

        active_effects = [e for e in (_row_to_effect(r) for r in active_rows) if e.is_active(now)]
        event_rows = await self._dao.get_recent_persona_events(scope_id, limit=5)
        recent_events = [
            PersonaEvent(
                event_type=EventType(e.get("event_type", "natural")),
                summary=e.get("summary", ""),
                causes=e.get("causes", "").split("|") if e.get("causes") else [],
                effects_applied=e.get("effects_applied", "").split("|") if e.get("effects_applied") else [],
                timestamp=float(e.get("timestamp", 0)),
            )
            for e in event_rows
        ]
        pending_rows = await self._dao.get_active_persona_todos(scope_id)
        pending_todos = [
            PersonaTodo(
                todo_type=TodoType(t.get("todo_type", "internal")),
                title=t.get("title", ""),
                reason=t.get("reason", ""),
                priority=int(t.get("priority", 5)),
                mood_bias=float(t.get("mood_bias", 0)),
                expires_at=float(t.get("expires_at", 0)),
            )
            for t in pending_rows
            if float(t.get("expires_at", 0)) <= 0 or now < float(t.get("expires_at", 0))
        ]
        return PersonaSnapshot(
            state=state,
            active_effects=active_effects,
            pending_todos=pending_todos[:5],
            recent_events=recent_events,
            snapshot_at=now,
        )
