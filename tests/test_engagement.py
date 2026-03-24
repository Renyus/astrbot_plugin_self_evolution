from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch
import time

from tests._helpers import (
    install_aiosqlite_stub,
    make_workspace_temp_dir,
    cleanup_workspace_temp_dir,
    load_engine_module,
)
from pathlib import Path

install_aiosqlite_stub()

from dao import SelfEvolutionDAO

social_state_module = load_engine_module("social_state")
EngagementLevel = social_state_module.EngagementLevel
SceneType = social_state_module.SceneType
GroupSocialState = social_state_module.GroupSocialState
EngagementEligibility = social_state_module.EngagementEligibility
EngagementPlan = social_state_module.EngagementPlan

planner_module = load_engine_module("engagement_planner")
EngagementPlanner = planner_module.EngagementPlanner


class FakePlugin:
    def __init__(self):
        self.dao = None
        self.cfg = SimpleNamespace(
            interject_cooldown=30,
            interject_min_msg_count=3,
            engagement_react_probability=0.15,
        )


class EngagementPlannerTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = make_workspace_temp_dir("engagement_planner")
        self.dao = SelfEvolutionDAO(str(Path(self.temp_dir) / "engagement_test.db"))
        await self.dao.init_db()

        self.plugin = FakePlugin()
        self.plugin.dao = self.dao
        self.planner = EngagementPlanner(self.plugin)

    async def asyncTearDown(self):
        await self.dao.close()
        cleanup_workspace_temp_dir(self.temp_dir)

    def _make_state(self, **kwargs):
        defaults = {
            "scope_id": "5001",
            "last_message_time": time.time() - 60,
            "last_bot_message_time": 0.0,
            "message_count_window": 5,
            "question_count_window": 0,
            "emotion_count_window": 0,
            "consecutive_bot_replies": 0,
            "scene": SceneType.CASUAL,
        }
        defaults.update(kwargs)
        return GroupSocialState(**defaults)

    def test_classify_scene_idle(self):
        state = self._make_state(message_count_window=1, last_message_time=time.time() - 600)
        scene = self.planner.classify_scene_from_state(state)
        self.assertEqual(scene, SceneType.IDLE)

    def test_classify_scene_help(self):
        state = self._make_state(question_count_window=4)
        scene = self.planner.classify_scene_from_state(state)
        self.assertEqual(scene, SceneType.HELP)

    def test_classify_scene_debate(self):
        state = self._make_state(emotion_count_window=5)
        scene = self.planner.classify_scene_from_state(state)
        self.assertEqual(scene, SceneType.DEBATE)

    def test_classify_scene_casual(self):
        state = self._make_state(message_count_window=10, question_count_window=1, emotion_count_window=1)
        scene = self.planner.classify_scene_from_state(state)
        self.assertEqual(scene, SceneType.CASUAL)

    def test_eligibility_cooldown_blocks(self):
        state = self._make_state(last_bot_message_time=time.time() - 10)
        result = self.planner.check_eligibility(state, cooldown_seconds=30)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "E_COOLDOWN")

    def test_eligibility_msg_count_blocks(self):
        state = self._make_state(message_count_window=1)
        result = self.planner.check_eligibility(state, min_new_messages=3)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "E_MSG_COUNT")

    def test_eligibility_bot_flood_blocks(self):
        state = self._make_state(consecutive_bot_replies=3)
        result = self.planner.check_eligibility(state)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "E_BOT_FLOOD")

    def test_eligibility_ok(self):
        state = self._make_state(
            last_bot_message_time=0.0,
            message_count_window=5,
            consecutive_bot_replies=0,
        )
        result = self.planner.check_eligibility(state, min_new_messages=3)
        self.assertTrue(result.allowed)
        self.assertEqual(result.reason_code, "OK")

    def test_plan_ignore_in_idle_no_mention(self):
        state = self._make_state(message_count_window=1, last_message_time=time.time() - 600)
        eligibility = self.planner.check_eligibility(state)
        plan = self.planner.plan_engagement(state, eligibility, has_mention=False, has_reply_to_bot=False)
        self.assertEqual(plan.level, EngagementLevel.IGNORE)

    def test_plan_brief_in_idle_with_mention(self):
        state = self._make_state(message_count_window=1, last_message_time=time.time() - 600)
        eligibility = self.planner.check_eligibility(state)
        plan = self.planner.plan_engagement(state, eligibility, has_mention=True, has_reply_to_bot=False)
        self.assertEqual(plan.level, EngagementLevel.BRIEF)

    def test_plan_full_in_help_with_mention(self):
        state = self._make_state(question_count_window=3, scene=SceneType.HELP)
        eligibility = self.planner.check_eligibility(state)
        plan = self.planner.plan_engagement(state, eligibility, has_mention=True, has_reply_to_bot=False)
        self.assertEqual(plan.level, EngagementLevel.FULL)

    def test_plan_ignore_in_debate_no_mention(self):
        state = self._make_state(emotion_count_window=5, scene=SceneType.DEBATE)
        eligibility = self.planner.check_eligibility(state)
        plan = self.planner.plan_engagement(state, eligibility, has_mention=False, has_reply_to_bot=False)
        self.assertEqual(plan.level, EngagementLevel.IGNORE)

    def test_plan_react_in_debate_with_mention(self):
        state = self._make_state(emotion_count_window=5, scene=SceneType.DEBATE)
        eligibility = self.planner.check_eligibility(state)
        plan = self.planner.plan_engagement(state, eligibility, has_mention=True, has_reply_to_bot=False)
        self.assertEqual(plan.level, EngagementLevel.REACT)

    def test_plan_brief_in_casual_with_mention(self):
        state = self._make_state(scene=SceneType.CASUAL)
        eligibility = self.planner.check_eligibility(state)
        plan = self.planner.plan_engagement(state, eligibility, has_mention=True, has_reply_to_bot=False)
        self.assertEqual(plan.level, EngagementLevel.BRIEF)

    def test_plan_react_probability_triggers(self):
        self.plugin.cfg.engagement_react_probability = 1.0
        planner = EngagementPlanner(self.plugin)
        state = self._make_state(scene=SceneType.CASUAL)
        eligibility = planner.check_eligibility(state)
        plan = planner.plan_engagement(state, eligibility, has_mention=False, has_reply_to_bot=False)
        self.assertEqual(plan.level, EngagementLevel.REACT)


class EngagementDAOPersistenceTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = make_workspace_temp_dir("engagement_dao")
        self.dao = SelfEvolutionDAO(str(Path(self.temp_dir) / "engagement_dao_test.db"))
        await self.dao.init_db()

    async def asyncTearDown(self):
        await self.dao.close()
        cleanup_workspace_temp_dir(self.temp_dir)

    async def test_save_and_get_engagement_state(self):
        state = {
            "scope_id": "5001",
            "last_bot_engagement_at": time.time(),
            "last_bot_engagement_level": "brief",
            "last_seen_message_seq": 12345,
            "scene_type": "casual",
            "message_count_window": 10,
            "question_count_window": 2,
            "emotion_count_window": 1,
            "consecutive_bot_replies": 0,
        }
        await self.dao.save_engagement_state("5001", state)

        saved = await self.dao.get_engagement_state("5001")
        self.assertIsNotNone(saved)
        self.assertEqual(saved["scope_id"], "5001")
        self.assertEqual(saved["scene_type"], "casual")
        self.assertEqual(saved["message_count_window"], 10)

    async def test_get_nonexistent_returns_none(self):
        saved = await self.dao.get_engagement_state("nonexistent")
        self.assertIsNone(saved)


class PassiveEngagementTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = make_workspace_temp_dir("passive_engagement")
        self.dao = SelfEvolutionDAO(str(Path(self.temp_dir) / "passive_engagement_test.db"))
        await self.dao.init_db()
        eavesdropping_module = load_engine_module("eavesdropping")
        EavesdroppingEngine = eavesdropping_module.EavesdroppingEngine
        self.plugin = SimpleNamespace(
            dao=self.dao,
            cfg=SimpleNamespace(
                interject_cooldown=30,
                engagement_react_probability=1.0,
            ),
            _get_bot_id=lambda: "bot123",
        )
        self.engine = EavesdroppingEngine(self.plugin)

    async def asyncTearDown(self):
        await self.dao.close()
        cleanup_workspace_temp_dir(self.temp_dir)

    def _make_event(self, message_str="hello", group_id="5001"):
        event = SimpleNamespace()
        event.get_group_id = lambda: group_id
        event.get_user_id = lambda: "user123"
        event.message_str = message_str
        event.is_at_or_wake_command = False
        event.get_extra = lambda key, default=None: default
        event.message_obj = SimpleNamespace(message=[])
        return event

    async def test_last_message_time_written_to_dao(self):
        now = time.time()
        old_state = {
            "scope_id": "5001",
            "last_message_time": now - 30,
            "last_bot_engagement_at": 0.0,
            "scene_type": "casual",
            "message_count_window": 5,
            "question_count_window": 0,
            "emotion_count_window": 0,
            "consecutive_bot_replies": 0,
        }
        await self.dao.save_engagement_state("5001", old_state)

        saved_states = []
        original_save = self.dao.save_engagement_state

        async def capture_save(scope_id, state):
            saved_states.append(state)
            return await original_save(scope_id, state)

        self.dao.save_engagement_state = capture_save
        event = self._make_event("hello world")
        await self.engine.process_passive_engagement(event)

        self.assertTrue(len(saved_states) > 0, f"save_engagement_state was never called (eligibility likely failed)")
        latest = saved_states[-1]
        self.assertIn("last_message_time", latest)
        self.assertGreaterEqual(latest["last_message_time"], now - 1)
        self.assertLessEqual(latest["last_message_time"], now + 1)

    async def test_eligibility_not_zero_despite_new_message(self):
        now = time.time()
        old_state = {
            "scope_id": "5001",
            "last_message_time": now - 15,
            "last_bot_engagement_at": 0.0,
            "scene_type": "casual",
            "message_count_window": 5,
            "question_count_window": 0,
            "emotion_count_window": 0,
            "consecutive_bot_replies": 0,
        }
        await self.dao.save_engagement_state("5001", old_state)

        captured_state = []
        original_save = self.dao.save_engagement_state

        async def capture_save(scope_id, state):
            captured_state.append(state)
            return await original_save(scope_id, state)

        self.dao.save_engagement_state = capture_save
        event = self._make_event("hello world")
        await self.engine.process_passive_engagement(event)

        self.assertTrue(len(captured_state) > 0, "save_engagement_state was never called")
        saved = captured_state[-1]
        self.assertGreater(saved["last_message_time"], now - 2)

    async def test_image_message_uses_placeholder(self):
        now = time.time()
        old_state = {
            "scope_id": "5001",
            "last_message_time": now - 60,
            "last_bot_engagement_at": 0.0,
            "scene_type": "casual",
            "message_count_window": 3,
            "question_count_window": 0,
            "emotion_count_window": 0,
            "consecutive_bot_replies": 0,
        }
        await self.dao.save_engagement_state("5001", old_state)

        saved_states = []
        original_save = self.dao.save_engagement_state

        async def capture_save(scope_id, state):
            saved_states.append(state)
            return await original_save(scope_id, state)

        self.dao.save_engagement_state = capture_save
        event = self._make_event(message_str="", group_id="5001")
        await self.engine.process_passive_engagement(event)

        self.assertTrue(len(saved_states) > 0, "image message caused crash or no save")
        self.assertIn("scene_type", saved_states[-1])
