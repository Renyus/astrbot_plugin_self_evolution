from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from tests._helpers import load_commands_module

sticker_commands = load_commands_module("sticker")


class _FakeEvent:
    def __init__(
        self,
        *,
        sender_id="1001",
        is_admin=False,
    ):
        self._sender_id = sender_id
        self._is_admin = is_admin
        self.message_str = ""

    def get_group_id(self):
        return None

    def get_sender_id(self):
        return self._sender_id

    def is_admin(self):
        return self._is_admin


class HandleStickerTests(IsolatedAsyncioTestCase):
    async def test_list_returns_empty_message(self):
        plugin = SimpleNamespace(
            dao=SimpleNamespace(
                get_stickers=AsyncMock(return_value=[]),
                get_sticker_count=AsyncMock(return_value=0),
                get_today_sticker_count=AsyncMock(return_value=0),
            ),
        )
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "list")
        self.assertEqual(result, "暂无表情包。")

    async def test_list_returns_sticker_info(self):
        plugin = SimpleNamespace(
            dao=SimpleNamespace(
                get_stickers=AsyncMock(
                    return_value=[
                        {"uuid": "u1", "user_id": "2001"},
                        {"uuid": "u2", "user_id": "2002"},
                    ]
                ),
                get_sticker_count=AsyncMock(return_value=5),
                get_today_sticker_count=AsyncMock(return_value=2),
            ),
        )
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "list")
        self.assertIn("5", result)
        self.assertIn("u1", result)
        self.assertIn("2001", result)
        self.assertIn("第 1/1 页", result)

    async def test_list_page_1_uses_zero_offset(self):
        plugin = SimpleNamespace(
            dao=SimpleNamespace(
                get_stickers=AsyncMock(return_value=[]),
                get_sticker_count=AsyncMock(return_value=0),
                get_today_sticker_count=AsyncMock(return_value=0),
            ),
        )
        event = _FakeEvent()
        await sticker_commands.handle_sticker(event, plugin, "list", "1")
        plugin.dao.get_stickers.assert_awaited_once_with(10, offset=0)

    async def test_list_page_2_uses_offset(self):
        plugin = SimpleNamespace(
            dao=SimpleNamespace(
                get_stickers=AsyncMock(return_value=[]),
                get_sticker_count=AsyncMock(return_value=0),
                get_today_sticker_count=AsyncMock(return_value=0),
            ),
        )
        event = _FakeEvent()
        await sticker_commands.handle_sticker(event, plugin, "list", "2")
        plugin.dao.get_stickers.assert_awaited_once_with(10, offset=10)

    async def test_list_invalid_page_rejected(self):
        plugin = SimpleNamespace(dao=SimpleNamespace())
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "list", "abc")
        self.assertIn("参数", result)

    async def test_delete_success(self):
        plugin = SimpleNamespace(
            dao=SimpleNamespace(
                delete_sticker_by_uuid=AsyncMock(return_value=True),
            ),
        )
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "delete", "u123")
        self.assertIn("已删除", result)

    async def test_delete_not_found(self):
        plugin = SimpleNamespace(
            dao=SimpleNamespace(
                delete_sticker_by_uuid=AsyncMock(return_value=False),
            ),
        )
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "delete", "u999")
        self.assertIn("未找到", result)

    async def test_delete_requires_param(self):
        plugin = SimpleNamespace(
            dao=SimpleNamespace(),
        )
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "delete")
        self.assertIn("请提供", result)

    async def test_clear_success(self):
        plugin = SimpleNamespace(
            dao=SimpleNamespace(
                get_sticker_count=AsyncMock(return_value=3),
                delete_oldest_sticker=AsyncMock(side_effect=[True, True, True]),
            ),
        )
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "clear")
        self.assertIn("已清空 3", result)

    async def test_clear_empty(self):
        plugin = SimpleNamespace(
            dao=SimpleNamespace(
                get_sticker_count=AsyncMock(return_value=0),
            ),
        )
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "clear")
        self.assertIn("已经是空的", result)

    async def test_stats(self):
        plugin = SimpleNamespace(
            dao=SimpleNamespace(
                get_sticker_stats=AsyncMock(return_value={"total": 42, "today": 3}),
            ),
        )
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "stats")
        self.assertIn("42", result)
        self.assertIn("3", result)

    async def test_unknown_action_shows_help(self):
        plugin = SimpleNamespace(dao=SimpleNamespace())
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "unknown_action")
        self.assertIn("/sticker list", result)


class CheckAdminTests(IsolatedAsyncioTestCase):
    def test_event_admin_true(self):
        plugin = SimpleNamespace(admin_users=[])
        event = _FakeEvent(is_admin=True)
        self.assertTrue(sticker_commands.check_admin(event, plugin))

    def test_plugin_admin_list_true(self):
        plugin = SimpleNamespace(admin_users=["1001"])
        event = _FakeEvent(is_admin=False)
        self.assertTrue(sticker_commands.check_admin(event, plugin))

    def test_both_false(self):
        plugin = SimpleNamespace(admin_users=[])
        event = _FakeEvent(is_admin=False)
        self.assertFalse(sticker_commands.check_admin(event, plugin))
