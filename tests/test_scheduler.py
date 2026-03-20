from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, call

from tests._helpers import ROOT, load_module_from_path

tasks = load_module_from_path("scheduler_tasks", ROOT / "scheduler" / "tasks.py")


class SchedulerTasksTests(IsolatedAsyncioTestCase):
    async def test_scheduled_interject_falls_back_to_platform_groups(self):
        bot = SimpleNamespace(call_action=AsyncMock(return_value={"data": [{"group_id": 1001}, {"group_id": "1002"}]}))
        platform = SimpleNamespace(get_client=lambda: bot)
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(target_group_scopes=[]),
            context=SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[platform])),
            eavesdropping=SimpleNamespace(active_users={}, interject_check_group=AsyncMock()),
        )

        await tasks.scheduled_interject(plugin)

        plugin.eavesdropping.interject_check_group.assert_has_awaits([call("1001"), call("1002")])

    async def test_scheduled_reflection_falls_back_to_platform_groups_when_active_users_empty(self):
        bot = SimpleNamespace(call_action=AsyncMock(return_value={"data": [{"group_id": 2001}]}))
        platform = SimpleNamespace(get_client=lambda: bot)
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(target_group_scopes=[]),
            context=SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[platform])),
            eavesdropping=SimpleNamespace(active_users={}),
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
        plugin.daily_batch.run_daily_batch.assert_awaited_once_with(["2001"])
        plugin.dao.recover_all_affinity.assert_awaited_once_with(recovery_amount=2)

    async def test_scheduled_reflection_keeps_private_active_scopes(self):
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(target_group_scopes=[]),
            eavesdropping=SimpleNamespace(active_users={"private_7001": {}, "2001": {}}),
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

        plugin.daily_batch.run_daily_batch.assert_awaited_once_with(["private_7001", "2001"])
        plugin.dao.recover_all_affinity.assert_awaited_once_with(recovery_amount=2)

    async def test_scheduled_reflection_appends_known_private_scopes_after_restart(self):
        bot = SimpleNamespace(call_action=AsyncMock(return_value={"data": [{"group_id": 2001}]}))
        platform = SimpleNamespace(get_client=lambda: bot)
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(target_group_scopes=["3001"]),
            context=SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[platform])),
            eavesdropping=SimpleNamespace(active_users={}),
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

        plugin.daily_batch.run_daily_batch.assert_awaited_once_with(["3001", "private_7001"])

    async def test_scheduled_profile_build_passes_cached_group_umo(self):
        plugin = SimpleNamespace(
            cfg=SimpleNamespace(
                auto_profile_enabled=True,
                target_group_scopes=["3001"],
                auto_profile_batch_size=1,
                auto_profile_batch_interval=0,
            ),
            get_group_umo=AsyncMock(return_value="qq:group:3001"),
            profile=SimpleNamespace(analyze_and_build_profiles=AsyncMock()),
        )

        plugin.get_group_umo = lambda group_id: "qq:group:3001"

        await tasks.scheduled_profile_build(plugin)

        plugin.profile.analyze_and_build_profiles.assert_awaited_once_with("3001", umo="qq:group:3001")
