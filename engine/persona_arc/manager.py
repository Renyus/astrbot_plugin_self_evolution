import time
from typing import TYPE_CHECKING

from .emotions import PersonaArcEmotionService
from .profiles import get_profile
from .rumination import PersonaArcRuminationService
from .scoring import score_consolidation_bonus, score_memory_pour
from .types import PersonaArcProfile, PersonaArcState

if TYPE_CHECKING:
    from ...main import SelfEvolutionPlugin


class PersonaArcManager:
    def __init__(self, plugin: "SelfEvolutionPlugin"):
        self.plugin = plugin
        self.dao = plugin.dao

        arc_id = plugin.cfg.persona_arc_active_arc_id or ""
        self._profile: PersonaArcProfile | None = None
        if arc_id:
            self._profile = get_profile(arc_id)

        self.emotions = PersonaArcEmotionService(plugin.dao)
        self.ruminations = PersonaArcRuminationService(plugin.dao)

    @property
    def enabled(self) -> bool:
        return self.plugin.cfg.persona_arc_enabled and self._profile is not None

    @property
    def profile(self) -> PersonaArcProfile | None:
        return self._profile

    def calc_stage(self, progress: float) -> int:
        if not self._profile:
            return 0
        stage = 0
        for s in self._profile.stages:
            if progress >= s.threshold:
                stage = s.stage
        return stage

    async def pour_from_message(self, scope_id: str, text: str, direct: bool = False):
        if not self.enabled:
            return
        amount = score_memory_pour(text, direct=direct)
        if amount <= 0:
            return
        await self.add_progress(scope_id, amount, reason="message_memory_pour")

    async def on_consolidation(self, scope_id: str, summary: str, stats: dict):
        if not self.enabled:
            return
        amount = score_consolidation_bonus(stats)
        if amount <= 0:
            return
        await self.add_progress(scope_id, amount, reason="daily_consolidation")

    async def add_progress(self, scope_id: str, amount: float, reason: str):
        state = await self.dao.get_persona_arc_state(scope_id)
        if not state.arc_id and self._profile:
            state.arc_id = self._profile.arc_id
        old_stage = state.arc_stage
        state.arc_progress += amount
        state.arc_stage = self.calc_stage(state.arc_progress)
        state.updated_at = time.time()
        await self.dao.upsert_persona_arc_state(state)
        if state.arc_stage != old_stage:
            await self.emit_stage_change(scope_id, old_stage, state.arc_stage, state.arc_progress, reason)

    async def emit_stage_change(self, scope_id: str, old_stage: int, new_stage: int, progress: float, reason: str):
        from ..persona_sim_types import EffectType, EventType, PersonaEffect, PersonaEvent

        arc_id = self._profile.arc_id if self._profile else "unknown"
        now = time.time()

        event = PersonaEvent(
            event_type=EventType.NATURAL,
            summary=f"PersonaArc stage changed: {old_stage} -> {new_stage}",
            causes=[f"arc_id={arc_id}", f"arc_progress={progress:.1f}", reason],
        )
        await self.dao.add_persona_event(scope_id, event)

        effect = PersonaEffect(
            effect_id=f"arc_stage_changed_{arc_id}_{new_stage}",
            effect_type=EffectType.NEUTRAL,
            name="人格弧线推进",
            source="persona_arc",
            intensity=1,
            started_at=now,
            expires_at=now + 3600,
            prompt_hint="刚刚有一段重要记忆沉了下来，心态变得不太一样",
            tags=["persona_arc", arc_id],
            source_detail=f"{arc_id} progressed to stage {new_stage}",
        )
        await self.dao.add_persona_effect(scope_id, effect)

    async def build_prompt(self, scope_id: str) -> str:
        if not self.enabled or not self._profile:
            return ""
        state = await self.dao.get_persona_arc_state(scope_id)
        if not state.arc_id:
            state.arc_id = self._profile.arc_id
            await self.dao.upsert_persona_arc_state(state)
        stage = self._profile.get_stage(state.arc_stage)
        prompt = f"{self._profile.lore_guard}\n\n{stage.prompt}"
        companion = await self.build_companion_prompt(scope_id)
        if companion:
            prompt += "\n\n" + companion
        return prompt

    async def record_emotion(
        self,
        scope_id: str,
        emotion_name: str,
        definition_by_user: str,
        source_text: str = "",
        confidence: float = 0.8,
    ) -> None:
        if not self.enabled or not self._profile:
            return
        arc_id = self._profile.arc_id
        await self.emotions.record_emotion(
            scope_id=scope_id,
            arc_id=arc_id,
            emotion_name=emotion_name,
            definition_by_user=definition_by_user,
            source_text=source_text,
            confidence=confidence,
        )

    async def build_companion_prompt(self, scope_id: str) -> str:
        if not self.enabled or not self._profile:
            return ""
        arc_id = self._profile.arc_id

        emotion_lines = []
        emotions = await self.emotions.list_emotions(scope_id, arc_id, limit=6)
        if emotions:
            emotion_lines.append("已学会的情感：")
            for em in emotions:
                name = em.get("emotion_name", "")
                definition = em.get("definition_by_user", "")
                if name and definition:
                    emotion_lines.append(f"- {name}：{definition}")

        ruminations = await self.ruminations.get_uninjected_ruminations(scope_id, arc_id, limit=3)
        rumination_lines = []
        injected_ids = []
        if ruminations:
            rumination_lines.append("最近离线反刍：")
            for rum in ruminations:
                text = rum.get("text", "")
                if text:
                    rumination_lines.append(f"- {text}")
                    rum_id = rum.get("id")
                    if rum_id:
                        injected_ids.append(rum_id)

        if not emotion_lines and not rumination_lines:
            return ""

        if injected_ids:
            await self.ruminations.mark_injected(injected_ids)

        parts = ["[人格弧线记忆]"]
        parts.extend(emotion_lines)
        if rumination_lines:
            parts.extend(rumination_lines)

        return "\n".join(parts)

    async def get_status_text(self, scope_id: str) -> str:
        if not self.enabled:
            return "Persona Arc 未启用。"
        state = await self.dao.get_persona_arc_state(scope_id)
        if not self._profile:
            return "Persona Arc 未配置 profile。"
        arc_id = state.arc_id or self._profile.arc_id
        arc_name = self._profile.display_name
        stage_num = state.arc_stage
        stage = self._profile.get_stage(stage_num)
        stage_name = stage.name
        progress = state.arc_progress

        lines = [
            "[Persona Arc]",
            f"arc: {arc_name} ({arc_id})",
            f"stage: {stage_num} {stage_name}",
            f"progress: {progress:.1f}",
        ]

        next_stage = None
        for s in self._profile.stages:
            if s.threshold > progress:
                next_stage = s
                break
        if next_stage:
            remaining = next_stage.threshold - progress
            lines.append(f"next: Stage {next_stage.stage} {next_stage.name} 还差 {remaining:.1f}")
        else:
            lines.append("已达到最高阶段")

        return "\n".join(lines)

    async def get_prompt_preview(self, scope_id: str) -> str:
        if not self.enabled:
            return "Persona Arc 未启用。"
        prompt = await self.build_prompt(scope_id)
        if not prompt:
            return "当前 scope 还没有弧线状态。"
        return prompt[:1500]

    async def debug_add_progress(self, scope_id: str, amount: float, reason: str = "debug") -> str:
        if not self.enabled:
            return "Persona Arc 未启用。"
        await self.add_progress(scope_id, amount, reason=f"debug:{reason}")
        return await self.get_status_text(scope_id)
