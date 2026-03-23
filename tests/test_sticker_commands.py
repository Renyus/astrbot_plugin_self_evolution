from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

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


def _make_mock_sticker_store(
    list_return=None,
    stats_return=None,
    get_sticker_return=None,
    get_sticker_path_return=None,
):
    store = MagicMock()
    store.list_stickers = AsyncMock(return_value=(list_return or [], 0))
    store.get_stats = AsyncMock(return_value=stats_return or {"total": 0, "today": 0})
    store.get_sticker = AsyncMock(return_value=get_sticker_return)
    store.get_sticker_path = MagicMock(return_value=get_sticker_path_return)
    store.delete_sticker = AsyncMock(return_value=True)
    store.clear_stickers = AsyncMock()
    store.sync_from_files = AsyncMock(return_value={"added": 0, "removed": 0})
    store.migrate_from_db = AsyncMock(return_value={"success": 0, "failed": 0, "errors": []})
    return store


class HandleStickerTests(IsolatedAsyncioTestCase):
    async def test_list_returns_empty_message(self):
        plugin = SimpleNamespace(
            sticker_store=_make_mock_sticker_store(list_return=[], stats_return={"total": 0, "today": 0})
        )
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "list")
        self.assertEqual(result, "暂无表情包。")

    async def test_list_returns_sticker_info(self):
        stickers = [
            {"uuid": "u1", "user_id": "2001", "filename": "abc.jpg"},
            {"uuid": "u2", "user_id": "2002", "filename": "def.jpg"},
        ]
        store = _make_mock_sticker_store(list_return=stickers, stats_return={"total": 5, "today": 2})
        store.list_stickers = AsyncMock(return_value=(stickers, 5))
        plugin = SimpleNamespace(sticker_store=store)
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "list")
        self.assertIn("5", result)
        self.assertIn("u1", result)
        self.assertIn("2001", result)
        self.assertIn("第 1/1 页", result)

    async def test_list_page_1_uses_zero_offset(self):
        store = _make_mock_sticker_store(list_return=[])
        plugin = SimpleNamespace(sticker_store=store)
        event = _FakeEvent()
        await sticker_commands.handle_sticker(event, plugin, "list", "1")
        store.list_stickers.assert_awaited_once_with(10, 0)

    async def test_list_page_2_uses_offset(self):
        store = _make_mock_sticker_store(list_return=[])
        plugin = SimpleNamespace(sticker_store=store)
        event = _FakeEvent()
        await sticker_commands.handle_sticker(event, plugin, "list", "2")
        store.list_stickers.assert_awaited_once_with(10, 10)

    async def test_list_invalid_page_rejected(self):
        plugin = SimpleNamespace(sticker_store=_make_mock_sticker_store())
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "list", "abc")
        self.assertIn("参数", result)

    async def test_delete_success(self):
        store = _make_mock_sticker_store()
        plugin = SimpleNamespace(sticker_store=store)
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "delete", "u123")
        self.assertIn("已删除", result)
        store.delete_sticker.assert_awaited_once_with("u123")

    async def test_delete_not_found(self):
        store = _make_mock_sticker_store()
        store.delete_sticker = AsyncMock(return_value=False)
        plugin = SimpleNamespace(sticker_store=store)
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "delete", "u999")
        self.assertIn("未找到", result)

    async def test_delete_requires_param(self):
        plugin = SimpleNamespace(sticker_store=_make_mock_sticker_store())
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "delete")
        self.assertIn("请提供", result)

    async def test_preview_success(self):
        fake_path = MagicMock(spec=Path)
        fake_path.__str__ = MagicMock(return_value="/fake/path/abc.jpg")
        fake_path.exists = MagicMock(return_value=True)

        store = _make_mock_sticker_store(
            get_sticker_return={"uuid": "u123", "filename": "abc.jpg"},
            get_sticker_path_return=fake_path,
        )
        plugin = SimpleNamespace(sticker_store=store)
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "preview", "u123")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["uuid"], "u123")
        self.assertIn("abc.jpg", result["image_path"])

    async def test_preview_not_found(self):
        store = _make_mock_sticker_store(get_sticker_return=None)
        plugin = SimpleNamespace(sticker_store=store)
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "preview", "u999")
        self.assertIn("未找到", result)

    async def test_preview_requires_param(self):
        plugin = SimpleNamespace(sticker_store=_make_mock_sticker_store())
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "preview")
        self.assertIn("请提供", result)

    async def test_clear_success(self):
        store = _make_mock_sticker_store()
        plugin = SimpleNamespace(sticker_store=store)
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "clear")
        self.assertIn("已清空", result)
        store.clear_stickers.assert_awaited_once()

    async def test_stats(self):
        store = _make_mock_sticker_store(stats_return={"total": 42, "today": 3})
        plugin = SimpleNamespace(sticker_store=store)
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "stats")
        self.assertIn("42", result)
        self.assertIn("总计", result)

    async def test_unknown_action_shows_help(self):
        plugin = SimpleNamespace(sticker_store=_make_mock_sticker_store())
        event = _FakeEvent()
        result = await sticker_commands.handle_sticker(event, plugin, "unknown_action")
        self.assertIn("/sticker list", result)
        self.assertIn("/sticker add", result)


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
