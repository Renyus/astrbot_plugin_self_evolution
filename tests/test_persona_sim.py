from __future__ import annotations

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

    def _make_state(self, energy=80, mood=70, social=50, satiety=80):
        s = MagicMock()
        s.energy = energy
        s.mood = mood
        s.social_need = social
        s.satiety = satiety
        return s

    def test_low_energy_triggers_effect(self):
        state = self._make_state(energy=25, mood=70, social=50, satiety=80)
        triggered = persona_sim_rules.eval_effect_triggers(state, set(), 1000)
        triggered_ids = [e.effect_id for e in triggered]
        self.assertIn("low_energy", triggered_ids)

    def test_low_mood_triggers_effect(self):
        state = self._make_state(energy=80, mood=25, social=50, satiety=80)
        triggered = persona_sim_rules.eval_effect_triggers(state, set(), 1000)
        triggered_ids = [e.effect_id for e in triggered]
        self.assertIn("low_mood", triggered_ids)

    def test_lonely_triggers_when_social_need_high(self):
        state = self._make_state(energy=80, mood=70, social=75, satiety=80)
        triggered = persona_sim_rules.eval_effect_triggers(state, set(), 1000)
        triggered_ids = [e.effect_id for e in triggered]
        self.assertIn("lonely", triggered_ids)

    def test_hungry_triggers_when_satiety_low(self):
        state = self._make_state(energy=80, mood=70, social=50, satiety=25)
        triggered = persona_sim_rules.eval_effect_triggers(state, set(), 1000)
        triggered_ids = [e.effect_id for e in triggered]
        self.assertIn("hungry", triggered_ids)

    def test_already_active_effect_not_retriggered(self):
        state = self._make_state(energy=25, mood=70, social=50, satiety=80)
        active_ids = {"low_energy"}
        triggered = persona_sim_rules.eval_effect_triggers(state, active_ids, 1000)
        triggered_ids = [e.effect_id for e in triggered]
        self.assertNotIn("low_energy", triggered_ids)

    def test_thriving_when_high_energy_and_mood(self):
        state = self._make_state(energy=85, mood=85, social=50, satiety=80)
        triggered = persona_sim_rules.eval_effect_triggers(state, set(), 1000)
        triggered_ids = [e.effect_id for e in triggered]
        self.assertIn("thriving", triggered_ids)


class GenerateTodosTests(IsolatedAsyncioTestCase):
    """测试 todo 生成。"""

    def _make_state(self, energy=80, mood=70, social=50, satiety=80):
        s = MagicMock()
        s.energy = energy
        s.mood = mood
        s.social_need = social
        s.satiety = satiety
        return s

    def test_hungry_generates_eating_todo(self):
        state = self._make_state(satiety=30)
        active = [MagicMock(effect_id="hungry", prompt_hint="", tags=[])]
        todos = persona_sim_rules.generate_todos(state, active)
        titles = [t.title for t in todos]
        self.assertTrue(any("吃" in t for t in titles))

    def test_lonely_generates_social_todo(self):
        state = self._make_state(social=80)
        active = [MagicMock(effect_id="lonely", prompt_hint="", tags=[])]
        todos = persona_sim_rules.generate_todos(state, active)
        titles = [t.title for t in todos]
        self.assertTrue(any("聊" in t or "人" in t for t in titles))

    def test_todos_sorted_by_priority(self):
        state = self._make_state(energy=20, satiety=20)
        active = [
            MagicMock(effect_id="low_energy", prompt_hint="", tags=[]),
            MagicMock(effect_id="hungry", prompt_hint="", tags=[]),
        ]
        todos = persona_sim_rules.generate_todos(state, active)
        priorities = [t.priority for t in todos]
        self.assertEqual(priorities, sorted(priorities, reverse=True))

    def test_max_5_todos(self):
        state = self._make_state(energy=20, mood=20, social=80, satiety=20)
        active = [
            MagicMock(effect_id="low_energy", prompt_hint="", tags=[]),
            MagicMock(effect_id="low_mood", prompt_hint="", tags=[]),
            MagicMock(effect_id="lonely", prompt_hint="", tags=[]),
            MagicMock(effect_id="hungry", prompt_hint="", tags=[]),
            MagicMock(effect_id="sleepy", prompt_hint="", tags=[]),
            MagicMock(effect_id="irritated", prompt_hint="", tags=[]),
        ]
        todos = persona_sim_rules.generate_todos(state, active)
        self.assertLessEqual(len(todos), 5)


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
