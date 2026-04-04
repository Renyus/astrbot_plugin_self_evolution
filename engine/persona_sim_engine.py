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


def _row_to_persona_effect(row: dict) -> PersonaEffect:
    """将 DB row 转换为 PersonaEffect 对象。"""
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


class PersonaSimEngine:
    def __init__(self, plugin):
        self.plugin = plugin
        self._dao = getattr(plugin, "dao", None)
        self._thought_cache: dict[str, str] = {}

    async def tick_time_only(self, scope_id: str, now: float | None = None) -> PersonaSnapshot:
        """只推进时间，不应用真实互动，不触发新效果。用于被动观察消息时的自动 tick。"""
        return await self.tick(scope_id, now=now, interaction_quality="none", skip_effect_triggers=True)

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
        active_effects = [e for e in (_row_to_persona_effect(r) for r in active_rows) if e.is_active(now)]
        active_ids = {e.effect_id for e in active_effects}

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
        skip_effect_triggers: bool = False,
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
        active_effects = [e for e in (_row_to_persona_effect(r) for r in active_rows) if e.is_active(now)]
        active_ids = {e.effect_id for e in active_effects}

        logger.debug(f"[PersonaSim] tick 从DB恢复 active_effects={list(active_ids)} scope={scope_id}")
        expired_ids = {row["effect_id"] for row in active_rows if now >= float(row["expires_at"]) > 0}
        if expired_ids and self._dao:
            await self._dao.deactivate_persona_effects(scope_id, list(expired_ids))

        triggered = []
        if not skip_effect_triggers:
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
        active_effects = [e for e in (_row_to_persona_effect(r) for r in active_rows) if e.is_active(now)]
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

    async def eat(self, food_data: dict, scope_id: str, user_id: str) -> PersonaSnapshot:
        """处理喂食事件，根据食物数据更新角色状态。

        Args:
            food_data: 包含 food_name, is_food, category, calories, tastiness 的字典
            scope_id: 作用域ID（群ID或private_用户ID）
            user_id: 喂食者ID

        Returns:
            更新后的 PersonaSnapshot
        """
        now = time.time()

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

        is_food = food_data.get("is_food", False)
        category = food_data.get("category", "non_food")
        calories = int(food_data.get("calories", 0))
        tastiness = int(food_data.get("tastiness", 0))
        food_name = food_data.get("food_name", "未知食物")

        new_effects: list[PersonaEffect] = []
        new_todos: list[PersonaTodo] = []
        satiety_delta = 0.0
        mood_delta = 0.0
        energy_delta = 0.0

        if not is_food or category == "dark_cuisine":
            mood_delta = -40.0
            satiety_delta = -10.0
            source_detail = f"被试图投喂不可描述的东西（{food_name}），感到极度恶心和愤怒"
            effect_type = EffectType.DEBUFF
            effect_id = f"bad_food_{int(now)}"
            new_effects.append(
                PersonaEffect(
                    effect_id=effect_id,
                    effect_type=effect_type,
                    name="摄入异常",
                    source="feed",
                    intensity=90,
                    started_at=now,
                    expires_at=now + 3600,
                    prompt_hint="被试图投喂恶心至极的东西，非常愤怒，想骂人",
                    tags=["negative", "food", "disgusting"],
                    source_detail=source_detail,
                    decay_style="gradual",
                    recovery_style="passive",
                )
            )
            new_todos.append(
                PersonaTodo(
                    todo_type=TodoType.INTERNAL,
                    title="想骂人",
                    reason="被试图喂恶心东西，非常不爽",
                    priority=9,
                    mood_bias=-15.0,
                    expires_at=now + 3600,
                )
            )

        elif state.satiety > 90:
            mood_delta = -10.0
            satiety_delta = 5.0
            source_detail = f"已经吃撑了还被硬塞了{food_name}，很难受"
            effect_id = f"overfed_{int(now)}"
            new_effects.append(
                PersonaEffect(
                    effect_id=effect_id,
                    effect_type=EffectType.DEBUFF,
                    name="过饱",
                    source="feed",
                    intensity=40,
                    started_at=now,
                    expires_at=now + 2400,
                    prompt_hint="已经吃撑了，很难受",
                    tags=["negative", "food"],
                    source_detail=source_detail,
                    decay_style="gradual",
                    recovery_style="passive",
                )
            )
            new_todos.append(
                PersonaTodo(
                    todo_type=TodoType.INTERNAL,
                    title="想安静消化",
                    reason="吃太多了，需要休息消化",
                    priority=8,
                    mood_bias=-3.0,
                    expires_at=now + 2400,
                )
            )

        else:
            satiety_delta = calories * 0.4
            mood_delta = tastiness * 0.15

            if category == "dessert":
                energy_delta = 10.0
                source_detail = f"品尝了甜点{food_name}，心情不错，还获得了短暂的能量加成~"
            else:
                source_detail = f"品尝了{food_name}，心情不错"

            effect_type = EffectType.BUFF
            effect_id = f"ate_{category}_{int(now)}"

            if category == "dessert":
                prompt_hint = f"刚吃了甜点{food_name}，甜甜的心情"
                tags = ["positive", "food", "dessert"]
                intensity = min(60, 30 + tastiness // 2)
            else:
                prompt_hint = f"刚吃了{food_name}，满足"
                tags = ["positive", "food"]
                intensity = min(50, 20 + tastiness // 3)

            new_effects.append(
                PersonaEffect(
                    effect_id=effect_id,
                    effect_type=effect_type,
                    name="进食满足",
                    source="feed",
                    intensity=intensity,
                    started_at=now,
                    expires_at=now + 3600,
                    prompt_hint=prompt_hint,
                    tags=tags,
                    source_detail=source_detail,
                    decay_style="gradual",
                    recovery_style="passive",
                )
            )

            if category == "dessert":
                energy_effect_id = f"dessert_energy_{int(now)}"
                new_effects.append(
                    PersonaEffect(
                        effect_id=energy_effect_id,
                        effect_type=EffectType.BUFF,
                        name="甜点能量",
                        source="feed",
                        intensity=30,
                        started_at=now,
                        expires_at=now + 1800,
                        prompt_hint="甜点带来的短暂能量boost",
                        tags=["positive", "energy", "dessert"],
                        source_detail="甜点带来的能量加成",
                        decay_style="quick",
                        recovery_style="passive",
                    )
                )

        state.satiety = max(0.0, min(100.0, state.satiety + satiety_delta))
        state.mood = max(0.0, min(100.0, state.mood + mood_delta))
        state.energy = max(0.0, min(100.0, state.energy + energy_delta))
        state.last_interaction_at = now

        eat_event = PersonaEvent(
            event_type=EventType.INTERACTION,
            summary=f"喂食: {food_name} is_food={is_food} category={category}",
            causes=[f"food_name={food_name}", f"calories={calories}", f"tastiness={tastiness}"],
            effects_applied=[e.effect_id for e in new_effects],
            timestamp=now,
            interaction_mode="passive",
            interaction_outcome="connected",
        )

        active_rows = await self._dao.get_active_persona_effects(scope_id) if self._dao else []
        active_effects = [e for e in (_row_to_persona_effect(r) for r in active_rows) if e.is_active(now)]
        active_ids = {e.effect_id for e in active_effects}

        for e in new_effects:
            active_effects.append(e)
            active_ids.add(e.effect_id)
            if self._dao:
                await self._dao.add_persona_effect(scope_id, e)

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

        triggered = eval_effect_triggers(state, active_ids, recent_events + [eat_event], now)
        for e in triggered:
            if e.effect_id not in active_ids:
                active_effects.append(e)
                active_ids.add(e.effect_id)
                if self._dao:
                    await self._dao.add_persona_effect(scope_id, e)

        all_todos = generate_todos(state, active_effects, recent_events + [eat_event])
        for td in new_todos:
            if not any(t.title == td.title for t in all_todos):
                all_todos.insert(0, td)

        if self._dao:
            await self._dao.clear_persona_todos(scope_id)
        for td in all_todos:
            if self._dao:
                await self._dao.add_persona_todo(scope_id, td)

        if self._dao:
            await self._dao.add_persona_event(scope_id, eat_event)
            await self._dao.upsert_persona_state(scope_id, state)

        return PersonaSnapshot(
            state=state,
            active_effects=active_effects,
            pending_todos=all_todos[:5],
            recent_events=recent_events + [eat_event],
            snapshot_at=now,
        )
