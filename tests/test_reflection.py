from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, call

from tests._helpers import load_engine_module

reflection_module = load_engine_module("reflection")
DailyBatchProcessor = reflection_module.DailyBatchProcessor
SessionReflection = reflection_module.SessionReflection


class DailyBatchProcessorTests(IsolatedAsyncioTestCase):
    @staticmethod
    def _shanghai_dt(year: int, month: int, day: int, hour: int = 0, minute: int = 0):
        return datetime(year, month, day, hour, minute, tzinfo=timezone(timedelta(hours=8)))

    @staticmethod
    def _build_message(message_seq: int, when: datetime, user_id: str, nickname: str, text: str):
        return {
            "message_seq": message_seq,
            "message_id": message_seq,
            "time": int(when.timestamp()),
            "sender": {"user_id": user_id, "nickname": nickname},
            "message": [{"type": "text", "data": {"text": text}}],
        }

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
        plugin = SimpleNamespace(profile=profile, _get_bot_id=MagicMock(return_value="1001"))
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
                call("2002", "8888", mode="update", force=False, umo=None),
                call("3003", "8888", mode="update", force=False, umo=None),
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
        profile = SimpleNamespace(build_profile=AsyncMock())
        plugin = SimpleNamespace(
            context=SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[SimpleNamespace(get_client=lambda: None)])),
            profile=profile,
            get_group_umo=MagicMock(return_value="qq:group:8888"),
        )
        processor = DailyBatchProcessor(plugin)
        processor._fetch_scope_messages = AsyncMock(return_value=([{"sender": {"user_id": "1001"}}], "2026-03-18"))
        processor.generate_group_daily_report = AsyncMock(return_value={"topic": "ok"})
        processor.save_group_daily_report = AsyncMock(return_value=True)
        processor.process_active_user_profiles = AsyncMock(return_value=1)

        await processor.run_daily_batch(["8888"])

        processor.generate_group_daily_report.assert_awaited_once_with(
            "8888", [{"sender": {"user_id": "1001"}}], umo="qq:group:8888", summary_date="2026-03-18"
        )
        processor.save_group_daily_report.assert_awaited_once_with(
            "8888", {"topic": "ok"}, summary_date="2026-03-18"
        )
        processor.process_active_user_profiles.assert_awaited_once_with(
            "8888", [{"sender": {"user_id": "1001"}}], umo="qq:group:8888"
        )

    async def test_run_daily_batch_private_scope_uses_friend_history_and_scope_umo(self):
        profile = SimpleNamespace(build_profile=AsyncMock())
        plugin = SimpleNamespace(
            context=SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[SimpleNamespace(get_client=lambda: None)])),
            profile=profile,
            get_scope_umo=MagicMock(return_value="qq:private:7001"),
        )
        processor = DailyBatchProcessor(plugin)
        processor._fetch_scope_messages = AsyncMock(return_value=([{"sender": {"user_id": "7001"}}], "2026-03-18"))
        processor.generate_group_daily_report = AsyncMock(return_value={"topic": "ok"})
        processor.save_group_daily_report = AsyncMock(return_value=True)
        processor.process_active_user_profiles = AsyncMock(return_value=1)

        await processor.run_daily_batch(["private_7001"])

        processor.generate_group_daily_report.assert_awaited_once_with(
            "private_7001",
            [{"sender": {"user_id": "7001"}}],
            umo="qq:private:7001",
            summary_date="2026-03-18",
        )
        processor.save_group_daily_report.assert_awaited_once_with(
            "private_7001", {"topic": "ok"}, summary_date="2026-03-18"
        )
        processor.process_active_user_profiles.assert_awaited_once_with(
            "private_7001", [{"sender": {"user_id": "7001"}}], umo="qq:private:7001"
        )

    async def test_fetch_scope_messages_collects_previous_day_across_pages(self):
        reference_dt = self._shanghai_dt(2026, 3, 19, 10, 0)
        page_1 = {
            "messages": [
                self._build_message(1000, self._shanghai_dt(2026, 3, 19, 8, 0), "1001", "Bot", "today"),
                self._build_message(999, self._shanghai_dt(2026, 3, 18, 23, 0), "2002", "Alice", "yesterday late"),
            ]
        }
        page_2 = {
            "messages": [
                self._build_message(999, self._shanghai_dt(2026, 3, 18, 23, 0), "2002", "Alice", "yesterday late"),
                self._build_message(998, self._shanghai_dt(2026, 3, 18, 10, 0), "3003", "Bob", "yesterday noon"),
                self._build_message(997, self._shanghai_dt(2026, 3, 17, 23, 50), "4004", "Carol", "too old"),
            ]
        }
        bot = SimpleNamespace(call_action=AsyncMock(side_effect=[page_1, page_2]))
        plugin = SimpleNamespace(
            context=SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[SimpleNamespace(get_client=lambda: bot)])),
        )
        processor = DailyBatchProcessor(plugin)

        messages, summary_date = await processor._fetch_scope_messages("8888", reference_dt=reference_dt)

        self.assertEqual(summary_date, "2026-03-18")
        self.assertEqual(
            [msg["sender"]["user_id"] for msg in messages],
            ["3003", "2002"],
        )
        self.assertEqual(bot.call_action.await_args_list[0].kwargs, {"group_id": 8888, "count": 100})
        self.assertEqual(bot.call_action.await_args_list[1].kwargs, {"group_id": 8888, "count": 100, "message_seq": 999})

    async def test_save_group_daily_report_uses_summary_date(self):
        dao = SimpleNamespace(save_group_daily_report=AsyncMock())
        processor = DailyBatchProcessor(SimpleNamespace(dao=dao))

        ok = await processor.save_group_daily_report("8888", {"topic": "话题"}, summary_date="2026-03-18")

        self.assertTrue(ok)
        dao.save_group_daily_report.assert_awaited_once()
        call_args = dao.save_group_daily_report.await_args
        self.assertEqual(call_args.args[0], "8888")
        self.assertIn("日期: 2026-03-18", call_args.args[1])
        self.assertEqual(call_args.kwargs["created_at"], "2026-03-18")
