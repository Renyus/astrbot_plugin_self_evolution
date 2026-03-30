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

    def test_plan_full_in_idle_with_mention(self):
        state = self._make_state(message_count_window=1, last_message_time=time.time() - 600)
        eligibility = self.planner.check_eligibility(state)
        plan = self.planner.plan_engagement(state, eligibility, has_mention=True, has_reply_to_bot=False)
        self.assertEqual(plan.level, EngagementLevel.FULL)

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

    def test_plan_full_in_debate_with_mention(self):
        state = self._make_state(emotion_count_window=5, scene=SceneType.DEBATE)
        eligibility = self.planner.check_eligibility(state)
        plan = self.planner.plan_engagement(state, eligibility, has_mention=True, has_reply_to_bot=False)
        self.assertEqual(plan.level, EngagementLevel.FULL)

    def test_plan_full_in_casual_with_mention(self):
        state = self._make_state(scene=SceneType.CASUAL)
        eligibility = self.planner.check_eligibility(state)
        plan = self.planner.plan_engagement(state, eligibility, has_mention=True, has_reply_to_bot=False)
        self.assertEqual(plan.level, EngagementLevel.FULL)

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
            "last_message_time": time.time() - 15,
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
        self.assertGreater(saved["last_message_time"], 0)
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
        event.get_sender_name = lambda: "TestUser"
        event.message_str = message_str
        event.is_at_or_wake_command = False
        event.get_extra = lambda key, default=None: default
        event.get_messages = lambda: [{"type": "text", "data": {"text": message_str}}] if message_str else []
        event.message_obj = SimpleNamespace(message=[])
        return event

    async def test_cold_start_no_historical_state_not_rejected_as_silence(self):
        saved_states = []
        original_save = self.dao.save_engagement_state

        async def capture_save(scope_id, state):
            saved_states.append(state)
            return await original_save(scope_id, state)

        self.dao.save_engagement_state = capture_save
        event = self._make_event("first message ever", group_id="fresh_scope")
        await self.engine.process_passive_engagement(event)

        self.assertTrue(len(saved_states) > 0, "cold start should reach eligibility and save state")
        saved = saved_states[-1]
        self.assertIn("last_message_time", saved)
        self.assertGreater(saved["last_message_time"], 0)

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

    async def test_recent_messages_accumulate_window_count(self):
        now = time.time()
        old_state = {
            "scope_id": "5001",
            "last_message_time": now - 10,
            "last_bot_engagement_at": 0.0,
            "scene_type": "casual",
            "message_count_window": 2,
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
        event = self._make_event("hello again", group_id="5001")
        await self.engine.process_passive_engagement(event)

        self.assertTrue(len(saved_states) > 0, "message window was never saved")
        self.assertEqual(saved_states[-1]["message_count_window"], 3)


class EngagementExecutorTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        executor_module = load_engine_module("engagement_executor")
        EngagementExecutor = executor_module.EngagementExecutor
        planner_module = load_engine_module("engagement_planner")
        EngagementPlanner = planner_module.EngagementPlanner

        self.mock_bot = AsyncMock()
        mock_platform = MagicMock()
        mock_platform.bot = self.mock_bot
        mock_ctx = MagicMock()
        mock_ctx.platform_manager.platform_insts = [mock_platform]

        self.plugin = SimpleNamespace(
            cfg=SimpleNamespace(
                engagement_debug_enabled=False,
                interject_cooldown=30,
                engagement_react_probability=1.0,
                persona_name="黑塔",
            ),
            context=mock_ctx,
            dao=MagicMock(),
        )
        planner = EngagementPlanner(self.plugin)
        self.executor = EngagementExecutor(self.plugin, planner)

    async def test_send_message_segment_type_is_text(self):
        await self.executor._send_message("5001", "hello")
        self.mock_bot.send_group_msg.assert_called_once()
        call_kwargs = self.mock_bot.send_group_msg.call_args
        msg_segments = call_kwargs[1]["message"] if "message" in call_kwargs[1] else call_kwargs[0][1]
        self.assertEqual(msg_segments[0]["type"], "text")
        self.assertEqual(msg_segments[0]["data"]["text"], "hello")


class PassiveEngagementMentionTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = make_workspace_temp_dir("eavesdropping_mention")
        self.dao = SelfEvolutionDAO(str(Path(self.temp_dir) / "mention_test.db"))
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

    def _make_event(
        self,
        message_str="hello",
        group_id="5001",
        is_at_extra=False,
        has_reply_extra=False,
        is_at_or_wake_command=False,
    ):
        event = SimpleNamespace()
        event.get_group_id = lambda: group_id
        event.get_user_id = lambda: "user123"
        event.message_str = message_str
        event.is_at_or_wake_command = is_at_or_wake_command
        event.get_extra = lambda key, default=None: {"is_at": is_at_extra, "has_reply": has_reply_extra}.get(
            key, default
        )
        event.message_obj = SimpleNamespace(message=[])
        return event

    async def test_pure_command_not_counted_as_mention(self):
        saved_states = []
        original_save = self.dao.save_engagement_state

        async def capture_save(scope_id, state):
            saved_states.append(state)
            return await original_save(scope_id, state)

        self.dao.save_engagement_state = capture_save
        event = self._make_event("!test", is_at_extra=False, has_reply_extra=False, is_at_or_wake_command=True)
        await self.engine.process_passive_engagement(event)
        if saved_states:
            has_mention = saved_states[0].get("last_bot_engagement_level")
            self.assertIsNone(has_mention)


class HelpSceneLowRelevanceTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = make_workspace_temp_dir("help_scene")
        self.dao = SelfEvolutionDAO(str(Path(self.temp_dir) / "help_scene_test.db"))
        await self.dao.init_db()
        planner_module = load_engine_module("engagement_planner")
        EngagementPlanner = planner_module.EngagementPlanner
        self.plugin = SimpleNamespace(
            dao=self.dao,
            cfg=SimpleNamespace(
                interject_cooldown=30,
                engagement_react_probability=0.15,
            ),
            _get_bot_id=lambda: "bot123",
        )
        self.planner = EngagementPlanner(self.plugin)

    async def asyncTearDown(self):
        await self.dao.close()
        cleanup_workspace_temp_dir(self.temp_dir)

    def test_help_scene_low_relevance_gives_full(self):
        social_module = load_engine_module("social_state")
        GroupSocialState = social_module.GroupSocialState
        SceneType = social_module.SceneType
        EngagementEligibility = social_module.EngagementEligibility

        state = GroupSocialState(
            scope_id="5001",
            last_message_time=time.time() - 10,
            last_bot_message_time=0,
            last_seen_message_seq=None,
            scene=SceneType.CASUAL,
            message_count_window=3,
            question_count_window=3,
            emotion_count_window=0,
            consecutive_bot_replies=0,
        )
        eligibility = EngagementEligibility(allowed=True, silence_seconds=30, reason_code="test", reason_text="test")
        plan = self.planner.plan_engagement(state, eligibility, has_mention=False, has_reply_to_bot=False)
        self.assertEqual(plan.level.value, "full")


class SyncFrameworkReplyStateTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = make_workspace_temp_dir("sync_reply")
        self.dao = SelfEvolutionDAO(str(Path(self.temp_dir) / "sync_reply_test.db"))
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

    async def test_sync_updates_cooldown_preserves_window(self):
        now = time.time()
        old_state = {
            "scope_id": "5001",
            "last_message_time": now - 10,
            "last_bot_engagement_at": now - 300,
            "last_bot_engagement_level": "brief",
            "last_seen_message_seq": 100,
            "scene_type": "casual",
            "message_count_window": 5,
            "question_count_window": 2,
            "emotion_count_window": 1,
            "consecutive_bot_replies": 1,
            "last_bot_message_at": now - 10,
            "last_bot_message_kind": "normal",
            "wave_started_at": now - 10,
            "bot_has_spoken_in_current_wave": 1,
            "new_user_message_after_bot": 0,
        }
        await self.dao.save_engagement_state("5001", old_state)

        result = await self.engine.sync_framework_reply_state("5001", level="full")

        self.assertTrue(result)
        saved = await self.dao.get_engagement_state("5001")
        self.assertEqual(saved["last_bot_engagement_level"], "full")
        self.assertGreater(float(saved["last_bot_engagement_at"]), now - 5)
        self.assertEqual(saved["consecutive_bot_replies"], 2)
        self.assertEqual(saved["message_count_window"], 5)
        self.assertEqual(saved["question_count_window"], 2)
        self.assertEqual(saved["emotion_count_window"], 1)
        self.assertEqual(saved["bot_has_spoken_in_current_wave"], 1)

    async def test_sync_clears_counters_on_expired_window(self):
        now = time.time()
        old_state = {
            "scope_id": "5001",
            "last_message_time": now - 200,
            "last_bot_engagement_at": now - 300,
            "last_bot_engagement_level": "brief",
            "last_seen_message_seq": 100,
            "scene_type": "casual",
            "message_count_window": 5,
            "question_count_window": 2,
            "emotion_count_window": 1,
            "consecutive_bot_replies": 1,
            "last_bot_message_at": now - 200,
            "last_bot_message_kind": "normal",
            "wave_started_at": now - 200,
            "bot_has_spoken_in_current_wave": 1,
            "new_user_message_after_bot": 0,
        }
        await self.dao.save_engagement_state("5001", old_state)

        result = await self.engine.sync_framework_reply_state("5001", level="full")

        self.assertTrue(result)
        saved = await self.dao.get_engagement_state("5001")
        self.assertEqual(saved["message_count_window"], 0)
        self.assertEqual(saved["question_count_window"], 0)
        self.assertEqual(saved["emotion_count_window"], 0)
        self.assertEqual(saved["consecutive_bot_replies"], 1)
        self.assertEqual(saved["scene_type"], "casual")
        self.assertEqual(saved["bot_has_spoken_in_current_wave"], 1)
        self.assertGreater(float(saved["wave_started_at"]), now - 5)

    async def test_sync_returns_false_for_unknown_scope(self):
        result = await self.engine.sync_framework_reply_state("nonexistent", level="full")
        self.assertFalse(result)


class ReplyPolicyStateTransitionTests(IsolatedAsyncioTestCase):
    """测试新状态机的关键状态转换。

    Bug 1: 主动插话被状态机永久堵死
    - bot 说过后，new_user_message_after_bot=True 时，主动路径应允许
    - new_user_message_after_bot=False 时，主动路径应拒绝（E_NO_NEW_USER_AFTER_BOT）

    Bug 2: 明确唤醒被静默吞掉
    - bot 说过后，用户明确 @bot/reply，被动路径应继续（不被 early return）
    """

    async def asyncSetUp(self):
        self.temp_dir = make_workspace_temp_dir("policy_state")
        self.dao = SelfEvolutionDAO(str(Path(self.temp_dir) / "policy_state_test.db"))
        await self.dao.init_db()
        eavesdropping_module = load_engine_module("eavesdropping")
        EavesdroppingEngine = eavesdropping_module.EavesdroppingEngine
        self.plugin = SimpleNamespace(
            dao=self.dao,
            cfg=SimpleNamespace(
                interject_cooldown=30,
                interject_min_msg_count=3,
                engagement_react_probability=1.0,
            ),
            _shut_until_by_group={},
            _get_bot_id=lambda: "bot123",
        )
        self.engine = EavesdroppingEngine(self.plugin)

    async def asyncTearDown(self):
        await self.dao.close()
        cleanup_workspace_temp_dir(self.temp_dir)

    def _make_event(self, message_str="hello", group_id="5001", is_at=False, has_reply=False):
        event = SimpleNamespace()
        event.get_group_id = lambda: group_id
        event.get_user_id = lambda: "user456"
        event.get_sender_name = lambda: "AnotherUser"
        event.message_str = message_str
        event.is_at_or_wake_command = False
        extras = {}
        if is_at:
            extras["is_at"] = True
        if has_reply:
            extras["has_reply"] = True
        event.get_extra = lambda key, default=None: extras.get(key, default)
        event.get_messages = lambda: [{"type": "text", "data": {"text": message_str}}]
        return event

    async def test_bug1_active_allowed_when_new_user_after_bot(self):
        """主动插话在 new_user_message_after_bot=True 时应被允许。

        场景：bot 在当前 wave 说过话，用户新消息到达（user_message_arrived），
        新消息续活 wave，新用户消息在 bot 发言之后（new_user_message_after_bot=True），
        user_message_arrived 会重置 bot_has_spoken_in_current_wave，允许主动路径通过。
        """
        now = time.time()
        reply_policy_module = load_engine_module("reply_policy")
        ReplyPolicy = reply_policy_module.ReplyPolicy
        reply_state_module = load_engine_module("reply_state")
        ConversationMomentum = reply_state_module.ConversationMomentum

        momentum = ConversationMomentum(scope_id="5001")
        momentum.last_message_time = now - 60
        momentum.last_bot_message_at = now - 60
        momentum.message_count_window = 3
        momentum.consecutive_bot_replies = 1
        momentum.bot_has_spoken_in_current_wave = True

        momentum.user_message_arrived(now)
        momentum.new_user_after_bot()

        momentum.last_message_time = now - 10

        self.assertFalse(
            momentum.bot_has_spoken_in_current_wave,
            "user_message_arrived 应重置 bot_has_spoken_in_current_wave",
        )

        policy = ReplyPolicy(self.plugin)
        decision = policy.check(
            momentum,
            cooldown_seconds=30,
            min_new_messages=1,
            require_new_user_after_bot=True,
            allow_active=True,
        )

        self.assertTrue(
            decision.allow,
            f"new_user_message_after_bot=True 时主动路径应允许（实际: {decision.reason_code} {decision.reason_text}）",
        )

    async def test_bug1_active_rejected_when_no_new_user_after_bot(self):
        """主动插话在 new_user_message_after_bot=False 时应被拒绝。

        场景：bot 在当前 wave 说过话（bot_has_spoken=True），
        用户没有继续发消息（new_user_message_after_bot=False），
        主动调度扫到这个群，应该被 E_NO_NEW_USER_AFTER_BOT 拒绝。
        """
        now = time.time()
        state = {
            "scope_id": "5002",
            "last_message_time": now - 60,
            "last_bot_engagement_at": now - 10,
            "last_bot_engagement_level": "full",
            "last_seen_message_seq": 50,
            "scene_type": "casual",
            "message_count_window": 2,
            "question_count_window": 0,
            "emotion_count_window": 0,
            "consecutive_bot_replies": 1,
            "last_bot_message_at": now - 10,
            "last_bot_message_kind": "normal",
            "wave_started_at": now - 60,
            "bot_has_spoken_in_current_wave": 1,
            "new_user_message_after_bot": 0,
        }
        await self.dao.save_engagement_state("5002", state)

        executed = await self.engine.check_engagement("5002")
        self.assertFalse(
            executed,
            "主动插话应在 new_user_message_after_bot=False 时被拒绝（E_NO_NEW_USER_AFTER_BOT）",
        )

    async def test_bug2_explicit_mention_after_bot_reply_not_dropped(self):
        """bot 说过后，用户明确 @bot/reply 时被动链路应继续。

        场景：bot 在当前 wave 说过话（bot_has_spoken=True），
        用户立即 @bot 追问，被动入口不应被静默吞掉。
        """
        now = time.time()
        state = {
            "scope_id": "5003",
            "last_message_time": now - 5,
            "last_bot_engagement_at": now - 10,
            "last_bot_engagement_level": "full",
            "last_seen_message_seq": 50,
            "scene_type": "casual",
            "message_count_window": 2,
            "question_count_window": 0,
            "emotion_count_window": 0,
            "consecutive_bot_replies": 1,
            "last_bot_message_at": now - 10,
            "last_bot_message_kind": "normal",
            "wave_started_at": now - 10,
            "bot_has_spoken_in_current_wave": 1,
            "new_user_message_after_bot": 1,
        }
        await self.dao.save_engagement_state("5003", state)

        saved_states = []
        original_save = self.dao.save_engagement_state

        async def capture_save(scope_id, st):
            saved_states.append(st)
            return await original_save(scope_id, st)

        self.dao.save_engagement_state = capture_save
        event = self._make_event("@bot 你刚才说的什么意思？", group_id="5003", is_at=True)
        await self.engine.process_passive_engagement(event)

        self.assertTrue(
            len(saved_states) > 0,
            "bot 已回复后用户 @bot 追问，被动链路不应被静默吞掉（应有状态回写）",
        )
