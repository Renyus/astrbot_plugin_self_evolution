from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from tests._helpers import load_engine_module

MemoryManager = load_engine_module("memory").MemoryManager


class MemoryManagerTests(IsolatedAsyncioTestCase):
    async def test_get_target_scopes_keeps_private_active_sessions(self):
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(profile_group_whitelist=[]),
            eavesdropping=SimpleNamespace(active_users={"6001": {}, "private_7001": {}}),
        )
        manager = MemoryManager(plugin)

        scopes = await manager._get_target_scopes()

        self.assertEqual(scopes, ["6001", "private_7001"])

    async def test_summarize_group_passes_cached_group_umo(self):
        plugin = SimpleNamespace(get_group_umo=MagicMock(return_value="qq:group:6001"))
        manager = MemoryManager(plugin)
        manager._fetch_scope_messages = AsyncMock(return_value=["Alice: hello"])
        manager._llm_summarize = AsyncMock(return_value="summary")
        manager._save_to_knowledge_base = AsyncMock()

        await manager._summarize_group("6001")

        manager._llm_summarize.assert_awaited_once_with(["Alice: hello"], umo="qq:group:6001")
        manager._save_to_knowledge_base.assert_awaited_once_with("6001", "summary")

    async def test_summarize_private_scope_passes_cached_private_umo(self):
        plugin = SimpleNamespace(get_scope_umo=MagicMock(return_value="qq:private:7001"))
        manager = MemoryManager(plugin)
        manager._fetch_scope_messages = AsyncMock(return_value=["Alice: hi in private"])
        manager._llm_summarize = AsyncMock(return_value="summary")
        manager._save_to_knowledge_base = AsyncMock()

        await manager._summarize_scope("private_7001")

        manager._llm_summarize.assert_awaited_once_with(["Alice: hi in private"], umo="qq:private:7001")
        manager._save_to_knowledge_base.assert_awaited_once_with("private_7001", "summary")

    async def test_fetch_scope_messages_uses_friend_history_for_private_scope(self):
        bot = SimpleNamespace(
            call_action=AsyncMock(
                return_value={
                    "messages": [
                        {
                            "sender": {"user_id": 7001, "nickname": "Alice", "role": "member"},
                            "message": [{"type": "text", "data": {"text": "hello"}}],
                        }
                    ]
                }
            )
        )
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(memory_msg_count=20),
            context=SimpleNamespace(
                platform_manager=SimpleNamespace(platform_insts=[SimpleNamespace(get_client=lambda: bot)])
            )
        )
        manager = MemoryManager(plugin)

        messages = await manager._fetch_scope_messages("private_7001")

        self.assertEqual(messages, ["Alice: hello"])
        bot.call_action.assert_awaited_once_with("get_friend_msg_history", user_id=7001, count=plugin.cfg.memory_msg_count)
