from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, call

from tests._helpers import load_engine_module

reflection_module = load_engine_module("reflection")
DailyBatchProcessor = reflection_module.DailyBatchProcessor
SessionReflection = reflection_module.SessionReflection


class DailyBatchProcessorTests(IsolatedAsyncioTestCase):
    async def test_generate_session_reflection_uses_umo_for_provider_lookup(self):
        provider = SimpleNamespace(
            text_chat=AsyncMock(
                return_value=SimpleNamespace(
                    completion_text='{"self_correction":"be careful","explicit_facts":["fact"],"cognitive_bias":"none"}'
                )
            )
        )
        get_using_provider = MagicMock(return_value=provider)
        plugin = SimpleNamespace(context=SimpleNamespace(get_using_provider=get_using_provider))
        reflection = SessionReflection(plugin)

        result = await reflection.generate_session_reflection("history", umo="qq:group:session-2")

        get_using_provider.assert_called_once_with(umo="qq:group:session-2")
        self.assertEqual(result["self_correction"], "be careful")

    async def test_process_active_user_profiles_counts_sender_user_id(self):
        profile = SimpleNamespace(build_profile=AsyncMock())
        plugin = SimpleNamespace(profile=profile)
        processor = DailyBatchProcessor(plugin)

        messages = [
            {"sender": {"user_id": "1001"}},
            {"sender": {"user_id": "1001"}},
            {"sender": {"user_id": "2002"}},
            {"user_id": "3003"},
        ]

        processed = await processor.process_active_user_profiles("8888", messages, top_n=2)

        self.assertEqual(processed, 2)
        profile.build_profile.assert_has_calls(
            [
                call("1001", "8888", mode="update", force=False, umo=None),
                call("2002", "8888", mode="update", force=False, umo=None),
            ],
            any_order=False,
        )

    async def test_process_active_user_profiles_private_scope_uses_private_target_user(self):
        profile = SimpleNamespace(build_profile=AsyncMock())
        plugin = SimpleNamespace(profile=profile)
        processor = DailyBatchProcessor(plugin)

        messages = [
            {"sender": {"user_id": "7001"}},
            {"sender": {"user_id": "9999"}},
        ]

        processed = await processor.process_active_user_profiles("private_7001", messages, top_n=5, umo="qq:private:7001")

        self.assertEqual(processed, 1)
        profile.build_profile.assert_awaited_once_with(
            "7001",
            "private_7001",
            mode="update",
            force=False,
            umo="qq:private:7001",
        )

    async def test_run_daily_batch_passes_cached_group_umo(self):
        bot = SimpleNamespace(call_action=AsyncMock(return_value={"messages": [{"sender": {"user_id": "1001"}}]}))
        profile = SimpleNamespace(build_profile=AsyncMock())
        plugin = SimpleNamespace(
            context=SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[SimpleNamespace(get_client=lambda: bot)])),
            profile=profile,
            get_group_umo=MagicMock(return_value="qq:group:8888"),
        )
        processor = DailyBatchProcessor(plugin)
        processor.generate_group_daily_report = AsyncMock(return_value={"topic": "ok"})
        processor.save_group_daily_report = AsyncMock(return_value=True)
        processor.process_active_user_profiles = AsyncMock(return_value=1)

        await processor.run_daily_batch(["8888"])

        processor.generate_group_daily_report.assert_awaited_once_with(
            "8888", [{"sender": {"user_id": "1001"}}], umo="qq:group:8888"
        )
        processor.process_active_user_profiles.assert_awaited_once_with(
            "8888", [{"sender": {"user_id": "1001"}}], umo="qq:group:8888"
        )

    async def test_run_daily_batch_private_scope_uses_friend_history_and_scope_umo(self):
        bot = SimpleNamespace(call_action=AsyncMock(return_value={"messages": [{"sender": {"user_id": "7001"}}]}))
        profile = SimpleNamespace(build_profile=AsyncMock())
        plugin = SimpleNamespace(
            context=SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[SimpleNamespace(get_client=lambda: bot)])),
            profile=profile,
            get_scope_umo=MagicMock(return_value="qq:private:7001"),
        )
        processor = DailyBatchProcessor(plugin)
        processor.generate_group_daily_report = AsyncMock(return_value={"topic": "ok"})
        processor.save_group_daily_report = AsyncMock(return_value=True)
        processor.process_active_user_profiles = AsyncMock(return_value=1)

        await processor.run_daily_batch(["private_7001"])

        bot.call_action.assert_awaited_once_with("get_friend_msg_history", user_id=7001, count=100)
        processor.generate_group_daily_report.assert_awaited_once_with(
            "private_7001", [{"sender": {"user_id": "7001"}}], umo="qq:private:7001"
        )
        processor.process_active_user_profiles.assert_awaited_once_with(
            "private_7001", [{"sender": {"user_id": "7001"}}], umo="qq:private:7001"
        )
