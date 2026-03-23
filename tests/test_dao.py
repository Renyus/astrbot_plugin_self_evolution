from __future__ import annotations

from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from tests._helpers import (
    cleanup_workspace_temp_dir,
    install_aiosqlite_stub,
    make_workspace_temp_dir,
)

install_aiosqlite_stub()

from dao import SelfEvolutionDAO


class SelfEvolutionDAOTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = make_workspace_temp_dir("dao")
        self.dao = SelfEvolutionDAO(str(Path(self.temp_dir) / "self_evolution_test.db"))
        await self.dao.init_db()

    async def asyncTearDown(self):
        await self.dao.close()
        cleanup_workspace_temp_dir(self.temp_dir)

    async def test_reset_affinity_refreshes_cached_value(self):
        self.assertEqual(await self.dao.get_affinity("42"), 50)

        await self.dao.reset_affinity("42", 10)

        self.assertEqual(await self.dao.get_affinity("42"), 10)

    async def test_recover_all_affinity_clears_stale_cache(self):
        await self.dao.reset_affinity("42", 10)
        self.assertEqual(await self.dao.get_affinity("42"), 10)

        await self.dao.recover_all_affinity(5)

        self.assertEqual(await self.dao.get_affinity("42"), 15)

    async def test_touch_known_scope_persists_private_scope(self):
        await self.dao.touch_known_scope("private_7001")
        await self.dao.touch_known_scope("2001")

        private_scopes = await self.dao.list_known_scopes(scope_type="private")
        all_scopes = await self.dao.list_known_scopes()

        self.assertEqual(private_scopes, ["private_7001"])
        self.assertEqual(all_scopes, ["2001", "private_7001"])

    async def test_reflection_and_report_tables_exist(self):
        await self.dao.save_session_reflection("session-1", "user-1", "note", facts="fact", bias="bias")
        reflection = await self.dao.get_session_reflection("session-1", "user-1")
        self.assertIsNotNone(reflection)
        self.assertEqual(reflection["note"], "note")

        await self.dao.save_group_daily_report("6001", "summary", created_at="2026-03-22")
        report = await self.dao.get_latest_group_report("6001")
        self.assertIsNotNone(report)
        self.assertEqual(report["summary"], "summary")

        await self.dao.add_pending_evolution("persona-1", "new prompt", "reason")
        pending = await self.dao.get_pending_evolutions(limit=10, offset=0)
        self.assertEqual(len(pending), 1)

    async def test_delete_and_rebuild_recreates_database_file(self):
        await self.dao.save_session_reflection("session-1", "user-1", "note")
        db_path = Path(self.dao.db_path)
        self.assertTrue(db_path.exists())

        result = await self.dao.delete_and_rebuild()

        self.assertTrue(result["rebuilt"])
        self.assertTrue(Path(result["db_path"]).exists())
        reflection = await self.dao.get_session_reflection("session-1", "user-1")
        self.assertIsNone(reflection)
