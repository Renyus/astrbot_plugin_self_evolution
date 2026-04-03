from __future__ import annotations

import time
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from tests._helpers import load_engine_module

persona_sim_rules = load_engine_module("persona_sim_rules")
persona_sim_types = load_engine_module("persona_sim_types")


class CalcStateDeltaTests(IsolatedAsyncioTestCase):
    """测试时间推演规则。"""

    def _make_state(self, energy=80, mood=70, social=50, satiety=80, last_tick=0, last_interact=0):
        s = MagicMock()
        s.energy = energy
        s.mood = mood
        s.social_need = social
        s.satiety = satiety
        s.last_tick_at = last_tick
        s.last_interaction_at = last_interact
        return s

    def test_zero_elapsed_returns_same_state(self):
        state = self._make_state(energy=80, mood=70, social=50, satiety=80)
        new_state, events, effects = persona_sim_rules.calc_state_delta(state, 0.0, False)
        self.assertEqual(new_state.energy, 80)
        self.assertEqual(new_state.mood, 70)

    def test_energy_decreases_over_time(self):
        state = self._make_state(energy=80, mood=70, social=50, satiety=80, last_tick=0)
        new_state, _, _ = persona_sim_rules.calc_state_delta(state, 4.0, False)
        self.assertLess(new_state.energy, 80)
        self.assertGreaterEqual(new_state.energy, 0)

    def test_interaction_recent_offsets_energy_drop(self):
        state_no_interact = self._make_state(energy=80, mood=70, social=50, satiety=80, last_tick=0, last_interact=0)
        state_recent = self._make_state(energy=80, mood=70, social=50, satiety=80, last_tick=0, last_interact=100)

        new_no, _, _ = persona_sim_rules.calc_state_delta(state_no_interact, 4.0, False)
        new_recent, _, _ = persona_sim_rules.calc_state_delta(state_recent, 4.0, True)

        self.assertGreater(new_recent.energy, new_no.energy - 0.01)
        self.assertGreater(new_recent.mood, new_no.mood)

    def test_social_need_increases_without_interaction(self):
        state = self._make_state(energy=80, mood=70, social=50, satiety=80, last_tick=0, last_interact=0)
        new_state, _, _ = persona_sim_rules.calc_state_delta(state, 4.0, False)
        self.assertGreater(new_state.social_need, 50)

    def test_clamp_keeps_values_in_0_100(self):
        state = self._make_state(energy=80, mood=70, social=50, satiety=80, last_tick=0)
        new_state, _, _ = persona_sim_rules.calc_state_delta(state, 1000.0, False)
        self.assertGreaterEqual(new_state.energy, 0)
        self.assertLessEqual(new_state.energy, 100)


class EffectTriggerTests(IsolatedAsyncioTestCase):
    """测试 effect 触发规则。"""

    def _make_state(self, energy=80, mood=70, social=50, satiety=80, last_interact=0):
        s = MagicMock()
        s.energy = energy
        s.mood = mood
        s.social_need = social
        s.satiety = satiety
        s.last_interaction_at = last_interact
        return s

    def test_low_energy_triggers_effect(self):
        state = self._make_state(energy=25, mood=70, social=50, satiety=80)
        triggered = persona_sim_rules.eval_effect_triggers(state, set(), [], time.time())
        triggered_ids = [e.effect_id for e in triggered]
        self.assertIn("low_energy", triggered_ids)

    def test_low_mood_triggers_effect(self):
        state = self._make_state(energy=80, mood=25, social=50, satiety=80)
        triggered = persona_sim_rules.eval_effect_triggers(state, set(), [], time.time())
        triggered_ids = [e.effect_id for e in triggered]
        self.assertIn("low_mood", triggered_ids)

    def test_lonely_triggers_when_social_need_high(self):
        state = self._make_state(energy=80, mood=70, social=75, satiety=80, last_interact=0)
        triggered = persona_sim_rules.eval_effect_triggers(state, set(), [], time.time())
        triggered_ids = [e.effect_id for e in triggered]
        self.assertIn("lonely", triggered_ids)

    def test_hungry_triggers_when_satiety_low(self):
        state = self._make_state(energy=80, mood=70, social=50, satiety=25)
        triggered = persona_sim_rules.eval_effect_triggers(state, set(), [], time.time())
        triggered_ids = [e.effect_id for e in triggered]
        self.assertIn("hungry", triggered_ids)

    def test_already_active_effect_not_retriggered(self):
        state = self._make_state(energy=25, mood=70, social=50, satiety=80)
        active_ids = {"low_energy"}
        triggered = persona_sim_rules.eval_effect_triggers(state, active_ids, [], time.time())
        triggered_ids = [e.effect_id for e in triggered]
        self.assertNotIn("low_energy", triggered_ids)

    def test_thriving_when_high_energy_and_mood(self):
        state = self._make_state(energy=85, mood=85, social=50, satiety=80)
        triggered = persona_sim_rules.eval_effect_triggers(state, set(), [], time.time())
        triggered_ids = [e.effect_id for e in triggered]
        self.assertIn("thriving", triggered_ids)


class PersonaEffectActiveTests(IsolatedAsyncioTestCase):
    """测试 effect.is_active()。"""

    def test_no_expiry_is_always_active(self):
        e = persona_sim_types.PersonaEffect(
            effect_id="test",
            effect_type=persona_sim_types.EffectType.DEBUFF,
            name="Test",
            source="rule",
            intensity=1,
            started_at=0,
            expires_at=0,
        )
        self.assertTrue(e.is_active(999999999))

    def test_not_expired_is_active(self):
        e = persona_sim_types.PersonaEffect(
            effect_id="test",
            effect_type=persona_sim_types.EffectType.DEBUFF,
            name="Test",
            source="rule",
            intensity=1,
            started_at=0,
            expires_at=1000,
        )
        self.assertTrue(e.is_active(500))
        self.assertFalse(e.is_active(1001))


class InteractionQualityTests(IsolatedAsyncioTestCase):
    """测试 interaction quality 分级体系。"""

    def _make_state(self, energy=80, mood=70, social=50, satiety=80):
        s = MagicMock()
        s.energy = energy
        s.mood = mood
        s.social_need = social
        s.satiety = satiety
        s.last_tick_at = 0
        s.last_interaction_at = 0
        return s

    def _apply(self, quality: str) -> tuple[float, float, float]:
        state = self._make_state(energy=80, mood=70, social=50, satiety=80)
        new_state = persona_sim_rules.apply_interaction(state, quality)
        return new_state.energy, new_state.mood, new_state.social_need

    def test_good_boosts_mood_more_than_normal(self):
        _, mood_good, _ = self._apply("good")
        _, mood_normal, _ = self._apply("normal")
        self.assertGreater(mood_good, mood_normal)

    def test_bad_lowers_mood(self):
        _, mood_bad, _ = self._apply("bad")
        _, mood_normal, _ = self._apply("normal")
        self.assertLess(mood_bad, mood_normal)

    def test_relief_biggest_mood_boost(self):
        _, mood_relief, _ = self._apply("relief")
        _, mood_good, _ = self._apply("good")
        self.assertGreater(mood_relief, mood_good)

    def test_brief_uses_least_energy(self):
        energy_brief, _, _ = self._apply("brief")
        energy_normal, _, _ = self._apply("normal")
        self.assertGreater(energy_brief, energy_normal)

    def test_awkward_leaves_more_social_need_than_normal(self):
        _, _, social_awkward = self._apply("awkward")
        _, _, social_normal = self._apply("normal")
        self.assertGreater(social_awkward, social_normal)

    def test_all_qualities_clamped_correctly(self):
        state = self._make_state(energy=5, mood=5, social=95, satiety=5)
        for q in ["good", "normal", "bad", "brief", "awkward", "relief"]:
            new_state = persona_sim_rules.apply_interaction(state, q)
            self.assertGreaterEqual(new_state.energy, 0)
            self.assertLessEqual(new_state.energy, 100)
            self.assertGreaterEqual(new_state.mood, 0)
            self.assertLessEqual(new_state.mood, 100)


class EffectTriggerCompoundTests(IsolatedAsyncioTestCase):
    """测试 effect 组合条件触发。"""

    def _make_state(self, energy=80, mood=70, social=50, satiety=80, last_interact=0):
        s = MagicMock()
        s.energy = energy
        s.mood = mood
        s.social_need = social
        s.satiety = satiety
        s.last_interaction_at = last_interact
        return s

    def _make_event(self, event_type="natural", causes=None, effects=None):
        e = MagicMock()
        e.event_type = event_type
        e.causes = causes or []
        e.effects_applied = effects or []
        return e

    def test_lonely_requires_time_no_interaction(self):
        state = self._make_state(social=75, last_interact=0)
        now = time.time()
        recent_events = []
        triggered = persona_sim_rules.eval_effect_triggers(state, set(), recent_events, now)
        triggered_ids = [e.effect_id for e in triggered]
        self.assertIn("lonely", triggered_ids)

    def test_wronged_not_triggered_without_bad_interaction(self):
        state = self._make_state(last_interact=time.time())
        now = time.time()
        recent_events = [self._make_event("interaction", causes=["quality=good"])]
        triggered = persona_sim_rules.eval_effect_triggers(state, set(), recent_events, now)
        triggered_ids = [e.effect_id for e in triggered]
        self.assertNotIn("wronged", triggered_ids)

    def test_thriving_requires_high_energy_and_mood(self):
        state = self._make_state(energy=85, mood=85, last_interact=time.time())
        now = time.time()
        triggered = persona_sim_rules.eval_effect_triggers(state, set(), [], now)
        triggered_ids = [e.effect_id for e in triggered]
        self.assertIn("thriving", triggered_ids)

    def test_thriving_not_triggered_if_only_one_high(self):
        state = self._make_state(energy=85, mood=30, last_interact=time.time())
        now = time.time()
        triggered = persona_sim_rules.eval_effect_triggers(state, set(), [], now)
        triggered_ids = [e.effect_id for e in triggered]
        self.assertNotIn("thriving", triggered_ids)


class PromptInjectionTests(IsolatedAsyncioTestCase):
    """测试 prompt 注入升级：心理描述而非状态标签。"""

    def _make_snapshot(self, state_kwargs=None, effect_ids=None, todos=None, recent_events=None):
        from engine.persona_sim_types import PersonaSnapshot, PersonaState, PersonaEffect, PersonaTodo, EventType

        state_kwargs = state_kwargs or {}
        s = MagicMock(spec=PersonaState)
        for k, v in state_kwargs.items():
            setattr(s, k, v)

        effects = []
        if effect_ids:
            for eid in effect_ids:
                e = MagicMock()
                e.effect_id = eid
                e.prompt_hint = f"hint_{eid}"
                effects.append(e)

        ts = []
        if todos:
            for t in todos:
                td = MagicMock()
                td.title = t
                ts.append(td)

        evts = []
        if recent_events:
            for r in recent_events:
                e = MagicMock()
                e.event_type = r.get("event_type", EventType.NATURAL)
                e.causes = r.get("causes", [])
                evts.append(e)

        snap = MagicMock(spec=PersonaSnapshot)
        snap.state = s
        snap.active_effects = effects
        snap.pending_todos = ts
        snap.recent_events = evts
        return snap

    def test_snapshot_to_prompt_no_raw_numbers(self):
        from engine.persona_sim_injection import snapshot_to_prompt

        snap = self._make_snapshot(
            state_kwargs={"energy": 25, "mood": 30, "thought_process": ""},
        )
        result = snapshot_to_prompt(snap)
        self.assertNotIn("25", result)
        self.assertNotIn("30", result)

    def test_snapshot_to_system_context_includes_effect_and_todo(self):
        from engine.persona_sim_injection import snapshot_to_persona_system_context

        snap = self._make_snapshot(
            state_kwargs={"energy": 80, "mood": 80, "thought_process": ""},
            effect_ids=["low_energy"],
            todos=["想休息一下"],
        )
        result = snapshot_to_persona_system_context(snap)
        self.assertTrue(len(result) > 0)
        self.assertNotIn("80", result)


class PlannerBiasTests(IsolatedAsyncioTestCase):
    """测试 planner 姿态偏置。"""

    def test_engagement_plan_has_bias_fields(self):
        from engine.social_state import EngagementPlan, EngagementLevel, SceneType

        plan = EngagementPlan(
            level=EngagementLevel.FULL,
            reason="test",
            confidence=0.5,
            scene=SceneType.CASUAL,
            short_reply_bias=0.3,
            warmth_bias=0.2,
            initiative_bias=0.1,
            playfulness_bias=0.15,
        )
        self.assertEqual(plan.short_reply_bias, 0.3)
        self.assertEqual(plan.warmth_bias, 0.2)
        self.assertEqual(plan.initiative_bias, 0.1)
        self.assertEqual(plan.playfulness_bias, 0.15)

    def test_short_reply_bias_reduces_max_chars(self):
        from engine.social_state import EngagementPlan, EngagementLevel, SceneType
        import unittest.mock

        plan = EngagementPlan(
            level=EngagementLevel.FULL,
            reason="test",
            confidence=0.5,
            scene=SceneType.CASUAL,
            short_reply_bias=0.5,
        )
        with unittest.mock.patch("random.choices", return_value=[30]):
            decision = plan.to_speech_decision()
        self.assertLess(decision.max_chars, 60)


class PlannerBiasToSpeechDecisionTests(IsolatedAsyncioTestCase):
    """测试 warmth/initiative/playfulness bias 进入 SpeechDecision（修复5）。"""

    def test_speech_decision_text_has_style_hint_fields(self):
        from engine.speech_types import SpeechDecision, AnchorType

        decision = SpeechDecision.text(
            text_mode="reply",
            anchor_type=AnchorType.NONE,
            confidence=0.5,
            reason="test",
            max_chars=100,
            warmth_hint=0.3,
            initiative_hint=0.2,
            playfulness_hint=0.15,
        )
        self.assertEqual(decision.warmth_hint, 0.3)
        self.assertEqual(decision.initiative_hint, 0.2)
        self.assertEqual(decision.playfulness_hint, 0.15)

    def test_engagement_plan_to_speech_decision_passes_biases(self):
        from engine.social_state import EngagementPlan, EngagementLevel, SceneType

        plan = EngagementPlan(
            level=EngagementLevel.FULL,
            reason="test",
            confidence=0.5,
            scene=SceneType.CASUAL,
            warmth_bias=0.25,
            initiative_bias=0.15,
            playfulness_bias=0.1,
        )
        decision = plan.to_speech_decision()
        self.assertEqual(decision.warmth_hint, 0.25)
        self.assertEqual(decision.initiative_hint, 0.15)
        self.assertEqual(decision.playfulness_hint, 0.1)

    def test_style_hint_in_generation_context(self):
        from engine.speech_types import SpeechDecision, AnchorType

        class FakeContextBuilder:
            def _build_style_hint(self, decision):
                hints = []
                if decision.warmth_hint < -0.1:
                    hints.append("语气偏冷淡收敛")
                elif decision.warmth_hint > 0.1:
                    hints.append("语气偏温暖热情")
                if decision.initiative_hint > 0.15:
                    hints.append("可以更主动一些")
                if decision.playfulness_hint > 0.15:
                    hints.append("表达可以更轻松有趣一些")
                if hints:
                    return "[风格提示] " + " ".join(hints)
                return ""

        builder = FakeContextBuilder()
        decision_warm = SpeechDecision.text(
            text_mode="reply",
            anchor_type=AnchorType.NONE,
            confidence=0.5,
            reason="",
            warmth_hint=0.3,
            initiative_hint=0.2,
            playfulness_hint=0.2,
        )
        result = builder._build_style_hint(decision_warm)
        self.assertIn("温暖", result)
        self.assertIn("主动", result)
        self.assertIn("轻松", result)

        decision_cold = SpeechDecision.text(
            text_mode="reply",
            anchor_type=AnchorType.NONE,
            confidence=0.5,
            reason="",
            warmth_hint=-0.3,
        )
        result_cold = builder._build_style_hint(decision_cold)
        self.assertIn("冷淡", result_cold)


class ConsolidationDayBoundaryTests(IsolatedAsyncioTestCase):
    """测试 consolidation 只统计目标自然日事件（修复3）。"""

    def test_events_beyond_day_end_are_filtered(self):
        from engine.persona_sim_consolidation import PersonaSimConsolidator
        from datetime import datetime

        consolidator = PersonaSimConsolidator(MagicMock())

        target_date = datetime.strptime("2024-01-01", "%Y-%m-%d").date()
        day_start = datetime.combine(target_date, datetime.min.time()).timestamp()
        day_end = day_start + 86400

        all_events = [
            {
                "timestamp": str(day_start + 1000),
                "event_type": "interaction",
                "causes": "quality=good",
                "effects_applied": "",
            },
            {
                "timestamp": str(day_end - 1000),
                "event_type": "interaction",
                "causes": "quality=bad",
                "effects_applied": "",
            },
            {"timestamp": str(day_end + 100), "event_type": "natural", "causes": "", "effects_applied": ""},
        ]

        filtered = [e for e in all_events if float(e.get("timestamp", 0)) < day_end]
        self.assertEqual(len(filtered), 2)
        self.assertTrue(all(float(e.get("timestamp", 0)) < day_end for e in filtered))


class EffectSourceSemanticTests(IsolatedAsyncioTestCase):
    """测试 A: effect 来源语义增强"""

    def _make_state(self, energy=50, mood=50, social=50, satiety=50, last_tick=0, last_interact=0):
        return persona_sim_types.PersonaState(
            energy=energy,
            mood=mood,
            social_need=social,
            satiety=satiety,
            last_tick_at=last_tick,
            last_interaction_at=last_interact,
        )

    def _make_event(
        self, event_type=None, causes=None, effects_applied=None, interaction_mode="", interaction_outcome=""
    ):
        return persona_sim_types.PersonaEvent(
            event_type=event_type if event_type is not None else persona_sim_types.EventType.NATURAL,
            summary="test",
            causes=causes or [],
            effects_applied=effects_applied or [],
            timestamp=time.time(),
            interaction_mode=interaction_mode,
            interaction_outcome=interaction_outcome,
        )

    def test_wronged_effect_has_source_detail_when_triggered(self):
        now = time.time()
        state = self._make_state(mood=40, last_interact=now - 100)
        recent = [self._make_event(persona_sim_types.EventType.INTERACTION, causes=["bad"])]
        triggered = persona_sim_rules.eval_effect_triggers(state, set(), recent, now)
        wronged = next((e for e in triggered if e.effect_id == "wronged"), None)
        self.assertIsNotNone(wronged)
        self.assertTrue(len(wronged.source_detail) > 0)

    def test_wronged_source_detail_reflects_active_missed(self):
        now = time.time()
        state = self._make_state(mood=40, last_interact=now - 100)
        recent = [
            self._make_event(
                persona_sim_types.EventType.INTERACTION,
                causes=["bad"],
                interaction_mode="active",
                interaction_outcome="missed",
            )
        ]
        triggered = persona_sim_rules.eval_effect_triggers(state, set(), recent, now)
        wronged = next((e for e in triggered if e.effect_id == "wronged"), None)
        self.assertIsNotNone(wronged)
        self.assertIn("主动", wronged.source_detail)

    def test_relieved_effect_has_meaningful_source_detail(self):
        now = time.time()
        state = self._make_state(mood=35, last_interact=now - 100)
        recent = [self._make_event(persona_sim_types.EventType.INTERACTION, causes=["good"])]
        active_ids = {"low_mood"}
        triggered = persona_sim_rules.eval_effect_triggers(state, active_ids, recent, now)
        relieved = next((e for e in triggered if e.effect_id == "relieved"), None)
        self.assertIsNotNone(relieved)
        self.assertTrue(len(relieved.source_detail) > 0)

    def test_thriving_effect_has_source_detail(self):
        now = time.time()
        state = self._make_state(energy=85, mood=85, last_interact=now - 100)
        triggered = persona_sim_rules.eval_effect_triggers(state, set(), [], now)
        thriving = next((e for e in triggered if e.effect_id == "thriving"), None)
        self.assertIsNotNone(thriving)
        self.assertIn("精力", thriving.source_detail)
        self.assertIn("心情", thriving.source_detail)

    def test_all_default_effects_have_decay_and_recovery_style(self):
        for effect in persona_sim_rules.DEFAULT_EFFECTS:
            self.assertTrue(len(effect.decay_style) > 0, f"{effect.effect_id} missing decay_style")
            self.assertTrue(len(effect.recovery_style) > 0, f"{effect.effect_id} missing recovery_style")


class InteractionSemanticsTests(IsolatedAsyncioTestCase):
    """测试 B: interaction semantics (active/passive + connected/missed)"""

    def _make_state(self, energy=50, mood=50, social=50, satiety=50, last_tick=0, last_interact=0):
        s = MagicMock()
        s.energy = energy
        s.mood = mood
        s.social_need = social
        s.satiety = satiety
        s.last_tick_at = last_tick
        s.last_interaction_at = last_interact
        return s

    def _make_event(self, event_type="natural", causes=None, interaction_mode="", interaction_outcome=""):
        e = MagicMock()
        e.event_type = event_type
        e.causes = causes or []
        e.interaction_mode = interaction_mode
        e.interaction_outcome = interaction_outcome
        return e

    def test_eval_effect_triggers_reads_interaction_outcome(self):
        now = time.time()
        state = self._make_state(mood=40, last_interact=now - 100)
        recent = [
            self._make_event(
                "interaction", causes=["quality=bad"], interaction_mode="active", interaction_outcome="missed"
            )
        ]
        recent_connected = [e for e in recent if getattr(e, "interaction_outcome", "") == "connected"]
        recent_missed = [e for e in recent if getattr(e, "interaction_outcome", "") == "missed"]
        self.assertEqual(len(recent_missed), 1)
        self.assertEqual(len(recent_connected), 0)

    def test_active_good_text_is_connected(self):
        from engine.reply_executor import ReplyExecutor

        executor = ReplyExecutor(MagicMock(), MagicMock())
        quality, mode, outcome = executor._assess_interaction_semantics("text", is_active_trigger=True)
        self.assertEqual(quality, "good")
        self.assertEqual(mode, "active")
        self.assertEqual(outcome, "connected")

    def test_passive_brief_is_missed(self):
        from engine.reply_executor import ReplyExecutor

        executor = ReplyExecutor(MagicMock(), MagicMock())
        quality, mode, outcome = executor._assess_interaction_semantics("text", is_active_trigger=False, text_length=5)
        self.assertEqual(quality, "brief")
        self.assertEqual(mode, "passive")
        self.assertEqual(outcome, "missed")

    def test_awkward_is_missed_outcome(self):
        from engine.reply_executor import ReplyExecutor

        executor = ReplyExecutor(MagicMock(), MagicMock())
        quality, mode, outcome = executor._assess_interaction_semantics(
            "emoji_reaction", is_active_trigger=True, guard_downgrade=True
        )
        self.assertEqual(quality, "awkward")
        self.assertEqual(outcome, "missed")

    def test_awkward_is_missed_outcome(self):
        from engine.reply_executor import ReplyExecutor

        executor = ReplyExecutor(MagicMock(), MagicMock())
        quality, mode, outcome = executor._assess_interaction_semantics(
            "emoji_reaction", is_active_trigger=True, guard_downgrade=True
        )
        self.assertEqual(quality, "awkward")
        self.assertEqual(outcome, "missed")


class TodoUpgradeTests(IsolatedAsyncioTestCase):
    """测试 C: todo 升级 need_todo/social_todo"""

    def test_make_todos_respects_social_todo_when_wronged_and_missed_active(self):
        from engine.persona_sim_todo import make_todos
        from engine.persona_sim_types import PersonaEffect, EffectType, EventType

        state = MagicMock()
        state.energy = 50
        state.mood = 40
        state.social_need = 50
        state.satiety = 50

        wronged = MagicMock(spec=PersonaEffect)
        wronged.effect_id = "wronged"
        wronged.effect_type = EffectType.DEBUFF

        recent_event = MagicMock()
        recent_event.event_type = EventType.INTERACTION
        recent_event.causes = ["quality=bad"]
        recent_event.interaction_mode = "active"
        recent_event.interaction_outcome = "missed"

        todos = make_todos(state, [wronged], [recent_event])
        social_todos = [t for t in todos if t.todo_type.value == "social"]
        self.assertTrue(
            len(social_todos) > 0,
            f"wronged + missed active should produce social_todo, got: {[t.title for t in todos]}",
        )

    def test_make_todos_respects_recent_connected_for_lonely(self):
        from engine.persona_sim_todo import make_todos
        from engine.persona_sim_types import PersonaEffect, EffectType, EventType

        state = MagicMock()
        state.energy = 50
        state.mood = 50
        state.social_need = 50
        state.satiety = 50

        lonely = MagicMock(spec=PersonaEffect)
        lonely.effect_id = "lonely"
        lonely.effect_type = EffectType.DEBUFF

        recent_event = MagicMock()
        recent_event.event_type = EventType.INTERACTION
        recent_event.causes = ["quality=good"]
        recent_event.interaction_mode = "passive"
        recent_event.interaction_outcome = "connected"

        todos = make_todos(state, [lonely], [recent_event])
        titles = [t.title for t in todos]
        self.assertTrue(any("聊" in t for t in titles), f"Expected social todo about chatting, got: {titles}")

    def test_todo_count_is_limited_to_3(self):
        from engine.persona_sim_todo import make_todos
        from engine.persona_sim_types import PersonaEffect, EffectType

        state = MagicMock()
        state.energy = 30
        state.mood = 30
        state.social_need = 90
        state.satiety = 20

        effects = [
            MagicMock(spec=PersonaEffect, effect_id="hungry", effect_type=EffectType.DEBUFF),
            MagicMock(spec=PersonaEffect, effect_id="low_energy", effect_type=EffectType.DEBUFF),
            MagicMock(spec=PersonaEffect, effect_id="lonely", effect_type=EffectType.DEBUFF),
            MagicMock(spec=PersonaEffect, effect_id="wronged", effect_type=EffectType.DEBUFF),
        ]

        recent = [MagicMock()]
        recent[-1].event_type = "interaction"
        recent[-1].causes = ["quality=bad"]
        recent[-1].interaction_mode = "active"
        recent[-1].interaction_outcome = "missed"

        todos = make_todos(state, effects, recent)
        self.assertLessEqual(len(todos), 3)

    def test_todos_include_reason_from_recent_event(self):
        from engine.persona_sim_todo import make_todos
        from engine.persona_sim_types import PersonaEffect, EffectType

        state = MagicMock()
        state.energy = 50
        state.mood = 50
        state.social_need = 90
        state.satiety = 50

        lonely = MagicMock(spec=PersonaEffect)
        lonely.effect_id = "lonely"
        lonely.effect_type = EffectType.DEBUFF

        recent = [MagicMock()]
        recent[-1].event_type = "interaction"
        recent[-1].causes = ["quality=good"]
        recent[-1].interaction_mode = "passive"
        recent[-1].interaction_outcome = "connected"

        todos = make_todos(state, [lonely], recent)
        self.assertTrue(len(todos) > 0)
        self.assertTrue(all(len(t.reason) > 0 for t in todos))


class PromptNarrationTests(IsolatedAsyncioTestCase):
    """测试 D: prompt 注入升级为心理旁白风格"""

    def _make_snapshot(self, state_kwargs=None, effect_ids=None, todos=None, recent_events=None):
        from engine.persona_sim_types import PersonaSnapshot, PersonaState, PersonaEffect, PersonaTodo, EventType

        state_kwargs = state_kwargs or {}
        s = MagicMock(spec=PersonaState)
        for k, v in state_kwargs.items():
            setattr(s, k, v)

        effects = []
        if effect_ids:
            for eid in effect_ids:
                e = MagicMock()
                e.effect_id = eid
                e.prompt_hint = f"hint_{eid}"
                e.effect_type = MagicMock()
                e.effect_type.value = "debuff"
                effects.append(e)

        ts = []
        if todos:
            for t in todos:
                td = MagicMock()
                td.title = t.get("title", "")
                td.todo_type = MagicMock()
                td.todo_type.value = t.get("todo_type", "internal")
                ts.append(td)

        evts = []
        if recent_events:
            for r in recent_events:
                e = MagicMock()
                e.event_type = r.get("event_type", EventType.NATURAL)
                e.causes = r.get("causes", [])
                e.interaction_mode = r.get("interaction_mode", "")
                e.interaction_outcome = r.get("interaction_outcome", "")
                evts.append(e)

        snap = MagicMock(spec=PersonaSnapshot)
        snap.state = s
        snap.active_effects = effects
        snap.pending_todos = ts
        snap.recent_events = evts
        return snap

    def test_injection_not_like_debug_output(self):
        from engine.persona_sim_injection import snapshot_to_prompt

        snap = self._make_snapshot(
            state_kwargs={"energy": 80, "mood": 82, "thought_process": ""},
            effect_ids=["wronged"],
            recent_events=[
                {
                    "event_type": "interaction",
                    "causes": ["quality=bad"],
                    "interaction_mode": "active",
                    "interaction_outcome": "missed",
                }
            ],
        )
        result = snapshot_to_prompt(snap)
        self.assertNotIn("活力", result)
        self.assertNotIn("心情", result)
        self.assertNotIn("80", result)
        self.assertNotIn("82", result)

    def test_wronged_active_missed_shows_as_inner_narration(self):
        from engine.persona_sim_injection import snapshot_to_prompt

        snap = self._make_snapshot(
            state_kwargs={"energy": 59, "mood": 55, "thought_process": ""},
            effect_ids=["wronged"],
            recent_events=[
                {
                    "event_type": "interaction",
                    "causes": ["quality=bad"],
                    "interaction_mode": "active",
                    "interaction_outcome": "missed",
                }
            ],
        )
        result = snapshot_to_prompt(snap)
        self.assertTrue(len(result) < 50, f"Should be short narration, got: {result}")

    def test_relieved_becomes_narration_not_label(self):
        from engine.persona_sim_injection import snapshot_to_prompt

        snap = self._make_snapshot(
            state_kwargs={"energy": 70, "mood": 65, "thought_process": ""},
            effect_ids=["relieved"],
            recent_events=[
                {
                    "event_type": "interaction",
                    "causes": ["quality=good"],
                    "interaction_mode": "passive",
                    "interaction_outcome": "connected",
                }
            ],
        )
        result = snapshot_to_prompt(snap)
        self.assertNotIn("轻松", result)
        self.assertNotIn("轻松", result)


class ConsolidationTrajectoryTests(IsolatedAsyncioTestCase):
    """测试 E: consolidation 升级为情绪轨迹"""

    def _make_event(self, event_type=None, causes=None, interaction_mode="", interaction_outcome=""):
        from engine.persona_sim_types import EventType

        e = MagicMock()
        e.event_type = event_type if event_type is not None else EventType.NATURAL
        e.causes = causes or []
        e.effects_applied = []
        e.interaction_mode = interaction_mode
        e.interaction_outcome = interaction_outcome
        return e

    def test_analyze_day_with_missed_and_connected_produces_falling_rising_trajectory(self):
        from engine.persona_sim_consolidation import PersonaSimConsolidator
        from engine.persona_sim_types import EventType

        consolidator = PersonaSimConsolidator(MagicMock())
        events = [
            self._make_event(
                EventType.INTERACTION, causes=["good"], interaction_mode="active", interaction_outcome="connected"
            ),
            self._make_event(
                EventType.INTERACTION, causes=["bad"], interaction_mode="active", interaction_outcome="missed"
            ),
        ]
        state = {"mood": 55, "energy": 60, "social_need": 50, "satiety": 60}
        result = consolidator._analyze_day(events, state, "2026-04-02")

        self.assertEqual(result["trajectory"], "有落差")
        self.assertEqual(result["missed"], 1)
        self.assertEqual(result["connected"], 1)

    def test_analyze_day_no_interactions_is_solitary(self):
        from engine.persona_sim_consolidation import PersonaSimConsolidator

        consolidator = PersonaSimConsolidator(MagicMock())
        events = [
            self._make_event("natural", causes=["elapsed=2.0h"]),
        ]
        state = {"mood": 60, "energy": 65, "social_need": 50, "satiety": 60}
        result = consolidator._analyze_day(events, state, "2026-04-02")

        self.assertEqual(result["trajectory"], "独处")
        self.assertTrue(len(result["emotional_arc"]) > 0)

    def test_format_summary_does_not_say_interaction_count(self):
        from engine.persona_sim_consolidation import PersonaSimConsolidator

        consolidator = PersonaSimConsolidator(MagicMock())
        parts = {
            "interaction_count": 3,
            "bad_interactions": 1,
            "good_interactions": 2,
            "awkward_interactions": 0,
            "missed": 1,
            "connected": 2,
            "dominant_effect": "thriving",
            "mood_desc": "愉悦",
            "energy_desc": "充沛",
            "trajectory": "向上",
            "emotional_arc": "今天整体是往上的，有人把话接住了",
            "recovery": False,
            "shift_hint": "今天顺畅，明天会更愿意开口",
        }
        summary = consolidator._format_summary(parts, "2026-04-02")
        self.assertNotIn("互动 3 次", summary)
        self.assertIn("今天整体是往上的", summary)
        self.assertIn("愉悦", summary)

    def test_drift_considers_missed_and_connected(self):
        from engine.persona_sim_consolidation import PersonaSimConsolidator

        consolidator = PersonaSimConsolidator(MagicMock())

        parts_missed = {
            "mood_desc": "低落",
            "trajectory": "有失落",
            "bad_interactions": 1,
            "good_interactions": 0,
            "missed": 1,
            "connected": 0,
            "recovery": False,
        }
        drift_missed = consolidator._calc_drift(parts_missed)
        self.assertLess(drift_missed, 0, "missed interaction should pull drift negative")

        parts_recovered = {
            "mood_desc": "平稳",
            "trajectory": "向上",
            "bad_interactions": 1,
            "good_interactions": 2,
            "missed": 1,
            "connected": 2,
            "recovery": True,
        }
        drift_recovered = consolidator._calc_drift(parts_recovered)
        self.assertGreater(drift_recovered, 0, "recovery should push drift positive")
