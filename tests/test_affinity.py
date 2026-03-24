from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from tests._helpers import (
    install_aiosqlite_stub,
    make_workspace_temp_dir,
    cleanup_workspace_temp_dir,
    load_engine_module,
)
from pathlib import Path

install_aiosqlite_stub()

from dao import SelfEvolutionDAO

affinity_module = load_engine_module("affinity")
AffinityEngine = affinity_module.AffinityEngine


class FakeEvent:
    def __init__(self, message_str, sender_id="1001", group_id=None, has_at=False, has_reply=False):
        self.message_str = message_str
        self._sender_id = sender_id
        self._group_id = group_id
        self._extra = {"is_at": has_at, "has_reply": has_reply, "self_evolution_message_text": message_str}

    def get_sender_id(self):
        return self._sender_id

    def get_group_id(self):
        return self._group_id

    def get_extra(self, key, default=None):
        return self._extra.get(key, default)


class AffinityEngineTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = make_workspace_temp_dir("affinity")
        self.dao = SelfEvolutionDAO(str(Path(self.temp_dir) / "affinity_test.db"))
        await self.dao.init_db()

        self.plugin = SimpleNamespace(
            dao=self.dao,
            cfg=SimpleNamespace(
                affinity_auto_enabled=True,
                affinity_direct_engagement_delta=1,
                affinity_friendly_language_delta=1,
                affinity_hostile_language_delta=-2,
                affinity_returning_user_delta=1,
                affinity_direct_engagement_cooldown_minutes=360,
                affinity_friendly_daily_limit=2,
                affinity_hostile_cooldown_minutes=60,
                affinity_returning_user_daily_limit=1,
            ),
            _get_bot_id=lambda: "99999",
        )
        self.engine = AffinityEngine(self.plugin)

    async def asyncTearDown(self):
        await self.dao.close()
        cleanup_workspace_temp_dir(self.temp_dir)

    async def test_at_bot_triggers_direct_engagement(self):
        event = FakeEvent("你好呀", sender_id="1001", group_id="5001", has_at=True)
        signals = await self.engine.process_message(event)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].signal_type, "direct_engagement")
        self.assertEqual(signals[0].delta, 1)
        score = await self.dao.get_affinity("1001")
        self.assertEqual(score, 51)

    async def test_reply_bot_triggers_direct_engagement(self):
        event = FakeEvent("你好呀", sender_id="1001", group_id="5001", has_reply=True)
        signals = await self.engine.process_message(event)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].signal_type, "direct_engagement")

    async def test_private_message_triggers_direct_engagement(self):
        event = FakeEvent("你好", sender_id="1001", group_id=None)
        signals = await self.engine.process_message(event)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].signal_type, "direct_engagement")

    async def test_friendly_word_triggers_friendly_language(self):
        event = FakeEvent("谢谢你的帮助", sender_id="1001", group_id="5001")
        signals = await self.engine.process_message(event)
        self.assertTrue(any(s.signal_type == "friendly_language" for s in signals))

    async def test_hostile_word_triggers_hostile_language(self):
        event = FakeEvent("滚，烦死了", sender_id="1001", group_id="5001")
        signals = await self.engine.process_message(event)
        self.assertTrue(any(s.signal_type == "hostile_language" for s in signals))
        score = await self.dao.get_affinity("1001")
        self.assertEqual(score, 48)

    async def test_command_only_message_no_signal(self):
        event = FakeEvent("/help", sender_id="1001", group_id="5001")
        signals = await self.engine.process_message(event)
        self.assertEqual(len(signals), 0)

    async def test_same_signal_cooldown(self):
        event1 = FakeEvent("@bot 你好", sender_id="1001", group_id="5001", has_at=True)
        await self.engine.process_message(event1)

        event2 = FakeEvent("@bot 你好又来", sender_id="1001", group_id="5001", has_at=True)
        signals = await self.engine.process_message(event2)
        direct_signals = [s for s in signals if s.signal_type == "direct_engagement"]
        self.assertEqual(len(direct_signals), 0)

    async def test_auto_disabled_no_signals(self):
        self.plugin.cfg.affinity_auto_enabled = False
        event = FakeEvent("@bot 你好", sender_id="1001", group_id="5001", has_at=True)
        signals = await self.engine.process_message(event)
        self.assertEqual(len(signals), 0)

    async def test_bot_self_message_no_signals(self):
        event = FakeEvent("我自己发的一条消息", sender_id="99999", group_id="5001", has_at=True)
        signals = await self.engine.process_message(event)
        self.assertEqual(len(signals), 0)


class AffinityDAOSignalTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = make_workspace_temp_dir("affinity_dao")
        self.dao = SelfEvolutionDAO(str(Path(self.temp_dir) / "affinity_dao_test.db"))
        await self.dao.init_db()

    async def asyncTearDown(self):
        await self.dao.close()
        cleanup_workspace_temp_dir(self.temp_dir)

    async def test_can_apply_first_time(self):
        can, reason = await self.dao.can_apply_affinity_signal("1001", "direct_engagement", 360)
        self.assertTrue(can)
        self.assertEqual(reason, "ok")

    async def test_record_and_check_signal(self):
        await self.dao.record_affinity_signal("1001", "5001", "direct_engagement", 1)
        can, reason = await self.dao.can_apply_affinity_signal("1001", "direct_engagement", 360)
        self.assertFalse(can)
        self.assertTrue("cooldown" in reason)

    async def test_daily_limit_enforced(self):
        for _ in range(3):
            await self.dao.record_affinity_signal("1001", "5001", "friendly_language", 1)
        can, reason = await self.dao.can_apply_affinity_signal("1001", "friendly_language", 360, daily_limit=2)
        self.assertFalse(can)
        self.assertTrue("daily_limit" in reason)

    async def test_check_returning_user(self):
        was_returning = await self.dao.check_returning_user("1001")
        self.assertFalse(was_returning)

    async def test_get_affinity_debug_info(self):
        await self.dao.update_affinity("1001", 5)
        await self.dao.record_affinity_signal("1001", "5001", "direct_engagement", 1)
        info = await self.dao.get_affinity_debug_info("1001")
        self.assertEqual(info["user_id"], "1001")
        self.assertEqual(info["affinity_score"], 55)
        self.assertEqual(len(info["recent_signals"]), 1)
        self.assertEqual(info["recent_signals"][0]["signal_type"], "direct_engagement")
