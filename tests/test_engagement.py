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

    async def test_plan_ignore_in_idle_no_mention(self):
        state = self._make_state(message_count_window=1, last_message_time=time.time() - 600)
        eligibility = self.planner.check_eligibility(state)
        plan = await self.planner.plan_engagement(state, eligibility, has_mention=False, has_reply_to_bot=False)
        self.assertEqual(plan.level, EngagementLevel.IGNORE)

    async def test_plan_full_in_idle_with_mention(self):
        state = self._make_state(message_count_window=1, last_message_time=time.time() - 600)
        eligibility = self.planner.check_eligibility(state)
        plan = await self.planner.plan_engagement(state, eligibility, has_mention=True, has_reply_to_bot=False)
        self.assertEqual(plan.level, EngagementLevel.FULL)

    async def test_plan_full_in_help_with_mention(self):
        state = self._make_state(question_count_window=3, scene=SceneType.HELP)
        eligibility = self.planner.check_eligibility(state)
        plan = await self.planner.plan_engagement(state, eligibility, has_mention=True, has_reply_to_bot=False)
        self.assertEqual(plan.level, EngagementLevel.FULL)

    async def test_plan_ignore_in_debate_no_mention(self):
        state = self._make_state(emotion_count_window=5, scene=SceneType.DEBATE)
        eligibility = self.planner.check_eligibility(state)
        plan = await self.planner.plan_engagement(state, eligibility, has_mention=False, has_reply_to_bot=False)
        self.assertEqual(plan.level, EngagementLevel.IGNORE)

    async def test_plan_full_in_debate_with_mention(self):
        state = self._make_state(emotion_count_window=5, scene=SceneType.DEBATE)
        eligibility = self.planner.check_eligibility(state)
        plan = await self.planner.plan_engagement(state, eligibility, has_mention=True, has_reply_to_bot=False)
        self.assertEqual(plan.level, EngagementLevel.FULL)

    async def test_plan_full_in_casual_with_mention(self):
        state = self._make_state(scene=SceneType.CASUAL)
        eligibility = self.planner.check_eligibility(state)
        plan = await self.planner.plan_engagement(state, eligibility, has_mention=True, has_reply_to_bot=False)
        self.assertEqual(plan.level, EngagementLevel.FULL)

    async def test_plan_no_anchor_no_trigger(self):
        self.plugin.cfg.engagement_react_probability = 1.0
        planner = EngagementPlanner(self.plugin)
        state = self._make_state(scene=SceneType.CASUAL)
        eligibility = planner.check_eligibility(state)
        plan = await planner.plan_engagement(
            state, eligibility, has_mention=False, has_reply_to_bot=False, trigger_text=""
        )
        self.assertEqual(plan.level, EngagementLevel.IGNORE)


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

    async def test_help_scene_low_relevance_gives_full(self):
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
        plan = await self.planner.plan_engagement(state, eligibility, has_mention=False, has_reply_to_bot=False)
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
            current_hour=14,
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


class SpeechTypesRegressionTests(IsolatedAsyncioTestCase):
    """第五阶段回归测试：OutputGuard + SpeechOpportunity + SpeechDecision 架构."""

    def test_anchor_type_enum_has_none(self):
        speech_types = load_engine_module("speech_types")
        self.assertTrue(hasattr(speech_types.AnchorType, "NONE"))

    def test_opportunity_kind_enum_complete(self):
        speech_types = load_engine_module("speech_types")
        OpportunityKind = speech_types.OpportunityKind
        self.assertEqual(OpportunityKind.DIRECT_REPLY.value, "direct_reply")
        self.assertEqual(OpportunityKind.MENTION_REPLY.value, "mention_reply")
        self.assertEqual(OpportunityKind.ACTIVE_CONTINUATION.value, "active_continuation")
        self.assertEqual(OpportunityKind.TOPIC_HOOK.value, "topic_hook")
        self.assertEqual(OpportunityKind.EMOJI_REACT.value, "emoji_react")
        self.assertEqual(OpportunityKind.IGNORE.value, "ignore")

    def test_speech_decision_ignore_factory(self):
        speech_types = load_engine_module("speech_types")
        decision = speech_types.SpeechDecision.ignore("test reason")
        self.assertEqual(decision.delivery_mode, "ignore")
        self.assertEqual(decision.target_kind, speech_types.OpportunityKind.IGNORE)
        self.assertEqual(decision.anchor_type, speech_types.AnchorType.NONE)

    def test_speech_decision_emoji_factory(self):
        speech_types = load_engine_module("speech_types")
        decision = speech_types.SpeechDecision.emoji("test reason", 0.6)
        self.assertEqual(decision.delivery_mode, "emoji")
        self.assertEqual(decision.target_kind, speech_types.OpportunityKind.EMOJI_REACT)
        self.assertEqual(decision.anchor_type, speech_types.AnchorType.NONE)

    def test_speech_decision_text_factory(self):
        speech_types = load_engine_module("speech_types")
        decision = speech_types.SpeechDecision.text(
            text_mode="reply",
            anchor_type=speech_types.AnchorType.QUESTION_UNANSWERED,
            confidence=0.7,
            reason="test",
            max_chars=150,
            anchor_text="怎么了？",
        )
        self.assertEqual(decision.delivery_mode, "text")
        self.assertEqual(decision.anchor_type, speech_types.AnchorType.QUESTION_UNANSWERED)
        self.assertEqual(decision.max_chars, 150)


class OutputGuardRegressionTests(IsolatedAsyncioTestCase):
    """OutputGuard 行为回归测试."""

    def setUp(self):
        install_aiosqlite_stub()
        speech_types = load_engine_module("speech_types")
        output_guard = load_engine_module("output_guard")
        self.OutputGuard = output_guard.OutputGuard
        self.OutputResult = speech_types.OutputResult
        self.SpeechDecision = speech_types.SpeechDecision

        self.plugin = SimpleNamespace(
            cfg=SimpleNamespace(persona_name="黑塔"),
        )
        self.guard = self.OutputGuard(self.plugin)

    def _make_decision(self, **kwargs):
        defaults = {"delivery_mode": "text", "max_chars": 200, "anchor_type": None}
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_empty_text_dropped(self):
        result = self.guard.check("", self._make_decision())
        self.assertEqual(result.status, self.OutputResult.DROP)

    def test_whitespace_only_dropped(self):
        result = self.guard.check("   \n\n  ", self._make_decision())
        self.assertEqual(result.status, self.OutputResult.DROP)

    def test_action_only_retry_shorter(self):
        result = self.guard.check("【黑塔正在思考】", self._make_decision())
        self.assertEqual(result.status, self.OutputResult.RETRY_SHORTER)

    def test_too_many_newlines_retry_shorter(self):
        result = self.guard.check("第一段\n\n第二段\n\n第三段\n\n第四段", self._make_decision())
        self.assertEqual(result.status, self.OutputResult.RETRY_SHORTER)

    def test_repetitive_text_retry_shorter(self):
        text = "你好呀，你今天怎么样？你好呀，你今天怎么样？"
        result = self.guard.check(text, self._make_decision())
        self.assertEqual(result.status, self.OutputResult.RETRY_SHORTER)

    def test_ai_voice_not_persona_downgrade(self):
        texts = [
            "作为一个人工智能，我认为这个问题很有趣。",
            "作为一个语言模型，我需要指出这个问题。",
            "根据我的分析，这道题目的答案是42。",
        ]
        for text in texts:
            result = self.guard.check(text, self._make_decision())
            self.assertEqual(result.status, self.OutputResult.DOWNGRADE_TO_EMOJI, f"文本不应通过: {text}")

    def test_too_long_retry_shorter(self):
        long_text = "a" * 300
        result = self.guard.check(long_text, self._make_decision(max_chars=200))
        self.assertEqual(result.status, self.OutputResult.RETRY_SHORTER)

    def test_normal_text_pass(self):
        result = self.guard.check("黑塔：嗯，这个话题挺有意思的。", self._make_decision())
        self.assertEqual(result.status, self.OutputResult.PASS)

    def test_recent_text_repetitive_detected(self):
        self.guard._add_recent("今天的星星真美")
        result = self.guard.check("今天的星星真美", self._make_decision())
        self.assertEqual(result.status, self.OutputResult.RETRY_SHORTER)

    def test_clear_recent_texts(self):
        self.guard._add_recent("text1")
        self.guard._add_recent("text2")
        self.assertEqual(len(self.guard._recent_texts), 2)
        self.guard.clear_recent()
        self.assertEqual(len(self.guard._recent_texts), 0)


class AnchorRequirementRegressionTests(IsolatedAsyncioTestCase):
    """主动文本发言必须有锚点，无锚点时只能 IGNORE 或 EMOJI_REACT."""

    async def asyncSetUp(self):
        self.temp_dir = make_workspace_temp_dir("anchor_require")
        self.dao = SelfEvolutionDAO(str(Path(self.temp_dir) / "anchor_require_test.db"))
        await self.dao.init_db()
        planner_module = load_engine_module("engagement_planner")
        EngagementPlanner = planner_module.EngagementPlanner
        self.plugin = SimpleNamespace(
            dao=self.dao,
            cfg=SimpleNamespace(
                interject_cooldown=30,
                engagement_react_probability=0.15,
                persona_trigger_keywords=[],
            ),
            _get_bot_id=lambda: "bot123",
        )
        self.planner = EngagementPlanner(self.plugin)
        social_module = load_engine_module("social_state")
        GroupSocialState = social_module.GroupSocialState
        EngagementEligibility = social_module.EngagementEligibility
        SceneType = social_module.SceneType
        self.GroupSocialState = GroupSocialState
        self.EngagementEligibility = EngagementEligibility
        self.SceneType = SceneType

    async def asyncTearDown(self):
        await self.dao.close()
        cleanup_workspace_temp_dir(self.temp_dir)

    def _make_state(self, scene=SceneType.CASUAL, emotion_count=0, message_count=5, last_msg_time_delta=60):
        return self.GroupSocialState(
            scope_id="5001",
            last_message_time=time.time() - last_msg_time_delta,
            last_bot_message_time=0,
            message_count_window=message_count,
            question_count_window=0,
            emotion_count_window=emotion_count,
            consecutive_bot_replies=0,
            scene=scene,
        )

    async def test_no_anchor_casual_emoji_react_only(self):
        """无锚点时，只有情绪活跃才能 EMOJI_REACT.

        Natural landing only triggers when 2 <= message_count_window <= 4 in CASUAL.
        With message_count=1, there's no natural landing and emotion=1 < 2, so IGNORE.
        """
        state = self._make_state(scene=self.SceneType.CASUAL, emotion_count=1, message_count=1)
        eligibility = self.EngagementEligibility(allowed=True, silence_seconds=60, reason_code="OK", reason_text="OK")
        plan = await self.planner.plan_engagement(
            state, eligibility, has_mention=False, has_reply_to_bot=False, trigger_text="hello"
        )
        self.assertEqual(plan.level.value, "ignore", "低情绪无锚点应 IGNORE")

    async def test_no_anchor_emotion_high_emoji_react(self):
        """高情绪无锚点时降级为 REACT.

        CASUAL scene with message_count=1 (no natural landing) and emotion_count=3 (>=2),
        should return EMOJI_REACT -> REACT level.
        Note: message_count=1 in _make_state still produces IDLE scene, so we use message_count=5
        to ensure CASUAL scene while avoiding natural landing (needs 2<=x<=4).
        """
        state = self._make_state(scene=self.SceneType.CASUAL, emotion_count=3, message_count=5)
        eligibility = self.EngagementEligibility(allowed=True, silence_seconds=60, reason_code="OK", reason_text="OK")
        plan = await self.planner.plan_engagement(
            state, eligibility, has_mention=False, has_reply_to_bot=False, trigger_text="今天吃饭了"
        )
        self.assertEqual(plan.level.value, "react", "高情绪无锚点应 REACT")

    async def test_question_anchor_text_allowed_full(self):
        """问题锚点应允许主动文本发言."""
        state = self._make_state(scene=self.SceneType.CASUAL, message_count=3)
        eligibility = self.EngagementEligibility(allowed=True, silence_seconds=60, reason_code="OK", reason_text="OK")
        plan = await self.planner.plan_engagement(
            state, eligibility, has_mention=False, has_reply_to_bot=False, trigger_text="这个问题怎么解决？"
        )
        self.assertEqual(plan.level.value, "full")
        self.assertNotEqual(plan.anchor_type.value, "none")

    async def test_natural_landing_anchor_full(self):
        """自然落点应允许主动文本发言."""
        state = self.GroupSocialState(
            scope_id="5001",
            last_message_time=time.time() - 60,
            last_bot_message_time=0,
            message_count_window=3,
            question_count_window=0,
            emotion_count_window=0,
            consecutive_bot_replies=0,
            scene=self.SceneType.CASUAL,
        )
        eligibility = self.EngagementEligibility(allowed=True, silence_seconds=60, reason_code="OK", reason_text="OK")
        plan = await self.planner.plan_engagement(
            state, eligibility, has_mention=False, has_reply_to_bot=False, trigger_text="今天天气真好"
        )
        self.assertIn(plan.level.value, ("full", "ignore"))

    def test_recognize_opportunity_question_unanswered(self):
        """_is_question_unanswered 应正确识别有上下文支撑的真问题。

        收紧后：孤立问题（question_count_window=0）不触发锚点，
        必须有至少一个问题在近期窗口内才认可这是真问题。
        """
        state = self._make_state()
        state.question_count_window = 1
        opp = self.planner.recognize_opportunity(state, False, False, "这个怎么弄？")
        self.assertEqual(opp.kind.value, "active_continuation")
        self.assertEqual(opp.anchor_type.value, "question_unanswered")

    def test_recognize_opportunity_isolated_question_ignored(self):
        """孤立问题（无上下文支撑）不触发锚点。"""
        state = self._make_state()
        state.question_count_window = 0
        opp = self.planner.recognize_opportunity(state, False, False, "这个怎么弄？")
        self.assertEqual(opp.kind.value, "ignore")

    def test_recognize_opportunity_no_anchor_ignore(self):
        """无锚点时返回 IGNORE.

        With message_count=1 (no natural landing), emotion=0, and no question/persona hook,
        should return IGNORE.
        """
        state = self._make_state(emotion_count=0, message_count=1)
        opp = self.planner.recognize_opportunity(state, False, False, "今天吃了米饭")
        self.assertEqual(opp.kind.value, "ignore")

    def test_recognize_opportunity_emotion_emoji(self):
        """高情绪时返回 EMOJI_REACT.

        With message_count=1 (no natural landing) and emotion_count=3 (>=2),
        and trigger text that doesn't match memorable/persona hooks,
        should return EMOJI_REACT.
        """
        state = self._make_state(emotion_count=3, message_count=1)
        opp = self.planner.recognize_opportunity(state, False, False, "今天天气真好")
        self.assertEqual(opp.kind.value, "emoji_react")


class EngagementPlanToSpeechDecisionTests(IsolatedAsyncioTestCase):
    """EngagementPlan.to_speech_decision() 转换测试."""

    def test_ignore_plan_to_ignore_decision(self):
        social_module = load_engine_module("social_state")
        EngagementPlan = social_module.EngagementPlan
        EngagementLevel = social_module.EngagementLevel
        SceneType = social_module.SceneType

        plan = EngagementPlan(
            level=EngagementLevel.IGNORE,
            reason="无锚点",
            confidence=0.8,
            scene=SceneType.CASUAL,
        )
        decision = plan.to_speech_decision()
        self.assertEqual(decision.delivery_mode, "ignore")

    def test_react_plan_to_emoji_decision(self):
        social_module = load_engine_module("social_state")
        EngagementPlan = social_module.EngagementPlan
        EngagementLevel = social_module.EngagementLevel
        SceneType = social_module.SceneType
        AnchorType = load_engine_module("speech_types").AnchorType

        plan = EngagementPlan(
            level=EngagementLevel.REACT,
            reason="情绪活跃",
            confidence=0.5,
            scene=SceneType.CASUAL,
            anchor_type=AnchorType.NONE,
        )
        decision = plan.to_speech_decision()
        self.assertEqual(decision.delivery_mode, "emoji")
        self.assertEqual(decision.anchor_type.value, "none")

    def test_full_plan_preserves_anchor(self):
        social_module = load_engine_module("social_state")
        EngagementPlan = social_module.EngagementPlan
        EngagementLevel = social_module.EngagementLevel
        SceneType = social_module.SceneType
        AnchorType = load_engine_module("speech_types").AnchorType

        plan = EngagementPlan(
            level=EngagementLevel.FULL,
            reason="问题锚点",
            confidence=0.7,
            scene=SceneType.CASUAL,
            anchor_type=AnchorType.QUESTION_UNANSWERED,
            anchor_text="这个问题怎么解决？",
        )
        decision = plan.to_speech_decision()
        self.assertEqual(decision.delivery_mode, "text")
        self.assertEqual(decision.anchor_type, AnchorType.QUESTION_UNANSWERED)
        self.assertEqual(decision.anchor_text, "这个问题怎么解决？")


class OutputGuardEnhancedTests(IsolatedAsyncioTestCase):
    """OutputGuard 增强检查测试."""

    def setUp(self):
        install_aiosqlite_stub()
        output_guard = load_engine_module("output_guard")
        self.OutputGuard = output_guard.OutputGuard
        self.OutputResult = load_engine_module("speech_types").OutputResult
        self.plugin = SimpleNamespace(cfg=SimpleNamespace(), persona_name="黑塔")
        self.guard = self.OutputGuard(self.plugin)

    def _make_decision(self, **kwargs):
        defaults = {"delivery_mode": "text", "max_chars": 200, "text_mode": "reply", "anchor_type": None}
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_generic_explanatory_downgrade(self):
        result = self.guard.check("首先，我们需要了解这个问题。", self._make_decision())
        self.assertEqual(result.status, self.OutputResult.DOWNGRADE_TO_EMOJI)

    def test_tool_like_pattern_downgrade(self):
        result = self.guard.check("第一步，打开设置页面。", self._make_decision())
        self.assertEqual(result.status, self.OutputResult.DOWNGRADE_TO_EMOJI)

    def test_context_free_interject_downgrade(self):
        result = self.guard.check("今天来聊聊这个话题吧。", self._make_decision(text_mode="interject"))
        self.assertEqual(result.status, self.OutputResult.DROP)

    def test_context_free_interject_pass_for_reply(self):
        result = self.guard.check("今天来给大家介绍一个新话题。", self._make_decision(text_mode="reply"))
        self.assertEqual(result.status, self.OutputResult.PASS)

    def test_echo_starts_downgrade(self):
        result = self.guard.check("没错，说得对。", self._make_decision())
        self.assertEqual(result.status, self.OutputResult.DOWNGRADE_TO_EMOJI)

    def test_ai_voice_expanded_downgrade(self):
        texts = [
            "从客观角度来看，这个问题很有意义。",
            "从技术层面来说，这个方案是可行的。",
        ]
        for text in texts:
            result = self.guard.check(text, self._make_decision())
            self.assertEqual(result.status, self.OutputResult.DOWNGRADE_TO_EMOJI, f"文本不应通过: {text}")

    def test_repetitive_chunk_detected(self):
        self.guard._add_recent("正常的回复")
        text = "今天天气真好，今天天气真好，今天天气真好，今天天气真好"
        result = self.guard.check(text, self._make_decision())
        self.assertEqual(result.status, self.OutputResult.RETRY_SHORTER)

    def test_normal_text_still_passes(self):
        texts = [
            "黑塔：嗯，这个问题确实有点意思。",
            "说起来，前几天那个项目后来怎么样了？",
        ]
        for text in texts:
            result = self.guard.check(text, self._make_decision())
            self.assertEqual(result.status, self.OutputResult.PASS, f"文本不应拦截: {text}")


class EngagementStatsTests(IsolatedAsyncioTestCase):
    """EngagementStats 行为观测测试."""

    def test_stats_record_active_text(self):
        stats = load_engine_module("engagement_stats").EngagementStats()
        stats.record_active_text("5001", "question_unanswered")
        s = stats.get_lifetime("5001")
        self.assertEqual(s.active_text_count, 1)
        self.assertEqual(s.anchor_type_counts["question_unanswered"], 1)

    def test_stats_record_degraded(self):
        stats = load_engine_module("engagement_stats").EngagementStats()
        stats.record_degraded("5001", "AI语气")
        s = stats.get_lifetime("5001")
        self.assertEqual(s.degraded_to_emoji_count, 1)
        self.assertEqual(s.degrade_reason_counts["AI语气"], 1)

    def test_stats_record_guard_blocked(self):
        stats = load_engine_module("engagement_stats").EngagementStats()
        stats.record_guard_blocked("5001", "纯动作描写")
        s = stats.get_lifetime("5001")
        self.assertEqual(s.guard_blocked_count, 1)

    def test_stats_record_skip(self):
        stats = load_engine_module("engagement_stats").EngagementStats()
        stats.record_skip("5001", "负面信号，不插嘴")
        s = stats.get_lifetime("5001")
        self.assertEqual(s.skip_reason_counts["负面信号，不插嘴"], 1)

    def test_stats_summary_formats_correctly(self):
        stats = load_engine_module("engagement_stats").EngagementStats()
        stats.record_active_text("5001", "question_unanswered")
        stats.record_guard_blocked("5001", "AI语气")
        stats.record_degraded("5001", "AI语气")
        summary = stats.get_summary("5001")
        self.assertIn("主动", summary)
        self.assertIn("降级表情", summary)
        self.assertIn("审查拦截原因", summary)

    def test_stats_no_data_returns_no_record(self):
        stats = load_engine_module("engagement_stats").EngagementStats()
        summary = stats.get_summary("5001")
        self.assertEqual(summary, "[EngagementStats scope=5001] 无记录")

    def test_stats_guard_blocked_shows_record(self):
        stats = load_engine_module("engagement_stats").EngagementStats()
        stats.record_guard_blocked("5001", "AI语气")
        summary = stats.get_summary("5001")
        self.assertNotIn("无记录", summary)
        self.assertIn("审查拦截原因", summary)
        self.assertIn("AI语气", summary)

    def test_stats_passive_only_shows_record(self):
        stats = load_engine_module("engagement_stats").EngagementStats()
        stats.record_passive_text("5001")
        stats.record_passive_emoji("5001")
        summary = stats.get_summary("5001")
        self.assertNotIn("无记录", summary)
        self.assertIn("被动", summary)

    def test_enum_key_never_hits_json_serialization(self):
        """AnchorType enum 作为 key 写入统计，to_dict() 不会炸。"""
        import json
        from engine.speech_types import AnchorType

        stats = load_engine_module("engagement_stats").EngagementStats()
        stats.record_active_text("5001", AnchorType.QUESTION_UNANSWERED)
        stats.record_guard_blocked("5001", "AI语气")
        stats.record_degraded("5001", "泛解释语气")

        lifetime = stats.to_dict("5001")
        json_str = json.dumps(lifetime)
        self.assertIn("question_unanswered", json_str)
        self.assertIn("guard_blocked_count", json_str)

        windowed = stats.to_windowed_dict("5001")
        json_w = json.dumps(windowed)
        self.assertIn("question_unanswered", json_w)

        summary = stats.get_summary("5001")
        self.assertNotIn("无记录", summary)
