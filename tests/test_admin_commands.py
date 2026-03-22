from __future__ import annotations

import time
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from tests._helpers import load_commands_module

admin_commands = load_commands_module("admin")


class _FakeEvent:
    def __init__(
        self,
        *,
        group_id=None,
        sender_id="1001",
        message_str="/shut",
        is_admin=False,
        unified_msg_origin="qq:private:1001",
    ):
        self._group_id = group_id
        self._sender_id = sender_id
        self.message_str = message_str
        self.unified_msg_origin = unified_msg_origin
        self._is_admin = is_admin

    def get_group_id(self):
        return self._group_id

    def get_sender_id(self):
        return self._sender_id

    def is_admin(self):
        return self._is_admin


class HandleShutTests(IsolatedAsyncioTestCase):
    async def test_rejects_non_admin(self):
        plugin = SimpleNamespace(
            admin_users=[],
            _shut_until_by_group={},
        )
        event = _FakeEvent(group_id="5001", is_admin=False)
        result = await admin_commands.handle_shut(event, plugin, "10")
        self.assertEqual(result, "权限拒绝：此操作仅限管理员执行。")

    async def test_rejects_private_chat(self):
        plugin = SimpleNamespace(
            admin_users=["1001"],
            _shut_until_by_group={},
        )
        event = _FakeEvent(group_id=None, is_admin=True)
        result = await admin_commands.handle_shut(event, plugin, "10")
        self.assertEqual(result, "此命令需要在群聊中使用")

    async def test_shut_group_success(self):
        plugin = SimpleNamespace(
            admin_users=["1001"],
            _shut_until_by_group={},
        )
        event = _FakeEvent(group_id="5001", is_admin=True)
        result = await admin_commands.handle_shut(event, plugin, "5")
        self.assertEqual(result, "[OK] 当前群已开启闭嘴模式，持续 5 分钟")
        self.assertIn("5001", plugin._shut_until_by_group)

    async def test_cancel_shut(self):
        plugin = SimpleNamespace(
            admin_users=["1001"],
            _shut_until_by_group={"5001": time.time() + 300},
        )
        event = _FakeEvent(group_id="5001", is_admin=True)
        result = await admin_commands.handle_shut(event, plugin, "0")
        self.assertEqual(result, "[OK] 已取消当前群闭嘴模式")
        self.assertNotIn("5001", plugin._shut_until_by_group)

    async def test_show_shut_status_when_active(self):
        plugin = SimpleNamespace(
            admin_users=["1001"],
            _shut_until_by_group={"5001": time.time() + 60},
        )
        event = _FakeEvent(group_id="5001", is_admin=True)
        result = await admin_commands.handle_shut(event, plugin, "")
        self.assertIn("闭嘴模式", result)

    async def test_show_normal_status_when_not_shut(self):
        plugin = SimpleNamespace(
            admin_users=["1001"],
            _shut_until_by_group={},
        )
        event = _FakeEvent(group_id="5001", is_admin=True)
        result = await admin_commands.handle_shut(event, plugin, "")
        self.assertEqual(result, "[OK] 当前群正常模式，未闭嘴")

    async def test_invalid_minutes_rejected(self):
        plugin = SimpleNamespace(
            admin_users=["1001"],
            _shut_until_by_group={},
        )
        event = _FakeEvent(group_id="5001", is_admin=True)
        result = await admin_commands.handle_shut(event, plugin, "abc")
        self.assertEqual(result, "请输入有效的参数。")


class HandleDbTests(IsolatedAsyncioTestCase):
    async def test_rejects_non_admin(self):
        plugin = SimpleNamespace(
            admin_users=[],
            dao=SimpleNamespace(get_db_stats=AsyncMock()),
        )
        event = _FakeEvent(is_admin=False)
        result = await admin_commands.handle_db(event, plugin, "show")
        self.assertEqual(result, "权限拒绝：此操作仅限管理员执行。")

    async def test_show_db_stats(self):
        plugin = SimpleNamespace(
            admin_users=["1001"],
            _pending_db_reset={},
            dao=SimpleNamespace(
                get_db_stats=AsyncMock(
                    return_value={
                        "pending_evolutions": 0,
                        "session_reflections": 5,
                        "stickers": 3,
                    }
                ),
            ),
        )
        event = _FakeEvent(is_admin=True)
        result = await admin_commands.handle_db(event, plugin, "show")
        self.assertIn("会话反思", result)
        self.assertIn("表情包", result)

    async def test_reset_initiates_confirmation(self):
        plugin = SimpleNamespace(
            admin_users=["1001"],
            _pending_db_reset={},
            dao=SimpleNamespace(),
        )
        event = _FakeEvent(is_admin=True)
        result = await admin_commands.handle_db(event, plugin, "reset")
        self.assertIn("确认", result)
        self.assertIn("1001", plugin._pending_db_reset)

    async def test_confirm_times_out(self):
        plugin = SimpleNamespace(
            admin_users=["1001"],
            _pending_db_reset={"1001": time.time() - 1},
            dao=SimpleNamespace(),
        )
        event = _FakeEvent(is_admin=True)
        result = await admin_commands.handle_db(event, plugin, "confirm")
        self.assertIn("超时", result)

    async def test_confirm_success(self):
        plugin = SimpleNamespace(
            admin_users=["1001"],
            _pending_db_reset={"1001": time.time() + 30},
            dao=SimpleNamespace(
                reset_all_data=AsyncMock(
                    return_value={
                        "pending_evolutions": 0,
                        "session_reflections": 10,
                    }
                ),
            ),
        )
        event = _FakeEvent(is_admin=True)
        result = await admin_commands.handle_db(event, plugin, "confirm")
        self.assertIn("数据库已清空", result)
        self.assertNotIn("1001", plugin._pending_db_reset)


class CheckAdminTests(IsolatedAsyncioTestCase):
    def test_event_admin_true(self):
        plugin = SimpleNamespace(admin_users=[])
        event = _FakeEvent(is_admin=True)
        self.assertTrue(admin_commands.check_admin(event, plugin))

    def test_plugin_admin_list_true(self):
        plugin = SimpleNamespace(admin_users=["1001"])
        event = _FakeEvent(is_admin=False)
        self.assertTrue(admin_commands.check_admin(event, plugin))

    def test_both_false(self):
        plugin = SimpleNamespace(admin_users=[])
        event = _FakeEvent(is_admin=False)
        self.assertFalse(admin_commands.check_admin(event, plugin))
