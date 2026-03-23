from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from tests._helpers import load_commands_module

system_commands = load_commands_module("system")


class _FakeEvent:
    def __init__(self, *, sender_id="1001", is_admin=False):
        self._sender_id = sender_id
        self._is_admin = is_admin

    def get_sender_id(self):
        return self._sender_id

    def is_admin(self):
        return self._is_admin


class HandleHelpTests(IsolatedAsyncioTestCase):
    async def test_help_includes_san_show_and_current_command_set(self):
        plugin = SimpleNamespace(admin_users=["1001"])
        event = _FakeEvent(is_admin=True)
        result = await system_commands.handle_help(event, plugin)
        self.assertIn("/system help", result)
        self.assertIn("/system version", result)
        self.assertIn("/今日老婆", result)
        self.assertIn("/san show", result)
        self.assertIn("/profile view", result)
        self.assertIn("/sticker list [页码]", result)
        self.assertIn("/evolution review", result)
