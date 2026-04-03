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
    TodoType,
)

logger = logging.getLogger("astrbot")


class PersonaSimEngine:
    def __init__(self, plugin):
        self.plugin = plugin
        self._dao = getattr(plugin, "dao", None)
        self._thought_cache: dict[str, str] = {}

    async def tick_time_only(self, scope_id: str, now: float | None = None) -> PersonaSnapshot:
        """只推进时间，不应用真实互动。用于被动观察消息时的自动 tick。"""
        return await self.tick(scope_id, now=now, interaction_quality="none")

    async def apply_interaction(
        self,
        scope_id: str,
        quality: str = "normal",
        mode: str = "passive",
        outcome: str = "connected",
        now: float | None = None,
    ) -> PersonaSnapshot:
        """独立公开接口：仅应用互动效果，不推进时间。返回更新后的 snapshot。

        互动后会立刻重算 effect（bad/awkward -> wronged/irritated, good/relief -> relieved/satisfied），
        确保"互动 -> effect -> todo -> snapshot"在同一拍完成。
        """
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
                thought_process=state_row.get("thought_process", ""),
            )
        else:
            state = PersonaState()

        state = apply_interaction(state, quality, mode, outcome)

        interaction_event = PersonaEvent(
            event_type=EventType.INTERACTION,
            summary=f"互动: quality={quality} mode={mode} outcome={outcome}",
            causes=[f"quality={quality}", f"mode={mode}", f"outcome={outcome}"],
            effects_applied=[],
            timestamp=now,
            interaction_mode=mode,
            interaction_outcome=outcome,
        )
        if self._dao:
            await self._dao.add_persona_event(scope_id, interaction_event)
            await self._dao.upsert_persona_state(scope_id, state)

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
                source_detail=row.get("source_detail", ""),
                decay_style=row.get("decay_style", "gradual"),
                recovery_style=row.get("recovery_style", "passive"),
            )
            if e.is_active(now):
                active_effects.append(e)
                active_ids.add(e.effect_id)

        logger.debug(f"[PersonaSim] apply_interaction 从DB恢复 active_effects={list(active_ids)} scope={scope_id}")
        event_rows = await self._dao.get_recent_persona_events(scope_id, limit=5) if self._dao else []
        recent_events: list[PersonaEvent] = [
            PersonaEvent(
                event_type=EventType(e.get("event_type", "natural")),
                summary=e.get("summary", ""),
                causes=e.get("causes", "").split("|") if e.get("causes") else [],
                effects_applied=e.get("effects_applied", "").split("|") if e.get("effects_applied") else [],
                timestamp=float(e.get("timestamp", 0)),
                interaction_mode=e.get("interaction_mode", ""),
                interaction_outcome=e.get("interaction_outcome", ""),
            )
            for e in event_rows
        ]

        triggered = eval_effect_triggers(state, active_ids, recent_events, now)
        for e in triggered:
            active_effects.append(e)
            active_ids.add(e.effect_id)
            if self._dao:
                await self._dao.add_persona_effect(scope_id, e)
            effect_trigger_event = PersonaEvent(
                event_type=EventType.EFFECT_TRIGGER,
                summary=f"触发 effect: {e.effect_id}",
                causes=[f"interaction={quality}"],
                effects_applied=[e.effect_id],
                timestamp=now,
            )
            if self._dao:
                await self._dao.add_persona_event(scope_id, effect_trigger_event)

        pending_todos = generate_todos(state, active_effects, recent_events)
        if self._dao:
            await self._dao.clear_persona_todos(scope_id)
        for td in pending_todos:
            if self._dao:
                await self._dao.add_persona_todo(scope_id, td)

        fresh_event_rows = await self._dao.get_recent_persona_events(scope_id, limit=5) if self._dao else []
        snapshot_recent = [
            PersonaEvent(
                event_type=EventType(e.get("event_type", "natural")),
                summary=e.get("summary", ""),
                causes=e.get("causes", "").split("|") if e.get("causes") else [],
                effects_applied=e.get("effects_applied", "").split("|") if e.get("effects_applied") else [],
                timestamp=float(e.get("timestamp", 0)),
                interaction_mode=e.get("interaction_mode", ""),
                interaction_outcome=e.get("interaction_outcome", ""),
            )
            for e in fresh_event_rows
        ]

        return PersonaSnapshot(
            state=state,
            active_effects=active_effects,
            pending_todos=pending_todos,
            recent_events=snapshot_recent,
            snapshot_at=now,
        )

    async def tick(
        self,
        scope_id: str,
        now: float | None = None,
        interaction_quality: str = "none",
        interaction_mode: str = "passive",
        interaction_outcome: str = "connected",
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
                thought_process=state_row.get("thought_process", ""),
            )
        else:
            state = PersonaState()

        elapsed = calc_time_delta_hours(state.last_tick_at, now)
        interaction_recent = (now - state.last_interaction_at) < 6.0 * 3600.0

        state, time_events, _ = calc_state_delta(state, elapsed, interaction_recent)

        event_rows = await self._dao.get_recent_persona_events(scope_id, limit=5) if self._dao else []
        recent_events: list[PersonaEvent] = [
            PersonaEvent(
                event_type=EventType(e.get("event_type", "natural")),
                summary=e.get("summary", ""),
                causes=e.get("causes", "").split("|") if e.get("causes") else [],
                effects_applied=e.get("effects_applied", "").split("|") if e.get("effects_applied") else [],
                timestamp=float(e.get("timestamp", 0)),
                interaction_mode=e.get("interaction_mode", ""),
                interaction_outcome=e.get("interaction_outcome", ""),
            )
            for e in event_rows
        ]

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
                source_detail=row.get("source_detail", ""),
                decay_style=row.get("decay_style", "gradual"),
                recovery_style=row.get("recovery_style", "passive"),
            )
            if e.is_active(now):
                active_effects.append(e)
                active_ids.add(e.effect_id)

        logger.debug(f"[PersonaSim] tick 从DB恢复 active_effects={list(active_ids)} scope={scope_id}")
        expired_ids = {row["effect_id"] for row in active_rows if now >= float(row["expires_at"]) > 0}
        if expired_ids and self._dao:
            await self._dao.deactivate_persona_effects(scope_id, list(expired_ids))

        triggered = eval_effect_triggers(state, active_ids, recent_events, now)
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
            state = apply_interaction(state, interaction_quality, interaction_mode, interaction_outcome)
            logger.info(
                f"[PersonaSim] tick 互动触达 scope={scope_id} quality={interaction_quality} mode={interaction_mode} outcome={interaction_outcome}"
            )
            interaction_event = PersonaEvent(
                event_type=EventType.INTERACTION,
                summary=f"互动: quality={interaction_quality} mode={interaction_mode} outcome={interaction_outcome}",
                causes=[f"quality={interaction_quality}", f"mode={interaction_mode}", f"outcome={interaction_outcome}"],
                effects_applied=[],
                timestamp=now,
                interaction_mode=interaction_mode,
                interaction_outcome=interaction_outcome,
            )
            post_interaction_triggers = eval_effect_triggers(
                state, active_ids, recent_events + [interaction_event], now
            )
            if post_interaction_triggers:
                logger.info(
                    f"[PersonaSim] tick post-interaction 触发 effect: {[e.effect_id for e in post_interaction_triggers]}"
                )
            post_interaction_effect_events: list[PersonaEvent] = []
            for e in post_interaction_triggers:
                active_effects.append(e)
                active_ids.add(e.effect_id)
                post_interaction_effect_events.append(
                    PersonaEvent(
                        event_type=EventType.EFFECT_TRIGGER,
                        summary=f"触发 effect: {e.effect_id}",
                        causes=[f"interaction={interaction_quality}"],
                        effects_applied=[e.effect_id],
                        timestamp=now,
                    )
                )
                if self._dao:
                    await self._dao.add_persona_effect(scope_id, e)
            time_events.append(interaction_event)
            effect_events.extend(post_interaction_effect_events)
            recent_events = recent_events + [interaction_event]

        pending_todos = generate_todos(state, active_effects, recent_events)
        if self._dao:
            await self._dao.clear_persona_todos(scope_id)
        for td in pending_todos:
            if self._dao:
                await self._dao.add_persona_todo(scope_id, td)

        all_events = time_events + effect_events
        for ev in all_events:
            if self._dao:
                await self._dao.add_persona_event(scope_id, ev)

        snapshot_recent_events = recent_events
        if all_events and self._dao:
            fresh_rows = await self._dao.get_recent_persona_events(scope_id, limit=5)
            snapshot_recent_events = [
                PersonaEvent(
                    event_type=EventType(e.get("event_type", "natural")),
                    summary=e.get("summary", ""),
                    causes=e.get("causes", "").split("|") if e.get("causes") else [],
                    effects_applied=e.get("effects_applied", "").split("|") if e.get("effects_applied") else [],
                    timestamp=float(e.get("timestamp", 0)),
                    interaction_mode=e.get("interaction_mode", ""),
                    interaction_outcome=e.get("interaction_outcome", ""),
                )
                for e in fresh_rows
            ]

        if self._dao:
            existing_thought = self._thought_cache.get(scope_id, getattr(state, "thought_process", ""))
            if existing_thought:
                state.thought_process = existing_thought
            await self._dao.upsert_persona_state(scope_id, state)

        snapshot = PersonaSnapshot(
            state=state,
            active_effects=active_effects,
            pending_todos=pending_todos,
            recent_events=snapshot_recent_events,
            snapshot_at=now,
        )

        return snapshot

    async def generate_thought_process(self, scope_id: str) -> str:
        """用 LLM 根据当前状态和待办生成个性化内心独白。"""
        snapshot = await self.get_snapshot(scope_id)
        if not snapshot:
            return ""

        todo_lines = []
        for td in snapshot.pending_todos[:3]:
            todo_lines.append(f"- {td.title}（原因: {td.reason}）优先级: {td.priority}/10")

        effect_lines = []
        for e in snapshot.active_effects:
            if e.prompt_hint:
                effect_lines.append(f"- {e.name}: {e.prompt_hint}")

        state = snapshot.state
        umo = (
            getattr(self.plugin, "get_group_umo", lambda g: None)(scope_id)
            if hasattr(self.plugin, "get_group_umo")
            else None
        )

        persona_prompt = ""
        if hasattr(self.plugin, "_get_active_persona_prompt") and umo:
            try:
                persona_prompt = await self.plugin._get_active_persona_prompt(umo)
            except Exception:
                pass

        persona_section = f"\n\n【角色设定】\n{persona_prompt}" if persona_prompt else ""

        prompt = f"""你是一个角色的内心独白生成器。根据以下信息，生成一段该角色此刻的内心想法（50-100字）。{persona_section}

【角色当前状态】
- 活力: {state.energy:.0f}/100
- 心情: {state.mood:.0f}/100
- 社交渴望: {state.social_need:.0f}/100
- 饱腹感: {state.satiety:.0f}/100

【当前效果】
{effect_lines if effect_lines else "（无特殊状态）"}

【待办事项】（按优先级排序，必须体现当前最想做的事）
{todo_lines if todo_lines else "（无待办）"}

要求：
- 严格遵循角色设定中的语气、性格、说话习惯
- 内心想法必须围绕当前最优先的待办事项，不要写无关内容
- 第一人称，代入角色视角
- 不要太长，简洁有力
- 不要用括号或星号
- 不要复述状态数值，要表达内心感受

内心独白："""

        try:
            llm_provider = self.plugin.context.get_using_provider(umo=umo)
            if not llm_provider:
                logger.warning(f"[PersonaSim] 无法获取 LLM provider for scope={scope_id}")
                return ""
            res = await llm_provider.text_chat(prompt=prompt, system_prompt="", contexts=[])
            thought = res.completion_text.strip() if hasattr(res, "completion_text") and res.completion_text else ""
            if thought and thought.startswith("内心独白："):
                thought = thought[len("内心独白：") :]
            thought = thought.strip("\"'。\n ")

            if thought:
                self._thought_cache[scope_id] = thought
                if self._dao:
                    state_row = await self._dao.get_persona_state(scope_id)
                    if state_row:
                        from .persona_sim_types import PersonaState

                        updated_state = PersonaState(
                            energy=float(state_row["energy"]),
                            mood=float(state_row["mood"]),
                            social_need=float(state_row["social_need"]),
                            satiety=float(state_row["satiety"]),
                            last_tick_at=float(state_row["last_tick_at"]),
                            last_interaction_at=float(state_row["last_interaction_at"]),
                            thought_process=thought,
                        )
                        await self._dao.upsert_persona_state(scope_id, updated_state)
                logger.info(f"[PersonaSim] 成功生成内心独白 for scope={scope_id}: {thought[:50]}...")
            return thought
        except Exception as e:
            logger.warning(f"[PersonaSim] 生成内心独白失败 scope={scope_id}: {e}")
            return ""

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
            thought_process=self._thought_cache.get(scope_id, "") or state_row.get("thought_process", ""),
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
                source_detail=row.get("source_detail", ""),
                decay_style=row.get("decay_style", "gradual"),
                recovery_style=row.get("recovery_style", "passive"),
            )

        active_effects = [e for e in (_row_to_effect(r) for r in active_rows) if e.is_active(now)]
        logger.debug(
            f"[PersonaSim] get_snapshot 从DB恢复 active_effects={[e.effect_id for e in active_effects]} scope={scope_id}"
        )
        event_rows = await self._dao.get_recent_persona_events(scope_id, limit=5)
        recent_events = [
            PersonaEvent(
                event_type=EventType(e.get("event_type", "natural")),
                summary=e.get("summary", ""),
                causes=e.get("causes", "").split("|") if e.get("causes") else [],
                effects_applied=e.get("effects_applied", "").split("|") if e.get("effects_applied") else [],
                timestamp=float(e.get("timestamp", 0)),
                interaction_mode=e.get("interaction_mode", ""),
                interaction_outcome=e.get("interaction_outcome", ""),
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
