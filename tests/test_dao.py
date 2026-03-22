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
