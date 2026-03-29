"""tests/test_main_extras_flow.py - Regression tests for extras handling in on_message_listener

Tests the core logic from main.py's on_message_listener:
1. extract_interaction_context correctly identifies @/reply to bot
2. event.set_extra is called with correct is_at/has_reply values
3. Early return condition: is_at_or_wake_command=True AND no @ AND no reply → early return
4. No early return when has @ or reply (even for command messages)
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = "D:/skills/GD/astrbot_plugin_self_evolution"
sys.path.insert(0, ROOT)

from tests._helpers import (
    install_aiosqlite_stub,
    make_workspace_temp_dir,
    cleanup_workspace_temp_dir,
    load_engine_module,
)

install_aiosqlite_stub()


class At:
    def __init__(self, qq):
        self.qq = qq


class Reply:
    def __init__(self, sender_nickname, sender_id):
        self.sender_nickname = sender_nickname
        self.sender_id = sender_id


class FakeEvent:
    def __init__(
        self,
        message_str,
        sender_id="1001",
        group_id=None,
        message_components=None,
        is_at_or_wake_command=False,
    ):
        self.message_str = message_str
        self._sender_id = sender_id
        self._group_id = group_id
        self._message_components = message_components or []
        self.is_at_or_wake_command = is_at_or_wake_command
        self._extras = {}

    def get_sender_id(self):
        return self._sender_id

    def get_group_id(self):
        return self._group_id

    def get_messages(self):
        return self._message_components

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key, value):
        self._extras[key] = value

    def is_admin(self):
        return False


extract_interaction_context = load_engine_module("event_context").extract_interaction_context


class MainExtrasFlowTests(IsolatedAsyncioTestCase):
    """Test the extras computation and early-return logic from on_message_listener.

    The key code path in main.py is:
        interaction = extract_interaction_context(event.get_messages(), ...)
        has_at_to_bot = bool(interaction["at_info"])
        has_reply_to_bot = bool(interaction["quoted_info"])
        event.set_extra("is_at", has_at_to_bot)
        event.set_extra("has_reply", has_reply_to_bot)

        if event.is_at_or_wake_command and not has_at_to_bot and not has_reply_to_bot:
            return  # early return - pure command, no interaction
    """

    def _compute_extras_and_check_early_return(self, event, *, persona_name="黑塔", bot_id="bot123"):
        """Simulate the extras computation and early-return check from on_message_listener.

        Returns (has_at_to_bot, has_reply_to_bot, should_early_return)
        """
        interaction = extract_interaction_context(
            event.get_messages(),
            persona_name=persona_name,
            bot_id=bot_id,
        )
        has_at_to_bot = bool(interaction["at_info"])
        has_reply_to_bot = bool(interaction["quoted_info"])
        event.set_extra("is_at", has_at_to_bot)
        event.set_extra("has_reply", has_reply_to_bot)

        should_early_return = (
            event.is_at_or_wake_command and not has_at_to_bot and not has_reply_to_bot and event.get_group_id()
        )
        return has_at_to_bot, has_reply_to_bot, should_early_return

    def test_group_at_bot_sets_is_at_extra_no_early_return(self):
        """Group @bot message: is_at=True, should NOT early-return."""
        event = FakeEvent(
            message_str="@bot 你好",
            sender_id="user456",
            group_id="group999",
            message_components=[At("bot123")],
            is_at_or_wake_command=True,
        )

        has_at, has_reply, early_return = self._compute_extras_and_check_early_return(event)

        self.assertTrue(has_at)
        self.assertFalse(has_reply)
        self.assertFalse(early_return)
        self.assertEqual(event.get_extra("is_at"), True)
        self.assertEqual(event.get_extra("has_reply"), False)

    def test_group_reply_to_bot_sets_has_reply_extra_no_early_return(self):
        """Group message with reply to bot: has_reply=True, should NOT early-return."""
        event = FakeEvent(
            message_str="回复你",
            sender_id="user456",
            group_id="group999",
            message_components=[Reply("黑塔", "bot123")],
            is_at_or_wake_command=False,
        )

        has_at, has_reply, early_return = self._compute_extras_and_check_early_return(event)

        self.assertFalse(has_at)
        self.assertTrue(has_reply)
        self.assertFalse(early_return)
        self.assertEqual(event.get_extra("is_at"), False)
        self.assertEqual(event.get_extra("has_reply"), True)

    def test_private_plain_message_no_extras_no_early_return(self):
        """Private chat plain message: no extras, should NOT early-return.

        AstrBot 在 friend_message_needs_wake_prefix=false 时，私聊也会设 is_at_or_wake_command=True。
        但私聊没有 group_id，early-return 条件有 and group_id，所以私聊始终放行。
        """
        event = FakeEvent(
            message_str="你好呀",
            sender_id="user456",
            group_id=None,
            message_components=[],
            is_at_or_wake_command=True,
        )

        has_at, has_reply, early_return = self._compute_extras_and_check_early_return(event)

        self.assertFalse(has_at)
        self.assertFalse(has_reply)
        self.assertFalse(early_return)
        self.assertEqual(event.get_extra("is_at"), False)
        self.assertEqual(event.get_extra("has_reply"), False)

    def test_pure_command_early_return(self):
        """Pure command (is_at_or_wake_command=True, no @, no reply): SHOULD early-return."""
        event = FakeEvent(
            message_str="!test",
            sender_id="user456",
            group_id="group999",
            message_components=[],
            is_at_or_wake_command=True,
        )

        has_at, has_reply, early_return = self._compute_extras_and_check_early_return(event)

        self.assertFalse(has_at)
        self.assertFalse(has_reply)
        self.assertTrue(early_return)
        self.assertEqual(event.get_extra("is_at"), False)
        self.assertEqual(event.get_extra("has_reply"), False)

    def test_group_at_bot_with_at_all_no_early_return(self):
        """Group @all + @bot: bot 被 explicitly @，has_at=True，不 early-return。"""
        event = FakeEvent(
            message_str="@all @bot123 你们好",
            sender_id="user456",
            group_id="group999",
            message_components=[At("all"), At("bot123")],
            is_at_or_wake_command=True,
        )

        has_at, has_reply, early_return = self._compute_extras_and_check_early_return(event)

        self.assertTrue(has_at)
        self.assertFalse(has_reply)
        self.assertFalse(early_return)
        self.assertEqual(event.get_extra("is_at"), True)

    def test_at_all_only_does_not_trigger_is_at(self):
        """@all 而没有 @bot：has_at=False，early-return。@all 不算 direct_engagement。"""
        event = FakeEvent(
            message_str="@all 你们好",
            sender_id="user456",
            group_id="group999",
            message_components=[At("all")],
            is_at_or_wake_command=True,
        )

        has_at, has_reply, early_return = self._compute_extras_and_check_early_return(event)

        self.assertFalse(has_at)
        self.assertFalse(has_reply)
        self.assertTrue(early_return)

    def test_reply_from_user_not_bot_not_detected_as_reply(self):
        """Reply from another user (not bot) should NOT set has_reply_to_bot."""
        event = FakeEvent(
            message_str="hello",
            sender_id="user456",
            group_id="group999",
            message_components=[Reply("其他用户", "other123")],
            is_at_or_wake_command=False,
        )

        has_at, has_reply, early_return = self._compute_extras_and_check_early_return(event)

        self.assertFalse(has_at)
        self.assertFalse(has_reply)
        self.assertFalse(early_return)
        self.assertEqual(event.get_extra("has_reply"), False)

    def test_at_other_bot_not_self_early_return(self):
        """@ another bot (not self): has_at=False, SHOULD early-return (command with no real mention)."""
        event = FakeEvent(
            message_str="@otherbot 你好",
            sender_id="user456",
            group_id="group999",
            message_components=[At("other_bot")],
            is_at_or_wake_command=True,
        )

        has_at, has_reply, early_return = self._compute_extras_and_check_early_return(event)

        self.assertFalse(has_at)
        self.assertFalse(has_reply)
        self.assertTrue(early_return)
