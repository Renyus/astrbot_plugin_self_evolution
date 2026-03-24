from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, call, patch

from tests._helpers import ROOT, load_module_from_path

tasks = load_module_from_path("scheduler_tasks", ROOT / "scheduler" / "tasks.py")


class SchedulerUtilitiesTests(IsolatedAsyncioTestCase):
    def test_get_previous_day_window_returns_previous_day(self):
        now = datetime(2025, 6, 15, 14, 30, tzinfo=timezone.utc)
        start, end, date_str = tasks._get_previous_day_window(now)
        self.assertEqual(start.date(), datetime(2025, 6, 14).date())
        self.assertEqual(end.date(), datetime(2025, 6, 15).date())
        self.assertEqual(date_str, "2025-06-14")

    def test_get_previous_day_window_defaults_to_now(self):
        before = datetime.now()
        start, end, date_str = tasks._get_previous_day_window()
        after = datetime.now()
        self.assertLessEqual(start.date(), before.date())
        self.assertGreaterEqual(end.date(), after.date())

    def test_dedupe_scopes_removes_duplicates_and_empty(self):
        result = tasks._dedupe_scopes(["1001", "  1002  ", "1001", "", None, "private_7001"])
        self.assertEqual(result, ["1001", "1002", "private_7001"])

    def test_dedupe_scopes_preserves_order(self):
        result = tasks._dedupe_scopes(["b", "a", "c", "b"])
        self.assertEqual(result, ["b", "a", "c"])


class ResolveTargetScopesTests(IsolatedAsyncioTestCase):
    async def test_whitelist_takes_priority(self):
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(target_scopes=["1001", "1002"]),
            eavesdropping=SimpleNamespace(get_active_scopes=lambda: ["2001"]),
            context=SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[])),
        )
        scopes, reason = await tasks._resolve_target_scopes(plugin, "TestTask")
        self.assertEqual(scopes, ["1001", "1002"])
        self.assertEqual(reason, "")

    async def test_whitelist_private_scopes_filtered_when_include_private_false(self):
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(target_scopes=["1001", "private_7001", "1002"]),
            eavesdropping=SimpleNamespace(get_active_scopes=lambda: []),
            context=SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[])),
        )
        scopes, reason = await tasks._resolve_target_scopes(plugin, "TestTask", include_private=False)
        self.assertEqual(scopes, ["1001", "1002"])
        self.assertNotIn("private_7001", scopes)

    async def test_active_users_used_when_no_whitelist(self):
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(target_scopes=[]),
            eavesdropping=SimpleNamespace(get_active_scopes=lambda: ["private_7001", "2001"]),
            context=SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[])),
        )
        scopes, reason = await tasks._resolve_target_scopes(plugin, "TestTask")
        self.assertEqual(scopes, ["private_7001", "2001"])

    async def test_include_private_false_filters_private_scopes(self):
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(target_scopes=[]),
            eavesdropping=SimpleNamespace(get_active_scopes=lambda: ["private_7001", "2001"]),
            context=SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[])),
        )
        scopes, reason = await tasks._resolve_target_scopes(plugin, "TestTask", include_private=False)
        self.assertEqual(scopes, ["2001"])

    async def test_platform_groups_used_when_no_whitelist_and_no_active_users(self):
        bot = SimpleNamespace(call_action=AsyncMock(return_value={"data": [{"group_id": 3001}, {"group_id": "3002"}]}))
        platform = SimpleNamespace(get_client=lambda: bot)
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(target_scopes=[]),
            eavesdropping=SimpleNamespace(get_active_scopes=lambda: []),
            context=SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[platform])),
        )
        scopes, reason = await tasks._resolve_target_scopes(plugin, "TestTask")
        self.assertEqual(scopes, ["3001", "3002"])

    async def test_returns_empty_when_no_sources(self):
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(target_scopes=[]),
            eavesdropping=SimpleNamespace(get_active_scopes=lambda: []),
            context=SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[])),
        )
        scopes, reason = await tasks._resolve_target_scopes(plugin, "TestTask")
        self.assertEqual(scopes, [])
        self.assertIn("无目标 scope", reason)


class RunTaskTests(IsolatedAsyncioTestCase):
    async def test_run_task_success_returns_result(self):
        plugin = SimpleNamespace()
        coro = AsyncMock()

        result = await tasks._run_task("TestTask", coro, plugin)

        self.assertEqual(result.task_name, "TestTask")
        self.assertTrue(result.success)
        self.assertFalse(result.skipped)
        self.assertGreaterEqual(result.elapsed_ms, 0)
        coro.assert_awaited_once_with(plugin)

    async def test_run_task_swallows_errors_when_enabled(self):
        plugin = SimpleNamespace()

        async def failing_coro(p):
            raise RuntimeError("test error")

        result = await tasks._run_task("FailingTask", failing_coro, plugin, swallow_errors=True)

        self.assertFalse(result.success)
        self.assertEqual(result.reason, "test error")
        self.assertFalse(result.skipped)

    async def test_run_task_propagates_error_when_swallow_disabled(self):
        plugin = SimpleNamespace()

        async def failing_coro(p):
            raise RuntimeError("test error")

        with self.assertRaises(RuntimeError):
            await tasks._run_task("FailingTask", failing_coro, plugin, swallow_errors=False)


class ScheduledTaskSkipTests(IsolatedAsyncioTestCase):
    async def test_scheduled_interject_skips_when_no_scopes(self):
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(target_scopes=[]),
            eavesdropping=SimpleNamespace(get_active_scopes=lambda: []),
            context=SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[])),
        )
        result = await tasks.scheduled_interject(plugin)
        self.assertTrue(result.skipped)
        self.assertIn("无目标 scope", result.reason)

    async def test_scheduled_profile_build_skips_when_disabled(self):
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(
                auto_profile_enabled=False,
                target_scopes=["3001"],
            ),
            get_group_umo=lambda g: None,
            profile=SimpleNamespace(analyze_and_build_profiles=AsyncMock()),
        )
        result = await tasks.scheduled_profile_build(plugin)
        self.assertTrue(result.skipped)
        self.assertIn("auto_profile_enabled=False", result.reason)


class SchedulerTasksTests(IsolatedAsyncioTestCase):
    async def test_scheduled_interject_falls_back_to_platform_groups(self):
        bot = SimpleNamespace(call_action=AsyncMock(return_value={"data": [{"group_id": 1001}, {"group_id": "1002"}]}))
        platform = SimpleNamespace(get_client=lambda: bot)
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(target_scopes=[]),
            context=SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[platform])),
            eavesdropping=SimpleNamespace(get_active_scopes=lambda: [], check_engagement=AsyncMock()),
        )

        await tasks.scheduled_interject(plugin)

        plugin.eavesdropping.check_engagement.assert_has_awaits([call("1001"), call("1002")])

    async def test_scheduled_reflection_falls_back_to_platform_groups_when_active_users_empty(self):
        bot = SimpleNamespace(call_action=AsyncMock(return_value={"data": [{"group_id": 2001}]}))
        platform = SimpleNamespace(get_client=lambda: bot)
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(target_scopes=[]),
            context=SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[platform])),
            eavesdropping=SimpleNamespace(get_active_scopes=lambda: []),
            dao=SimpleNamespace(
                init_db=AsyncMock(),
                recover_all_affinity=AsyncMock(),
                list_known_scopes=AsyncMock(return_value=[]),
            ),
            daily_batch=SimpleNamespace(
                run_daily_batch=AsyncMock(
                    return_value={"groups_processed": 1, "users_processed": 2, "reports_saved": 1}
                )
            ),
        )

        await tasks.scheduled_reflection(plugin)

        plugin.dao.init_db.assert_awaited_once()
        plugin.daily_batch.run_daily_batch.assert_awaited_once()

    async def test_scheduled_affinity_recovery_calls_recover_all_affinity(self):
        plugin = SimpleNamespace(
            dao=SimpleNamespace(recover_all_affinity=AsyncMock()),
        )

        await tasks.scheduled_affinity_recovery(plugin)

        plugin.dao.recover_all_affinity.assert_awaited_once_with(recovery_amount=2)

    async def test_scheduled_reflection_keeps_private_active_scopes(self):
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(target_scopes=[]),
            eavesdropping=SimpleNamespace(get_active_scopes=lambda: ["private_7001", "2001"]),
            context=SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[])),
            dao=SimpleNamespace(
                init_db=AsyncMock(),
                recover_all_affinity=AsyncMock(),
                list_known_scopes=AsyncMock(return_value=[]),
            ),
            daily_batch=SimpleNamespace(
                run_daily_batch=AsyncMock(
                    return_value={"groups_processed": 2, "users_processed": 1, "reports_saved": 2}
                )
            ),
        )

        await tasks.scheduled_reflection(plugin)

        call_args = plugin.daily_batch.run_daily_batch.call_args[0][0]
        self.assertIn("private_7001", call_args)
        self.assertIn("2001", call_args)

    async def test_scheduled_reflection_appends_known_private_scopes_after_restart(self):
        bot = SimpleNamespace(call_action=AsyncMock(return_value={"data": [{"group_id": 2001}]}))
        platform = SimpleNamespace(get_client=lambda: bot)
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(target_scopes=["3001"]),
            context=SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[platform])),
            eavesdropping=SimpleNamespace(get_active_scopes=lambda: []),
            dao=SimpleNamespace(
                init_db=AsyncMock(),
                recover_all_affinity=AsyncMock(),
                list_known_scopes=AsyncMock(return_value=["private_7001"]),
            ),
            daily_batch=SimpleNamespace(
                run_daily_batch=AsyncMock(
                    return_value={"groups_processed": 2, "users_processed": 1, "reports_saved": 2}
                )
            ),
        )

        await tasks.scheduled_reflection(plugin)

        call_args = plugin.daily_batch.run_daily_batch.call_args[0][0]
        self.assertIn("3001", call_args)
        self.assertIn("private_7001", call_args)

    async def test_scheduled_profile_build_passes_cached_group_umo(self):
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(
                auto_profile_enabled=True,
                target_scopes=["3001"],
                auto_profile_batch_size=1,
                auto_profile_batch_interval=0,
            ),
            get_group_umo=lambda group_id: "qq:group:3001",
            profile=SimpleNamespace(analyze_and_build_profiles=AsyncMock()),
        )

        await tasks.scheduled_profile_build(plugin)

        plugin.profile.analyze_and_build_profiles.assert_awaited_once_with("3001", umo="qq:group:3001")
