from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from tests._helpers import load_engine_module

SessionMemorySummarizer = load_engine_module("session_memory_summarizer").SessionMemorySummarizer


class SessionMemorySummarizerTests(IsolatedAsyncioTestCase):
    async def test_daily_summary_accepts_group_list_dict_payload(self):
        bot = SimpleNamespace(call_action=AsyncMock(return_value={"data": [{"group_id": 6001}, {"group_id": "6002"}]}))
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(memory_debug_enabled=False),
            dao=SimpleNamespace(list_known_scopes=AsyncMock(return_value=["private_7001"])),
            context=SimpleNamespace(
                platform_manager=SimpleNamespace(platform_insts=[SimpleNamespace(get_client=lambda: bot)])
            ),
        )
        summarizer = SessionMemorySummarizer(plugin)
        summarizer._summarize_scope = AsyncMock(return_value="")

        result = await summarizer.daily_summary()

        self.assertEqual(result["skipped_scopes"], ["private_7001", "6001", "6002"])
        self.assertEqual(result["failed_scopes"], [])
        self.assertEqual(result["success_scopes"], [])
