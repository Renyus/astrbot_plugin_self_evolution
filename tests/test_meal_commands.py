# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock


class _FakeEvent:
    def __init__(self, *, group_id=None, sender_id="1001", is_admin=False):
        self._group_id = group_id
        self._sender_id = sender_id
        self._is_admin = is_admin

    def get_group_id(self):
        return self._group_id

    def get_sender_id(self):
        return self._sender_id

    def is_admin(self):
        return self._is_admin

    @property
    def is_at_or_wake_command(self):
        return getattr(self, "_is_at_or_wake_command", False)

    @is_at_or_wake_command.setter
    def is_at_or_wake_command(self, value):
        self._is_at_or_wake_command = value

    async def reply(self, text):
        pass


def _make_mock_meal_store():
    store = MagicMock()
    store.add_meal = AsyncMock(return_value=(True, "已添加：红烧肉（当前 1 道菜）"))
    store.del_meal = AsyncMock(return_value=(True, "已删除：红烧肉（剩余 0 道菜）"))
    return store


class MealStoreCommandLogicTests(IsolatedAsyncioTestCase):
    async def test_addmeal_success(self):
        store = _make_mock_meal_store()
        store.add_meal = AsyncMock(return_value=(True, "已添加：红烧肉（当前 1 道菜）"))

        success, message = await store.add_meal("group1", "红烧肉", 100)
        self.assertTrue(success)
        self.assertIn("红烧肉", message)

    async def test_addmeal_deduplication(self):
        store = _make_mock_meal_store()
        store.add_meal = AsyncMock(return_value=(False, "'红烧肉' 已在菜单中"))

        success, message = await store.add_meal("group1", "红烧肉", 100)
        self.assertFalse(success)
        self.assertIn("已在菜单中", message)

    async def test_addmeal_exceed_limit(self):
        store = _make_mock_meal_store()
        store.add_meal = AsyncMock(return_value=(False, "菜单已满（100 道），请先删除一些菜品"))

        success, message = await store.add_meal("group1", "新菜", 100)
        self.assertFalse(success)
        self.assertIn("菜单已满", message)

    async def test_delmeal_success(self):
        store = _make_mock_meal_store()
        store.del_meal = AsyncMock(return_value=(True, "已删除：红烧肉（剩余 0 道菜）"))

        success, message = await store.del_meal("group1", "红烧肉")
        self.assertTrue(success)
        self.assertIn("已删除", message)

    async def test_delmeal_not_found(self):
        store = _make_mock_meal_store()
        store.del_meal = AsyncMock(return_value=(False, "'不存在的菜' 不在菜单中"))

        success, message = await store.del_meal("group1", "不存在的菜")
        self.assertFalse(success)
        self.assertIn("不在菜单中", message)

    async def test_addmeal_uses_correct_group(self):
        store = _make_mock_meal_store()
        store.add_meal = AsyncMock(return_value=(True, "OK"))

        await store.add_meal("group123", "水煮鱼", 50)
        store.add_meal.assert_awaited_once_with("group123", "水煮鱼", 50)

    async def test_delmeal_uses_correct_group(self):
        store = _make_mock_meal_store()
        store.del_meal = AsyncMock(return_value=(True, "OK"))

        await store.del_meal("group456", "佛跳墙")
        store.del_meal.assert_awaited_once_with("group456", "佛跳墙")


class MealNLTriggerGuardTests(IsolatedAsyncioTestCase):
    async def test_eat_patterns_include_all_keywords(self):
        from tests._helpers import load_engine_module

        entertainment = load_engine_module("entertainment").EntertainmentEngine
        plugin = SimpleNamespace(
            meal_store=MagicMock(),
            cfg=SimpleNamespace(
                entertainment_enabled=True, meal_eat_keywords=["吃啥", "吃什么", "今天吃啥", "今天吃什么", "吃点啥"]
            ),
        )
        eng = entertainment(plugin)

        self.assertIn("吃啥", eng.eat_keywords)
        self.assertIn("吃什么", eng.eat_keywords)
        self.assertIn("今天吃啥", eng.eat_keywords)
        self.assertIn("今天吃什么", eng.eat_keywords)
        self.assertIn("吃点啥", eng.eat_keywords)

    async def test_banquet_patterns_include_all_keywords(self):
        from tests._helpers import load_engine_module

        entertainment = load_engine_module("entertainment").EntertainmentEngine
        plugin = SimpleNamespace(
            meal_store=MagicMock(),
            cfg=SimpleNamespace(
                entertainment_enabled=True, meal_banquet_keywords=["摆酒席", "开席", "整一桌", "来一桌", "上菜"]
            ),
        )
        eng = entertainment(plugin)

        self.assertIn("摆酒席", eng.banquet_keywords)
        self.assertIn("开席", eng.banquet_keywords)
        self.assertIn("整一桌", eng.banquet_keywords)
        self.assertIn("来一桌", eng.banquet_keywords)
        self.assertIn("上菜", eng.banquet_keywords)

    async def test_nl_trigger_skipped_when_disabled(self):
        from tests._helpers import load_engine_module

        entertainment = load_engine_module("entertainment").EntertainmentEngine
        store = MagicMock()
        store.get_random_meals = AsyncMock(return_value=["dish"])
        plugin = SimpleNamespace(meal_store=store, cfg=SimpleNamespace(entertainment_enabled=False))
        event = _FakeEvent(group_id="5001", sender_id="1001")

        eng = entertainment(plugin)
        result = await eng.handle_meal_nl_trigger(event, "今天吃啥")

        self.assertFalse(result)
        store.get_random_meals.assert_not_awaited()

    async def test_nl_trigger_skipped_for_command_message(self):
        from tests._helpers import load_engine_module

        entertainment = load_engine_module("entertainment").EntertainmentEngine
        store = MagicMock()
        store.get_random_meals = AsyncMock(return_value=["dish"])
        plugin = SimpleNamespace(meal_store=store, cfg=SimpleNamespace(entertainment_enabled=True))
        event = _FakeEvent(group_id="5001", sender_id="1001")
        event.is_at_or_wake_command = True

        eng = entertainment(plugin)
        result = await eng.handle_meal_nl_trigger(event, "今天吃啥")

        self.assertFalse(result)
        store.get_random_meals.assert_not_awaited()

    async def test_nl_trigger_skipped_when_no_group(self):
        from tests._helpers import load_engine_module

        entertainment = load_engine_module("entertainment").EntertainmentEngine
        store = MagicMock()
        store.get_random_meals = AsyncMock(return_value=["dish"])
        plugin = SimpleNamespace(meal_store=store, cfg=SimpleNamespace(entertainment_enabled=True))
        event = _FakeEvent(group_id=None, sender_id="1001")

        eng = entertainment(plugin)
        result = await eng.handle_meal_nl_trigger(event, "今天吃啥")

        self.assertFalse(result)
        store.get_random_meals.assert_not_awaited()

    async def test_nl_trigger_prompts_addmeal_when_menu_empty(self):
        from tests._helpers import load_engine_module

        entertainment = load_engine_module("entertainment").EntertainmentEngine
        store = MagicMock()
        store.get_random_meals = AsyncMock(return_value=[])
        plugin = SimpleNamespace(
            meal_store=store,
            cfg=SimpleNamespace(entertainment_enabled=True),
            context=MagicMock(
                platform_manager=MagicMock(platform_insts=[MagicMock(bot=MagicMock(send_group_msg=AsyncMock()))])
            ),
        )
        event = _FakeEvent(group_id="5001", sender_id="1001")

        eng = entertainment(plugin)
        result = await eng.handle_meal_nl_trigger(event, "今天吃啥")

        self.assertTrue(result)
        bot = plugin.context.platform_manager.platform_insts[0].bot
        bot.send_group_msg.assert_awaited_once()
        args = bot.send_group_msg.call_args
        self.assertIn("/addmeal", args[1]["message"][0]["data"]["text"])
