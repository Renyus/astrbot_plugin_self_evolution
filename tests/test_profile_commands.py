from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from tests._helpers import load_commands_module

profile_commands = load_commands_module("profile")
common_module = load_commands_module("common")
parse_target_user = common_module.parse_target_user


class _FakeEvent:
    def __init__(
        self,
        *,
        group_id=None,
        sender_id="1001",
        message_str="/view",
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


class ProfileCommandTests(IsolatedAsyncioTestCase):
    async def test_handle_view_uses_private_scope_for_self(self):
        plugin = SimpleNamespace(
            admin_users=[],
            profile=SimpleNamespace(
                view_profile=AsyncMock(return_value="profile"),
                build_profile=AsyncMock(),
            ),
        )
        event = _FakeEvent(message_str="/view")

        result = await profile_commands.handle_view(event, plugin)

        self.assertEqual(result, "profile")
        plugin.profile.view_profile.assert_awaited_once_with("private_1001", "1001")

    async def test_handle_view_rejects_other_user_in_private_chat(self):
        plugin = SimpleNamespace(
            admin_users=["1001"],
            profile=SimpleNamespace(
                view_profile=AsyncMock(),
                build_profile=AsyncMock(),
            ),
        )
        event = _FakeEvent(message_str="/view 2002", is_admin=True)

        result = await profile_commands.handle_view(event, plugin)

        self.assertEqual(result, "私聊场景仅支持查看当前会话用户的画像。")
        plugin.profile.view_profile.assert_not_awaited()

    async def test_handle_create_builds_private_profile_for_current_user(self):
        plugin = SimpleNamespace(
            admin_users=[],
            profile=SimpleNamespace(build_profile=AsyncMock(return_value="画像已创建")),
        )
        event = _FakeEvent(message_str="/create")

        result = await profile_commands.handle_create(event, plugin)

        self.assertEqual(result, "画像已创建")
        plugin.profile.build_profile.assert_awaited_once_with(
            "1001",
            "private_1001",
            mode="create",
            umo="qq:private:1001",
        )

    async def test_handle_update_rejects_other_user_in_private_chat(self):
        plugin = SimpleNamespace(
            admin_users=["1001"],
            profile=SimpleNamespace(build_profile=AsyncMock()),
        )
        event = _FakeEvent(message_str="/update 2002", is_admin=True)

        result = await profile_commands.handle_update(event, plugin)

        self.assertEqual(result, "私聊场景仅支持更新当前会话用户的画像。")
        plugin.profile.build_profile.assert_not_awaited()

    async def test_handle_delete_rejects_other_user_in_group_for_non_admin(self):
        plugin = SimpleNamespace(
            admin_users=[],
            profile=SimpleNamespace(delete_profile=AsyncMock()),
        )
        event = _FakeEvent(group_id="5001", message_str="/delete 2002", is_admin=False)

        result = await profile_commands.handle_delete(event, plugin)

        self.assertEqual(result, "权限拒绝：普通用户无法操作他人画像。")
        plugin.profile.delete_profile.assert_not_awaited()


class ParseTargetUserTests(IsolatedAsyncioTestCase):
    def _make_event(self, message_str, sender_id="1001"):
        return _FakeEvent(message_str=message_str, sender_id=sender_id)

    def test_profile_create_no_user_defaults_to_sender(self):
        event = self._make_event("/profile create")
        target, raw = parse_target_user(event)
        self.assertEqual(target, "1001")
        self.assertEqual(raw, "")

    def test_profile_create_with_user_id(self):
        event = self._make_event("/profile create 2002")
        target, raw = parse_target_user(event)
        self.assertEqual(target, "2002")
        self.assertEqual(raw, "2002")

    def test_profile_view_no_user_defaults_to_sender(self):
        event = self._make_event("/profile view")
        target, raw = parse_target_user(event)
        self.assertEqual(target, "1001")
        self.assertEqual(raw, "")

    def test_profile_view_with_user_id(self):
        event = self._make_event("/profile view 2002")
        target, raw = parse_target_user(event)
        self.assertEqual(target, "2002")
        self.assertEqual(raw, "2002")

    def test_old_create_no_user_defaults_to_sender(self):
        event = self._make_event("/create")
        target, raw = parse_target_user(event)
        self.assertEqual(target, "1001")
        self.assertEqual(raw, "")

    def test_old_create_with_user_id(self):
        event = self._make_event("/create 2002")
        target, raw = parse_target_user(event)
        self.assertEqual(target, "2002")
        self.assertEqual(raw, "2002")

    def test_old_view_with_user_id(self):
        event = self._make_event("/view 2002")
        target, raw = parse_target_user(event)
        self.assertEqual(target, "2002")
        self.assertEqual(raw, "2002")
